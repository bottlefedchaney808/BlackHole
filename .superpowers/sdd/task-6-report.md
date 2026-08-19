# Task 6 Report — Cache ingest + localhost:8791 launcher

**Status:** DONE
**Commit:** `be5413f25b3919237b7a5fa571ae139421ddbb09`
**Subject:** `feat(chart-app): cache ingest and localhost:8791 launcher`
**Branch:** `feat/native-chart-app` (HEAD before this task: `3009528`)

## Files

- Created: `chart_app/ingest.py`
- Created: `chart_app/tests/test_ingest.py`
- Created: `chart_app.bat` (verbatim brief)
- Created: `chart_app.sh` (posix equivalent: unset PYTHONPATH/VIRTUAL_ENV, uvicorn `chart_app.server:app` on `127.0.0.1:8791`)
- Modified: `chart_app/server.py`

Only those five files were staged/committed. Pre-existing dirty/untracked paths (including `.superpowers/sdd/*`) were left untouched. This report is not committed.

## TDD

1. **RED** — `test_refresh_cache_upserts` (brief verbatim, minus unused `SimpleNamespace`)
   - Command: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_ingest.py::test_refresh_cache_upserts -q`
   - Result: `ModuleNotFoundError: No module named 'chart_app.ingest'`

2. **GREEN** — `refresh_cache(cache, ticker, interval, lookback, *, daily_fn, intrad_fn) -> int`
   - `interval == "1d"` → `daily_fn(ticker, lookback=lookback)`
   - else → `intrad_fn(ticker, interval=interval, lookback=lookback)`
   - upsert `payload.observations`; return row count
   - `test_refresh_cache_upserts` passed (0.04s)

3. **Intrad path** — `test_refresh_cache_uses_intrad_fn` (injected `15m` payload, no network) passed.

4. **RED** — `test_api_refresh_uses_injected_fetchers`
   - Result: `TypeError: create_app() got an unexpected keyword argument 'daily_fn'`

5. **GREEN** — `create_app(..., daily_fn=..., intrad_fn=...)` defaults to `fetch_daily_candles` / `fetch_intraday_candles`. `POST /api/refresh` body `{"lookback": "30d"}` uses session ticker/interval. Module-level `app = create_app(BarCache(Path("artifacts/chart_app_bars.db")))` after `mkdir` of `artifacts/`.

## Test summary

- `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests -q` → **16 passed in 0.98s**
  - ingest: 6 new (fake payload upsert, intrad routing, injected `/api/refresh`, default fetchers, module `app` route, launchers 8791)
  - prior chart_app tests: 10
- `py_compile` on ingest/server/test_ingest → exit 0
- `ruff check chart_app/ingest.py` → All checks passed
- `git diff --check` on task files → exit 0
- Import smoke (no network): `from chart_app.server import app` → FastAPI routes include `/api/refresh`, no `/api/order`

No live 1y PH. No live 5d/15m or 30d/1d ThetaData call. Smoke was TestClient `POST /api/refresh` with an injected 1-bar `30d`/`1d` payload.

## Concerns

- Importing `chart_app.server` now creates `artifacts/chart_app_bars.db` (gitignored) as a module-level side effect. Parent `mkdir` is extra vs the brief's one-liner so sqlite does not fail on a missing `artifacts/` dir.
- `chart_app.sh` is not specified verbatim in the brief; it mirrors the bat (8791, clean env, `chart_app.server:app`) in dashboard.sh style and runs uvicorn in the foreground.
- Ruff I001 / DTZ001 on `test_ingest.py` left to match the brief's naive `datetime(...)` and prior chart_app test import style. Server I001 on the long spot_history import also left.
- Production `POST /api/refresh` without a prior `/api/symbol` uses default interval `15m`, so it calls `intrad_fn` (not daily).
- Live bounded refresh is Task 8; this task did not hit ThetaData/PH.
