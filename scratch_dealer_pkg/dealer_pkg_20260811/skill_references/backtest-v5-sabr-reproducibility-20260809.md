# Backtest v5 SABR Reproducibility Gap — 2026-08-09

## What happened

Jason compared a UUUU chart from his Windows tree (stale, pre-lock, "Vol-Surface + Replication (v2.1)") against the current Linux canonical run (V5 Direction, sabr_deviation, 150d accumulation). The hedge gap was 100x (1,677 vs 171,715 sh/1%). He said "these shouldn't be so different" — and "we backtest like 50 companies, they weren't that different" — then asked to re-run the backtest on QQQ + SPY the same way as before ("run a 30 so it matches that test to see if it matches then run a 90d vs v5").

Investigation exposed a reproducibility gap in the DECISION EVIDENCE itself, not just the chart comparison: the committed backtest never tested the model the decision evidence claimed to validate, AND the live model had silently diverged from the backtested model (gate removed).

## Timeline (all times CT)

| Event | Time |
|---|---|
| Decision runs `pooled_20260809_010805→011046` (uniform/gamma_weighted/whale_decomposed/sabr_deviation) | 2026-08-08 19:58–20:10 |
| `_compute_sabr_deviation_for_expiry` first committed (`9366d1a`, "default per-expiry direction to SABR deviation") | 2026-08-09 02:16 |
| `compute_expiry_sign_map` first committed (`eb8b5b6`, lock) | 2026-08-09 16:10 |
| 5f9833c (SABR deviation is sign source; accumulated book feeds all greeks) — **removed the gate** | 2026-08-09 16:49 |
| Live-wired backtest reproduction flips v5 sign; Jason orders gate restored ("wire in the fucking gate") | 2026-08-09 evening |
| Gate restored + gated verification runs | 2026-08-09 night |

**Key fact:** the decision runs at 19:58–20:10 on 08-08 predate the FIRST commit containing `_compute_sabr_deviation_for_expiry` (02:16 on 08-09). The committed `backtest_stage3.py` at decision-time HEAD (`f558ecd`) had a v5 leg using only run-level bias:

```python
# f558ecd and HEAD, BEFORE fix:
net_v5 = _net_gamma_v5_direction(gamma_map, oi_map, chain_iv, spot, T,
                                 bias_by_date.get(d, 0.0))
```

`git diff f558ecd..HEAD -- Vol_Suite/backtest_stage3.py` is EMPTY — the committed backtest NEVER threaded per-expiry SABR deviation. The four mode runs therefore used an uncommitted working tree whose backtest threading was lost. The recorded decision evidence (`docs/superpowers/specs/per-expiry-direction-backtest-20260808.md` table) is NOT reproducible from committed code.

## The recorded decision evidence (from pooled_20260809_* summaries)

SPY+AAPL, BT_LOOKBACK_DAYS=30, 5d fwd, 2000 perms, seed 42, direction + accumulation ON:

| Mode | v5 coef | t | perm p full | block p |
|---|---:|---:|---:|---:|
| uniform | −0.0003 | −1.659 | 0.0335 | 0.1114 |
| gamma_weighted | −0.0003 | −1.606 | 0.0350 | 0.1064 |
| whale_decomposed | −0.0003 | −1.733 | 0.0260 | 0.0890 |
| **sabr_deviation** | −0.0003 | **−1.919** | **0.0135** | **0.0680** |

Pooled dir mapping: 010805=uniform, 010903=gamma_weighted, 010955=whale_decomposed, 011046=sabr_deviation. (005804 = failed 5-ticker attempt, 005828 = SPY-only degenerate.) The evidence report `2026-08-09-sabr-deviation-default-evidence.md` and plan `2026-08-09-sabr-deviation-default.md` contain the same table.

## The GATE — the decision-time helper vs the live helper

The decision-time code (committed as `9366d1a`, documenting the uncommitted tree's behavior) GATED the SABR deviation on a non-zero whale/direction read:

```python
# 9366d1a (decision-time):
if not fallback_bias:
    return 0.0          # no whale signal -> no trade
if vol_surface_ref is None:
    return fallback_bias
...
```

The 5f9833c "fix" REMOVED that gate ("the SABR deviation fires on its own even when the run-level direction/whale read is neutral"). That ungated variant is what ran live — and it is NOT the model the backtest validated. Jason's order after seeing the sign flip: **"wire in the fucking gate"**. Restored 2026-08-09 night (docstring rewritten to record the validated-vs-ungated evidence; two tests flipped in test_dealer_positioning_direction.py).

## The fix (applied; two changes)

1. **Backtest wiring** — `Vol_Suite/backtest_stage3.py` `_build_day_records`, v5 leg now calls the live per-expiry resolver:
```python
vsr = vol_surface_reference.compute_vol_surface_reference(
    ticker, chain_iv, spot, forward=forward, T=T)
per_expiry_bias = dealer_positioning._compute_sabr_deviation_for_expiry(
    vsr, bias_by_date.get(d, 0.0))
net_v5 = _net_gamma_v5_direction(gamma_map, oi_map, chain_iv, spot, T,
                                 per_expiry_bias)
```
2. **Gate restoration** — `_compute_sabr_deviation_for_expiry` returns 0.0 when fallback_bias is 0.

Verification (all fresh, scrubbed env): `py_compile` OK; focused 5-file battery (backtest_stage3 + pooled_panel + direction + sign_model + hedge_requirement_units) **75 passed**; full `Vol_Suite/tests` **378 passed, 5 skipped**.

## Reproduction runs — the numbers

| Run | v5 coef | t | perm p | SPY short | AAPL short |
|---|---:|---:|---:|---:|---:|
| Recorded decision (sabr_deviation, gated) | **−0.0003** | −1.919 | 0.0135 | 39/46 | 46/46 |
| Ungated live-wired, QQQ+SPY 30d | +0.0015 | +3.419 | 0.0005 | 0/46 | — |
| Ungated live-wired, SPY+AAPL 30d | +0.0010 | +4.503 | 0.0005 | 0/46 | 4/46 |
| **Gated (restored), SPY+AAPL 30d** | +0.0009 | +3.472 | 0.0005 | 24/46 | 19/46 |

Hypothesis: coef<0 (dealer-short → higher fwd vol). Both live-wired variants produced the opposite sign. The gate moved SPY short 0→24 (toward recorded 39) but did NOT reproduce the recorded row.

**Still UNRESOLVED at session end.** The exact recorded numbers (coef −0.0003, SPY 39/46, AAPL 46/46) were NOT reproduced by the gated model with default knobs. Remaining candidate knobs (env-only, no code change):
- `DEALER_DIRECTION_MIN_SCORE` — gated run had 22/46 non-zero at min_score=3; recorded may have used a different min_score
- `WHALE_THRESHOLD_BPS` — 08-06 sweep recommended 3000; decision-time tree may have used it

Next step proposed (not yet run): SPY+AAPL 30d sweep × min_score∈{1,3} × bps∈{0,3000} to find the config reproducing the recorded row. Do NOT claim the model is validated OR invalidated until that lands.

## Confounders in the live-wired runs

1. **QQQ data degenerated**: `option_bulk_hist_oi_by_day` returned 39/48 "no session" → 9 usable days (vs SPY's 46). ThetaData OI-by-day route was flaky that evening. Prefer SPY+AAPL for decision reproduction; verify per-ticker usable-day counts before trusting a pooled row.
2. **Accumulation gap**: the recorded test had accumulation ON (150d book, "wired into live path"). `backtest_stage3._build_day_records` uses per-day LEVEL OI (`oi_by_date[d]`) — the wiring did not add accumulation. So even the fixed v5 leg is NOT yet the full live model in the backtest. (The live path accumulates via `replication_reference.get_accumulated_position` / `_accumulate_from_history`; the backtest has no accumulation seam for v5.)

## Jason's exact words (drive the operational rule)

- "so this model could suck and we never knew? if we were just picking some weighted version of v5 LARP as sabr_deviation we have no idea how good this is"
- "run a 30 so it matches that test to see if it matches then run a 90d vs v5"
- "fuick that 90d run / its worthless this shit is broke / wire in the fucking gate"

## Operational rule

Before citing ANY stage-3/pooled v5 number as evidence for the live canonical model:
1. `grep -n "_compute_sabr_deviation_for_expiry" Vol_Suite/backtest_stage3.py` — if absent, the backtest tests the OLD run-level-bias v5, not the live model.
2. Confirm the v5 leg matches `compute_expiry_sign_map` (SABR primary, run-level bias as fallback).
3. Check the GATE is present in `_compute_sabr_deviation_for_expiry` (`if not fallback_bias: return 0.0`) — the ungated variant is NOT the validated model and flips the sign.
4. Check usable-day counts per ticker before believing a pooled row (QQQ can drop to 9 days on OI-by-day flakiness).
5. Accumulation: the backtest v5 leg uses level OI; the live model accumulates. Until a backtest accumulation seam exists, backtest v5 ≠ live model even with the SABR wiring.
