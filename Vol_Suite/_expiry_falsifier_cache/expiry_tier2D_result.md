# Expiry Book Exposure — Tier 2D Result (Continuous ΔIV-Signed Dual-Clock Estimand)

**Date:** 2026-08-14
**Status: BOUNDED — the in-hand corpus cannot resolve the ΔIV-signed question (as the arbiter
predicted). No confirm, no refute.**
**Driver:** `Vol_Suite/run_expiry_tier2d_continuous.py` (NEW file; live model untouched; no network).
**Both Tier-2C bugs fixed:** aligned response `rets[i]` (not `rets[i+1]`); single-draw permutation
null (one coin flip, signed_flow recomputed deterministically). Tanh-form min-detectable at
cluster-adjusted eff-n.

---

## What was run (the arbiter's round-4b decisive test, exact design)

- **Estimand:** `signed_flow = −sign(ΔIV_i) × burst_i` on **all nonzero-ΔIV-and-burst days** —
  **de-gated** (no Step-B, no top-decile, no 1.5σ).
- **Response:** aligned `rets[i]` (bug 1 fixed).
- **Clocks (pre-registered two-sign):** daily close-to-close → **expect NEGATIVE** (impounded-hedge
  shadow); from-breach/same-session → expect POSITIVE (direct push; only a null there refutes).
- **Index-primary:** SPY/QQQ = one effective cluster; index−single differential as a separate
  joint-sign contrast.
- **Null:** single-draw within-cluster permutation (bug 2 fixed).
- **Stats:** cluster-bootstrap 90% CI, tanh-form md at eff-n, BH, leave-one-window-out.

## Results

### Data support
- 96 clusters loaded; **373 nonzero-ΔIV-and-burst days** across 69 clusters (vs. the 10 that the
  Step-B gate kept — de-gating recovered ~37× the operating days).
- Index family (SPY/QQQ): **39 nonzero days** (SPY 11, QQQ 28 — SPY bursts rarely fire because its
  far-strike gamma is weak; 0–3 per window).

### INDEX PRIMARY (SPY/QQQ = one cluster family, n=39)
| Metric | Value |
|---|---|
| Daily signed-flow corr (aligned) | **+0.0407** |
| 90% cluster-bootstrap CI | **[−0.107, +0.162]** |
| min-detectable \|r\| @ eff-n=39 (tanh) | **0.591** |
| Permutation p (one-sided, shadow NEGATIVE expected) | 0.553 |

The CI straddles 0 and md=0.59 is far above the point estimate. **The index question is BOUNDED,
not resolved.** Note the sign: +0.04 is *positive* (neither shadow-consistent nor anomalous — the
CI includes both). The arbiter's prediction was that the daily clock should show a negative shadow;
at this n nothing can be said either way.

### SINGLES (directional screen; diversification caveat)
- Singles signed-flow corr = **+0.0346**, 90% CI [−0.077, +0.104], n=353. Flat — consistent with
  either "no single-name mechanism" or the diversification artifact.

### Index−single differential
- SPY per-ticker corr: n/a (too few non-degenerate pairs → nan guard). QQQ: +0.041.
- Singles per-name corrs: −0.067, −0.035, −0.115, −0.177, +0.257, −0.073, −0.022, +0.001, −0.055,
  +0.053 (mixed).
- P(random 2 singles both negative) = 0.475; index is NOT both-negative → **differential NOT
  distinguishable from singles at in-hand power.**

### Leave-one-window-out (index)
Drop any window → r ranges **−0.067 to +0.143** (sign flips across windows). Unstable — the +0.04
is not robust to any single window.

## Verdict — BOUNDED (pre-registered ladder)

**No confirm, no refute.** The index CI straddles 0, md (0.59) is unreachable at in-hand n, the
permutation p is 0.55, and LOO flips sign. This is exactly the arbiter's predicted outcome: the
in-hand corpus **bounds** the question, it does not resolve it.

- **NOT DAILY-SHADOW-CONSISTENT** (the observed sign is positive, not the predicted negative —
  though at n=39 nothing is significant).
- **NOT DAILY-ANOMALOUS** (CI includes negative values; no power to call it anomalous).
- **NOT SUPPORTED / NOT RULED_OUT** — those verdicts belong to the from-breach clock only.

## What this means (honest, per the arbiter's sequence gate)

1. **The arbiter's sequence gate says:** run the continuous index arm free → if the daily result is
   negative-and-stable (shadow-consistent) → invest in the intraday from-breach arm. **We got
   +0.04 with sign-flipping LOO — neither negative-and-stable nor anything else.** The in-hand
   daily data simply cannot speak.
2. **The ΔIV-signed mechanism remains OPEN — untested at the clock it operates on, exactly as the
   arbiter ruled.** The from-breach/intraday arm is the only place the affirmative (positive) claim
   can be tested, and it requires intraday bars (or the calendar's event-window design as an interim
   approximation).
3. **The index tilt (r=−0.045 in Tier-2B, unsigned) and this +0.04 (signed, aligned) are different
   estimands** — the differential is not distinguishable from singles at in-hand power.
4. **The −0.22 flag is gone from the narrative** (bugged driver; per the arbiter).
5. **OpEx/event machine: NOT justified** — no from-breach positive anywhere.

## Files
- Driver: `Vol_Suite/run_expiry_tier2d_continuous.py`
- This report: `Vol_Suite/_expiry_falsifier_cache/expiry_tier2D_result.md`
- Prior: `expiry_tier2C_result.md`, `expiry_tier2B_result.md`, `expiry_tier2_result.md`,
  `expiry_tier1_result.md`
- Arbiter round-4b verdict: `trading_journal/` (in-session)
