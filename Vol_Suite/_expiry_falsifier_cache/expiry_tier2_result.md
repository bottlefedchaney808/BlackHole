# Expiry Book Exposure — Tier 2 Result (short-DTE anchor)

**Status:** SUPPORTED (provisional — small sample) on the short-DTE habitat.
**Driver:** `Vol_Suite/run_expiry_tier1.py` **unchanged**, run against genuine 2–7 DTE ThetaData
seed files (`_scratch_tier2/`). Independently re-run by the controller on 2026-08-14.

---

## 1. Real data acquisition — verified (12/12)

Genuine short-DTE pull via ThetaData. Manifest verified: real listed expiries, real rows.

| Ticker | Expiry | DTE | greeks | OI | spot |
|---|---|---|---|---|---|
| AAPL | 20260817 | 3 | 796 | 950 | 11 |
| AMD | 20260817 | 3 | 2409 | 2588 | 11 |
| AMZN | 20260817 | 3 | 806 | 880 | 11 |
| GOOGL | 20260817 | 3 | 811 | 844 | 11 |
| JPM | 20260821 | 7 | 1537 | 1816 | 11 |
| META | 20260817 | 3 | 1998 | 2184 | 11 |
| MSFT | 20260817 | 3 | 1106 | 1238 | 11 |
| NFLX | 20260821 | 7 | 2140 | 2728 | 11 |
| NVDA | 20260817 | 3 | 949 | 1072 | 11 |
| QQQ | 20260817 | 3 | 2581 | 2852 | 11 |
| SPY | 20260817 | 3 | 2553 | 2860 | 11 |
| TSLA | 20260817 | 3 | 1804 | 1914 | 11 |

**No far-DTE file was shifted/relabeled.** Expiries are genuinely 2–7 DTE (20260817 dte=3,
20260821 dte=7).

## 2. Corrected driver output (unchanged `run_expiry_tier1.py`)

### STEP A — TRUE hedge-flow burst (hedge_flow_at, lag-gamma), pooled de-meaned
| Metric | Value |
|---|---|
| Pooled n | **110** (9–10 short-DTE days × 12) |
| corr | **−0.2009** |
| cluster-bootstrap CI (2000 iter) | **[−0.3509, −0.0364]** |
| min-detectable (nominal n) | 0.2581 |
| min-detectable (rough eff-n ~300) | 0.1597 |
| **VERDICT** | **SUPPORTED** |

Pooled |r|=0.201 ≥ effective-n md (0.160) AND the cluster-bootstrap CI **excludes 0**. Per the
arbiter's pre-registered Tier 2 rule → **SUPPORTED on the short-DTE habitat** → evidence for an
event-clock dealer-hedge edge → **justifies building the OpEx/event machine**.

### Per-ticker corr (burst vs continuous fwd) — HIGH sign heterogeneity
AAPL +0.425 | AMZN +0.205 | JPM +0.087 | SPY 0.000 | GOOGL −0.130 | MSFT −0.187 |
QQQ −0.180 | NFLX −0.228 | TSLA −0.252 | AMD −0.078 | NVDA −0.426 | META −0.595

The pooled −0.20 is driven by a handful of large negative names (META, NVDA, TSLA); several
positive (AAPL, AMZN). **Sign heterogeneity is extreme** — this is not a uniform edge.

### STEP B — vol-shock vanna-lead probe
**No shock days found** (the short-DTE window is only ~9 days/ticker, so the |ΔIV| top-decile +
1.5σ gate yields zero exogenous-vol-shock days). The probe is vacuous on this data.

## 3. Honest verdict — SUPPORTED but FRAGILE

**Per the arbiter's pre-registered rule, the short-DTE habitat returns SUPPORTED** (pooled
|r|=0.201, cluster-bootstrap CI excludes 0). This is the first real evidence the dealer-hedge-flow
object relates to forward returns **in the short-DTE habitat** — the exact place Karsan's framework
says the edge lives.

**But three hard caveats, reported honestly:**
1. **Tiny sample.** n=110 = only 9–10 days per ticker (a single 2–7 DTE snapshot each). This is a
   provisional signal, not a robust confirmation.
2. **Extreme sign heterogeneity.** META −0.59 and NVDA −0.43 drive the pooled negative; AAPL +0.42
   is strongly positive. The pooled mean hides real cross-ticker disagreement — consistent with the
   round-3 panelist-2 warning that a single pooled corr can mask per-ticker edges.
3. **Step B vacuous** (no vol-shock days in the 9-day window), so the vanna-lead question remains
   unanswered even on short-DTE.

## 4. Per-habitat comparison
| Habitat | Pooled corr | CI | Verdict |
|---|---|---|---|
| **Far-DTE** (Tier 1, n=1782) | +0.0007 | [−0.024, +0.028] | **RULED_OUT (narrow)** |
| **Short-DTE** (Tier 2, n=110) | **−0.2009** | [−0.351, −0.036] | **SUPPORTED** |

The contrast is stark: **no uniform edge far-DTE, but a real (negative) burst→forward-return
relation short-DTE.** This is the first data that distinguishes the two habitats in the direction
Karsan predicted.

## Files
- Driver: `Vol_Suite/run_expiry_tier1.py` (unchanged)
- Short-DTE seed: `.worktrees/dealer-exposure-dev/Vol_Suite/_scratch_tier2/` (12 files)
- Pull manifest: `.../_scratch_tier2/pull_manifest.json`
- This report: `Vol_Suite/_expiry_falsifier_cache/expiry_tier2_result.md`
- Tier 1: `Vol_Suite/_expiry_falsifier_cache/expiry_tier1_result.md`

---

## Tier 2b — Multi-window short-DTE confirmation (2026-08-14)

**Full report:** `expiry_tier2B_result.md`. **Pre-registered:** `tier2B_pre_registration.md`.
**Data:** 7 genuine prior 2–7 DTE windows × 12 tickers (real ThetaData, explicit-expiry pulls,
84/84 OK, 0 BLOCKED) + the reused most-recent window. **Harness:** no-drift check passed (max
dev 0.00004 vs Tier 2's published per-ticker corrs).

### Result — the −0.20 did NOT replicate
| Metric | Value |
|---|---|
| Nominal obs | 926 |
| Independent ticker×window clusters | 96 |
| Pooled corr (cluster bootstrap) | −0.0087, CI [−0.039, +0.014] |
| Meta-analytic pooled (DS-L, 8 windows) | **−0.0509, CI [−0.116, +0.014]** |
| Single-window effect (reuse_20260814) | −0.2009 (p=0.033) |
| All 7 prior windows | null (p 0.22–0.96) |
| Leave-one-window-out (drop reuse) | **r → −0.0068, CI [−0.040, +0.015]** |
| Per-ticker after BH | all q ≥ 0.93 (none survive) |
| Placebo shifted-horizon | −0.118 (comparable to real effect) |
| Index vs single | index r=−0.045, single r=−0.0005 |

### VERDICT: FRAGILE / INCONCLUSIVE
**Tier 2's short-DTE SUPPORTED (−0.20) was single-window-driven and does not survive independent
windows.** Meta CI includes 0; dropping the reuse window collapses the effect to ≈0. No per-ticker
effect survives multiplicity. **Downgrade Tier 2's SUPPORTED to artifact-risk.** This does NOT
justify building the OpEx/event machine on this evidence, and does NOT permanently demote the
event-clock thesis (vanna-shock arm has 360 testable obs; intraday/event-clock channels remain
unaddressed by a daily-clock test). The descriptive/conditional instrument ships regardless.
