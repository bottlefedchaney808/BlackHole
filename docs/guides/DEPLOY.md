# Deployment

Three supported targets for the FinancialDevelopment dashboard + DTCC ingestion
scheduler:

| Target     | Files                                                              | Best for                          |
|------------|--------------------------------------------------------------------|-----------------------------------|
| Heroku     | `Procfile`, `.python-version`                                       | Demos, review apps                |
| systemd    | `system/financialdevelopment-{dashboard,scheduler}.service`         | A single Linux VM (recommended)   |
| Kubernetes | `k8s/deployment.yaml`, `k8s/service.yaml`                           | Existing cluster, HA dashboard    |

Two processes make up the application:

- **dashboard** — `uvicorn dashboard.app:app`, serves the web UI and the
  orchestrator run endpoints. Reads `swaps.db`, writes `dashboard:*` rows to
  `orchestrator_runs`.
- **scheduler** — `python scheduled_ingest.py --start-scheduler`, polls DTCC's
  live slice feed every `POLL_INTERVAL_MINUTES` and decodes new UPIs. Sole
  writer of ingested swap rows.

---

## Read this first

Four properties of the current codebase shape every deployment below. None are
blockers, but ignoring them produces a deployment that looks healthy and quietly
does the wrong thing.

**1. `scheduled_ingest.py` needs `--start-scheduler`.**
A bare `python scheduled_ingest.py` prints its argparse help and exits 0. Under
any supervisor with a restart policy that becomes a silent restart loop that
never ingests anything. Every config here passes the flag.

**2. `SWAPS_DB_PATH` is declared but not yet read.**
`.env.example`, `Dockerfile.dashboard` and `Dockerfile.scheduler` all set it,
but `orchestrator.py`, `db_loader.py`, `swaps_query.py` and `setup_db.py` each
compute `DB_PATH` from their own `__file__` — always `<repo root>/swaps.db`.
- *systemd*: harmless. The repo root **is** the persistent location.
- *Kubernetes*: the manifests symlink `/app/swaps.db -> /app/data/swaps.db` at
  container start so the database lands on the PVC. See the header comment in
  `k8s/deployment.yaml` for how to remove that workaround once the code honours
  the variable.

**3. `DASHBOARD_API_KEY` has an insecure default.**
`dashboard/auth.py` falls back to the literal string
`dev-key-change-in-production` when the variable is unset. That default is in
the published source, so an unset key means `POST /run/{suite}` is open to
anyone. Set it on every target. The Kubernetes manifest marks it
`optional: false` so a missing key fails pod startup rather than degrading
silently.

**4. Suite runs are now in-process (no Windows-path subprocess dependency).**
Since Phase 7 (2026-09-04), registered modules run in-process — either from
`POST /api/widgets/{slug}/run` or via `shared.module_execution.run_selected_modules`
— so there is no longer a "Shared interpreter not found" failure mode for the
run path on Linux. (The old `POST /run/{suite}` route and its `run_suite`
subprocess driver were removed.) The dashboard's read-only views, the `/swaps`
pages and the whole ingestion pipeline were already unaffected by that old
Windows-path issue; `GET /health` still reports `shared_python_exists` as an
informational field.

**5. Multi-source data ingestion via `DATA_SOURCES` env var (optional).**
The system defaults to DTCC-only ingestion. To enable ingestion from multiple
sources (CME, OTC, etc.), set `DATA_SOURCES=DTCC,CME,OTC`. Each source must
have an adapter module in `adapters/<source_lower>_adapter.py`. The orchestrator
discovers enabled sources via `orchestrator.discover_adapters()` and uses them
in the `run_unified_sources()` flow. Dashboard endpoints support cross-source
filtering via the `?source=DTCC,CME` query parameter.

---

## Multi-source analytics

### Enable additional data sources

Each data source requires two components:

1. **Adapter module** at `adapters/<source_lower>_adapter.py` implementing
   `DataSourceAdapter` interface (see `shared/data_source.py`). For example:
   - `adapters/cme_adapter.py` with class `CMEAdapter`
   - `adapters/otc_adapter.py` with class `OTCAdapter`

2. **Enable via `DATA_SOURCES` env var** (comma-separated source names):

   ```bash
   # Single source (default)
   export DATA_SOURCES=DTCC

   # Multiple sources (parallel ingestion + aggregation)
   export DATA_SOURCES=DTCC,CME,OTC
   ```

### Dashboard cross-source endpoints

Once adapters are in place and sources are enabled, the dashboard provides:

- `GET /trades?source=DTCC,CME` — Filter swap trades by source
- `GET /instruments/{upi}?resolve_cross_source=true` — Find a UPI across all sources
- `GET /analytics/cross-source-notional?source=DTCC,CME` — Aggregate notional by source
- `GET /analytics/timeseries?source=DTCC,CME&days_back=90` — Daily time-series per source

### Unified suite runs with cross-source data

Use orchestrator's `run_unified_sources()` to:

1. Parallel-ingest from all enabled sources
2. Aggregate results before Vol_Suite analysis
3. Pass `context.data_sources` to Vol_Suite for dealer positioning across sources

Example:

```python
from orchestrator import run_unified_sources

result = run_unified_sources(
    tickers=['MSFT', 'NVDA'],
    target_years=0.25,
    sources=['DTCC', 'CME'],  # Explicit sources override DATA_SOURCES env var
)

# result includes:
# - trades_by_source: {'DTCC': 1523, 'CME': 842}
# - unified_result: { suite outputs... }
```

---

## Heroku

### Persistence caveat

Heroku dynos have an ephemeral filesystem. `swaps.db` is wiped on every deploy,
config change, and daily dyno cycle, and the `web` and `scheduler` dynos do not
share a filesystem at all — the scheduler ingests into its own copy that the
dashboard can never see. **Heroku is suitable for demos and review apps only.**
For anything durable, use systemd or Kubernetes.

### Deploy

```bash
heroku create financialdevelopment

# Buildpack + runtime. .python-version pins 3.12 to match the wheel set the
# requirements.txt pins were resolved against.
heroku buildpacks:set heroku/python

# Required. Generate a real key -- do not reuse the source default.
heroku config:set DASHBOARD_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Optional: market data credentials and ingest cadence.
heroku config:set THETADATA_CF_ACCESS_CLIENT_ID=...
heroku config:set THETADATA_CF_ACCESS_CLIENT_SECRET=...
heroku config:set POLL_INTERVAL_MINUTES=5
heroku config:set LOG_LEVEL=info

git push heroku master

# The web dyno starts automatically; the scheduler is a worker type and does not.
heroku ps:scale web=1 scheduler=1
```

### Verify

```bash
heroku ps                              # both dynos "up"
heroku logs --tail --dyno scheduler    # expect "Scheduler started / Polling every 5 minute(s)"
curl -fsS https://financialdevelopment.herokuapp.com/health | python -m json.tool
```

### Notes

- The `release: python setup_db.py --migrate` line in the `Procfile` runs before
  each release goes live. A non-zero exit blocks the deploy — which is the
  intended behaviour for a failed migration.
- `requirements.txt` is the full shared-venv union (scipy, matplotlib,
  statsmodels, arch, yfinance, yt-dlp…) and compiles to a slug in the high
  hundreds of MB. If you hit Heroku's 500 MB slug limit, split out a
  `requirements-web.txt` containing only `fastapi uvicorn jinja2 slowapi
  python-dotenv pandas numpy APScheduler httpx requests python-dateutil` and
  point the buildpack at it.
- `--workers 1` in the `Procfile` is deliberate — see the comment in
  `k8s/deployment.yaml`; the dashboard keeps in-flight run state in process
  memory.

---

## systemd (Linux VM)

The recommended production target: one host, one filesystem, one SQLite file,
no distributed-locking problem.

### Install

```bash
# 1. Code
sudo mkdir -p /opt/financialdevelopment
sudo git clone https://github.com/Zinko83/FinancialDevelopment.git /opt/financialdevelopment
cd /opt/financialdevelopment

# 2. Virtualenv at the exact path the units reference (.venv/bin/python)
sudo python3.12 -m venv .venv
sudo .venv/bin/pip install --upgrade pip
sudo .venv/bin/pip install -r requirements.txt

# 3. Schema
sudo .venv/bin/python setup_db.py --migrate
sudo .venv/bin/python setup_db.py --status     # confirm version

# 4. Ownership. www-data must be able to write swaps.db plus its -wal/-shm
#    siblings, which SQLite creates in the same directory.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin www-data 2>/dev/null || true
sudo chown -R www-data:www-data /opt/financialdevelopment
```

### Secrets

Both units read `/etc/financialdevelopment/financialdevelopment.env`. Keep it
out of the repo and off the world-readable path:

```bash
sudo mkdir -p /etc/financialdevelopment
sudo tee /etc/financialdevelopment/financialdevelopment.env >/dev/null <<EOF
DASHBOARD_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
POLL_INTERVAL_MINUTES=5
# THETADATA_CF_ACCESS_CLIENT_ID=
# THETADATA_CF_ACCESS_CLIENT_SECRET=
EOF

sudo chown root:www-data /etc/financialdevelopment/financialdevelopment.env
sudo chmod 640 /etc/financialdevelopment/financialdevelopment.env
```

### Install and start the units

```bash
sudo cp /opt/financialdevelopment/system/*.service /etc/systemd/system/
sudo systemctl daemon-reload

sudo systemctl enable --now financialdevelopment-scheduler.service
sudo systemctl enable --now financialdevelopment-dashboard.service
```

### Verify

```bash
systemctl status financialdevelopment-scheduler financialdevelopment-dashboard
journalctl -u financialdevelopment-scheduler -f     # "Scheduler started"
journalctl -u financialdevelopment-dashboard -n 50

curl -fsS http://127.0.0.1:8000/health | python3 -m json.tool
```

A healthy response has `"ok": true` and `"db_exists": true`. Expect
`"shared_python_exists": false` on Linux — see *Read this first* item 4.

### Operating

```bash
sudo systemctl restart financialdevelopment-dashboard
sudo systemctl stop financialdevelopment-scheduler     # drains gracefully, up to 300s
journalctl -u financialdevelopment-scheduler --since "1 hour ago"

# One-off jobs, run as the service user so file ownership stays consistent:
sudo -u www-data /opt/financialdevelopment/.venv/bin/python \
     /opt/financialdevelopment/scheduled_ingest.py --run-now
sudo -u www-data /opt/financialdevelopment/.venv/bin/python \
     /opt/financialdevelopment/scheduled_ingest.py --backfill
```

Stop the scheduler before running `--backfill` by hand — two writers on one
SQLite file will produce `database is locked` errors.

### Reverse proxy

The dashboard binds `0.0.0.0:8000` and speaks plain HTTP. Terminate TLS in
nginx/Caddy in front of it. The unit passes
`--forwarded-allow-ips=127.0.0.1`, so the proxy must run on the same host and
must set `X-Forwarded-For`; otherwise `auth.get_client_ip` and the slowapi rate
limiter will see the proxy's address and throttle all users as one.

---

## Kubernetes

### Build and push images

```bash
docker build -f Dockerfile.dashboard -t ghcr.io/zinko83/financialdevelopment-dashboard:1.0.0 .
docker build -f Dockerfile.scheduler -t ghcr.io/zinko83/financialdevelopment-scheduler:1.0.0 .
docker push ghcr.io/zinko83/financialdevelopment-dashboard:1.0.0
docker push ghcr.io/zinko83/financialdevelopment-scheduler:1.0.0
```

If you use a different registry, update the four `image:` fields in
`k8s/deployment.yaml`.

### Namespace and secrets

```bash
kubectl create namespace financialdevelopment

kubectl create secret generic financialdevelopment-secrets \
  -n financialdevelopment \
  --from-literal=DASHBOARD_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --from-literal=THETADATA_CF_ACCESS_CLIENT_ID='...' \
  --from-literal=THETADATA_CF_ACCESS_CLIENT_SECRET='...'
```

No Secret manifest is committed to the repo — create it with the command above,
or wire in External Secrets / Sealed Secrets.

### Check storage before applying

`replicas: 2` on the dashboard requires the `swaps-db` PVC to be genuinely
ReadWriteMany. SQLite in WAL mode mmaps a shared-memory index (`swaps.db-shm`)
and relies on POSIX advisory locks; **NFSv3 provides neither and will corrupt
the database**, not merely slow it down. CephFS, Longhorn RWX and Portworx
shared volumes are fine.

```bash
kubectl get storageclass
```

Set `storageClassName` in `k8s/deployment.yaml` to a class you actually have. If
the only RWX option is NFS, change the dashboard to `replicas: 1` and the PVC to
`ReadWriteOnce`.

### Apply

```bash
kubectl apply -n financialdevelopment -f k8s/deployment.yaml
kubectl apply -n financialdevelopment -f k8s/service.yaml
```

`k8s/deployment.yaml` creates the PVC, the 2-replica dashboard Deployment, and
the single-replica scheduler Deployment (whose init container runs
`setup_db.py --migrate` against the PVC before the scheduler starts).

### Check pods

```bash
kubectl get pods -n financialdevelopment -w
kubectl get pvc  -n financialdevelopment          # swaps-db must reach Bound
kubectl get svc  -n financialdevelopment          # note the LoadBalancer EXTERNAL-IP

kubectl describe pod -n financialdevelopment -l app.kubernetes.io/component=dashboard
```

A PVC stuck in `Pending` almost always means `storageClassName` does not exist
or the class cannot satisfy `ReadWriteMany`.

### Logs

```bash
kubectl logs -n financialdevelopment -l app.kubernetes.io/component=dashboard --all-containers -f
kubectl logs -n financialdevelopment -l app.kubernetes.io/component=scheduler -f
kubectl logs -n financialdevelopment -l app.kubernetes.io/component=scheduler -c migrate

# Health from inside the cluster, bypassing the LoadBalancer:
kubectl port-forward -n financialdevelopment svc/fd-dashboard 8000:80
curl -fsS http://127.0.0.1:8000/health | python -m json.tool
```

### Operating

```bash
kubectl rollout status  -n financialdevelopment deploy/fd-dashboard
kubectl rollout restart -n financialdevelopment deploy/fd-dashboard
kubectl rollout undo    -n financialdevelopment deploy/fd-dashboard

# One-off backfill. Scale the scheduler to 0 first -- two writers on one SQLite
# file produce "database is locked".
kubectl scale -n financialdevelopment deploy/fd-scheduler --replicas=0
kubectl run fd-backfill -n financialdevelopment --rm -it --restart=Never \
  --image=ghcr.io/zinko83/financialdevelopment-scheduler:1.0.0 \
  --overrides='{"spec":{"containers":[{"name":"fd-backfill","image":"ghcr.io/zinko83/financialdevelopment-scheduler:1.0.0","command":["/bin/sh","-euc","ln -sfn /app/data/swaps.db /app/swaps.db; exec python scheduled_ingest.py --backfill"],"volumeMounts":[{"name":"swaps-db","mountPath":"/app/data"}]}],"volumes":[{"name":"swaps-db","persistentVolumeClaim":{"claimName":"swaps-db"}}]}}'
kubectl scale -n financialdevelopment deploy/fd-scheduler --replicas=1
```

**Do not scale `fd-scheduler` above 1.** Two ingestion loops double-poll DTCC
and race on the same slice inserts. Its update strategy is `Recreate` for the
same reason.

---

## Health and troubleshooting

`GET /health` returns:

```json
{
  "ok": true,
  "db_path": "/app/swaps.db",
  "db_exists": true,
  "shared_python": "/app/.venv/Scripts/python.exe",
  "shared_python_exists": false,
  "in_flight": []
}
```

| Symptom | Cause | Fix |
|---|---|---|
| Scheduler restart-loops, exits 0 immediately | `--start-scheduler` missing | Use the shipped configs verbatim |
| `db_exists: false` after ingesting | DB landed in the container layer, not the PVC | Confirm the `/app/swaps.db` symlink is present; see *Read this first* item 2 |
| `POST /run/{suite}` → 401 | `DASHBOARD_API_KEY` mismatch | `Authorization: Bearer <key>`; confirm the secret is mounted |
| `POST /run/{suite}` → "Shared interpreter not found" | Windows-only `SHARED_PYTHON` | Expected on Linux; see *Read this first* item 4 |
| `database is locked` | Two writers on one SQLite file | Scale the scheduler to 0 before manual backfills |
| Rate limiter throttles everyone at once | Proxy IP seen as the client | Verify `--proxy-headers` reaches the app and the proxy sets `X-Forwarded-For` |
| Scheduler pod killed by liveness probe | Heartbeat file older than 150s | `kubectl logs` for a wedged poll; confirm `SCHEDULER_HEARTBEAT_FILE` is on the PVC |
