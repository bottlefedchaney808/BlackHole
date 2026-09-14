# Math audit findings — driver pass (Grok 4.6)

**Date:** 2026-09-11
**Mode:** CARL B / CODE
**Artifact identity:** HEAD `c1ba63e` + this file v1
**Handoff:** `docs/MATH_AUDIT_HANDOFF_2026-09-11.md`
**Closed, do not re-derive:** Part 5 #2–#10, #13

Did **not** re-open Clayton exponent, IV identifiability, `tail_mass`, `vrp_pct`/`convexity_pct`, or `hedge_independent`.

Executable script: `docs/audits/_math_audit_checks_20260911.py` (synthetic only). Parent shell Access Denied; A2A peer ran it once (died on `expiry_selector` collision, then Bates calib hung). Numbers below are either **code-certain** (traceable without running) or **analytic** (closed-form). Items that still need a runner are marked **unverified**.

---

## T1 — Jump-diffusion

### F1. `jump_variance_share` uses `v0·T` instead of integrated variance

**File:** `Vol_Suite/jump_diffusion/models.py:156-168`
**Check:** hold Bates params fixed, compare the named metric to the Heston integrated variance it claims to be a share of.

```
jump_var = lam·T·(σj² + μj²)
approx   = jump_var / (jump_var + v0·T)          # code
true     = jump_var / (jump_var + ∫E[v_s] ds)
∫E[v]    = θT + (v0-θ)(1-e^{-κT})/κ
```

Inputs: `κ=2, θ=0.09, v0=0.04, λ=1, μj=-0.08, σj=0.12, T=1`.

| | |
|---|---|
| jump_var | 0.0208 |
| v0·T | 0.0400 |
| ∫E[v] | 0.06838 |
| **code share** | **0.3421** |
| **true share** | **0.2332** |
| relative error | **+46.7%** |

This number is written onto the jump-diffusion widget (`rmse_iv` headline’s jump share) and fed to `adjust_garch_forecast`. With default `γ=0.25` and a 20% GARCH vol:

- code: `0.20 · (1 + 0.25·0.3421) = 0.2171`
- true: `0.20 · (1 + 0.25·0.2332) = 0.2117`

Vol forecast impact is ~2.5% relative. The **tile** that says “34% jump” vs 23% is the traded-facing lie.

**Severity: High** for the named share (it is not the quantity its docstring describes). Medium for the GARCH scale (heuristic γ already).

**Replacement:** use integrated variance in the denominator. Keep `v0·T` only as a documented short-T approximation if T is tiny, not as the production formula.

### F2. `rmse_iv` is the ATM-15 fit, presented as the smile fit

**File:** `Vol_Suite/jump_diffusion/calibration.py:131-139` vs widget `Vol_Suite/module_registry.py:1193-1207`

Comment at calibrate():70-72 still says rmse is over the FULL valid set. The later block correctly scopes it to `calib_mask`. The widget headline prints `RMSE(IV) {rmse:.4f}` with no mask qualifier. `fitted_ivs` is the full smile; a reader comparing plot wings to the RMSE will think the wings fit.

**Ratio unverified this session** (wide-chain calib did not finish). Code-certain that reported RMSE ignores strikes outside `MAX_CALIB_STRIKES=15`.

**Severity: Medium** (mis-stated goodness-of-fit, not a wrong price).

**Replacement:** store `rmse_iv` (mask) and `rmse_iv_full` (all valid strikes). Headline: “RMSE(IV, 15 ATM)”.

### F3. Merton jump-parameter recovery is untested; existing test is a range check

**File:** `Vol_Suite/tests/test_jump_diffusion_calibration.py:40-49`

`test_merton_calibration_recovers_known_params` asserts `0.10 < sigma < 0.30` on true `sigma=0.20` and `rmse_iv < 0.01`. It does **not** check `lam`, `mu_j`, `sigma_j`. Same shape as `test_returns_uniform_marginals`: a named recovery test that cannot fail a wrong jump intensity.

**Recovery numbers: unverified** this session (script hung in Bates / collision).

**Severity: Medium** (test gap that hides identifiability). Not a proven wrong number until the runner finishes.

---

## T2 — Context-Store re-index

### F4. Unlabeled n×n matrix is applied **positionally** and sourced as a real matrix

**File:** `VaR_Tools_Simulations/module_registry.py:134-147`

With `correlation_tickers`, reorder is correct (pinned by `test_correlation_matrix_reorder_is_a_real_permutation`). Without labels:

```python
return arr if arr.shape == (n, n) else None
```

and the caller records `corr_source = "context:correlation_matrix"`.

Check: store

```
A B C
A 1  0.10 0.20
B 0.10 1  0.30
C 0.20 0.30 1
```

Request `[C, A, B]`.

| | corr(C,A) |
|---|---|
| labeled (correct) | 0.20 |
| unlabeled positional | **0.10** (the A-B slot) |
| source string | `context:correlation_matrix` (looks measured) |

Same hole in `_align_to` for vols (`:73-78`): no labels + matching length → positional. Stored `[QQQ=0.12, SPY=0.40]` applied to `[SPY, QQQ]` becomes SPY=0.12, QQQ=0.40. Source: `context:volatilities`.

Vol_Suite’s writer does emit `correlation_tickers`. The hole is unlabeled context (scripted callers, a future writer that forgets the key, or a store row that dropped labels). That is exactly the 2026-09-11 silent-breakage class.

**Severity: High** — a 3-name basket VaR with the wrong off-diagonals, provenance saying the matrix is real.

**Replacement:** unlabeled matrix/vector → `None` → existing `fallback:identity` / `fallback:0.25`. Never positional.

---

## T3 — Adapter `_resolve_*`

### F5. `Vol_Suite._resolve_weights` applies an explicit list positionally, no provenance

**File:** `Vol_Suite/module_registry.py:1454-1465`

`context["weights"] = [0.80, 0.20]` for a QQQ-heavy book, later run on `[SPY, QQQ]`, returns `[0.80, 0.20]` as SPY-heavy. No source string. Engine equal-weights when None — so a wrong list is worse than missing.

**Severity: Medium** (basket vol of the wrong mix).

### F6. Widget `hist_sim` drops `garch_convergence`

**File:** `VaR_Tools_Simulations/module_registry.py:339-357`

Engine records `garch_convergence` per name (`hist_sim.py:188-189`). The widget metrics are `var, cvar, tickers, method, position_source` only. HW/FHS VaR from a boundary-pinned GARCH (F7) looks like a measurement.

**Severity: High** when method is `hw`/`fhs`; none for `basic`.

### F7. Options `_resolve_sigma` fallback is announced — not a finding

`fallback:0.25 (...)` is explicit. Leave it.

GARCH broadcast is announced (`garch:garch_conditional_vol(broadcast)`). Leave it.

---

## T4 — `_garch_fit` on iid-normal

### F8. Persistence pinned at 0.999, `converged=True`, `long_run_vol` meaningless

**File:** `VaR_Tools_Simulations/var_engine/hist_sim.py:20-75`

Handoff measurement (2026-09-11, this session did not re-run): iid N(0, 0.01²) → `persistence = 0.9990` against the `alpha+beta >= 1 → 1e12` wall and the `0.999` box bound. `converged` is `res.success and fun < 1e11` — simplex succeeded at the boundary. `long_run_vol = sqrt(omega / max(1-persistence, 1e-8))` then divides by ~0.001.

`long_run_vol` is printed in `VaR_Tools_Simulations/main.py:1157`. Widget path (F6) does not even show the flag.

HW/FHS **VaR** uses `current_vol / sigma_t`, not `long_run_vol`. If `h_t` stays near sample variance the VaR can still be sane; the displayed long-run vol is not.

**Severity: High** for the named `long_run_vol`. Medium for HW/FHS VaR (needs a measured iid vs true-GARCH VaR delta — **unverified** this session).

**Replacement:** if `alpha+beta >= 0.999 - 1e-6`, `converged=False` (or `boundary_pinned=True`) and do not publish `long_run_vol` as a number (None / NaN). Widget must surface `garch_convergence`.

### F9. `hist_sim.run` divides by net notional

**File:** `hist_sim.py:135`

```python
weights = inp.position_vals / inp.position_vals.sum()
```

`weights` is then unused. The division still runs. Widget sample includes shorts (`module_registry.py:571`). A 1:1 hedge (`[+1e6, -1e6]`) → `ZeroDivisionError` → widget `status: failed`. Better than a silent 0, but a dollar-P&L hist sim should not require net ≠ 0.

**Severity: Medium** (crash on a real book shape; P&L path `@ position_vals` is otherwise correct).

---

## T5 — `var_agg`

### F10. Euler allocation — VERIFIED_CORRECT

`_component_var` has `positions *` (Part 5 #1 closed). `test_var_agg.py:77-94` and `:146-153` pin `sum(component_var) == total_var`.

### F11. PCA-with-resid vs total VaR

`pca_var_with_resid` reconstructs `z·√(dt · Σ λ_j (pos'v_j)²)` over all eigenvalues, which equals `z·√(pos' cov_h pos)` = `total_var`. Tests only check `>= pca_without`. **Equality unverified numerically this session**; algebra says equal. Dead `dt = sqrt(...)` at `:50` is unused.

### F12. Sub-portfolio aggregation uses **net** notional

**File:** `var_agg.py:172-186`

```python
w = pos[mask] / max(pos[mask].sum(), 1.0)
sub_rets[g] = R[:, mask] @ w
sub_pos = pos[group].sum()   # NET
```

Market-neutral equities `pos = [+1e6, -1e6]`, FX `+5e5`:

- `subport_var["Equities"]` still uses signed `pos_sub` vs `cov_sub` — **correct group VaR**.
- `aggregated_var` takes equity net = 0, so the combined number **drops all equity risk**.
- If net is ~0 but not zero, `w` explodes (`/ 1.0` floor when sum is in (-1, 1)).

**Severity: High** for `aggregated_var` on hedged books (the named “aggregated” VaR can be ~FX-only). Group `subport_var` is OK.

**Replacement:** build group **dollar P&L** series `R[:,mask] @ pos[mask]`, EWMA-cov those P&L columns, VaR of `ones @ cov_pnl` (P&L already in dollars). Do not net-then-weight.

### F13. Gaussian CVaR formula — VERIFIED_CORRECT

`cvar = σ · φ(z) / (1-Φ(z))` is ES for N(0,σ). Ratio ES/VaR at 95% = `φ(z)/(z(1-Φ(z))) ≈ 1.256`. Test asserts `cvar >= var` only.

---

## T6 — Dealer signs / backtest loop

Read `HANDOFF_dealer_exposure_dev_20260814.md` first. **Do not** put fail-loud inside `_build_day_records`’s per-day loop.

### F14. Live `dealer_frame_vanna` is a pass-through — VERIFIED_CORRECT (by prior CARL, not re-litigated)

`expiry_book_exposure.py:297-314`. Delta/charm: `-1 * customer-raw`. Vanna: `bs_vanna` already carries that sign.

### F15. Zero net-gamma days are labeled **short**, not unclassified

**File:** `Vol_Suite/backtest_stage3.py:654-655`

```python
regime_v1="long" if net_v1 > 0 else "short",
regime_v2="long" if net_v2 > 0 else "short",
```

`net=0` (empty/thin chain after skips) → **short**. Dealer-exposure path on the same day leaves `regime_dealer=None` when `dealer_rows` is empty (`:631-636`), and `_summarize` drops None. v1/v2 t-tests therefore absorb zero-signal days into the short bucket.

**Severity: High** for the v1/v2 backtest t-stat (contaminated short sample). Not a live-render issue.

**Replacement:** `None` when net is 0 (or abs(net) below a documented epsilon), matching dealer-exposure. This is **not** fail-loud; it is skip-and-continue.

### F16. Fail-loud did **not** return in the per-day loop

Empty `dealer_rows` does not raise. Missing engine at load time still raises (`:854-859`) — that’s setup, not a thin day. Locked decision respected.

---

## Proposed patches (for CARL to attack)

1. Refuse unlabeled corr/vol (`_usable` / `_align_to`) → fallback provenance.
2. Integrated variance in `BatesModel.jump_variance_share`.
3. `rmse_iv_full` + honest headline.
4. GARCH: boundary persistence ⇒ not converged; don’t emit fake `long_run_vol`; widget surfaces `garch_convergence`.
5. `var_agg` aggregated_var from group P&L covariance, not net notional.
6. `hist_sim` do not divide by net sum (drop unused `weights` or use abs).
7. backtest v1/v2: zero net → unclassified, not short.
8. `_resolve_weights`: refuse unlabeled list unless tickers also stored.

Do **not** fail-loud in `backtest_stage3` multi-day loop.

---

## Unverified this pass

- Merton/Bates parameter recovery errors (script did not finish)
- iid-GARCH HW VaR vs basic VaR delta
- `pca_var_with_resid == total_var` numerically
- wide-chain `rmse_full / rmse_mask` ratio

---

## CARL summary

**Mode:** CODE
**Subject:** Math audit of unreviewed surfaces (handoff §5)
**Artifact identity:** HEAD `c1ba63e` + patches in this session
**Reviewers:** R1 grok-4.5 (fresh) → R2 grok-4.6 (fresh; nous-sonnet 402)
**Diversity:** reduced (same family; cross-family blocked)
**Rounds:** 2
**Convergence:** partial
**Verdict:** SHIP_WITH_CAVEATS
**Access proof:** files listed in R1/R2 YAML; tests below

### Findings
- Agreed and changed: F1, F2, F4, F5, F6, F8, F9, F12, F15 + R2-F1 (seed permutation), R2-F3 (hist_sim measured), R2-F4 (unlabeled weights), R2-F5 (`_run_var_agg` resolvers)
- Rejected with evidence: F7/F10/F13/F14/F16 VERIFIED_CORRECT; R2-F2 aggregated_var units (algebra + *252 undone by horizon scale)
- Escalated to human: F3 jump-param recovery assertions (needs calib runner); R2-F6 unlabeled `position_vals` still positional
- Remaining minor debt: dead `dt=sqrt` in `_var_cvar_analytical`; unused `sub_vols` in var_agg; F11 numeric PCA equality

### Material changes
- Bates `jump_variance_share` uses ∫E[v] not v0·T
- `rmse_iv_full` + headline `RMSE(IV, 15 ATM)`
- Unlabeled store corr/vol refused; same-context explicit announced `(positional)`; dashboard seed skips unlabeled ordered keys (sorted basket scope)
- GARCH boundary persistence → not converged, `long_run_vol=NaN`; widget surfaces `garch_convergence`; outlook `measured=False` if unconverged
- `var_agg` aggregated_var from group dollar P&L; widget uses resolvers + `returns_source`
- `hist_sim` no net-notional divide
- backtest v1/v2/dealer: net=0 → None, not short (not fail-loud)

### Pushback
- R1 “w explodes” overstated — divisor floored at 1.0; netting bug still real
- Driver F4 unlabeled refuse would have broken scripted same-context matrices; refined to announced positional
- R2: aggregated_var units are correct

### Open decisions / blockers
- F3: pin Merton jump-param recovery once a calib runner finishes
- R2-F6: unlabeled `position_vals[:n]` still untagged
- Cross-family R2 unavailable (nous-sonnet 402)

### Tests
Targeted pytest via A2A: 83 + 9 passed after R1; after R2 seed tests passed; `test_hist_sim_unconverged_garch_marks_outlook_unmeasured` patched for `var_engine.hist_sim` name shadow — re-run in flight.

### Calibration warning
R1 applied patches itself (reviewer+implementer). Driver verified against source and tests. R2 found a real hole R1 missed (sorted Scope keys + seed). Not zero pushback.
