# Docker Setup

Containerized deployment of the DTCC swaps pipeline: a background scheduler
(polls DTCC, decodes UPIs) and the FastAPI dashboard, sharing one persistent
`swaps.db`.

## Files

| File | Purpose |
|---|---|
| `Dockerfile.scheduler` | Background polling service (`scheduled_ingest.py --start-scheduler`) |
| `Dockerfile.dashboard` | FastAPI web UI (`uvicorn dashboard.app:app`), port 8000 in-container |
| `docker-compose.yml` | Wires both services to a shared `swaps-data` volume + `.env` |
| `.dockerignore` | Keeps the build context small (no `.git`, `.venv`, logs, the multi-GB `swaps.db`/`recover.sql`, etc.) |

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2) running
- A `.env` file in the repo root — copy the template and fill in real values:

  ```powershell
  Copy-Item .env.example .env
  ```

  At minimum, set:
  ```
  THETADATA_CF_ACCESS_CLIENT_ID=your_client_id
  THETADATA_CF_ACCESS_CLIENT_SECRET=your_client_secret
  DASHBOARD_API_KEY=some-long-random-string
  ```
  `docker-compose.yml` loads this file for both containers via `env_file`, so
  neither Dockerfile nor the image itself ever bakes in credentials.

## Build

```powershell
docker-compose build
# or, to build just one:
docker build -f Dockerfile.scheduler -t financialdevelopment-scheduler .
docker build -f Dockerfile.dashboard -t financialdevelopment-dashboard .
```

## First-run: initialize the schema

A brand-new `swaps-data` volume has no `swaps.db` schema yet (schema
init/migration is a separate, explicit step in this codebase — see
`SETUP_GUIDE.md`). Run it once, into the same volume the services will use:

```powershell
docker-compose run --rm scheduler python setup_db.py
```

Skip this if you already have a `swaps.db` you're bind-mounting in instead of
the default named volume (see the volumes note below).

## Run

```powershell
docker-compose up -d --build
```

This starts both containers and creates a named volume, `swaps-data`, mounted
at `/app/data` in each container. `SWAPS_DB_PATH=/app/data/swaps.db` (set in
`docker-compose.yml`) is what points both services at the same DB file — the
scheduler ingests into it, the dashboard reads from it, and the data survives
`docker-compose down` / container recreation (though not `docker-compose down
-v`, which deletes volumes).

Check status:

```powershell
docker-compose ps
docker-compose logs -f scheduler
docker-compose logs -f dashboard
```

Both services define container `HEALTHCHECK`s (visible in `docker-compose ps`
/ `docker inspect`):
- **dashboard** — `curl -f http://localhost:8000/health` against the app's
  existing `/health` endpoint.
- **scheduler** — no HTTP surface exists, so it checks the freshness of a
  heartbeat file (`/app/data/.scheduler_heartbeat`) that the scheduler's
  background job touches every 60 seconds once it's running.

## Verify

Open the dashboard at **http://localhost:8787** (host port 8787 is mapped to
the container's port 8000 per the task requirement). You should see the live
dashboard UI. Confirm the API directly:

```powershell
curl http://localhost:8787/health
```

Expected: a JSON body with `"ok": true`.

## Stopping / cleanup

```powershell
docker-compose down       # stop containers, keep swaps-data volume
docker-compose down -v    # also delete the volume (wipes swaps.db)
```

`docker-compose stop` / `down` send `SIGTERM`; both entry points
(`scheduled_ingest.py` via `shutdown_signal.py`, and uvicorn) shut down
gracefully rather than being killed mid-write.

## Using an existing local `swaps.db`

To reuse a `swaps.db` you already have locally instead of starting from an
empty named volume, bind-mount it in `docker-compose.yml` instead of using
the `swaps-data` volume, e.g. replace both services' volume entries with:

```yaml
    volumes:
      - ./swaps.db:/app/data/swaps.db
```

(Skip the "First-run: initialize the schema" step above in that case.)

## Notes / known limitations

- **`SWAPS_DB_PATH` is now honored everywhere the DB path is resolved**
  (`db_loader.py`, `swaps_query.py`, `orchestrator.py`, `setup_db.py`,
  `decode_upis.py`). Previously this env var was documented in
  `.env.example` but not actually read anywhere, which would have made the
  shared-volume setup silently write to `/app/swaps.db` (invisible to the
  volume mount) instead of the intended shared path — that gap has been
  closed as part of this change.
- The scheduler's bare `python scheduled_ingest.py` (no args) just prints
  `--help` and exits; the Dockerfile's `CMD` explicitly passes
  `--start-scheduler` so the container actually runs the polling loop.
- `orchestrator.py`'s "run a suite" feature shells out to a dedicated
  `.venv` interpreter (`SHARED_PYTHON`, resolved cross-platform to
  `.venv/Scripts/python.exe` or `.venv/bin/python`). These Dockerfiles
  install dependencies straight into the image's global site-packages and
  never create a `.venv`, so `SHARED_PYTHON` won't resolve inside the
  containers and suite-launching will report "shared interpreter not
  found." That's out of scope for this change — the dashboard's read-only
  views (swap data, ingestion state, run history) work fine regardless. If
  you need suite-launching to work in-container, create a `.venv` inside the
  image (e.g. `python -m venv .venv && .venv/bin/pip install -r
  requirements.txt`) instead of installing globally.
- `POLL_INTERVAL_MINUTES` can be overridden via `.env` or the shell
  environment (defaults to 5).
