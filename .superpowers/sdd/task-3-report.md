# Task 3 Report: Heston + jump models (Bates, Kou, VG)

## What was implemented

Appended 4 new characteristic-function models to `Vol_Suite/jump_diffusion/models.py`, following the
existing `MertonModel` shape exactly (dataclass with `phi`, `to_array`, `from_array`, `PARAM_BOUNDS`,
`param_names`, `name` ClassVars):

- `HestonModel(kappa, theta, xi, rho, v0)` — Gatheral "Little Trap" CF form.
- `BatesModel(kappa, theta, xi, rho, v0, lam, mu_j, sigma_j)` — internally builds a `HestonModel` and
  multiplies its CF by a Merton-style jump factor; also carries a `jump_variance_share(T)` helper
  (not part of the required interface, included per the brief's exact code).
- `KouModel(sigma, lam, p, eta1, eta2)` — double-exponential jump-diffusion.
- `VarianceGammaModel(sigma, nu, theta_vg)` — pure-jump VG via Madan-Carr-Chang CF.
- `ALL_MODELS = (MertonModel, HestonModel, BatesModel, KouModel, VarianceGammaModel)` registry tuple.

Code was implemented verbatim from the task brief (formulas pre-verified by CARL per the task
instructions — not re-derived).

## TDD evidence

**RED** — appended the 6 new test functions (4 new + kept the 2 existing Merton tests) to
`Vol_Suite/tests/test_jump_diffusion_models.py`, then ran:
```
cd Vol_Suite && ../.venv/Scripts/python.exe -m pytest tests/test_jump_diffusion_models.py -v
```
Result: collection error —
`ImportError: cannot import name 'BatesModel' from 'jump_diffusion.models'`
(confirmed the tests actually exercise the new import, not a stale cache).

**GREEN** — after appending the 4 model classes + `ALL_MODELS` to `models.py`, reran the same command:
```
6 passed in 0.09s
```
All 6 tests pass: `test_merton_phi_zero_is_one`, `test_merton_to_array_from_array_round_trip`,
`test_heston_phi_zero_is_one`, `test_bates_reduces_to_heston_when_no_jumps`,
`test_kou_phi_zero_is_one`, `test_vg_phi_zero_is_one`.

The `test_bates_reduces_to_heston_when_no_jumps` test is a genuine cross-model check: it constructs
independent `HestonModel` and `BatesModel(lam=0.0)` instances and asserts `np.allclose` on their `phi`
values evaluated at two nontrivial complex `u` points (`0.3-0.2j`, `1.0+0j`) — it does exercise both
the Heston branch and the Bates-with-zero-jump-intensity branch (jump_factor collapses to
`exp(0) = 1` when `lam=0`, but the test verifies this numerically rather than assuming it).

Also ran `ruff check` on both changed files: `All checks passed!`.

## Files changed

- `C:\Users\bottl\FinancialDevelopment\Vol_Suite\jump_diffusion\models.py` — appended 4 models + registry.
- `C:\Users\bottl\FinancialDevelopment\Vol_Suite\tests\test_jump_diffusion_models.py` — appended 4 new
  test functions and the corresponding import line.

## Self-review

- **Completeness**: all 4 models present, `ALL_MODELS` registry present and correctly ordered
  (Merton, Heston, Bates, Kou, VarianceGamma) matching the brief.
- **Quality**: matches `MertonModel`'s existing shape/conventions (ClassVar declarations, `astuple`-based
  `to_array`, `from_array` via `dict(zip(param_names, arr))`). Docstrings from the brief preserved
  verbatim (design rationale comments re: JUMP_MODEL_DEFAULT, GARCH-X tie, etc.).
- **Discipline**: nothing beyond scope — no changes to `pricer.py`, no calibration code added, single
  file (`models.py`) kept as the brief specifies (not split).
- **Testing**: RED confirmed for the right reason (ImportError on the new symbols, not a typo/syntax
  error elsewhere); GREEN confirmed all 6 tests pass; ruff clean.

## Concerns

None. Implementation matches the brief exactly; `MertonModel`'s actual current fields/order matched
the brief's assumptions with no discrepancies to flag.

## Commit

`438f9c8` — `feat(vol-suite): add Heston, Bates, Kou, VG jump-diffusion models`
(2 files changed, 259 insertions(+))
