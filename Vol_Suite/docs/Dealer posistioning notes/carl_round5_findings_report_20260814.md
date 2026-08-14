# Dealer-Exposure Study — Full Findings & Testing Methods Report
**For panel review (CARL round 5 — mandate of insight to improve)**

**Date:** 2026-08-14  **Tree:** Dealer-Exposure-Dev (`.worktrees/dealer-exposure-dev`)
**Author session:** 20260814 (Hermes default profile)

---

## 0. Executive summary

1. The **live dealer model** and the **new expiry-book exposure model** were compared head-to-head on 96 snapshots (12 tickers × 8 short-DTE windows). After fixing a **y≡0 predictiveness bug** and a **units confound**, the honest picture is: the models are *different objects* (50% sign agreement, within-ticker concordance CI straddling 0) but *rank names similarly* on units-comparable exposure (cross-sectional corr **+0.84** after standardization vs +0.16 raw).
2. **Charm-at-OpEx is CONFIRMED on intraday data** (Karsan's ESC-3 claim): 43×–6109× short-vs-long charm in the final 2 hours; 9–16× open→close acceleration on OpEx day.
3. **The from-breach intraday clock is BOUNDED with a NEGATIVE flag**: ΔIV-signed flow corr −0.088 (CI [−0.143,−0.040] excludes 0) at n=28 QQQ-only firing buckets — opposite of the EXPECT-POSITIVE prediction, but |r| ≪ min-detectable (0.508) → not a refutation, a warning.
4. **What was NOT tested**: the live model's ΔIV-signed vannaflow on the intraday from-breach clock (only the new model's execution-locus burst was wired in; live vannaflow was compared only at the daily clock, B4: 55.2% burst-sign agreement, BOUNDED).

---

## 1. Models under test

### Live dealer model (production, both trees, corrected 08-14)
- `sign_model = 'vol_surface_replication'` (Layer 1a+1b), `VOL_SURFACE_FITTER = svi`, `IV_DEADBAND_VOL = 0.01`, `DEALER_VANNA_FLOW = 1`, **accumulation ON by default** (the 08-14 fix; was silently OFF for 2 days of testing).
- NOT direction, NOT SABR-as-live, NOT oi_heuristic (vestigial signature default only).
- Signal compared: `total_net_gamma` (raw, ~1e2–1e3) and net vanna shares (`vanna_call+put`).

### New expiry-book exposure model (`expiry_book_exposure.py`, Phases 0–6)
- Per-strike/per-expiry/per-greek snapshot vector: DEX/GEX/VEX/ChaEX/vega/VoEX; `execution_locus` (zero-gamma, call/put walls, tolerance band, gex_slope); `hedge_flow_at` (threshold-gated burst); `scenario_hedge_flow` (the actionable output); structural regime arm; SVI overlays; falsifier.
- Signal compared: `gex()` = Σ Γ·OI·100·spot²·0.01 (dollar-gamma-per-1%, ~1e6–1e8).

---

## 2. Methods — the comparison harness (daily, both models)

**Corpus:** 96 clusters = 12 tickers (AAPL AMD AMZN GOOGL JPM META MSFT NFLX NVDA QQQ SPY TSLA) × 8 short-DTE windows (7 prior windows 20260508..20260731 + reuse_20260814), 9–11 days each, single regime.

**Harness:** `run_compare_live_vs_new.py` — `_SeedTD` serves one seed file to `compute_dealer_positioning` (full live path, offline, time frozen at as-of; **accumulation ON**); `run_new_model` builds `build_net_exposure` on the same day's rows. Both models run on identical data.

**Three harness fixes this session (each moved the headline — proof of measurement dominance):**
1. `SIGN_MODEL` default: `oi_heuristic` (vestigial) → `vol_surface_replication` (the settled live model). [76% → 83%]
2. `_SeedTD` strike shape: was pre-dividing by 1000 while `compute_dealer_positioning` re-applies `strike_from_theta` → double conversion dropped every strike-dependent leg → degenerate all-SHORT book. Fixed to serve raw theta-int strikes. [→ real accumulated live book]
3. Accumulation wiring: `_accumulation_hist_rows` now feeds the seed's full history so `_accumulate_from_history` actually executes (verified: 186-strike book, no silent fallback). [83% → 50%]

### Statistics (all computed on the 96 clusters)

| Stat | What | Method | Result |
|---|---|---|---|
| (1) Sign agreement | binary LONG/SHORT per snapshot | sign(new_gex) vs sign(live_gamma) | **48/96 = 50.0%** (SE 5.1%, CI [40,60] — on the no-agreement null) |
| (2) Cross-sectional | per-ticker mean exposure | Pearson over 12 ticker means | **+0.1595** (CI straddles 0 at n=12) |
| (2b) B2 units-standardized | same, both in dollar-gamma-per-1% | live_gamma × spot² × 100 × 0.01 | **+0.8449** — the +0.16 was a spot² size confound |
| (3) B1 per-day predictiveness | FIXED y≡0 bug | per-day sign signal vs real aligned fwd[i], pooled de-meaned, cluster-bootstrap 90% CI | NEW **−0.0163** CI [−0.074,+0.039]; LIVE **−0.0741** CI [−0.142,−0.009] — both BOUNDED (md 0.102) |
| (4) B3 within-ticker concordance | units-normalized rank agreement | per-ticker z-scored Spearman, Fisher-tanh CI | **+0.1603** CI [−0.43,+0.65] → disagree-by-habitat-not-rank |
| (4b) B3 divergence decomposition | is JPM 12% systematic? | per-ticker disagreement-flip count | **ALL 12 tickers oscillating/noise** — no consistently-opposite ticker; JPM 12% = sign-determinacy |
| (5) B4 firing-day burst-sign | where the hedge fires | |ΔIV|>0.01 AND burst≠0 days; sign(new burst) vs sign(live net vanna × ΔIV) | INDEX **55.2%** (n=29); SINGLES **49.0%** (n=245) — BOUNDED |

### The y≡0 bug (R2 cross-examiner catch, verified 96/96)
Old statistic (3) fed `s['fwd'][-1]` — the terminal **0.0 placeholder** in `build_burst_series_from_seed` (appended on the last day, no next-day return in-window). So `y≡0` for every cluster → corr 0.0000 **by construction** for both models. All three R1 panelists reasoned about 0.0000 as a genuine null; none caught it. **Lesson: a clean 0.0000 for both models in any comparison = check for a zero-target/placeholder bug first.**

---

## 3. Methods — intraday arms (option C: intraday bars)

**Data:** ThetaData proxy intraday routes — `hist/option/all_greeks` with `ivl=600000` (10-min buckets, 40/day, 9:30–16:00 ET, full charm/gamma/vanna/IV) + `hist/stock/ohlc` (1-min) + `hist/option/open_interest` per strike. Expired expiries queryable by explicit expiry. **Route facts:** per-day calls only (first date of range); strikes must be on the listed grid (SPY/QQQ $5, exact strikes 404); `hist/option/quote` RTH-gated pre-2026-08-17; some monthlies 404 entirely (20260619 = coverage gap).

### Tier-3A — Charm-at-OpEx (`run_intraday_charm_opex.py`)
**Method:** 3 past OpEx weeks (May 15, Jun 19, Jul 17 2026); per week T-1 + OpEx day; expiry pair = OpEx-week short (1–0 DTE) vs next-month long (~30–36 DTE); ATM strike snapped to $5 grid; mean |charm| in final 2 hours (≥14:00 ET) vs open 2 hours.
**Result: SUPPORTED 4/4 paired days.**

| Week | Day | Ticker | short (DTE) | final2h \|charm\| | long (DTE) | final2h \|charm\| | ratio |
|---|---|---|---|---|---|---|---|
| jul | T-1 | SPY | 20260717 (1) | 8.49 | 20260821 (36) | 0.195 | 43.4× |
| jul | T-1 | QQQ | 20260717 (1) | 7.38 | 20260821 (36) | 0.147 | 50.1× |
| jul | OPEX | SPY | 20260717 (0) | 692.1 | 20260821 (35) | 0.278 | 2489× |
| jul | OPEX | QQQ | 20260717 (0) | 628.6 | 20260821 (35) | 0.103 | 6109× |

OpEx-day acceleration open→final-2h: SPY **16.4×**, QQQ **9.4×**.
Coverage: May/Jun long comparators (20260619) 404 on proxy — 4 fully-paired days all July.

### Tier-3B — From-breach intraday clock (`run_intraday_flow.py`, v2)
**Method (pre-registered round-4b estimand on the intraday clock):**
- `signed_flow = −sign(ΔIV_i) × burst_i`; burst = `hedge_flow_at` at the **day-anchored** execution locus (C+P chain, real OI, band = open spot ±1% fixed for the day).
- Support: ALL 10-min buckets with nonzero ΔIV AND nonzero burst (de-gated).
- Response: forward 10-min return from the breach bucket.
- Prediction: from-breach/intraday **EXPECT POSITIVE** (direct vanna push; only a null here refutes).
- Stats: pooled de-meaned corr, cluster-bootstrap 90% CI, tanh min-detectable, verdict ladder.

**Two v1 bugs fixed:** (1) C-only chain → one-sided zero_gamma garbage (locus needs C+P); (2) per-bucket band re-anchor → breach required >1% move within one 10-min bucket = structurally zero bursts (band must be anchored once per day).

**Result: BOUNDED — negative directional flag.**

| Metric | Value |
|---|---|
| Firing buckets (index) | **28 — all QQQ** (SPY fired 0: 0.84–1.43% ranges never breached ±1% band) |
| from-breach signed-flow corr | **−0.0884** |
| 90% cluster-CI | **[−0.143, −0.040]** (excludes 0) |
| min-detectable \|r\| @ n=28 (tanh) | **0.508** |
| Verdict | underpowered — CI excludes 0, NEGATIVE sign (opposite of prediction), but \|r\| ≪ md → **no claim** |

---

## 4. What was tested on which model — the honest ledger

| Test | Live model | New model | Both? |
|---|---|---|---|
| Sign agreement (1) | ✓ total_net_gamma | ✓ gex() | **BOTH** |
| Cross-sectional (2/2b) | ✓ | ✓ | **BOTH** |
| Per-day predictiveness (3/B1) | ✓ | ✓ | **BOTH** |
| Within-ticker concordance (4/B3) | ✓ | ✓ | **BOTH** |
| Firing-day burst-sign (5/B4) | ✓ live net vanna × ΔIV | ✓ hedge_flow_at burst | **BOTH** (daily clock) |
| Tier-3A charm-at-OpEx | — (market data; model-agnostic charm field) | — | **NEITHER** (data fact both would share) |
| Tier-3B from-breach intraday | ✗ **NOT wired** | ✓ execution-locus burst | **NEW ONLY** |

**GAP (explicit):** the from-breach intraday clock tested the **new model's** execution-locus burst only. The **live model's** ΔIV-signed vannaflow term was NOT computed on the intraday clock — its only comparison is the daily-clock B4 (55.2%, BOUNDED). The arbiter's decisive flow question ("do the models point the hedge the same way where it fires") is therefore **only half-answered intraday**: we know the new model's burst direction vs forward returns (negative flag), but not whether the live model's vannaflow agrees/disagrees with it on the same breach days.

---

## 5. Open questions for the panel

1. **The negative from-breach flag**: sign convention error, real mechanism counter-signal, or daily-clock shadow leaking into the 10-min window? What single test separates these?
2. **The live-model intraday gap**: is wiring live ΔIV-signed vannaflow into Tier-3B (same breach days, same buckets) the correct next build — or is there a cheaper way to get the two-model flow agreement on the firing clock?
3. **SPY firing days**: FOMC/earnings days or 0.5% tolerance — which first, and does the negative flag change the priority?
4. **+0.84 cross-sectional vs +0.16 within-ticker**: the models agree on who's big/small but not on when a name flips. Is "habitat difference" a real economic object (150d book vs single expiry) or a measurement artifact we haven't killed yet?
5. **Charm-at-OpEx confirmed** — what does the panel now recommend the OpEx/event machine should be (given it's the first empirically-confirmed channel)?
6. **The y≡0 bug class**: any other placeholder/zero-target traps lurking in the falsifier chain (Tier-1/2B/2C/2D) worth a mechanical audit?

---

## 6. Files & artifacts (Dealer-Exposure-Dev)

- Comparison driver (B1–B4): `Vol_Suite/run_compare_live_vs_new.py`
- Comparison result: `Vol_Suite/_expiry_falsifier_cache/compare_live_vs_new_result.md`
- R1 ledger: `docs/Dealer posistioning notes/carl_consult_20260814_r1_ledger.md`
- R2 cross-examiner: `~/.hermes/cache/delegation/subagent-summary-0-20260814_164746_716688.txt`
- R3 Karsan arbiter: `~/.hermes/cache/delegation/subagent-summary-0-20260814_165209_514983.txt`
- Tier-3A driver + result: `Vol_Suite/run_intraday_charm_opex.py`, `_intraday_cache/Tier3A_charm_opex_RESULT.md`
- Tier-3B driver + result: `Vol_Suite/run_intraday_flow.py`, `_intraday_cache/Tier3B_flow_from_breach_RESULT.md`
- Tests: 50 passed (7 phase files) after all changes.
- Evidence kit hash: `f2f4f6faa6adebbc06896165e84fd52761dc915940de165394f97bbc71eb97c0` (FILE hash of compare result md)
