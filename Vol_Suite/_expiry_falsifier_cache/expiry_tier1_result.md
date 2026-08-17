# Expiry Book Exposure — Tier 1 Result (corrected measurement + vol-shock probe)

**Source:** `Vol_Suite/run_expiry_tier1.py`, reusing `expiry_book_exposure.py`
(`build_net_exposure`, `execution_locus`, `hedge_flow_at`, `_date_of`). Network-free, runtime **6s**.
**Design:** the Karsan arbiter's round-3 Step A (corrected measurement) + Step B (vol-shock
vanna-lead probe), Tier 1 = zero new data on the existing offline seed corpus.
**Corrections applied per the arbiter:** (i) TRUE burst object via `hedge_flow_at(locus, price)` at
the zero-gamma/wall (NOT the gex_level×dS revaluation proxy); (ii) **gamma lagged one day**
(locus from day t-1, fired by day-t spot move — fixes the contemporaneous contamination);
(iii) continuous forward return; (iv) per-ticker cluster bootstrap CI + effective-n min-detectable;
(v) BH on real two-sided p (Step B); (vi) per-ticker corr distribution.

---

## STEP A — TRUE hedge-flow burst (hedge_flow_at, lag-gamma), pooled de-meaned
| Metric | Value |
|---|---|
| Pooled n | 1782 |
| corr (burst vs continuous fwd) | **+0.0007** |
| cluster-bootstrap CI (2000 iter) | **[−0.0237, +0.0275]** |
| min-detectable \|r\| nominal (n=1782) | 0.0662 |
| min-detectable \|r\| rough effective-n (~300) | 0.1597 |
| **Verdict** | **RULED_OUT (narrow)** |

The cluster-bootstrap CI is genuinely narrow and centered on zero (−0.024 to +0.028), and it
excludes the effective-n min-detectable. So the **true burst object** also shows **no homogeneous
mean daily-clock edge** — consistent with the fast test, but now on the correct object with
honest cluster-robust power.

### Per-ticker corr (burst vs continuous fwd) — the dispersion the pooled mean masks
| Ticker | r | | Ticker | r |
|---|---|---|---|---|
| AAPL | +0.095 | | MSFT | **+0.153** |
| AMD | −0.020 | | NFLX | +0.068 |
| AMZN | **+0.152** | | NVDA | −0.065 |
| GOOGL | +0.062 | | QQQ | −0.074 |
| JPM | −0.085 | | SPY | −0.005 |
| META | −0.059 | | TSLA | −0.069 |

**Real sign-heterogeneity:** AMZN (+0.152) and MSFT (+0.153) sit essentially AT the theorized
|r|≈0.15 edge in the positive direction; several single names are negative. The pooled ≈0 mean is a
cancellation, exactly as the round-3 panelist-2 flagged. This is a directional hint worth noting —
the far-DTE daily burst shows *no uniform* edge, but two names are at the theorized magnitude with
positive sign.

---

## STEP B — VOL-SHOCK VANNA-LEAD PROBE (exogenous vol-shock days)
Shock day = |ΔIV_atm| top-decile AND |daily return| > 1.5×prior-20d realized vol. Signal = burst
(hedge_flow_at) on those days; response = next-1-day continuous return.

| Ticker | shock_days | r | | Ticker | shock_days | r |
|---|---|---|---|---|---|---|
| AMZN | 7 | −0.201 | | NVDA | 5 | −0.569 |
| GOOGL | 6 | +0.082 | | SPY | 6 | +0.151 |
| META | 5 | −0.711 | | TSLA | 7 | +0.525 |

**POOLED: n=36, corr −0.0234, two-sided p=0.891, min-detectable@80% = 0.4231 → RULED_OUT (narrow),
BUT n=36 is far too small to be informative** (min-detectable 0.42 — a real 0.10–0.15 edge is
invisible at this n). This is effectively **UNDERPOWERED/INCONCLUSIVE**, not a meaningful null.

---

## What this decides (per the arbiter's pre-registered ladder)
- **STEP A:** clean narrow **RULED_OUT** on the far-DTE daily burst clock at honest cluster-robust
  power — no *homogeneous mean* edge on the true burst object. Plus a real **per-ticker dispersion**
  hint (AMZN/MSFT at +0.15 positive) that a pooled mean washes out.
- **STEP B:** the vol-shock probe is **underpowered** (only 36 pooled shock days; min-detectable
  0.42) — it cannot resolve the vanna-lead question.
- **Per the arbiter's rule:** both results are on the **far-DTE daily clock only**. A far-DTE
  RULED_OUT (narrow) **does NOT clear the short-DTE / OpEx charm channel**, and the underpowered
  Step B means **the event-clock question is still open**. **Tier 2 (one 2–7 DTE anchor per ticker,
  re-run the identical corrected driver, verdicts separately by habitat) is the mandated decisive
  next step** before any permanent "no edge."

## Files
- Tier-1 driver: `Vol_Suite/run_expiry_tier1.py`
- This report: `Vol_Suite/_expiry_falsifier_cache/expiry_tier1_result.md`
- Prior: `expiry_fast_test_result.md`, `expiry_falsifier_FULL_12T_report.md`
- Arbiter round-3 verdict: `trading_journal/` (in-session)
