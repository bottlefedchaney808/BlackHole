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
R10.3 fixed the smoke wiring (previously `run_causal_arm_v2_smoke.py::build_day` dropped `surprise`/`event_habitat` into `**kw` without writing them into `l2`, so the smoke never exercised the OPERATIONAL branch and the READY's original reconciliation #1 was wrong). Now `surprise` + `event_habitat` are propagated into `l2`; the regenerated smoke shows the **4 event days (2026105/2026112/2026119/2026126) as `FOMC | OPERATION`** and all non-event days as `NONE | DESCRIPTI`. The production path does NOT fabricate surprise — when none is supplied it labels the finding DESCRIPTIVE-HABITAT and marks `causal_surprise_eligible=False`. `surprise_status` (run_causal_arm_v2.py:475) is unit-tested for both branches (`test_surprise_operational_when_supplied`, `test_surprise_descriptive_habitat_when_missing`). This satisfies L5: surprise is either operational or explicitly descriptive-only, never silently 0.

### 2. BOTH-CLOCK-CONFIRMED stricter than R10.1 (L7) — R1 adjudicated: STAND as a conservative tightening
The driver's re-admission state (`:636-662`) requires BOTH clocks confirmed **AND** family opposite-sign rule passes **AND** identifiable β. R10.1's helper (`test_causal_arm_r10_helpers.py::both_clock_confirmed`) used only the two clock signs. **This is an explicit R10.2 conservative tightening, not the original R10.1 rule** — disclosed here and adjudicated by the R1 audit (deleg_e0773e24), which ruled **STAND**: every conjunct is a pre-registered R10.1 requirement (family rule §5; identifiability from the re-audit's mandated rank/cond/VIF disposition), conjoined monotonically; it can only withhold confirmation, never manufacture one; locked pre-acquisition. The per-day two-clock results remain surfaced in `rows`/`n_confirmed_days` for transparency. Reported as a **diagnostic state, never a stand-alone gate** (`:615-618`, `:636-638`).

**Single-family caveat (documented per R1):** `family_claim_allowed` requires both β_SPY and β_QQQ present/opposite/nonzero — a single-family (SPY-only or QQQ-only) acquisition can NEVER reach BOTH-CLOCK-CONFIRMED. This is conservative-correct for a family-wide claim, but a single-family null must NOT be read as a mechanism negative.

### 3. Power limitation — explicit (per R1 + the driver's own honesty)
The **29-unique-day target sizes the correlational arm (md≤0.5), NOT the causal L2 interaction β.** The smoke returns honest one-sided β power ≈ 0.005, reach_80=False, n_for_80=None — the L2 β test is expected underpowered at n=29 (p≈11 → df≈18). The driver correctly refuses an 80% claim below the β-specific threshold (`beta_reach_80` requires n_eff≥29 AND power≥0.80). **29 days is a MINIMUM acquisition target, not a guarantee of causal identification.** The round's expected honest outcome at 29 days is "β powered = NO" on the flagship causal test; the powered arm is the correlational one. The β-power deficit must be reported prominently (it already is: `beta_power`, `beta_reach_80`, `n_for_80` in the decision table), never described as causal-arm 80% power unless the β calculation supports it.

## Verification summary
- Tests: `tests/test_causal_arm_v2.py` (25) + `tests/test_causal_arm_r10_helpers.py` (8) = **33 passed**, network-free, deterministic. Compile OK.
- Smoke: `run_causal_arm_v2_smoke.py` → 31 unique-day decision table, locked h=1, cutoff OK, rank 11/11, cond 1.11e3, max VIF 9.9 (no flag), β IDENTIFIABLE, honest β power 0.005 (reach_80=False), family rule False (same-sign), BOTH-CLOCK NOT-CONFIRMED (blocked by family rule). `_intraday_cache/causal_arm_v2_smoke_decision_table.md`.
- ZERO acquisition. No secrets committed.

## Blocking gate
**Do NOT begin the ≥29-day sequential acquisition until a fresh read-only R1 audit (design/power + mechanism/implementation) and R2 source cross-exam clear this driver.** The audit checks each of the 8 locks against file:line and adjudicates the BOTH-CLOCK over-gate. Only after R2 approval does sequential acquisition begin (THETADATA_HIST_CONCURRENCY=1, recording unique calendar days + data gaps, never imputing).
