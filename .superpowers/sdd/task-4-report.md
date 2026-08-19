# Task 4 Report — FastAPI create_app (no live PH)

**Status:** DONE_WITH_CONCERNS
**Commit:** `897a80a3c6c193a4277a9f1b5841212d8f8096df`
**Subject:** `feat(chart-app): local state API for the native window`
**Branch:** `feat/native-chart-app`

## Files

- Created: `chart_app/server.py`
- Created: `chart_app/tests/test_server.py`

Only those two files were staged/committed. Pre-existing dirty/untracked paths (including `.superpowers/sdd/*`) were left untouched.

## TDD

1. **RED** — wrote the brief's two TestClient tests. Command:
   `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_server.py -q`
   Result: collection ERROR `ModuleNotFoundError: No module named 'chart_app.server'`

2. **GREEN** — implemented `create_app(cache, *, default_ticker="SPY", default_interval="15m")`:
   - In-memory session: `ticker`, `interval`, `rh`. One injected `BarCache`.
   - `GET /api/state` → `build_state(...)` plus `{"ok": true}`
   - `POST /api/symbol` validates via `shared.spot_history.validate_ticker` and `SUPPORTED_INTERVALS` (400 on failure). No fetch.
   - `POST /api/rh` stores `{position, fills}`; next `/api/state` echoes it
   - `GET /` returns HTML stub `chart-app`
   - No `/api/order` route. No bind. No live PH.

3. **PASS** — same pytest command: `2 passed in 0.78s`
   Full package: `chart_app/tests` → `9 passed in 0.79s`

## Concerns

- Brief tests do not assert `ok: true`, GET `/` stub body, interval after symbol switch, invalid-ticker/interval 400s, or absence of `/api/order` (Task 7 owns the order-route test). Those behaviors are implemented but untested here.
- `GET /` is the mandated one-line stub; Task 5 will replace it with `static/index.html` + StaticFiles.
- `ruff check` I001 on `test_server.py` import order — file matches the brief verbatim (same style as Tasks 1–3). `server.py` is clean.
- No module-level `app` / uvicorn bind (Task 6).
