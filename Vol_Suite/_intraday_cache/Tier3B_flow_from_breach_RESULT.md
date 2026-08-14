# Tier-3B — From-breach intraday clock (ΔIV-signed vanna flow) — RESULT

**Date:** 2026-08-14  **Built on:** Dealer-Exposure-Dev
**Driver:** `Vol_Suite/run_intraday_flow.py` (v2: C+P chain, real OI, day-anchored locus)
**Data:** ThetaData proxy intraday 10-min `all_greeks` + 1-min stock OHLC, days 20260716/17/31, SPY+QQQ
**Pre-registered (round-4b on the intraday clock):** `signed_flow = −sign(ΔIV)×burst` on firing buckets (|ΔIV|>0.01 AND burst≠0), forward 10-min return response, **from-breach EXPECT POSITIVE** (direct vanna push) — only a null HERE refutes the mechanism.

## Result: **BOUNDED — negative directional flag (opposite of prediction), far underpowered**

| Metric | Value |
|---|---|
| Firing buckets (index) | 28 — **all QQQ** (SPY fired 0) |
| SPY breach status | never breached ±1% day-anchored band (daily ranges 0.84–1.43% vs 2% band) |
| from-breach signed-flow corr | **−0.0884** |
| 90% cluster-CI | **[−0.143, −0.040]** (excludes 0) |
| min-detectable \|r\| @ n=28 (tanh) | **0.508** |
| n vs bar | 28 < required ~20+ per-family; 1 ticker (QQQ), 3 day-clusters |
| Verdict | **underpowered — CI excludes 0 with NEGATIVE sign, but \|r\| ≪ md → no claim** |

## What this does and does NOT establish

- **DOES establish:** the intraday from-breach machinery works end-to-end (day-anchored locus from real C+P OI-weighted chain, band-breach burst, ΔIV-signed flow, forward-10-min response). QQQ breaches its band on all 3 days (6–13 firing buckets/day). The proxy serves everything needed.
- **DOES NOT establish — and the sign is a flag:** the observed −0.088 (CI excluding 0) is **NEGATIVE — the opposite of the Karsan from-breach prediction (EXPECT POSITIVE)**. At n=28 single-ticker with md 0.508, this is NOT a refutation (way underpowered) — but it is directionally inconsistent with the mechanism claim on the only clean clock tested, and it is QQQ-only (SPY contributed nothing).
- **Honest read:** neither SUPPORTED nor RULED_OUT. The from-breach clock at in-hand n gives a **negative directional flag** that the arbiter's ladder says must be chased with more data before any disposition — exactly the "wrong sign at low n = warning flag, not verdict" rule from round-4b (the −0.22 Tier-2C lesson, same shape).

## Why SPY fired zero (data fact, not a bug)
SPY short-DTE daily ranges on the 3 test days were 0.84–1.43%; the day-anchored ±1% band (2% wide) was never breached. QQQ ranges 1.52–2.18% breached on all 3 days. To get SPY firing buckets: use higher-vol days (FOMC/earnings) or a tighter tolerance band (0.5%) — both are cheap follow-ups on this exact driver.

## Next step (if Jason wants the decisive read)
More index firing buckets: run the same driver over FOMC/earnings days + OpEx weeks (e.g. 10–20 days), tighten tolerance to 0.5%, keep the pre-registered ladder. ~30s/day/ticker proxy cost. Until then: **mechanism OPEN, negative flag recorded.**

## Files
- Driver: `Vol_Suite/run_intraday_flow.py`
- Raw table: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3B_flow_from_breach_RESULT.md`
- Companion: `Tier3A_charm_opex_RESULT.md` (charm-at-OpEx **SUPPORTED** — the other ESC-3 arm)
