# Expiry Book Exposure — FAST Test Result (2026-08-14)

**Source:** `Vol_Suite/run_expiry_fast_test.py`, reusing `expiry_book_exposure.py`
(`build_net_exposure`, `ne.gex()` level, `execution_locus`/`hedge_flow_at` band-gating,
`_safe_corr`, `_block_perm_p`). Network-free. Runtime **3.7s**.
**Design:** the Karsan arbiter's round-2 fast test — Step 1 (interpretability layer) + Step 2
(true hedge-flow object), pooled cross-sectional on the existing offline seed corpus.

---

## PRIMARY — TRUE HEDGE-FLOW OBJECT (impulse = gex_level × dS/spot, band-gated at 1%)
Pooled across 12 tickers, de-meaned per ticker (fixed-effects):

| Metric | Value |
|---|---|
| Pooled n | **1782** |
| corr | **−0.0016** |
| 95% CI | **[−0.048, +0.045]** |
| min-detectable \|r\| @ 80% power | **0.0662** |
| block-perm p | 0.942 |
| SPY / QQQ corr | −0.063 / −0.037 (sign-consistent = True) |

### Decision rule (pre-registered): **RULED_OUT**
pooled |r| (0.0016) < min-detectable (0.0662) AND the 95% CI [−0.048, +0.045] excludes
min-detectable. → **Clean, honest "no edge in the primary GEX/hedge-flow channel at the daily
clock."** The 95% CI is narrow and centered on zero; it excludes the theorized |r|≈0.10–0.15 edge
at 80% power. Block-perm p=0.942 confirms a null.

## Exploratory secondaries (pooled de-meaned, BH over the true family)
| channel | n | corr | BH q |
|---|---|---|---|
| proxy (Δ dollar-gamma) | 1782 | −0.038 | 0.057 |
| vanna (level) | 1782 | +0.018 | 0.057 |
| charm | 1782 | +0.040 | 0.040 |

All null; none reaches significance.

---

## What this decides (per the arbiter's pre-registered rule)
- **RULED_OUT** on the primary GEX/hedge-flow channel at the daily clock → **permanently demote the
  forward-predictive framing and ship the descriptive/conditional instrument (Phases 0–3, 5)**. Do
  NOT build the OpEx/event forecasting machine on the strength of a daily-clock edge.
- **Scope honesty:** this rules out the edge **at the daily close-to-close clock only**. The
  event-clock channels (OpEx Thu→Fri charm, post-FOMC vanna drift, intraday) were NOT tested — they
  require the OpEx/FOMC/earnings calendar + short-DTE anchor that the offline seed corpus lacks. So
  the correct statement is: *"no detectable edge in the primary GEX/hedge-flow channel at the daily
  clock at proper power (n=1782); the event-clock channels remain untested."*
- The **descriptive/conditional instrument ships regardless** — it's valuable as a measurement
  product (per-strike per-greek net exposure, scenario-conditional hedge-flow budget, execution
  locus, SVI-RP overlays) whether or not the flow forecast clears.

## Acceptance-bar check ("Jason says ready")
1. ✅ Falsifier now interpretable (RULED_OUT via power analysis + effect-size CI, not a bare
   NOT_SUPPORTED).
2. ⚠️ OpEx/charm event-window arm still n=0 — NOT run (no calendar in corpus). Required before any
   broader claim.
3. ✅ Primary tested on the true hedge-flow object (impulse = gex_level × dS/spot, band-gated), not
   the Δ(dollar-gamma) proxy.
4. ✅ BH over the true family on secondaries; SPY/QQQ sign-consistency honored.
5. ✅ Descriptive/conditional instrument (Phases 0–3, 5) ships regardless of the predictive null.

## Files
- Fast-test driver: `Vol_Suite/run_expiry_fast_test.py`
- This report: `Vol_Suite/_expiry_falsifier_cache/expiry_fast_test_result.md`
- Full 12-ticker daily falsifier (prior): `_expiry_falsifier_cache/expiry_falsifier_FULL_12T_report.md`
- Arbiter round-2 verdict: `trading_journal/` (delivered in-session)
