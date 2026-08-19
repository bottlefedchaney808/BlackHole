# Task 3 Report — Snapshot contract

**Status:** DONE_WITH_CONCERNS
**Commit:** `9bfff5d` (`feat(chart-app): agent snapshot contract`)
**Full hash:** `9bfff5d80a633621c4d24f7aeabcb52916eaeaf2`
**Branch:** `feat/native-chart-app` (parent HEAD `22447b1`)
**Tests:** `chart_app/tests/test_snapshot.py` 2 passed; chart_app suite 7 passed in 0.66s.

## What was delivered

- `chart_app/snapshot.py` — `build_state(cache, ticker, interval, rh=None) -> dict`
- `chart_app/tests/test_snapshot.py` — brief-verbatim empty + length tests

Commit staged **only** those two files. Not staged: `.superpowers/sdd/*`, `sentiment-scanner/*`, `trading_journal/*`, `docs/`.

## TDD

1. **RED:** wrote `test_snapshot.py` first. Collection failed with `ModuleNotFoundError: No module named 'chart_app.snapshot'` (expected).
2. **GREEN:** implemented `build_state`. Both tests passed.
3. **No extra tests** beyond the brief.

## Contract

Exact keys: `ticker`, `interval`, `as_of`, `bars`, `scores`, `markers`, `overlays`, `live`, `rh`.

- Empty cache: `bars=[]`, `live={"conviction":"NONE","score":0}`, `rh.position is None`, `as_of=None`, no raise.
- Lengths: `scores`, `markers`, and every overlay list match `len(bars)`.
- Overlays keys: `ema20`, `ema50`, `vwap`, `bb_mid`, `bb_upper`, `bb_lower`.
- Scores are raw ints from `price_scores`; markers from `gated_markers`.
- `rh=None` → `{"position": None, "fills": []}`. Passed-in `rh` is echoed (`position` + `fills`).
- Live conviction uses the **same rule as** `signal_generator.generate` (HIGH = whale AND wave3 AND (squeeze OR trend) AND score >= 3; MEDIUM = whale AND score >= 3 AND not HIGH). whale/liq stay False via `price_scores`, so HIGH/MEDIUM from whale will not fire. Live stamp is honest `NONE (n/5)`. Whale is not faked. No live PH.

## Test command

```
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_snapshot.py chart_app/tests/test_score_engine.py chart_app/tests/test_bar_cache.py -v
```

→ **7 passed in 0.66s**

## Concerns

1. **HIGH/MEDIUM never fire on this path.** Brief says "HIGH if wave3 and (squeeze or trend) and score >= 3" while also saying use `generate` **price-only** and that whale-gated HIGH/MEDIUM will not fire. Implementation keeps whale in the generate rule (honest). If the reviewer wanted a price-only HIGH that drops the whale AND, that is a plan ambiguity — current live stamp will stay `NONE`.
2. Brief tests do not assert ticker/interval/as_of/bar field names/overlay key set/rh.fills. Those are implemented to the contract but untested.
3. `chart_app/tests` still not in `pyproject.toml` testpaths (Task 1 carry). Tests were invoked by explicit path.
