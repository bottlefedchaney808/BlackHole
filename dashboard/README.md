# dashboard/

A local, offline web view over `swaps.db` and `orchestrator.py`. FastAPI +
Jinja2, no CDN dependencies -- all CSS and JS is inlined in
`templates/base.html` and `templates/index.html`, so it works with the machine
disconnected.

## Run it

From inside `dashboard/`:

```
..\.venv\Scripts\python.exe -m uvicorn app:app --reload
```

Then open **http://127.0.0.1:8000**.

`--reload` is optional; drop it for a run you don't want restarting under you
mid-orchestrator-run (a reload kills in-flight background jobs -- the
`orchestrator_runs` row stays stuck at `running`, which is why that row is
written up front rather than only on completion).

### Binding beyond localhost (opt-in)

Uvicorn defaults to `127.0.0.1`, and this app is deliberately left that way:
there is **no authentication**, and `POST /run/...` launches subprocesses on
this machine. Anyone who can reach the port can start a 30-minute suite run.

If you explicitly want it reachable from another device on your LAN:

```
..\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
```

Only do that on a network you trust, and expect to add a Windows Firewall rule
for the port. Do not expose it to the internet.

## Routes

| Route | What it does |
| --- | --- |
| `GET /` | Overview: swap DB stats, top notional products, ingestion state, scrape log, last 20 `orchestrator_runs`, and the run trigger form. |
| `GET /swaps` | Paginated `swap_trades` table. Query params: `regulator`, `asset_class`, `page`, `per_page`. |
| `POST /run/{suite_or_unified}` | `{suite_or_unified}` is `unified` or one of `options`, `vol`, `var`, `sentiment`. JSON or urlencoded body: `ticker` (required), plus optional `strike`, `expiry` (YYYY-MM-DD), `option_type`, `target_years`, `index`, `timeout`. Returns `202` with a `run_id` immediately. |
| `GET /runs/{run_id}` | Status + result JSON for one run. Poll this while a run is in flight. |
| `GET /suites/{suite}` | Newest output file on disk for that suite, parsed and rendered as a table. Empty state if nothing has been produced yet. |
| `GET /health` | Liveness plus paths and in-flight run ids. |

Example trigger from the shell:

```
curl -X POST http://127.0.0.1:8000/run/unified ^
     -H "Content-Type: application/json" ^
     -d "{\"ticker\":\"NVDA\",\"target_years\":0.25}"
```

## How it maps onto orchestrator.py

* `POST /run/unified` -> `orchestrator.run_unified(focus)`, which builds its own
  context and runs sentiment -> vol -> options + var.
* `POST /run/{suite}` -> `orchestrator.build_context(focus)` then
  `orchestrator.run_suite(suite, context, timeout=...)` -- the same two-step the
  CLI's non-`--unified` path takes.
* `focus` is assembled exactly like `orchestrator._focus_from_args`: `expiry`
  becomes `expiration_date` and wins; otherwise `target_years` (default 0.25) is
  used, because `build_context` requires one or the other.
* Per-child timeout defaults to `orchestrator.DEFAULT_TIMEOUT_SEC` (1800s,
  overridable process-wide via the `SUITE_CHILD_TIMEOUT_SEC` env var).
* Top notional products come from `orchestrator.get_recent_swap_activity()`,
  which anchors on `MAX(effective_date)` instead of `top_notional_products`'
  yesterday default and normalizes the pandas path to `list[dict]`.

## Run tracking

Two layers, on purpose:

* **`orchestrator_runs` (SQLite) is the durable record.** The dashboard inserts
  its own row with `run_type` `dashboard:unified` or `dashboard:suite:<name>` and
  `status='running'` *before* starting the job, then updates `status`,
  `completed_at`, and `results_json` when it finishes. The row `id` is the
  `run_id`.
* **An in-memory dict is only for fast polling** of in-flight runs.
  `GET /runs/{run_id}` reads it first and falls back to the DB row. It is not
  persisted -- after a server restart the DB row is the surviving truth.

Note that one dashboard-triggered unified run produces **five** rows: the
`dashboard:unified` row this app writes, plus the four `suite:*` rows
`orchestrator.log_run` writes from inside `run_suite`. That is expected; the
history table shows both.

## Suite output files

`GET /suites/{suite}` globs for the newest matching file (virtualenvs,
`site-packages`, `__pycache__`, and `.git` are excluded so vendored files named
`comparisons.py` / `test_gamma.py` don't win):

| Suite | Looks for |
| --- | --- |
| `options` | `Options_Suite/comparison_*.csv`, then `orchestrator_output/*/options_result.json` |
| `vol` | `Vol_Suite/outputs/*/*gamma*.csv` and `*vanna*.csv`, then the same at `Vol_Suite/` root, then any CSV in the newest run dir |
| `var` | `orchestrator_output/*/var_result.json`, then CSVs under `VaR_Tools_Simulations/` |
| `sentiment` | `orchestrator_output/*/sentiment_result.json`, then exported ticker packs |

CSV renders as a table (capped at 250 rows / 40 columns); JSON renders as a
key/value table with the raw payload in a `<details>` block.

## Notes

* Read-only against `swaps.db` -- the only writes are the dashboard's own
  `orchestrator_runs` rows.
* No `python-multipart` dependency: `POST /run/...` parses JSON or urlencoded
  bodies itself.
* Background jobs are plain `def` functions so FastAPI runs them in the
  threadpool -- `run_suite` blocks on `subprocess.run` and would otherwise stall
  the event loop for the whole child timeout.
* Every panel degrades to an empty state: a missing `swaps.db`, an empty
  `orchestrator_runs`, or a suite that has never run all render fine.
