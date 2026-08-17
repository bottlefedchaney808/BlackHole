# Expiry Book Exposure — Tier 2B Result (multi-window short-DTE confirmation)

**Status:** PENDING — data pull in progress; analysis runs on completion.
**Protocol:** locked pre-registration `Vol_Suite/_expiry_falsifier_cache/tier2B_pre_registration.md`
(written BEFORE inspecting any multi-window results).
**Driver:** `Vol_Suite/run_expiry_tier2_multi.py` (NEW file; `run_expiry_tier1.py` and
`expiry_book_exposure.py` UNCHANGED — hashes match the pre-run record).

---

## 1. Pre-registration (locked 2026-08-14, before results)

Full text: `tier2B_pre_registration.md`. Key commitments:
- **Window** = (as_of, expiry) with end-of-window DTE ∈ [2,7]; each ticker×window an independent
  cluster; observation days span the 14-day lookback, de-meaned PER cluster.
- **Primary estimand** = pooled (meta-analytic) corr between the corrected burst object
  (`hedge_flow_at` at zero-gamma/wall, gamma lagged one day) and continuous forward return.
- **Reused most-recent window** (20260817/20260821) is included in the pooled analysis but does
  NOT count toward the ≥3-prior-windows target.
- **Effective-N reported separately**: nominal obs / # ticker×window clusters / effective sample.
- **Multiplicity**: BH q<0.10 over the window family AND the per-ticker family.
- **Stability set**: leave-one-window-out, leave-one-ticker-out, placebo (non-breach days +
  shifted horizon), DTE buckets 2–3/4–5/6–7, index (SPY/QQQ) vs single names, vanna-shock arm
  (UNTESTABLE unless ≥20 genuine shock obs).
- **Verdict ladder**: SUPPORTED / FRAGILE / RULED_OUT / BLOCKED.
- **Honest rule**: BLOCKED ≠ no effect; BLOCKED is a data-acquisition limitation.

## 2. Data acquisition — REAL ThetaData, verified

Historical windows pulled by EXPLICIT expiry (list_expirations only returns currently-listed
expiries — documented limitation, NOT a data null). Full manifest written to
`_scratch_tier2b/tier2b_pull_manifest_final.json` with SHA-256 per file.

| as_of (window) | tickers | files | DTE range | source |
|---|---|---|---|---|
| 2026-08-14 (reused) | 12 | 12 | 3–7 | `_scratch_tier2/` (Tier 2 pull, not re-pulled) |
| 2026-07-31 | 12 | 12 | 3–7 | `_scratch_tier2b/window_20260731/` |
| 2026-07-17 | 12 | 12 | 3–7 | `_scratch_tier2b/window_20260717/` |
| 2026-07-03 | 12 | 12 | 3–7 | `_scratch_tier2b/window_20260703/` |
| 2026-06-19 | 12 | 12 | 3–7 | `_scratch_tier2b/window_20260619/` |
| 2026-06-05 | 12 | 12 | 3–7 | `_scratch_tier2b/window_20260605/` |
| 2026-05-22 | … | … | … | `_scratch_tier2b/window_20260522/` (in progress) |
| 2026-05-08 | … | … | … | `_scratch_tier2b/window_20260508/` (in progress) |

All pulls report **0 genuine errors**; per-window spot coverage 11–12 days, OI > 0.

## 3. No-drift check (harness validation) — PASSED

Reproduced Tier 2's published per-ticker correlations on the reused window with the new harness.
**Max deviation 0.00004 across all 12 tickers** (e.g. AAPL +0.4248 vs +0.4248, META −0.5948 vs
−0.5948, SPY 0.0000 vs 0.0000). The multi-window driver computes the identical burst object.

## 4. Analysis — FULL MULTI-WINDOW RESULT (7 prior windows + reuse, n=926, 96 clusters)

### PRIMARY (pre-registered estimand: pooled meta-analytic corr, burst→continuous fwd return)
| Metric | Value |
|---|---|
| Nominal obs | **926** |
| Independent ticker×window clusters | **96** (12 tickers × 8 windows) |
| Pooled de-meaned corr (cluster bootstrap) | **−0.0087** |
| Cluster-bootstrap CI | **[−0.039, +0.014]** |
| Meta-analytic pooled (DS-L over 8 windows) | **−0.0509** |
| Meta z-CI (back-transformed) | **[−0.1156, +0.0144]** |
| τ² (between-window heterogeneity) | 0.0000 |

**The meta-analytic CI straddles zero.** The multi-window pooled effect does NOT exclude 0.

### The single most important finding — the −0.20 did NOT replicate
| Window (as_of) | r | n | p |
|---|---|---|---|
| **reuse_20260814** (Tier 2's window) | **−0.2009** | 110 | **0.033** |
| window_20260508 | +0.0044 | 123 | 0.962 |
| window_20260522 | +0.0044 | 121 | 0.962 |
| window_20260605 | −0.0138 | 111 | 0.885 |
| window_20260619 | −0.1054 | 111 | 0.269 |
| window_20260703 | −0.0127 | 108 | 0.896 |
| window_20260717 | −0.1120 | 120 | 0.221 |
| window_20260731 | +0.0188 | 122 | 0.837 |

**The −0.20 SUPPORTED came from exactly ONE window (the most recent). All 7 independent prior
windows are null** (p 0.22–0.96; two positive, five negative-but-tiny, none significant). The
pre-registered primary estimand across all 8 windows is **FRAGILE/INCONCLUSIVE**.

### Leave-one-out (cluster bootstrap, n_iter=1000)
- **Drop reuse_20260814: r → −0.0068, CI [−0.0397, +0.0153]** — the entire negative signal
  collapses when the single SUPPORTED window is removed. This is decisive evidence the Tier-2
  result was **single-window-driven**.
- Drop any prior window: r stays −0.006 to −0.013, CI includes 0 in every case.
- Drop any ticker: r stays −0.004 to −0.014, CI includes 0 except MSFT (−0.014, CI [−0.053,−0.005]).

### Per-ticker (all windows pooled, BH q<0.10)
| Ticker | r | n | p | q |
|---|---|---|---|---|
| AAPL | −0.064 | 76 | 0.584 | 1.00 |
| AMD | +0.021 | 80 | 0.855 | 0.93 |
| AMZN | −0.022 | 76 | 0.848 | 1.00 |
| GOOGL | −0.047 | 76 | 0.684 | 1.00 |
| JPM | +0.029 | 82 | 0.794 | 1.00 |
| META | −0.132 | 76 | 0.254 | 1.00 |
| MSFT | +0.061 | 76 | 0.597 | 1.00 |
| NFLX | −0.110 | 82 | 0.322 | 1.00 |
| NVDA | −0.119 | 76 | 0.303 | 1.00 |
| QQQ | −0.096 | 75 | 0.409 | 1.00 |
| SPY | −0.044 | 75 | 0.707 | 1.00 |
| TSLA | −0.004 | 76 | 0.971 | 0.93 |

**No per-ticker effect survives multiplicity** (all q ≥ 0.93). 9/12 negative in sign — a weak
directional tilt, but nothing significant.

### Placebo / control
- **Non-breach days (burst=0):** n=498, corr = 0.0000 — degenerate (all x=0; zero variance, not a
  real test — reported honestly).
- **Shifted forward-return horizon (burst_t vs fwd_{t+1}):** per-cluster mean **−0.118** — the
  placebo shows a NEGATIVE correlation of comparable magnitude to the real effect, meaning the
  burst→next-day-return relation is **not distinguishable from a shifted-horizon artifact**.
  This is a red flag against claiming a causal daily lead.

### DTE buckets / index vs single
- DTE 2–3: n=38 (too few). DTE 4–5: n=150, r=+0.062. DTE 6–7: n=163, r=−0.022. No clean
  DTE gradient.
- **Index (SPY/QQQ): n=150, r=−0.045.** Single names: n=776, r=−0.0005. The weak negative tilt
  lives almost entirely in the index ETFs; single names are flat.
- SPY r=−0.044 / QQQ r=−0.096 — sign-consistent (both negative) but neither significant.

### Vanna-shock arm
**360 genuine shock observations found** → the arm is testable, but was NOT run in this pass
(pre-registration scoped Tier-2B to the burst primary; vanna-shock remains a separate question).

## 5. Verdict — FRAGILE / INCONCLUSIVE (per the pre-registered ladder)

**The Tier-2 short-DTE SUPPORTED (pooled corr −0.20) did NOT survive multi-window confirmation.**

- Meta-analytic pooled across 8 independent windows: **−0.0509, CI [−0.1156, +0.0144] — includes 0.**
- **Leave-one-window-out on the reuse window collapses the effect to −0.007.** The −0.20 was
  driven by a single window (the most recent).
- All 7 independent prior windows null; no per-ticker effect survives BH.
- Placebo shifted-horizon corr (−0.118) is comparable to the real effect — the daily-lead claim is
  not distinguishable from a horizon artifact.
- Weak directional tilt only: 9/12 tickers negative, index ETFs carry it (r=−0.045), single names
  flat (−0.0005), SPY/QQQ sign-consistent.

**Pre-registered disposition: FRAGILE/INCONCLUSIVE.** This is NOT RULED_OUT (the pooled sign is
still negative, SPY/QQQ sign-consistent) and NOT SUPPORTED (CI includes 0; effect is
single-window-dependent). The honest statement: **there is no robust, replicable evidence of a
dealer-hedge-flow edge at the short-DTE daily clock in this corpus.** The single-window SUPPORTED
must be downgraded from "provisional" to "artifact-risk."

**Per the arbiter's ladder:** FRAGILE/INCONCLUSIVE does NOT justify building the OpEx/event machine
on this evidence. It also does not justify permanently demoting the event-clock thesis — the
vanna-shock arm (360 obs, testable) and the intraday/event-clock channels remain unaddressed by a
daily-clock test. The descriptive/conditional instrument ships regardless.

## 6. Integrity
- `run_expiry_tier1.py` SHA-256: `96db3ff985395e5dcc906b4875f7e4269b860a50828289178fc6bbe6ac3a0e7a` (unchanged)
- `expiry_book_exposure.py` SHA-256: `5a623ab4ce6059654b5902f1dd16cfcbbb7a4ce0eafe90a2a9fd61c963543d6d` (unchanged)
- `git status --short`: only NEW untracked files; no tracked live-model file modified.
