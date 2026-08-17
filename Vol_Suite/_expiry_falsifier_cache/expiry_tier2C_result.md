# Expiry Book Exposure — Tier 2C Result (ΔIV-signed vanna-shock event study)

**Date:** 2026-08-14
**Status: BLOCKED — insufficient genuine shock observations in the in-hand corpus.**
**Driver:** `Vol_Suite/run_expiry_tier2c_signed.py` (NEW file; live model untouched; no network).

---

## What was run

The Karsan arbiter's round-4 **decisive next test**, exactly as mandated:
- **Estimand:** `signed_flow = −sign(ΔIV_atm_t) × burst_t` (burst = the unchanged `hedge_flow_at`
  object, gamma lagged one day), response = next-day continuous return.
- **Gate:** Tier-1 **Step-B exogenous-shock rule** — `|ΔIV_atm|` top-decile (per ticker, pooled
  across all 8 windows) AND `|daily return| > 1.5σ` (prior-20d realized), pooled across all 8
  windows.
- **Cells:** vol-up (framework expects negative fwd return) vs vol-down (expects positive), index
  (SPY/QQQ = ONE cluster family) vs singles; ≥20 obs/cell required for the index primary.
- **Null:** within-cluster permutation (shuffle ΔIV sign inside each ticker×window cluster).
- **Multiplicity:** BH q<0.10. **Power:** min-detectable-at-effective-n reported.

## Result — BLOCKED (insufficient index cells)

| Metric | Value |
|---|---|
| Clusters loaded | 96 (12 tickers × 8 windows) |
| Candidate days screened | 734 |
| **Shock days surviving the Step-B gate** | **10 total (1 index, 9 singles)** |
| Index vol-up / vol-down cells | 1 / 0 |
| Required per-cell minimum | ≥20 |
| **Verdict** | **BLOCKED (insufficient index shock cells)** |

**Diagnosis (why the gate is so restrictive):** the corpus is 8 short-DTE windows × ~11 obs each.
The Step-B gate was designed for the far-DTE 150-day corpus (where prior-20d realized vol is a
stable statistic). On 14-day windows the conditions (top-decile |ΔIV| AND |ret| > 1.5σ of a
short-window vol) rarely co-occur — only 10 of 734 days pass both (35 pass the IV leg alone, 81
pass the return leg alone). This is a **data-availability limitation of the in-hand corpus**, NOT
a result, and NOT a null.

### Exploratory sensitivity (CLEARLY NOT the pre-registered test — informative only)
Relaxing the gate (top-quintile |ΔIV|, 1.0σ) yields index n=17 at its loosest useful setting with
a signed corr of **−0.22** — the **wrong sign** (framework expects *positive* signed-flow corr:
vol-down → dealers buy → positive forward bias). But n=17 is far below power, so this is
directionally suggestive **against** the framework's ΔIV-signed mechanism at the daily clock, not
conclusive. It must not be read as evidence — the pre-registered arm is BLOCKED.

## What this means (honest, per the arbiter's ladder)

1. **The decisive test CANNOT be resolved on the in-hand corpus.** The arbiter's own ruling
   anticipated this: *"the in-hand daily corpus cannot recover the true intraday clock"* — and it
   also cannot power a strict exogenous-shock event study. BLOCKED ≠ no-edge.
2. **The "360 shock obs" from Tier-2B were counted with the loose gate** (|ΔIV|≥0.005 AND breach —
   the statistics panelist's F7 flagged exactly this). Under the proper Step-B exogenous rule, only
   10 genuine shocks exist. The earlier count over-stated the arm's power.
3. **What would unblock it** (any one):
   - **More history** — additional short-DTE windows (weeks/months) to accumulate genuine
     exogenous vol-shock days (FOMC/earnings/OpEx), OR
   - **An explicit event calendar** (FOMC/earnings/OpEx dates) instead of a statistical shock
     proxy — the dated-shock criterion the panelists recommended, OR
   - **Intraday bars** for the event clock (the arbiter's final-two-hours charm claim — a live,
     separate arm that daily data structurally cannot test).
4. **The exploratory wrong-sign (−0.22 at n=17) is a warning flag, not a finding.** If a properly
   powered future run reproduces a *negative* signed-flow corr, that would be **RULED_OUT** against
   the framework's mechanism at the daily clock. But it cannot carry any weight at n=17.

## Disposition per the arbiter's ruling (unchanged by this BLOCKED result)
- **SHIP** the descriptive/conditional instrument + honest verdicts + the real-data acquisition
  pipeline (the durable asset).
- **DEMOTE** the daily level-lead predictor (dead); Tier-2 SUPPORTED → artifact-risk.
- **OPEN** the event/ΔIV-conditional thesis and the index-primary hypothesis — neither confirmed
  nor refuted; the decisive test is BLOCKED on data, not negative.
- **OpEx/event machine: NOT justified** — and the gate to build it remains: a properly powered
  ΔIV-signed event study resolving positive.

## Files
- Driver: `Vol_Suite/run_expiry_tier2c_signed.py`
- This report: `Vol_Suite/_expiry_falsifier_cache/expiry_tier2C_result.md`
- Prior: `expiry_tier2B_result.md`, `expiry_tier2_result.md`, `expiry_tier1_result.md`
- Arbiter round-4 verdict: `trading_journal/` (in-session)
