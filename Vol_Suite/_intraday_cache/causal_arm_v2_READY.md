# Causal-Arm v2 — READY packet (ROUND-10.2)

**Date:** 2026-08-15
**Status:** IMPLEMENTED + TESTED — awaiting read-only R1 audit + R2 cross-exam BEFORE any ≥29-day acquisition.
**Branch/commit:** Dealer-Exposure-Dev, `814f84c` (driver + tests + smoke). Master untouched.
**Model status:** descriptive/conditional throughout. A positive residualized β does NOT promote — it must survive reverse/placebo, A6, gamma, market/spillover, opposite-convention, family-interaction, and both-clock rules, then fresh R1→R2→R3 Cem.

## The 8 pre-registration locks — file:line + confirmation

| # | Lock | Location | Implemented |
|---|---|---|---|
| L1 | Pre-event vanna exposure with pre-window cutoff | `run_causal_arm_v2.py:86` `compute_pre_event_vanna_exposure` | Requires `cutoff_ts < breach_ts`; uses only chain rows `ts <= cutoff` via `ebe.build_net_exposure`; returns NaN + `cutoff_pass=False` otherwise. Fixes run_intraday_flow.py:231-232 contemporaneous-ΔIV defect. Test `test_cutoff_rejects_contemporaneous_post_window` (cutoff==breach → fails). |
| L2 | Production-grade L2 residualized model in driver | `:339` `build_design`, `:262` `_fit_ols` | Full multivariate fit `y ~ β(pre_vanna×ΔIV) + γ(gamma/burst) + ζ(ΔIV) + θ(ΔS) + λ(market) + event + A6_reflexivity + cross_family_spillover + family_interaction`. numpy.lstsq (SVD). Rank/condition/VIF diagnostics. `NOT-IDENTIFIABLE` (β=NaN) when rank<p, cond>1e10, non-finite, or target missing. FWL-equivalence test. |
| L3 | Unique-day effective-n everywhere | `:155` `unique_day_count`, `:162` `deduplicate_family_day` | Same-day SPY/QQQ = ONE unit. md/power/CI/gates/decision-table rows all at unique-day n. Legacy `n_eff>=20` and pooled/`(ticker,day)` gates removed. |
| L4 | Locked horizon + support | `:465` `enforce_locked_horizon`, `LOCKED_H_BREACH=1`/`LOCKED_H_DAILY=1` `:79` | Firing eligibility + no-firing controls defined pre-outcome. Best-lag sweep cannot change locked h (`horizon_locked=False` if it tries). Tests `test_enforce_locked_horizon_rejects_best_lag`, `test_run_causal_arm_horizon_sweep_cannot_change_locked_h`. |
| L5 | Surprise operational OR descriptive-habitat | `:475` `surprise_status` | Missing surprise → `DESCRIPTIVE-HABITAT`, `causal_surprise_eligible=False`, never 0. Operational surprise (`actual - expected`, pre-specified) → `OPERATIONAL` when event_habitat present. |
| L6 | Reflexivity/market/spillover covariates + family rule | `:339` (A6, ΔIV, ΔS, gamma, market, spillover all IN regression, not parallel check), `:502` `family_claim_allowed` | Opposite-sign families → NO family-wide claim. Same-sign/zero/missing/non-identifiable → fails rule. Test coverage. |
| L7 | Single decision table + BOTH-CLOCK-CONFIRMED cell | `:532` `run_causal_arm`, `:518` `_both_clock_confirmed` | ONE table, one row per unique day (pre-event vanna, controls, β, reverse/placebo/no-firing/opposite/event outputs, honest unique-day power). BOTH-CLOCK cell present. |
| L8 | Production-grade network-free tests | `tests/test_causal_arm_v2.py` (25 tests) | Rank deficiency, exact unique-day md, family dedup, locked-h, cutoff integrity, β-unavailable-when-collinear, FWL equivalence, both-clock + family rule, surprise, smoke determinism. |

## Reconciliation of the two builder-flagged items

### 1. Surprise end-to-end (L5)
The smoke fixture (`run_causal_arm_v2_smoke.py:74-75`) wires `surprise` + `event_habitat="FOMC"` onto the 4 event days; `surprise_status` returns `OPERATIONAL` for those and `DESCRIPTIVE-HABITAT` for all non-event days. Test `test_surprise_operational_vs_descriptive` (test_causal_arm_v2.py:345) covers it. **The production path does not fabricate a surprise variable** — when none is supplied it labels the finding DESCRIPTIVE-HABITAT and marks `causal_surprise_eligible=False`. The captured smoke table renders only the first 9 chars (`DESCRIPTI`/`OPERATIO`), which understates the distinction; the structured JSON (smoke lines 87-107) carries the full status. This satisfies the pre-registration's L5: surprise is either operational or explicitly descriptive-only, never silently 0.

### 2. BOTH-CLOCK-CONFIRMED stricter than R10.1 (L7)
The driver's re-admission state (`:636-662`) requires BOTH clocks confirmed **AND** family opposite-sign rule passes **AND** identifiable β. R10.1's helper (`test_causal_arm_r10_helpers.py::both_clock_confirmed`) used only the two clock signs. **Justification:** this is a deliberate, defensible tightening. The family-interaction opposite-sign rule and the NOT-IDENTIFIABLE disposition are both locked requirements from the R10.1 audit (the same audit that demanded A6/reflexivity/market/spillover as covariates and the family rule). A re-admission claim cannot rest on a β that the rank/condition diagnostic flags as unidentifiable, nor on same-sign families where "which sign binds" is ambiguous. It is **strictly more conservative** than R10.1 — it cannot manufacture a false confirmation, only withhold one. It is reported as a **diagnostic state, never a stand-alone gate** (docstring `:615-618`, `:636-638`), and the per-day clock signs are still surfaced in the table for transparency. **This over-gate is submitted for the read-only audit to adjudicate** — if the panel deems it too strict, the per-day both-clock cell (still present in `rows`) is the R10.1-compliant fallback.

## Verification summary
- Tests: `tests/test_causal_arm_v2.py` (25) + `tests/test_causal_arm_r10_helpers.py` (8) = **33 passed**, network-free, deterministic. Compile OK.
- Smoke: `run_causal_arm_v2_smoke.py` → 31 unique-day decision table, locked h=1, cutoff OK, rank 11/11, cond 1.11e3, max VIF 9.9 (no flag), β IDENTIFIABLE, honest β power 0.005 (reach_80=False), family rule False (same-sign), BOTH-CLOCK NOT-CONFIRMED (blocked by family rule). `_intraday_cache/causal_arm_v2_smoke_decision_table.md`.
- ZERO acquisition. No secrets committed.

## Blocking gate
**Do NOT begin the ≥29-day sequential acquisition until a fresh read-only R1 audit (design/power + mechanism/implementation) and R2 source cross-exam clear this driver.** The audit checks each of the 8 locks against file:line and adjudicates the BOTH-CLOCK over-gate. Only after R2 approval does sequential acquisition begin (THETADATA_HIST_CONCURRENCY=1, recording unique calendar days + data gaps, never imputing).
