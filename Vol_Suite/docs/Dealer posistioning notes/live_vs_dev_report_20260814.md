# LIVE vs Dealer-Exposure-Dev — Tree Development Report

**Date:** 2026-08-14
**Prepared from:** actual git + file state (verified, not from memory)

---

## 1. The trees

| Tree | Path | Branch | HEAD | Role |
|---|---|---|---|---|
| LIVE | `C:\Users\bottl\FinancialDevelopment` | master | a2fe86a | the live model runs from here |
| DEV | `C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev` | Dealer-Exposure-Dev | 1bc6fe9 | this tree — study home |

**Critical structural fact: `Dealer-Exposure-Dev` has ZERO unique commits.**
`git log master..HEAD` on the worktree is EMPTY. The branch is identical to master minus
one commit (`a2fe86a fix(quant): write canonical suite_context.json`, which is on master
only). **Everything on this tree is uncommitted working-tree state** — nothing was ever
committed to the branch. This is the "mixed with master" situation: the branch doesn't
actually contain the study; the files just sit untracked in the working dir.

## 2. Live model — identical on BOTH trees (verified)

The live dealer model code is byte-identical in both trees:

| Setting | Value | Verified |
|---|---|---|
| Sign model | `vol_surface_replication` (Layer 1a+1b) | `VALID_SIGN_MODELS` trio; **not** oi_heuristic, **not** direction (never merged), **not** SABR |
| Sign-source fitter | `VOL_SURFACE_FITTER=svi` (single default) | `vol_surface_reference.py:140` |
| IV dead-band | `IV_DEADBAND_VOL = 0.01` | `vol_surface_reference.py:133` |
| Vannaflow | `DEALER_VANNA_FLOW` default `"1"` | `replication_reference.py:759` |
| Accumulation | **ON by default** (just fixed, both trees) | `run_dealer_positioning` passes `accumulate = DEALER_ACCUMULATION != "0"` |

The accumulation-ON fix is applied to `dealer_positioning.py` in **both** trees (identical
diff, uncommitted). This was the correction from the 08-14 session — the live default was
silently OFF; the intended model is accumulation ON.

## 3. The NEW exposure model — present on BOTH trees as untracked files

All 8 core files are **byte-identical** between the two trees (`diff` verified SAME):

```
expiry_book_exposure.py          run_expiry_fast_test.py
run_compare_live_vs_new.py       run_expiry_falsifier_full.py
run_expiry_tier1.py              run_expiry_tier2_multi.py
run_expiry_tier2c_signed.py      run_expiry_tier2d_continuous.py
tests/test_expiry_book_phase{0..6}_*.py   (7 test files, both trees)
docs/PLAN_expiry_book_exposure_20260814.md + _v2
_expiry_falsifier_cache/  (results, both trees)
```

**The one thing ONLY this tree has: the short-DTE seed data.**
- `_scratch_tier2/` and `_scratch_tier2b/` (windowed short-DTE pulls: `window_20260508`
  → `window_20260731` + `reuse_20260814`) exist **only in the worktree**, not in the live
  tree. The compare driver reads them via absolute path into the worktree.

## 4. Test state

| Tree | Exposure-model tests | Result |
|---|---|---|
| DEV (this tree) | 7 phase test files | **50 passed** (3.5s, just run) |
| LIVE | same 7 files | not re-run this session; files identical so expected same |

## 5. Comparison result — live model (accumulation ON) vs new exposure model

96 snapshots (12 tickers × 8 windows), both models on identical seed rows, no network:

| Metric | Value |
|---|---|
| Sign agreement (LONG/SHORT) | **48/96 = 50.0%** |
| Cross-sectional corr (mean exposure) | **+0.1595** |
| Forward-return predictiveness | new 0.0000 / live 0.0000 (tied) |

Per-ticker: TSLA/NVDA/AMZN 75%, NFLX 62%, AAPL/MSFT/SPY/GOOGL 50%, QQQ/META 38%,
AMD 25%, **JPM 12%**.

**This is the accurate result.** The earlier 76% (oi_heuristic) and 83%/+0.56 (snapshot,
accumulation off) numbers were computed against the WRONG live model and are superseded —
documented in `_expiry_falsifier_cache/compare_live_vs_new_result.md`.

## 6. What Jason must retest (tested the wrong model for 2 days)

Everything that used the dealer live model with **accumulation OFF** or **sign_model ≠
vol_surface_replication** since ~08-12 is suspect. At minimum:
- Any falsifier/comparison whose live leg ran same-day snapshot (accumulation off)
- Any test asserting SPY vannaflow ≈ +254K (that number assumes the accumulated book)
- The 83%/+0.56 live-vs-new comparison (snapshot) → correct number is 50%/+0.16

## 7. Open items / decisions

1. **Nothing is committed to Dealer-Exposure-Dev** — the branch has zero unique commits.
   Recommend committing the baseline (accumulation fix + exposure model + corrected results)
   so retests start from a known state.
2. **Scratch data is only in the worktree** — if the study should live on this tree, that's
   correct; if it must survive on the live tree too, copy `_scratch_tier2*` over.
3. **`direction` / SABR are NOT part of this model** — confirmed by source, not memory.
   Skill text describing the old WSL canonical (`direction`, `sabr_deviation`) is stale for
   this tree and must not be recited as live behavior.

## Files
- This report: `docs/Dealer posistioning notes/live_vs_dev_report_20260814.md` (this tree)
- Accurate comparison: `_expiry_falsifier_cache/compare_live_vs_new_result.md` (this tree)
