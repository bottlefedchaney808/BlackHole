# CARL Consult — R1 Panel Ledger (live-vs-new dealer-model comparison)

**Date:** 2026-08-14
**Protocol:** multi-agent-debate CARL panel — R1 3 adversarial panelists, R2 cross-examiner, R3 Cem Karsan arbiter (elitist persona, verified framework, mandate of insight to improve).
**Evidence kit hash:** `f2f4f6faa6adebbc06896165e84fd52761dc915940de165394f97bbc71eb97c0` (FILE hash of `compare_live_vs_new_result.md`, NOT a git ref)
**Subject:** Corrected live-vs-new comparison — LIVE dealer model (`vol_surface_replication` + `VOL_SURFACE_FITTER=svi` + IV dead-band 0.01 + vannaflow + **accumulation ON**) vs NEW expiry-book exposure model (`expiry_book_exposure.py`, Phases 0-6). Result: **48/96 = 50.0% sign agreement, cross-sectional corr +0.1595, forward-return 0.0000 both** (96 snapshots, 12 tickers × 8 short-DTE windows). Per-ticker: TSLA/NVDA/AMZN 75%, NFLX 62%, AAPL/MSFT/SPY/GOOGL 50%, QQQ/META 38%, AMD 25%, JPM 12%.

**Locked decisions (verbatim, do not relitigate):**
- Live model = `vol_surface_replication` (Layer 1a+1b) + `VOL_SURFACE_FITTER=svi` (single fitter) + `IV_DEADBAND_VOL=0.01` + `DEALER_VANNA_FLOW=1` + **accumulation ON by default** (corrected 08-14; was silently OFF for 2 days — the last 2 days of tests ran the wrong model)
- `direction` / SABR / `oi_heuristic` are NOT part of this model (direction ditched, never merged into the Windows tree; SABR is an alternate toggle only)
- rec.vanna = −1×BS pinned; real spot, not median-strike
- Live model files untouched except the accumulation-default fix
- All prior CARL verdicts stand, incl. round-4b: continuous ΔIV-signed estimand is the decisive test; two-clock sign prediction (daily close-to-close EXPECT NEGATIVE/shadow, from-breach/intraday EXPECT POSITIVE); index tilt = primary hypothesis carrier

---

## PANELIST 1 — FRAMING (confidence 0.82; ranking: 1 framing, 2 dealer-mechanics, 3 data-measurement)

**Position: SUPPORTED.** The headline numbers are a framing artifact, not a verdict on the models. Three framings collide:

- **(a) Coin-flip sign read.** `run_compare_live_vs_new.py:284-290` collapses two continuous exposure objects into one binary LONG/SHORT bit per snapshot. 48/96 = 50% = statistically indistinguishable from a coin flip on a 2-level read; throws away the entire magnitude distribution. Per-ticker spread (JPM 12% vs TSLA 75%) is the signature of sign-flipping near a zero net book — precisely where a LEVEL read is meaningless.
- **(b) Horizon mismatch on the wrong clock.** `driver:315-332, 266-268; expiry_book_exposure.py:1217,1286` correlate net-gamma LEVEL sign to next-day close-to-close. Net-gamma level is a persistent structural carry (Karsan brief:38-46) — the SLOW variable defining the regime, NOT expected to predict a daily sign. The thing that predicts the daily increment is the ΔIV-signed FLOW (brief:52-55). Running a level against a daily return is a GUARANTEED 0.00 — same 0.00 the whole Tier 1-2B chain produced. The design guarantees it.
- **(c) The real question is the DELTA of exposure.** Karsan: "change in positioning is important" (brief:143); tradeable daily quantity is the flow increment, not the stock (brief:52-55). The comparison never measures Δexposure, ignores the new model's own event/flow machinery (`opex_event_window_arm`, model:1302).

**Attacks:** On data-measurement — n is not the problem; every row feeds the WRONG statistic (level→daily corr is structurally 0; more n makes the wrong question more precisely wrong). Cross-sectional +0.16 is n=12 ticker means, not model evidence. On dealer-mechanics — the right greeks EXIST but the driver throws them away (reduces new model to a single `.gex()` level); "a different greek column" re-derives the same null in costume; mechanics need the corrected estimand first.

**Best method (hybrid):** Replace the three headline stats with ONE pre-registered falsifier on the ΔIV-signed vanna flow — `signed_flow = -sign(ΔIV_i) × burst_i` on all nonzero days, aligned returns, SPY/QQQ as one cluster, single-draw permutation null (the round-4b Tier-2C decisive test), run on BOTH clocks (daily = expect negative/shadow; from-breach = expect positive).

**INSIGHT TO IMPROVE:** Add a **DELTA-OF-EXPOSURE / CHANGE-IN-POSITIONING falsifier arm** — regress forward returns on the SIGN of ΔGEX / ΔVanFlow (window-over-window and day-over-day change), not on exposure level. Retires the level-null; producible today with zero new data by both models; pre-registers an explicit acceptance bar (beat the level-null AND permutation CI excluding 0). Synergy: data-measurement panelist supplies permutation/block-null + SPY/QQQ clustering (model already ships `_block_perm_p`, `opex_event_window_arm` at horizon-k); dealer-mechanics supplies which Δ channel (vanna vs charm vs gamma) and on which clock; framing supplies the CHANGE-not-LEVEL object.

---

## PANELIST 2 — DATA-MEASUREMENT (confidence 0.82; ranking: 1 data-measurement, 2 framing, 3 dealer-mechanics)

**Position: SUPPORTED.** Measurement artifact stack, line-by-line:

- **(a) Effective n is far below 96.** Sign agreement 48/96 = 50% has SE sqrt(0.25/96) = 5.1% → 95% CI [40%, 60%] — sits exactly ON the no-agreement null. Cross-sectional +0.16 is n=12 per-ticker means, SE ~1/sqrt(12) = 0.29 → CI ~[-0.42, +0.66] — no different from zero. SPY/QQQ are near-twins (~0.99 correlated, both in the 50% row); 8 windows are 7 temporally-adjacent short-DTE pulls + 1 reuse inside a single persistent regime (Karsan: structural carry stays in ONE regime, brief:47-49). True independent information ≈ (11 effective ticker groups) × (1-2 regime-windows) → effective n in the low tens; every statistic underpowered ~3-5×.
- **(b) Units are mixed; only signs are comparable.** new_gex = Γ·OI·100·spot²·0.01 (dollar-gamma-per-1%, ~1e6-1e8, model:21,265) vs live_gamma raw (~1e2-1e3, no spot², no 0.01, driver:277). Cross-sectional ranking of new_gex is contaminated by spot² (SPY~740, QQQ~520, JPM~230) → +0.16 is partly corr(spot²-weighted gamma, raw gamma) — a size/level confound. Binary-sign test (driver:289-292) throws away all magnitude and has minimal power.
- **(c) Habitats differ by construction.** Live signal = total_net_gamma of a 150-DAY ACCUMULATED book (`dealer_positioning.py:16458`); new gex = dollar-gamma of a SINGLE short-DTE (2-7 DTE) expiry snapshot. Comparing a broad multi-expiry aggregate vs a single-expiry slice. Harness feeds `_accumulation_hist_rows` (driver:196,203-207) but seeds only carry ~10 dates of history → "150d" accumulation is really a ~10-day replication against short-DTE expiries — matches NEITHER habitat.
- **(d) Per-ticker agreement is a data-determinacy effect, not model agreement.** JPM 12% is NOT OI sparsity (1537 greeks / 85 strikes, comparable to TSLA 1804/117) — it is CONSISTENTLY OPPOSITE = systematic per-ticker sign divergence. Report never decomposes WHY a ticker disagrees (accumulation flip? spot² weighting? single-expiry-vs-book mismatch? chain moneyness distribution?).
- **(e) The exact 0.0000 is a red flag, not a result.** corr(sign,ret) EXACTLY +0.0000 to 4dp for TWO different models means sum(sign·y)=0 for both — implausibly clean; suggests degenerate sign/return structure or the copysign(±1) projection washing out signal (driver:320-322). Must be inspected, not read at face value.
- **(f) Strongest evidence: the harness history.** This session's 3 fixes (strike shape, sign_model default, accumulation wiring; result:25-31) moved the headline 83%/+0.56 → 50%/+0.16. The number moved MORE from measurement plumbing than from any dealer-mechanics hypothesis — measurement-dominated, not signal-dominated. Prior verdicts tested the wrong model and are all void.

**Attacks:** On framing — asserting "the models measure different things" REQUIRES rejecting the null of no-agreement, and 50% ± 5.1% sits exactly ON that null; framing is a DIAGNOSIS not a METHOD (unfalsifiable until a habitat-matched comparison shows the difference persists). Also can't explain why the divergence is per-ticker (JPM vs TSLA) — a pure "different objects" objection should hit all tickers uniformly. On dealer-mechanics — you cannot litigate which greek/clock when the test never isolated ANY single greek (static gamma signs only, no ΔIV, no time-conditioning); the ΔIV-signed vanna-shock design is the RIGHT test but inherits every measurement defect (effective-n collapse, non-independence, no declared effect size/power).

**Best method (hybrid):** FIRST reconcile units and habitat (express BOTH models as dollar-gamma-per-1% of notional with the IDENTICAL Γ·OI·100·spot²·0.01 formula; run live on a corpus spanning its 150d home or restrict both to the SAME single expiry). THEN compute effective n (drop/collapse SPY-QQQ twin, correct within-regime window correlation), replace binary-sign with CONTINUOUS concordance (rank correlation of normalized magnitudes + per-ticker systematic-divergence decomposition), pre-register effect size. ONLY THEN run the arbiter's ΔIV-signed vanna-shock event study with the power analysis in hand.

**INSIGHT TO IMPROVE:** Pre-register an **effective-n-corrected POWER ANALYSIS** and switch the comparison metric from binary sign-agreement to **units-normalized CONTINUOUS concordance** BEFORE any event study: (1) compute the design-effect of the 96-snapshot corpus (collapse/downweight SPY/QQQ twin; estimate within-regime window correlation) → report TRUE effective n and CI on every statistic ("50% ± effective-n SE", not "50%"); (2) re-derive live_gamma as dollar-gamma-per-1% with the IDENTICAL formula → continuous, spot²-free, units-comparable rank concordance; (3) decompose per-ticker agreement into systematic-vs-noise (is JPM consistently-opposite and WHY) → a 12% row becomes a falsifiable per-ticker hypothesis; (4) declare the min effect size the event study must detect + effective-n power → a "null" is interpretable rather than read off a degenerate artifact. Makes both other panels' proposals testable — the exact discipline the 3 harness fixes prove is missing.

---

## PANELIST 3 — DEALER-MECHANICS (confidence 0.78; ranking: 1 dealer-mechanics, 2 framing, 3 data-measurement)

**Position: SUPPORTED.** Mechanics artifact:

- **(a) The compare runs the shadow clock.** The forced hedge fires INTRADAY at tolerance-band breach — `hedge_flow_at` returns 0.0 inside the band and only emits a burst once price crosses band_lower/band_upper (`expiry_book_exposure.py:645-660`). The daily residual is EXPECTED to mean-revert, NOT carry a positive sign signal → flat r=0.0000 on daily close-to-close is the EXPECTED null, not a refutation.
- **(b) The decisive object is never invoked.** `vanna_flow(ne, d_iv) = Σ signed_vanna·OI·100·VANNA_PP_SCALE·(dIV/0.01)` (`:358-377`) is literally the arbiter's `signed_flow = -sign(ΔIV_i) × burst_i` — never called anywhere in the compare.
- **(c) Neither model's net-GEX LEVEL was ever the claim.** New model's product = `scenario_hedge_flow` (the forced-hedge flow budget; DEX demoted to carry_descriptor, :671,:686) + execution-locus burst (`hedge_flow_at` sized by gex_slope, :656-660); live model's product = ΔIV-signed vannaflow + accumulated carry. The compare discards all of that and tests a scalar neither model claims is tradeable.
- **(d) Per-ticker disagreement = mechanics fingerprint, not noise.** Index-concentrated carry (QQQ 38%, SPY 50%) vs single-name low-participation habitats (JPM 12%, AMD 25%) sit at opposite ends — consistent with the arbiter's structural index-carry hypothesis and different time-horizon mechanics (150d book vs expiry book). The compare is a valid COHERENCE check (models don't measure the same object) but NOT a falsification of the flow hypothesis, which was never on trial.

**Attacks:** On framing — "the question is wrong" throws out the only real signal (the divergence IS the mechanics result: 50%/+0.16 precisely because time-horizon/carry mechanics differ); also would derail the round-4b mandate by demanding re-litigating comparability first. On data-measurement — more observations of the WRONG object on the WRONG clock = a tighter, more confident estimate of zero; 96 snapshots are already enough to falsify the LEVEL-vs-close claim (r=0.0000 for both is a CLEAN null); n-based lens would smooth JPM/QQQ/AMD away as "small-cell dispersion" when they cluster by HABITAT.

**Best method (hybrid):** Run the continuous ΔIV-signed vanna-shock event study on THIS short-DTE corpus (signed_flow = -sign(ΔIV_i) × burst_i on all nonzero-ΔIV-and-breach days, SPY/QQQ one cluster, single-draw permutation null, cluster bootstrap + BH q<0.10) AND add an execution-locus burst arm to the compare (new scenario_hedge_flow / hedge_flow_at burst vs live ΔIV-signed vannaflow term) on from-breach/same-session/intraday + OpEx-FOMC-earnings event windows — never next-day close-to-close.

**INSIGHT TO IMPROVE:** Convert the agreement STATISTIC from level-sign to **BURST-sign on the firing clock**: per-snapshot `corr( sign(hedge_flow_at burst from new execution_locus), sign(live vannaflow ΔIV-signed term) )` RESTRICTED to days where |ΔIV| > dead-band AND price breaches the tolerance band, plus a QQQ/SPY-vs-single-name split of that burst-agreement. Keeps the legitimate head-to-head (rejects "wrong question"), gives a proper event-driven sample (firing days, not 96 arbitrary close-to-close snapshots), and tests whether the models agree on the FLOW where the structural index-carry fingerprint lives — "test where the hedge actually fires." Expected: burst-agreement >> 50% level-sign agreement on index names (QQQ/SPY), lower on single names (JPM) — confirming both the models' coherence on the tradeable object AND the index-tilt carry hypothesis in one statistic. If it fails even there, THAT is a true refutation.

---

## Consensus across R1 (for R2 to attack)

1. The 50%/+0.16/0.0000 as computed is an ARTIFACT, not an adjudication — all three panelists agree it cannot license any model conclusion (agree/disagree/wrong-object).
2. All three independently converge on the SAME next test: the round-4b continuous ΔIV-signed vanna-shock event study (`signed_flow = -sign(ΔIV_i) × burst_i`, all nonzero days, aligned returns, SPY/QQQ one cluster, single-draw permutation null, BH q<0.10, index vs singles).
3. All three accept the accumulation-ON live model as corrected.
4. Disagreement: ORDER of fixes (framing vs measurement vs mechanics first) and the JPM 12% reading (sign-determinacy vs habitat mechanics).
