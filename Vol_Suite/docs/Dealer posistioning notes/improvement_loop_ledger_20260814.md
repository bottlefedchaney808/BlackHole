# New-Model Improvement Loop — Ledger (until Cem approves)

**Started:** 2026-08-14  **Tree:** Dealer-Exposure-Dev
**Acceptance gate:** Cem Karsan arbiter verdict = APPROVED (new expiry-book exposure model acceptable).
**Protocol per round:** R1 (3 adversarial panelists, all insights accepted) → build merged upgrade set on Dealer-Exposure-Dev → test → findings report update → Cem arbiter verdict.

---

## Round 1 — COMPLETE
- **R1:** all 3 panelists converged — wire live ΔIV-signed vannaflow into the from-breach buckets (paired two-model agreement).
- **R2 (conf 0.90):** channel-conflation CONFIRMED (Tier-3B ran a gamma burst against a vanna prediction); +0.84 CI real [0.53,0.96]; B1 LIVE CI-excluding-0 hidden in BOUNDED; y≡0 class still live in tier1/tier2c; 3 QQQ day-clusters not 4.
- **Cem arbiter: NOT ACCEPTED.** 7-item build set (zero-target audit GATE, wire live vannaflow, paired table, shadow-leak sub-sample, effective-n md, surface B1 LIVE, cluster-count record). Un-named risk: shared-convention false independence (both models share rec.vanna=−1×BS — agreement can be manufactured; needs an independent-convention flow estimate). Stop condition: EXPECT-POSITIVE beyond effective-n md on from-breach, leak-separated, convention-guarded, index sample, zero-target audit passed.

## Round 2 — COMPLETE (build + run)
- **Build (all 7 items):** R2-1 zero-target audit ✅ (tier1:277 + tier2c:92 placeholders removed, chain sweep clean); R2-2 live vannaflow wired into all 28 firing buckets ✅; R2-3 paired table ✅; R2-4 shadow-leak sub-sample ✅; R2-5 effective-n md ✅; R2-6 B1 LIVE surfaced ✅; R2-7 cluster count ✅. 50 tests green, compile OK.
- **Round-2 results (28 firing buckets, all QQQ, 3 day-clusters) — HONEST LABELS (R2 source-verified):**
  - (a) NEW gamma-burst: −0.0884 CI [−0.143,−0.040] (unchanged — the conflated leg)
  - **(b) NEW-model ΔIV-signed vannaflow (ebe.vanna_flow, −1×BS — NOT production live): +0.2327 CI [+0.205,+0.305]** — POSITIVE sign, CI excludes 0, but single-engine, linear-in-ΔIV, QQQ-only, underpowered (md 0.993 eff-n=3) → **FRAGILE SIGN-HINT, NOT a mechanism confirmation**
  - (c) WITHIN-ENGINE sign-agreement: **67.9%** (19/28) — dIV cancels ⇒ sign(burst)==−sign(net_vanna); NOT two-model coherence
  - (d) shadow-leak split (post-hoc EXPLORATORY): **+0.4343 (n=14)** — **below its own md 0.6883 → code verdict BOUNDED/negative** (reporting-integrity corrected; earlier prose 'strongly POSITIVE / hypothesis supported' was a contradiction, now fixed)
- **Read (corrected):** costume-change CONFIRMED (gamma-on-vanna-clock artifact) but on a SINGLE engine; vanna channel POSITIVE on the clean clock but convention×reflexivity consistent, not independently mechanistic; leak split exploratory & below md; models do NOT show independent coherence. Cem's bar NOT met.
- **R2 cross-examiner (deleg_036331dc, source-verified):** channel (b) labeled "LIVE" is a re-derivation from `expiry_book_exposure.vanna_flow`, never calling production `dealer_positioning` (run_intraday_flow imports only the new engine); the true dual-pipeline test is REQUIRED NEW EVIDENCE (seeds EOD, expiry-mismatched for 2/3 firing anchors, no 20260717 seed, no intraday accumulation history). 12/14 F/D/M claims VERIFIED; F3/D6 accepted insights.
- **R3 Cem arbiter:** NOT YET DISPATCHED for round-2 — result is below approval bar. Round-3 cheap fixes implemented (honest labels, per-day sign-consistency b2, exposure terciles b3, opposite-convention rerun b4, K=3 bootstrap disclosure R2-8, (d) integrity fix). Required new evidence identified: production-live dual-pipeline on firing buckets + index (SPY) coverage + broader QQQ day-clusters.

## Round 3 — COMPLETE (cheap-fix build + run, committed 04691e1)
- **R2 verdict accepted (source-verified):** round-2 NOT approval-ready. Built all (A) cheap fixes in `run_intraday_flow.py` + corrected result docs + 7 new locking tests (57 green).
- **Round-3 run (28 buckets, all QQQ, 3 day-clusters, SPY 0):**
  - (b) NEW-model vannaflow (ebe.vanna_flow, −1×BS): **+0.2327** CI [+0.205,+0.305] — CI excludes 0 but |r|<<md 0.993 ⇒ fragile sign-hint
  - **(b2) PER-DAY sign-consistency: QQQ 0716 +0.3051, 0717 +0.2939, 0731 +0.4434 — ALL 3 QQQ days POSITIVE** (the genuine directional whisper)
  - (b3) exposure-response terciles: +0.4867 / +0.5917 / +0.4053 — **NON-MONOTONIC** (no mechanism signature)
  - **(b4) opposite-convention rerun (+1×BS): EXACT sign flip to −0.2327 ⇒ CONVENTION-BOUND**, not mechanism-proven
  - (c) within-engine 67.9% (dIV cancels); (d) leak split +0.4346 < md 0.6883 ⇒ BOUNDED/negative (integrity fixed)
- **Honest read:** b2 gives one genuine directional whisper (all 3 QQQ days positive on 3 independent units), BUT b4 proves the headline is convention-bound (locked −1×BS × reflexivity) and b3 is non-monotonic. **Cem's bar NOT met** — still single-engine, convention-bound, QQQ-only, eff-n=3 (md 0.993 = zero power).
- **R3 Cem arbiter dispatched** (deleg_5ca3bd76): adjudicating APPROVED / NOT ACCEPTED on the honest round-3 packet, with mandate to produce the round-4 merged upgrade set (A cheap / B new evidence) without relaxing his stop condition.

## Round 3 — CEM VERDICT: NOT ACCEPTED (deleg_5ca3bd76)
- **Ruling on b4 (convention flip): DISPOSITIVE against approval.** Exact mirror flip to ±0.2327 ⇒ magnitude lives in shared input (ΔIV × reflexivity), sign lives in the locked prior. A quantity whose sign flips with the frame carries zero independent mechanistic information. NOT mechanism evidence.
- **Ruling on b2:** genuine directional whisper (3/3 per-day positive stable across 3 units) but computed from the SAME convention-locked quantity on the SAME engine on the SAME days — survives day-stability, NOT convention-independence.
- **Ruling on b3:** non-monotonic (+0.487/+0.592/+0.405) kills the exposure-weighted mechanism claim — the mechanism's signature is absent where it would have to show.
- **Un-named risk (Cem's):** the 3 "independent" day-clusters are NOT independent — same family, same short-DTE same-locus construction, same engine, same convention, same acquisition window. "3/3 positive" = one family saying the same thing with three mouths. The only real independence test = production pipeline agreeing on those buckets.
- **ROUND-4 MERGED UPGRADE SET:**
  - **(A) Cheap fixes:** A1 convention-dependence sweep (+1×BS..−1×BS, show whole corr-vs-convention curve); A2 decouple exposure-weighting from sign prior (corr on |vf| vs |fwd| magnitude separately); A3 drop/header-demote the K=3 discrete-quantile CI (no CI worth reporting at eff-n=3); A4 per-day = THE headline statistic + sign-stability vs binomial null; A5 pre-register round-5 estimand in driver docstring before new data runs; A6 reflexivity baseline corr(ΔIV, fwd-return) on the same buckets (if positive ⇒ +0.23 is ΔIV/reflexivity, vanna sign downstream).
  - **(B) Required new evidence (priority):** **P0-1 dual-pipeline convention-independence gate** — production `dealer_positioning` vanna (150d accumulated, SVI sign map, vanna_call+put_shares) on the same intraday firing buckets vs new engine; requires NEW intraday chain acquisition via ThetaData at IVL=600000 over a 150d accumulation window (existing seeds EOD/expiry-mismatched/missing 20260717). **P0-2 index (SPY) firing coverage** (FOMC/earnings or justified 0.5% band). **P0-3 independent day-clusters to lift eff-n past md.** P1-4 zero-target audit gate; P1-5 component ablation; P1-6 regime robustness.
- **Final:** new model ships descriptive/conditional only. Loop continues until APPROVED.

## Round 2 — PANEL REVIEW IN FLIGHT (R1 re-dispatched, deleg_3dd627ee)
- Round-2 build was committed (`edbb61a`) but NEVER adjudicated — no round-2 Cem verdict existed.
- Re-dispatched R1 (3 adversarial panelists: framing/mechanism, data/measurement, dealer-mechanics) against the committed round-2 artifacts, each with a synergy-framed INSIGHT TO IMPROVE. All insights accepted, merge non-overlapping.
- Awaiting: R1 -> R2 cross-examiner -> R3 Cem arbiter (APPROVED / NOT ACCEPTED). Loop continues automatically until Cem says APPROVED.

## Round 2 — R1 PANEL COMPLETE (unanimous: NOT approval-ready)
- **Framing/mechanism (F):** `+0.2327` real but `vanna_flow` is `signed_vanna·ΔIV` — sign can arise from locked `−1×BS` × ΔIV/return reflexivity, not independently from dealer exposure. No exposure-contrast (all 28 buckets short-DTE high-vol QQQ, 3 day-clusters). **Insight:** exposure-response monotonicity test (terciles by |net vanna|, mechanism⇒monotonize, reflexivity⇒flat) + opposite-convention rerun as sensitivity falsifier.
- **Data/measurement (D):** K=3 cluster bootstrap → only **10 distinct resamples** → the "CI excludes 0" is a discrete-quantile artifact, not valid at eff-n=3 (md 0.993=zero power). (d) conditions on the same ΔIV that defines the x-var (selection bias); its prose "strongly POSITIVE" CONTRADICTS its own code verdict `BOUNDED/negative` (0.4343 < md 0.6883). (c) dIV cancels → reduces to sign(burst)==−sign(C), no null/CI. QQQ-only, not index. **Insight:** per-day (3-cluster) sign-consistency as the PRIMARY statistic — all 3 positive ⇒ real, mixed ⇒ mean artifact. No new data needed.
- **Dealer-mechanics (M):** channels (a)–(d) are ONE engine (`expiry_book_exposure.py`) in ONE driver — channel (b) labeled "LIVE" is NOT production `dealer_positioning` vanna output, it's a new-module re-derivation under shared `−1×BS`. (c) is within-engine coherence. Sign formula-clean but economic direction (net-short vanna buys on vol-down) never recorded. No lag mismatch. Scaling ×100×0.01 consistent (imported from dealer_positioning); real divergence is input provenance (single-expiry raw-IV vs 150d accumulated+SVI sign map). **Insight:** TRUE dual-pipeline (b_live from production live model vs b_new) on the same 28 buckets = the convention-independence gate Cem demands.
- **R2 dispatched** (deleg_036331dc): verify every F/D/M claim in source; answer (1) does run_intraday_flow.py ever call the PRODUCTION live vanna pipeline, (2) can the dual-pipeline test run on existing seed (_scratch_tier2); produce the precise merged upgrade set (cheap code/reporting fixes vs required new evidence).

---

