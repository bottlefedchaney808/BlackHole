# Task 1 Report — SQLite BarCache

**Status:** DONE
**Commit:** `6668a63` — `feat(chart-app): sqlite bar cache for the native tape`
**Branch:** `feat/native-chart-app` (HEAD was `5334216` before this task)
**Tests:** 2/2 passed (`chart_app/tests/test_bar_cache.py`)

## What was implemented

`BarCache(path)` in `chart_app/bar_cache.py`:
- SQLite table `bars(ticker, interval, ts, open, high, low, close, volume)` with `PRIMARY KEY(ticker, interval, ts)`
- `upsert(ticker, interval, records) -> int` via `INSERT OR REPLACE`; returns `len(records)`
- `load(ticker, interval) -> list[CandleRecord]` ordered `ORDER BY ts ASC`
- `last_ts(ticker, interval) -> datetime | None`
- `ts` stored as `datetime.isoformat()`

Also created empty `chart_app/__init__.py`.

## TDD evidence

- RED: tests written first; collection failed with `ModuleNotFoundError: No module named 'chart_app.bar_cache'`
- GREEN: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_bar_cache.py -q` → `2 passed in 0.07s`

## Commit hygiene

- Staged ONLY `chart_app/__init__.py`, `chart_app/bar_cache.py`, `chart_app/tests/test_bar_cache.py`
- Pre-existing dirty/untracked files left untouched

## Concerns

- `chart_app/tests` is not in `pyproject.toml` `testpaths`; default `pytest` will miss these unless the path is passed explicitly (as the brief does).
- Brief “Consumes” line names `shared.spot_history` fetchers; this task is cache-only and does not call them (no live PH/ThetaData).
- `last_ts` on an empty series returns `None` (interface-specified, untested).
- `ORDER BY ts` is lexicographic on ISO strings; correct for naive `isoformat()` timestamps used here.
