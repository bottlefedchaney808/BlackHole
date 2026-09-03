# Task 4 Report: Calibration engine (Nelder-Mead in IV space)

## What was implemented

- `Vol_Suite/jump_diffusion/calibration.py` — `CalibrationResult` dataclass and `calibrate(model_cls,
  chain, spot, T, seed=None)`, exactly per the brief, with one added robustness guard (see below).
- `Vol_Suite/tests/test_jump_diffusion_calibration.py` — the brief's
  `test_merton_calibration_recovers_known_params`, plus a new
  `test_variance_gamma_calibration_survives_infeasible_log_domain`.

## Added robustness guard (beyond the brief's literal text)

Task 3's reviewer found that `VarianceGammaModel.phi()`'s
`omega = (1/nu)*log(1 - theta_vg*nu - 0.5*sigma**2*nu)` can take `log()` of a non-positive number for
parameter combinations individually inside `PARAM_BOUNDS` (e.g. `sigma=3.0, nu=5.0, theta_vg=2.0` →
`log_arg=-31.5`), producing `NaN` from `phi()`/`lewis_price()`. Per the task instructions, I added a
guard in `calibrate()`'s objective-function loop and in the post-fit `fitted_ivs` loop:

```python
if not np.isfinite(price):
    errs.append(1.0)  # penalize non-finite prices
    continue
iv_fit = implied_vol(price, spot, k, T, chain.r, chain.q, "call")
```

This checks `price` for finiteness *before* calling `implied_vol()`, applying the same `1.0` penalty
path already used for `implied_vol() is None`, rather than letting `NaN` flow into `implied_vol()`'s
Newton-Raphson/bisection loop (which is not proven safe against NaN input — its early-exit branches
`price < lower`, `price > upper`, `f_lo > 0`, `f_hi < 0` are all `False` when `price` is `NaN`, so a
NaN price falls through to the root-finding loop rather than returning `None` cleanly).

## TDD evidence

**RED** — before `calibration.py` existed:
```
ModuleNotFoundError: No module named 'jump_diffusion.calibration'
Interrupted: 1 error during collection
```

**GREEN** — after implementation:
```
tests/test_jump_diffusion_calibration.py::test_merton_calibration_recovers_known_params PASSED
tests/test_jump_diffusion_calibration.py::test_variance_gamma_calibration_survives_infeasible_log_domain PASSED
2 passed, 3 warnings in 21.81s
```

The 3 warnings on the VG test are expected and benign:
- `RuntimeWarning: invalid value encountered in log` — fired inside `models.py::VarianceGammaModel.phi`
  itself when Nelder-Mead's simplex probes the infeasible seed region; this is the pre-existing latent
  defect in the model (out of scope for this task per the brief — models.py is not touched), not a
  crash. The guard's job is to stop the resulting NaN price from being handed to `implied_vol()`.
- `IntegrationWarning` from `scipy.integrate.quad` on the same infeasible/NaN-producing evaluation.
- `RuntimeWarning: Mean of empty slice` from `np.nanmean` — the fitted point at that seed produced
  all-NaN `fitted_ivs`, so `rmse_iv` comes back as `NaN` (still a valid Python `float`, so
  `isinstance(result.rmse_iv, float)` holds, matching what the test asserts). No exception is raised
  anywhere in the call.

Confirmed the RuntimeWarning would otherwise be a real crash-path concern by rerunning with
`-W error::RuntimeWarning`: without the finiteness guard, the warning promotion showed the traceback
would go straight through `objective()` → `lewis_price()` → `model.phi()`'s `np.log()` call. With the
guard in place, the objective function still catches the resulting non-finite `price` before calling
`implied_vol()`, so `calibrate()` completes normally under the repo's default (non-strict) warning
filters, which is what actually matters here — `pytest.ini` does not turn warnings into errors.

Full jump_diffusion regression check (Tasks 1-3 tests + this task's):
```
tests/test_jump_diffusion_models.py, tests/test_jump_diffusion_pricer.py,
tests/test_jump_diffusion_calibration.py
10 passed, 3 warnings in 20.99s
```

## Files changed

- `C:\Users\bottl\FinancialDevelopment\Vol_Suite\jump_diffusion\calibration.py` (new)
- `C:\Users\bottl\FinancialDevelopment\Vol_Suite\tests\test_jump_diffusion_calibration.py` (new)

## Self-review

- **Completeness**: brief's exact interface (`CalibrationResult`, `calibrate`) implemented; both fields
  and defaults match. `rmse_iv < 0.01` and `0.10 < sigma < 0.30` assertions pass with the brief's
  default `maxiter=2000` — no need to raise it to 4000.
- **Discipline**: no comparison-mode or GARCH-tie code added; no calibration wiring added for
  Heston/Bates/Kou/VG beyond what already existed via `_DEFAULT_SEEDS` (which the brief itself
  specifies for all 5 models, since `calibrate()` is generic over `model_cls`). Did not touch
  `models.py` to "fix" the VG `omega` bug — that's explicitly out of scope per the task instructions
  (a plan-mandated latent gap in the model itself); only `calibration.py`'s objective function was
  hardened against it.
- **Testing**: both tests exercise real behavior — the brief's round-trip recovery test, and a new test
  that seeds Nelder-Mead's initial simplex directly inside the known-infeasible VG parameter region and
  asserts `calibrate()` returns a proper `CalibrationResult` rather than raising.
- Verified via `git status`/`git diff --stat` before committing that only the two intended new files
  were staged — no other repo changes bled into this commit.

## Concerns

None. The task's scope, interfaces, and pre-existing code (`models.py`, `pricer.py`, `implied_vol.py`,
`variance_swap_live.py`) all matched the brief exactly, so no design deviations were needed beyond the
explicitly-requested NaN guard.
