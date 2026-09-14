# Math audit verification — 2026-09-13

**Scope:** re-verify, against the current working tree (HEAD `68bdf78` + the uncommitted 5351),
that the 2026-09-11 math-audit fixes are actually in the code. This pass read the code; it did not
re-derive the math. Sources: `docs/audits/2026-09-11-math-audit-findings.md` (findings F1–F16 +
CARL summary) and `docs/MATH_AUDIT_HANDOFF_2026-09-11.md`.

**Answer to "did they auto-fix?":** Yes — the CARL pass on 2026-09-11 applied the patches in this
working tree. They were never committed, which is why the tree still looks dirty. Verified
item-by-item below.

## Disposition

| # | Finding | Severity | Status in working tree | Evidence (read this pass) |
|---|---|---|---|---|
| F1 | `jump_variance_share` used `v0·T` instead of integrated variance | High | **FIXED** | `models.py:157-175` — `∫E[v]ds = θT + (v0-θ)(1-e^{-κT})/κ` |
| F2 | `rmse_iv` is the 15-ATM mask RMSE, presented as full-smile | Medium | **FIXED** | `rmse_iv_full` present; headline `RMSE(IV, 15 ATM)` per CARL summary |
| F3 | Merton jump-param recovery unpinned (`lam`, `mu_j`, `sigma_j` unchecked) | Medium | **OPEN — needs calibration runner** | findings doc §T1-F3, "unverified this session" |
| F4 | Unlabeled n×n corr matrix applied positionally | High | **FIXED** | `VaR/.../module_registry.py` `_usable(...)` — labeled reorder only; unlabeled refused → `fallback:identity` / `fallback:0.25` provenance |
| F5 | `_resolve_weights` positional apply of explicit list | Medium | **FIXED** | `Vol_Suite/module_registry.py:1483-1503` — unlabeled list refused unless book present |
| F6 | Widget `hist_sim` dropped `garch_convergence` | High (hw/fhs) | **FIXED** | `VaR/.../module_registry.py:382-386` surfaces it; failed names listed |
| F7 | Options `_resolve_sigma` fallback | — | **VERIFIED_CORRECT, no change** | announced `fallback:0.25 (...)` |
| F8 | GARCH boundary pin → fake `long_run_vol` | High | **FIXED** | `hist_sim.py:61-83` — `boundary_pinned`, `converged` requires it false, `long_run_vol=NaN` when pinned |
| F9 | `hist_sim` divides by net notional | Medium | **FIXED** | `weights = pos/sum` gone; no `position_vals.sum()` divide |
| F10 | Euler allocation | — | **VERIFIED_CORRECT** | `positions *` present; tests pin sum(components)==total |
| F11 | PCA-with-resid vs total VaR | Low | **minor debt** — algebra says equal, numeric check still owed | findings doc |
| F12 | Sub-portfolio aggregation netted equity group | High | **FIXED** | `var_agg.py:156-188` — group dollar P&L, `sub_pos_total` recorded, no `max(pos[mask].sum())` divide |
| F13 | Gaussian CVaR | — | **VERIFIED_CORRECT** | ES formula ratio 1.256 |
| F14 | Live `dealer_frame_vanna` sign | — | **VERIFIED_CORRECT** | `expiry_book_exposure.py` pass-through |
| F15 | Zero net-gamma days labeled short | High | **FIXED** | backtest/dealer paths: net=0 → None, not short |
| F16 | Fail-loud in per-day loop | — | **VERIFIED_CORRECT (absent by design)** | setup-time raise only |
| R2-F1..F5 | R2 additions (seed perm, hist_sim measured, unlabeled weights, var_agg resolvers) | — | **FIXED** | CARL summary "Agreed and changed" |
| R2-F6 | Unlabeled `position_vals[:n]` positional | Medium | **OPEN** | CARL "Open decisions" — still untagged |

## Bottom line

- 9 findings fixed + 5 R2 items + 5 verified-correct. Open: **F3** (needs a calibration runner;
  math never re-derived) and **R2-F6** (unlabeled position_vals positional — a provenance gap, not
  a proven wrong number).
- Minor debt: dead `dt=sqrt` in `_var_cvar_analytical`, unused `sub_vols` in var_agg, F11 numeric
  check.
- The audit fixes are riding in the same uncommitted diff as two later workstreams (picker
  contract, desk book). Commit them before anything else touches these files — that is what makes
  the tree look dirty, not dead code.

## Test evidence

- Pinned block (pickability, requires-expansion, thetadata EOD gaps, desk book, module registry):
  **41 passed, 1 skipped** on this working tree, 2026-09-13.
- Full suite first `-x` run: **1 failed, 1084 passed, 41 skipped** — the one failure was
  `test_sparse_gap_strike_skipped`, a stale test at HEAD (commit `1018a70` changed new-strike
  handling from skip to last-observed-OI carry-forward and left the old assertion in place; the
  test even asserted `n_new_strikes == 1`, contradicting itself). Renamed to
  `test_sparse_gap_strike_solved`, re-pinned to the documented behavior; file passes 14/14. Not a
  working-tree regression. Full rerun (no `-x`) in flight — final line to be recorded in
  `00-tree-status.md`.
