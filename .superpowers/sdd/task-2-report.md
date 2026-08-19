# Task 2 Report — Local score engine (no live PH)

**Status:** DONE
**Commit:** `22447b1` — `feat(chart-app): local price scores and classic overlays`
**Branch:** `feat/native-chart-app` (HEAD was `6668a63` before this task)
**Tests:** 3 passed in `chart_app/tests/test_score_engine.py`

## What was implemented

`chart_app/score_engine.py` computes:

- `classic_overlays(records)` — `ema20`, `ema50`, `vwap`, `bb_mid`, `bb_upper`, `bb_lower` (same length as `records`; EMA/BB `None` until 20/50 bars).
- `price_scores(records)` — closed-bar prefixes; `whale`/`liquidity` always `False`; `wave3`/`squeeze`/`trend` copy `Direction/indicator.py::_price_signals` boolean rules against in-memory OHLCV only. Prefixes shorter than 50 bars stay all-False / score 0. Score is the count of True among the five signals.
- `gated_markers(entries)` — `shared.candlestick_chart.apply_position_gate(entries)`.

Direction modules were not modified. `_price_signals` was not imported (it can fetch OHLCV). No live PH / `flow.*` / `dealer.weighted_greeks` calls.

## TDD evidence

- RED: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_score_engine.py -q`
  - `ModuleNotFoundError: No module named 'chart_app.score_engine'` (collection error, 0.11s).
- GREEN: same command → `3 passed in 0.59s` then `3 passed in 0.57s` after ruff cleanup.

## Other verification

- `py_compile chart_app/score_engine.py chart_app/tests/test_score_engine.py` — exit 0
- `ruff check chart_app/score_engine.py` — All checks passed
- `git diff --check -- chart_app/score_engine.py chart_app/tests/test_score_engine.py` — clean
- `pytest chart_app/tests/test_bar_cache.py chart_app/tests/test_score_engine.py -q` — 5 passed

## Commit hygiene

- Staged ONLY `chart_app/score_engine.py` and `chart_app/tests/test_score_engine.py` (2 files, +170).
- Pre-existing dirty/untracked files (including `.superpowers/sdd/*`) left unstaged.
- Direction modules not modified.

## Concerns

- Importing `Direction.elliott_wave` / `bollinger_analyzer` / `trend_engine` still loads `Direction.data` (ThetaData controller class) at import time. No fetcher is called; tests finished in <1s. Live PH is not invoked.
- `regime` is listed as a consumer in the brief but is unused; squeeze uses `detect_squeeze(get_bands(...))` only, matching `_price_signals`.
- Price-score warmup is `len < 50` (brief), stricter than `_price_signals` (`_MIN_BARS = 3`).
- EMA uses SMA-seed then Wilder-style `2/(n+1)` EMA. Tests only assert warmup, not numeric identity with any other EMA.
- This report is written to `.superpowers/sdd/task-2-report.md` and was **not** staged (per task: do not stage `.superpowers/sdd/*`).
