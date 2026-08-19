# Task 5 Report — ECharts window (vendored, no CDN)

**Status:** DONE
**Commit:** `3009528607f28491f64123800a2158421270503b`
**Subject:** `feat(chart-app): echarts window for one ticker`
**Branch:** `feat/native-chart-app` (HEAD before this task: `897a80a`)

## Files

- Created: `chart_app/static/index.html`
- Created: `chart_app/static/vendor/echarts.min.js` (Apache ECharts 6.1.0, `npm pack echarts` once; 1,121,883 bytes)
- Modified: `chart_app/server.py`
- Modified: `chart_app/tests/test_server.py`

Only those four files were staged/committed. Pre-existing dirty/untracked paths (including `.superpowers/sdd/*`) were left untouched. This report is not committed.

## TDD

1. **RED** — added `test_root_serves_chart_window`:
   - `GET /` 200
   - body contains `id="chart"`
   - body contains `echarts` or `chart-app`
   - body has no `cdn` substring
   - `GET /static/vendor/echarts.min.js` 200 and payload > 10k
   Command: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_server.py::test_root_serves_chart_window -q`
   Result: FAIL `assert 'id="chart"' in 'chart-app'`

2. **GREEN** — vendored `echarts.min.js`, wrote vanilla `index.html`, mounted StaticFiles `/static`, serve `index.html` at `GET /`.
   - Dark `#0e1117`; ticker/interval + live stamp only
   - Candlestick from `state.bars`; volume second grid
   - Lines: ema20, ema50, vwap, bb_upper/mid/lower
   - Scatter markers: buy lime ▲, add gold ◆, hold gray ●, sell orchid ▼, none skip
   - Poll `GET /api/state` every 5s
   - Local script `/static/vendor/echarts.min.js` only
   - No `/api/order`. No bind. No live PH.

3. **PASS**
   - `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_server.py -q` → `3 passed in 0.93s`
   - same interpreter `chart_app/tests` → `10 passed in 0.95s` (after HTML cleanup)

## Other verification

- `py_compile chart_app/server.py chart_app/tests/test_server.py` → exit 0
- `ruff check chart_app/server.py chart_app/tests/test_server.py` → I001 on `test_server.py` import order (pre-existing Task 4 style; file left matching prior tasks)
- `git diff --check` on changed text files → exit 0
- `index.html` has no `http` / `cdn` script tags

## Concerns

- No browser open / visual smoke (Task 6).
- Ruff I001 on `test_server.py` import order left as-is (same as Tasks 1–4).
- Sell marker is ECharts `triangle` with `symbolRotate: 180`, not a custom path.
- Vendored min.js is ~1.1MB text; git printed a CRLF warning on add. Functionally a single-line minified file.
- Test also asserts vendor JS is served and the HTML has no `cdn` substring — slightly beyond the brief's `id="chart"` check.
