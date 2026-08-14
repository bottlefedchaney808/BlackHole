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
- **Round-2 results (28 firing buckets, all QQQ, 3 day-clusters):**
  - (a) NEW gamma-burst: −0.0884 CI [−0.143,−0.040] (unchanged — the conflated leg)
  - **(b) LIVE ΔIV-signed vannaflow: +0.2327 CI [+0.205,+0.305] — POSITIVE, the EXPECT-POSITIVE prediction, CI excludes 0** (underpowered at md 0.993 eff-n=3 but RIGHT SIGN)
  - (c) paired two-model sign-agreement: **67.9%** (19/28)
  - **(d) shadow-leak sub-sample: +0.4343 (n=14) — strongly POSITIVE — the −0.088 was daily-shadow leak**
- **Read:** costume-change CONFIRMED (gamma-on-vanna-clock artifact); vanna channel POSITIVE on the clean clock; leak-separated POSITIVE; models cohere where the hedge fires. Honest limit: md 0.993 at eff-n=3 not exceeded; SPY still 0 buckets; not yet an index result.
- **R3 dispatched:** re-submitting to Cem (deleg_…)

---

