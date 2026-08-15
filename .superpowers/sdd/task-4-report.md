# Task 4 — Evaluation/falsifier harness

## Status

Implemented a network-free, fail-closed evaluator. It consumes Task 3 `comparison` artifacts and per-row causal/provenance records; descriptive agreement is kept separate from predictive/causal evidence. It never acquires data, changes the locked live model, promotes the expiry-book model, or imputes missing values.

## Implementation

- `Vol_Suite/run_task4_evaluation.py`
  - one independent unit per unique calendar day; same-day tickers are deterministically collapsed and never counted twice;
  - fixed daily close-to-close and from-breach clocks, with expected negative/positive directions and counts;
  - required `gamma_burst`, `delta_s`, `market`, `a6_reflexivity`, and `cross_family_spillover` controls plus event/no-firing strata;
  - residualized `Vanna_orth × ΔIV_PRE_WINDOW` primary target and explicit causal-provenance gate;
  - placebo and reverse lead/lag falsifiers, with best-lag selection disabled;
  - opposite-convention sensitivity labeled sensitivity only;
  - rank, condition, VIF, beta SE, power, and `n_for_80` diagnostics; `n=29` remains correlational context and beta target is `n=257`;
  - explicit `BETTER` / `WORSE` / `INDETERMINATE` decision ladder; underpowered or causally blocked evidence cannot yield causal acceptance;
  - invalid comparison/common-input coverage, missing outcome/control/event, blocked provenance, and non-finite data fail closed.
- `Vol_Suite/tests/test_task4_evaluation.py`
  - deterministic tests for zero-outcome prevention, same-day de-duplication, blocked provenance, placebo/reverse registration, power interpretation, decision ladder, invalid artifacts, opposite-convention sensitivity, and underpowered decisions.

## Verification

Fresh focused commands (network-free, with `PYTHONPATH` unset because the ambient Hermes path contains an incompatible NumPy build):

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py` — **9 passed**
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — **All checks passed**
- `git diff --check` — passed

No acquisition/network-heavy call was made. `dealer_positioning.py`, live config, master, secrets, and unrelated untracked corpora were not modified.

## Limitations

This task provides the evaluation boundary only; no model-selection or promotion claim is made. Real-data causal evidence remains blocked until a complete, registry-bound Task 3 corpus passes the strict provenance contract and reaches the locked beta-power target.

## Commit

Task 4 commit was created on branch `Dealer-Exposure-Dev`; its final hash is reported in the handback.

## Review-fix retry (2026-08-15)

- Primary remains the all-eligible unique-calendar-day analysis; same-day ticker records are deterministically sorted/collapsed and never counted as independent observations.
- Added deterministic `equal-family/day` balanced-panel weighting as a separately labeled sensitivity; it cannot replace or auto-promote the all-eligible primary.
- Added event-only, control-only, and pooled strata with the same descriptive, daily-clock, from-breach-clock, causal, power, coverage, `n`, and status diagnostics. Pooled explicitly retains no-firing days in its denominator.
- Added strict Task 3 artifact validation: `VALID` requires complete coverage (`live/new/common/total`), SHA-256 input/artifact/source identities, and record/provenance/artifact identity agreement. Minimal `{"status":"VALID"}`, incomplete coverage, source/hash mismatches, and record-artifact mismatches fail closed.
- Missing placebo or reverse lead-lag evidence is `NOT_AVAILABLE` with `INDETERMINATE` interpretation and `drives_decision=False`; only observed falsifier failures can drive `WORSE`.
- Preserved fixed clocks, controls, unique-day unit/cluster, `n_for_80` power interpretation, no auto-promotion, and no acquisition/live/master/secrets changes.
- Added focused regressions covering balanced sensitivity determinism, all strata/no-firing denominator, strict schema/provenance identity, missing-falsifier handling, and observed falsifier failure.

Verification for this retry:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py` — **18 passed**
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — passed
- `git diff --check` — passed

Only `Vol_Suite/run_task4_evaluation.py`, `Vol_Suite/tests/test_task4_evaluation.py`, and this report were changed for the retry.
