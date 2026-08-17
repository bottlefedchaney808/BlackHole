# HANDOFF — Dealer-Exposure-Dev improvement loop (continue in a fresh session)

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

## LOCKED LIVE MODEL (do not relitigate)

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
