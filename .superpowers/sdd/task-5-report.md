# Task 5 Report: Calibration round-trip tests for remaining 4 models

## Status: DONE

## What was implemented

Appended 4 new tests to `Vol_Suite/tests/test_jump_diffusion_calibration.py`, exactly as specified
in the task brief:

- `test_heston_calibration_recovers_known_params` — HestonModel(kappa=3.2, theta=0.06, xi=0.9,
  rho=-0.75, v0=0.05), asserts `rmse_iv < 0.01`.
- `test_bates_calibration_recovers_known_params` — BatesModel(kappa=3.0, theta=0.06, xi=0.8,
  rho=-0.7, v0=0.05, lam=0.9, mu_j=-0.09, sigma_j=0.15), asserts `rmse_iv < 0.015` (looser
  tolerance for the 7-param fit).
- `test_kou_calibration_recovers_known_params` — KouModel(sigma=0.24, lam=0.8, p=0.35, eta1=12.0,
  eta2=6.0), asserts `rmse_iv < 0.01`.
- `test_vg_calibration_recovers_known_params` — VarianceGammaModel(sigma=0.19, nu=0.25,
  theta_vg=-0.12), asserts `rmse_iv < 0.01`.

Each true-parameter set was deliberately offset 20-50% from `calibration.py`'s `_DEFAULT_SEEDS`
per the brief's CARL-review rationale, so Nelder-Mead has to actually search.

No changes were made to `calibration.py`, `models.py`, or `pricer.py` — `calibrate()` proved fully
model-agnostic as expected; none of the 4 new tests revealed a defect.

The added `from jump_diffusion.models import HestonModel, BatesModel, KouModel, VarianceGammaModel`
import line was moved/reordered by the repo's ruff-format pre-commit hook to sit alongside the
existing `VarianceGammaModel` import already present in the file (it now reads
`from jump_diffusion.models import BatesModel, HestonModel, KouModel` — `VarianceGammaModel` was
already imported at the top of the file for the pre-existing infeasible-log-domain test). This is a
cosmetic formatter artifact, not a content change.

## TDD evidence

Before adding the tests, the 4 new model classes were not imported/used anywhere in the test file,
so collection would have failed (`NameError`/`ImportError`) per the brief's Step 2 expectation. I
proceeded straight to Step 4 (brief explicitly says no new implementation is needed at Step 3) and
ran the full file:

```
cd Vol_Suite && ../.venv/Scripts/python.exe -m pytest tests/test_jump_diffusion_calibration.py -v
```

Result: **6 passed** (2 pre-existing Merton/VG tests + 4 new ones), 3 warnings (pre-existing,
from the infeasible-log-domain VG test's deliberate edge-case probing — `invalid value encountered
in log`, a scipy `IntegrationWarning`, and `Mean of empty slice`; none from the 4 new tests).
Runtime: 438.9s (~7.3 min) total for the whole file, entirely due to Nelder-Mead search cost across
4 multi-parameter models with real seed offsets — no test needed a `maxiter` bump or any tolerance
change from what the brief specified.

## Files changed

- `C:\Users\bottl\FinancialDevelopment\Vol_Suite\tests\test_jump_diffusion_calibration.py`
  (+58 lines, purely additive — `git diff --stat` confirms 0 deletions)

## Self-review findings

- All 4 new tests present, passing, matching the brief's brief text verbatim (params, tolerances,
  docstrings).
- Pre-existing 2 tests (`test_merton_calibration_recovers_known_params`,
  `test_variance_gamma_calibration_survives_infeasible_log_domain`) untouched and still passing —
  confirmed via full-file diff (`git diff --stat` shows only insertions, no deletions/modifications
  to existing lines).
- No changes to `calibration.py`/`models.py`/`pricer.py`.
- No scope creep: nothing else in the file restructured; new tests simply appended at the end.

## Commit

```
5833fb3 test(vol-suite): verify calibration round-trip for all 5 jump-diffusion models
```

Committed exactly per the brief's specified git commands (`git add
Vol_Suite/tests/test_jump_diffusion_calibration.py Vol_Suite/jump_diffusion/calibration.py` —
`calibration.py` had no working-tree changes to stage, so the commit only contains the test file).

## Concerns

None. Convergence was slow (~7 min for the whole file) but every test passed on the first run at
the brief's stated tolerances, with no need to raise `maxiter` or touch any "true" parameter value.
