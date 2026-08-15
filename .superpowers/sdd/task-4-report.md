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

## Important evaluator review closure (2026-08-15)

- Task 3 registry binding is now mandatory at Task 4 evaluation: every record must carry a verified registry, resolve its artifact entry, and match the registry manifest/payload for raw payload hash, source hashes, canonical input hash, candidate identity, ticker/day, and record artifact hash. Missing, incomplete, detached, or forged registry evidence raises structured `EvaluationInvalid` rather than entering analysis.
- Restored top-level fixed-clock diagnostics and extended every event-only/control-only/pooled stratum with daily and from-breach negative/positive/zero day counts, coverage, and means. Regression tests assert the counts and means.
- Falsifier failure can drive `WORSE` only for an identifiable, finite, beta-bearing fit whose declared power threshold is reached and whose primary comparison is identifiable. Missing data, missing beta, non-identifiable, or underpowered fits are `NOT_AVAILABLE`/`INDETERMINATE` and neutral. Removed fallback `or 0.0` semantics; regression covers non-identifiable primary and placebo neutrality.

Verification:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py` — **21 passed**
- Python 3.12 `py_compile` — passed
- Python 3.12 Ruff check — passed
- `git diff --check` — passed

Only Task 4 evaluator, focused tests, and this report were changed; no production/live model or acquisition code was modified.

## Final provenance strictness fix (2026-08-15)

- Missing, `None`, or non-mapping `provenance` now raises `EvaluationInvalid` with machine-readable `status=COMPARISON_INVALID` before a record can enter `_collapse` or receive a `VALID` result.
- Mapping provenance is still required to pass complete registry/hash identity binding and `no_imputation`; causal status must be explicitly `CAUSAL_ELIGIBLE`, or an explicit `ASSOCIATIONAL`/`NON_CAUSAL` appendix with non-empty structured reasons. `CAUSAL_BLOCKED` without that explicit appendix declaration is invalid.
- Added regressions for absent, `None`, and wrong-type provenance plus a complete explicit associational appendix. Existing registry binding, balanced sensitivity, strata clocks, falsifier neutrality, and network-free/no-live-master scope remain unchanged.

Final verification:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py` — **25 passed in 0.11s**
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/run_task4_evaluation.py Vol_Suite/tests/test_task4_evaluation.py` — **All checks passed**
- `git diff --check` — passed (Git emitted only the normal LF→CRLF working-copy warning)

## Task 4 specification-gap closure (2026-08-15)

- `_fit()` now publishes same-day clustered finite-sample diagnostics: `ci_low`, `ci_high`, `ci_level`, `cluster_count`, `ci_status`, `ci_method`, and an explicit reason. The method is a CR1 clustered sandwich correction with finite-sample correction; intervals are unavailable/INDETERMINATE when the fit or same-day cluster count is insufficient. No pooled-observation interval is fabricated.
- Primary, event/control/pooled stratum, and balanced-panel fits carry the same CI fields; top-level primary diagnostics expose the CI fields as well.
- Added a deterministic event/control acceptance gate: minimum 2 unique days per arm, minimum 20% coverage per arm, and maximum 2:1 arm-size imbalance. Event-only, control-only, and pooled strata remain descriptive/reportable, but missing or materially imbalanced mix forces `INDETERMINATE` and cannot produce a winner.
- Added regressions for known clustered data with published CI, insufficient same-day clusters, balanced mix, missing event, missing control, and imbalanced mix.

Verification for this closure:

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py` — **32 passed**
- Python 3.12 `py_compile` — passed
- Python 3.12 Ruff check — passed
- `git diff --check` — passed
