# HANDOFF — Dealer-Exposure-Dev improvement loop (continue in a fresh session)

## SUPERSEDED 2026-08-17 — expiry_book_exposure is now the LIVE model

Everything below this notice describes the state as of 2026-08-14, while the
new model was still an unapproved, descriptive-only study running alongside
the untouched legacy `dealer_positioning.py` live model, gated on an explicit
Cem-arbiter APPROVED verdict before promotion.

**That plan changed on 2026-08-17.** Jason made the call to promote
`expiry_book_exposure.py` (via `expiry_book_production.py`) to the live path
across `volatility_suite.py`, `options_chain_scanner.py`, and
`sentiment-scanner/scanner/gex_scanner.py`, and to lock
`dealer_positioning.py`'s legacy `compute_dealer_positioning`/
`compute_accumulated_position` to backtest/test callers only
(`_assert_legacy_backtest_access`). Reason, in his words: the old
accumulation model was broken, and continuing to sink cost into fixing it
again and again wasn't worth it when the new model looked more promising.
This was **not** a claim that the Round-2 statistical validation below
crossed Cem's acceptance bar — it was a pragmatic/cost call made in spite of
the last recorded verdict being **NOT ACCEPTED** (see below). Do not read the
"LOCKED LIVE MODEL" section below as still describing the live path — it
describes the RETIRED model. `expiry_book_exposure.py`'s own module
docstring carries the current, load-bearing description of what's live now.

A 2026-08-17 CARL adversarial review of the swap found and the same session
fixed, all confirmed as intentional fixes by Jason (not by Cem/the CARL loop
below):
- a guaranteed crash in `gex_scanner.py` on every successful scan, and a
  latent `total_net_gamma`/`total_net_dollar_gamma` field-duplication bug
  (commit `1020eac`)
- a cross-greek vanna sign-composition bug: `dealer_frame_vanna` applied a
  double negation, composing vanna as `+1*customer-raw` while delta composed
  as `-1*customer-raw` (commit `bd30cd8`)
- while wiring dividend yield `q` through the engine (previously always
  implicit 0.0, F5 from the same review): `bs_charm` had no `right` param
  (always computed call charm) and was missing a whole term, exact only at
  q=0 by coincidence; fixing that surfaced the SAME double-negation bug in
  charm that vanna had, undetected until charm's q=0 call==put coincidence
  broke while adding the `right` param (commits `6f589e4`, `3a7d5fa`) — so as
  of this fix, delta/vanna/charm all correctly compose as `-1*customer-raw`.

The statistical-validation content below (Round-2 results, effective-n,
Cem's required upgrade set) was run against the PRE-fix vanna/charm sign
composition and was NOT re-litigated by the 2026-08-17 review — it remains
exactly as accurate/inaccurate as it was on 2026-08-14. If vanna or charm
sign entered those Round-2 numbers anywhere, they may need re-running against
the fixed composition; nothing about the promotion decision retroactively
validates or invalidates those
numbers.

## Lesson learned: don't resubmit "fail loudly" for a per-day backtest classification gap

`7e4b739` ("dealer_exposure_model fails loudly instead of silent None fallback", 2026-08-16) changed
`backtest_stage3.py::_build_day_records` so a day with no classifiable dealer chain rows raised
`ValueError` instead of leaving that day's regime unclassified. It was reverted the same day (`6e3cfa4`),
no rationale recorded in either commit message. Reading the diff: this was a fail-loud check inside a
**multi-day backtest loop**, not the single-day live render — raising on any one bad/thin data day would
abort an entire 90+-day backtest run rather than skip that one day, which is the likely reason it didn't
stick (inferred from the diff's context, not a stated rationale — if you know the real reason, replace
this paragraph with it). If "fail loudly instead of silent fallback" comes up again for this code path,
distinguish the live-render case (fail loud is right — CLAUDE.md's whole "no silent fallbacks" theme)
from the backtest-loop case (a single bad day should probably still skip-and-continue, not abort the
whole run) before resubmitting the same change.

---

**Copy this as the first message in the new session.** Begin by anchoring to the worktree before any other tool use.

---

```
Resume the Dealer-Exposure-Dev improvement loop. You MUST begin by anchoring to this exact worktree:

cd C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev
pwd
git branch --show-current
git status --short
git log --oneline -3

Do not work in C:/Users/bottl/FinancialDevelopment (master). The study belongs only on branch Dealer-Exposure-Dev.
```

## CURRENT STATE

- **Worktree:** `C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev`
- **Branch:** `Dealer-Exposure-Dev`
- **Current study commit:** `edbb61a` — `feat(vol): expiry exposure intraday validation loop (round-2)`
- **Master HEAD:** `a2fe86a` — the study commit is **unique to Dealer-Exposure-Dev**, not on master.
- **Do not** reset, cherry-pick, merge, or delete the Dealer-Exposure-Dev commit.
- **Master was explicitly cleaned** of leaked study files. Do not recreate or modify study files there.
- Master still has the intended `dealer_positioning.py` accumulation-default fix plus an **unrelated** `sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json` modification. Do not touch the unrelated manifest.

## USER'S GOAL

Keep improving the **NEW expiry-book exposure model** in a CARL loop until **Cem Karsan says APPROVED/acceptable**. The new model remains descriptive/conditional until approval. The live model is the benchmark and must not be redesigned.

## RETIRED LIVE MODEL (as of 2026-08-14; superseded 2026-08-17 — see notice above)

- `sign_model = vol_surface_replication`
- `VOL_SURFACE_FITTER = svi`
- `IV_DEADBAND_VOL = 0.01`
- `DEALER_VANNA_FLOW = 1`
- **accumulation ON by default** in both trees
- `direction`, SABR-as-live, and `oi_heuristic` are NOT the live model
- `rec.vanna = -1xBS`
- real spot, not median strike
- live model files must remain untouched except the already-approved accumulation-default fix

## CARL LOOP STATUS

**Round 1:**
- R1: framing, data-measurement, dealer-mechanics panelists
- R2 cross-examiner
- **Cem verdict: NOT ACCEPTED**

**Cem's required round-2 upgrade set:**
1. Zero-target audit: remove terminal fake-`0.0` forward-return placeholders
2. Wire live ΔIV-signed vannaflow into the same from-breach buckets
3. Paired two-model × forward-return output + per-bucket sign agreement
4. Daily-shadow-leak subsample
5. min-detectable bars at effective cluster-n, not pooled n
6. Surface LIVE B1 CI-excluding-0 directional asymmetry (don't hide in BOUNDED)
7. Correct firing-cluster count

## ROUND-2 BUILD STATUS — ALL SEVEN COMMITTED IN `edbb61a`

- zero-target fixes in `run_expiry_tier1.py` + `run_expiry_tier2c_signed.py`
- round-2 intraday flow wiring in `run_intraday_flow.py`
- effective-n changes in `run_compare_live_vs_new.py`
- result artifacts + CARL ledgers committed
- 50 phase tests pass, Python compiles

## CRITICAL VALIDITY CHECK (already closed)

The live-equivalent vanna call is valid, not a silent-zero artifact:
- `expiry_book_exposure.py:280` accepts `ticker`
- `vanna_flow` exists at `expiry_book_exposure.py:374`
- rerun proved **nonzero-vf buckets = 28/28**

## ROUND-2 RESULTS (28 firing buckets, all QQQ, 3 day-clusters; SPY fired zero)

| Channel | corr | 90% CI | note |
|---|---|---|---|
| NEW gamma-burst signed flow | −0.0884 | [−0.1431,−0.0399] | old channel-conflated leg — says nothing about vanna |
| **LIVE ΔIV-signed vannaflow** | **+0.2327** | **[+0.2052,+0.3051]** | EXPECT-POSITIVE sign, nonzero-vf 28/28 |
| Paired sign agreement | 19/28 = **67.9%** | — | coherence, NOT predictiveness |
| Shadow-leak subsample | n=14, **+0.4343** | — | daily-shadow leak hypothesis supported |

Effective-n = 3 clusters → md = 0.993, so +0.2327 is directionally correct but **still underpowered** by Cem's strict bar. **This is progress, not approval.**

## KEY ARTIFACTS

- Full findings report: `Vol_Suite/docs/Dealer posistioning notes/carl_round5_findings_report_20260814.md`
- Improvement-loop ledger: `Vol_Suite/docs/Dealer posistioning notes/improvement_loop_ledger_20260814.md`
- Round-2 result: `Vol_Suite/_intraday_cache/Tier3B_round2_RESULT.md`
- Raw flow result: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- Driver: `Vol_Suite/run_intraday_flow.py`
- Comparison driver: `Vol_Suite/run_compare_live_vs_new.py`
- Core model: `Vol_Suite/expiry_book_exposure.py`
- Cem arbiter brief: `C:/Users/bottl/FinancialDevelopment/trading_journal/karsan_expert_agent_brief_20260814.md`
- CARL panel summaries: `C:/Users/bottl/AppData/Local/hermes/cache/delegation/subagent-summary-*-20260814_*.txt` (R1 framing/data-measurement/dealer-mechanics, R2 cross-examiner, R3 Karsan arbiter)

## NEXT ACTION — do NOT assume Cem approved

Re-submit round-2 results through the CARL loop:
1. **R1:** dispatch three adversarial panelists (framing / data-measurement / dealer-mechanics), each reading the committed round-2 artifacts and producing a **synergy-framed INSIGHT TO IMPROVE** (mandate: all accepted, merge non-overlapping).
2. **R2:** cross-examiner verifies all claims — especially effective-n, independence, sign convention, and whether +0.2327 is genuinely model-derived (not a silent-zero artifact).
3. **R3:** dispatch Cem as acceptance arbiter (elitist persona; the `cem-karsan-consult` skill has the ROLE PROMPT + verified framework).
4. If **NOT ACCEPTED**, build only Cem's merged upgrade set on Dealer-Exposure-Dev, test, update the ledger, repeat.
5. **Stop only when Cem explicitly says APPROVED/acceptable.**

Do **not** claim approval merely because the sign is positive. Cem's stop condition: positive flow direction beyond honest effective-n power, leak separation, convention guardrails, an index sample (SPY firing days via FOMC/earnings or 0.5% tolerance), and a passed zero-target audit.
