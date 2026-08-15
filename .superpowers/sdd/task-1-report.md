# Task 1 — Deterministic universe manifest / eligibility layer

## Status

**PASS — implemented and committed on `Dealer-Exposure-Dev`.**

Task 1 freezes a network-free contract layer. It does not acquire data, call market-data endpoints, modify `dealer_positioning.py`, modify live configuration, or modify `master`.

## Files created

- `Vol_Suite/dealer_exposure_universe.py`
  - Canonical `Candidate`, `ManifestUnit`, `ProbeResult`, and `UniverseManifest` contracts.
  - Candidate normalization with ticker canonicalization, required provenance, point-in-time sector, asset type, and SPY/QQQ reference-family exclusion.
  - Recursive JSON/file-name held-pair extraction for ticker×calendar-day exclusion. It supports acquisition manifests, record corpora, and `seed_data_TICKER_YYYYMMDD_short.json` seed files.
  - Locked DTE strata `(1–3, 4–7, 8–10)`, event-habitat vocabulary, PASS/INELIGIBLE/HARD_GAP probe schema, complete probe checks, and explicit rejection of zero-DTE/imputed-zero representations.
  - Deterministic manifest construction: stable ordering, duplicate exclusion keys, held-pair exclusion, DTE/event validation, sector/ticker caps, unique-day reporting, quota schema, and serializable output.
- `tests/test_dealer_exposure_universe.py`
  - 19 network-free contract tests covering normalization, reference-family handling, held-pair extraction, probe schema, zero-DTE and invalid-event exclusions, caps, stable ordering, duplicate exclusions, serialization, input immutability, and quota schema.
- `.superpowers/sdd/task-1-report.md`
  - This report.

## Commands and output

### Focused tests

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q tests/test_dealer_exposure_universe.py --disable-warnings
...................                                                      [100%]
19 passed in 0.05s
```

The normal `pytest` launcher in the Hermes runtime was blocked by an unrelated environment failure (`pydantic_core._pydantic_core` missing while loading the `langsmith` plugin). The focused command above uses the repository's installed Python 3.12 and disables third-party plugin autoloading; no network or credentials are needed.

### Syntax verification

```text
C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_universe.py tests/test_dealer_exposure_universe.py
exit 0
```

### Existing-corpus smoke scan

```text
python -c "... held_pairs_from_paths([_causal_acquisition_20260815, _scratch_tier2, _scratch_tier2b]) ..."
held_pairs 234 spy_days 77 qqq_days 77 seed_tickers ['AAPL', 'AMD', 'AMZN', 'GOOGL', 'JPM', 'META', 'MSFT', 'NFLX', 'NVDA', 'TSLA']
```

This read-only smoke scan confirms the extractor sees both SPY/QQQ corpus days and the ten seed tickers. It did not write to any corpus.

## Requirements mapping

- Point-in-time candidate provenance: `selection_date` and `source_list` are required by normalization/building; sector is required; no current-winner selection or network lookup exists.
- SPY/QQQ: rejected as expansion candidates and retained only as held reference families when scanning corpus artifacts.
- Held ticker×day exclusion: normalized calendar days are clustered as one day and exact pairs are excluded; duplicate candidates retain distinct exclusion keys.
- Explicit eligibility/probe schema: probe checks cover chain listing, historical greeks/IV, OI, spot/OHLC, timestamp granularity, expiry/DTE, and post-window returns. Failures require reasons and cannot become imputed zero.
- Concentration controls: sector cap defaults to 20% and ticker cap to 10% of intended units, with recorded cap values and achieved counts.
- Event/control mix: quota schema records 1/3 event-habitat and 2/3 controls, FOMC/EARNINGS/OPEX vocabulary, and the causal-surprise requirement. `DESCRIPTIVE-HABITAT` is accepted for unavailable surprise information.
- DTE strata: locked 1–3, 4–7, and 8–10 strata; zero/out-of-range DTE is explicitly excluded.

## Concerns / follow-up boundaries

- This task intentionally does not invent a candidate list or sector/event labels. A later acquisition task must provide an approved static point-in-time list and real probe results.
- The existing acquisition and scratch corpus directories were already untracked before this task. They were read only and are intentionally not included in the commit.
- The repository's default pytest plugin environment has an unrelated broken `pydantic_core` installation; the focused no-plugin command is the verified test path for this task.
- The module records caps and quota schema; it does not pretend to make a causal claim or impute missing data.

## Commit

```text
feat(vol): add deterministic dealer exposure universe contracts (final commit recorded in handback)
```

Unrelated untracked files were preserved and are excluded from the commit.

## Reviewer fix report (2026-08-15)

- Corrected held-pair extraction for actual `_scratch_tier2` and `_scratch_tier2b` schemas: seed filename dates are treated as expiries, while `manifest.as_of`, acquisition-manifest `as_of`, and explicit `window_YYYYMMDD` acquisition directories provide held calendar days. Added minimized fixtures matching both artifact formats.
- `build_manifest` now validates and deterministically orders probes, associates by ticker/day/expiry/DTE, admits only exactly one validated `PASS`, and records `missing_probe`, `ambiguous_probe`, and non-PASS exclusions explicitly.
- Expanded the probe contract to require evidence for spot/OHLC windows, same-expiry/grid OI+IV, both strike sides and moneyness, strict PRE_WINDOW ordering, distinct return clocks, no imputation, and positive locked-stratum DTE. Invalid dates and reversed duplicate/probe inputs are deterministic and explicit.

### Fix verification

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q tests/test_dealer_exposure_universe.py --disable-warnings
.............                                                            [100%]
13 passed in 0.05s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_universe.py tests/test_dealer_exposure_universe.py
exit 0

python -c "from Vol_Suite.dealer_exposure_universe import held_pairs_from_paths; p=held_pairs_from_paths(['Vol_Suite/_scratch_tier2','Vol_Suite/_scratch_tier2b']); print(len(p), sorted(p)[:5], sorted(p)[-5:])"
96 [('AAPL', '2026-05-08'), ..., ('TSLA', '2026-08-14')]
```

Remaining concern: this remains a network-free contract layer; real acquisition probes must populate the evidence fields from approved data sources before any unit is admitted.
