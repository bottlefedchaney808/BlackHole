# Stale-model chart comparison — UUUU 2026-08-09

User pasted two side-by-side UUUU dealer charts and asked "these shouldnt be so
different....." (a `/using-superpowers` skill-check invocation). Investigation
showed the two panels were NOT comparable: left = canonical direction run,
right = the DELETED `vol_surface_replication` model.

## The two panels

| Panel | Title | Filename watermark | Hedge req | Flip | Gamma call |
|---|---|---|---|---|---|
| Left | `UUUU Dealer Greek Exposure Comparison (150 days) — V5 Direction (sabr_deviation, 150d accumulation)` | `UUUU_greek_exposure_comparison_20260809_180853.png` visible | 1,677 sh/1% | $14.68 | NO-CALL (evidence floor $1,255,091) |
| Right | `UUUU Dealer Greek Exposure Comparison (150 days) — Vol-Surface + Replication (v2.1)` | NONE | 171,715 sh/1% | $14.27 | DAMPENING |

Second image (hedging heatmap) same split: left `UUUU_hedging_heatmap_20260809_180828.png`
with watermark, right v2.1 with no watermark.

## Provenance evidence

- Current tree CANNOT render a "Vol-Surface + Replication (v2.1)" chart:
  `_resolve_sign` raises `ValueError` for anything ≠ `CANONICAL_SIGN_MODEL`
  (`dealer_positioning.py:349-352`); `VALID_SIGN_MODELS == ('direction',)`.
- Grep `"Vol-Surface + Replication (v2.1)"` across active `.py` → zero hits
  (only backtests/tests/docs mention deleted models).
- Run `9aa4db13964d46bd8106ac66d399af45` (18:08, after the 16:10 lock commit
  `eb8b5b6`): bridge log line 613 `[Running] Dealer Positioning (sign_model=direction)`,
  line 643 `Net dealer gamma: -1.19e+02, Hedge req: 1,677 shares`.
- **The right panel came from the STALE WINDOWS TREE**, not a repo run dir:
  user confirmed "its from my other suite in windows using the old model".
  Verified at `/mnt/c/Users/bottl/FinancialDevelopment/Vol_Suite/dealer_positioning.py`:
  HEAD `6f72f6a` (ancient, pre-lock), dataclass default `sign_model='oi_heuristic'`,
  `VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')`,
  no `direction`, no `DEALER_DIRECTION_PER_EXPIRY`, no NO_CALL gate.
- Lock timeline: `eb8b5b6` 16:10 (direction everywhere), `5f9833c` 16:49
  (SABR deviation sign source + accumulation feeds all greeks), run at 18:08.
- **User does NOT want the two trees synced**: "not make them agree I just
  remember it different during development". The Windows copy is a development
  memory, not a live-model contradiction. Do NOT propose syncing/patching
  `/mnt/c/...` unless he asks (canonical-path rule: `/home/bottl/Financial_Development`
  is the only editable copy).

## Why the 100x hedge gap (mechanical reconciliation, NOT a bug)

`hedge_requirement = abs(net_dollar_gamma * 0.01)` (`dealer_positioning.py:945`).

- **Direction model (left):** run-level bias +1 bullish (score 3/3, conviction
  MEDIUM) → calls get −1, puts get +1 (`_resolve_sign`: `-bias * direction`),
  further decomposed per-expiry via SABR deviation. Signs cancel across the
  chain → net dollar gamma ≈ $167.7k → below 5% gross floor ($1,255,091) →
  NO_CALL → small hedge.
- **Vol-Surface + Replication v2.1 (right, deleted):** per-strike IV
  richness/cheapness signs (rich→short, cheap→long, in-deadband→0→fallback −1)
  mostly aligned → net dollar gamma ≈ $17.2M → DAMPENING → 100x hedge.

Lesson: sign philosophy differences produce magnitude differences of 100x; the
divergence is exactly why the backtest picked direction and the user locked ONE
model. Never "explain" such a gap as a units/scaling bug without first
reconciling the sign source.

## Backtest metrics vs. live magnitudes ("they werent that different")

Jason's pushback: the 50-company pooled backtest showed v2 and v5 were NOT that
different — so why is UUUU 100x apart? Reconciliation: the backtest compares
SIGN-level metrics; the live chart shows MAGNITUDES.

- Pooled panel per-ticker short-gamma day counts (v2 vs v5) show real
  disagreement: AAPL 58 vs 93, AMD 47 vs 43, NVDA 47 vs 39, SPY 63 vs 50,
  TSLA 12 vs 45 (of ~113 days). The "not that different" read is the pooled
  regression (all models null at daily horizon: v2 perm_p 0.54, v5 perm_p 0.08-
  0.21, block perm p not < 0.05) — i.e. none predicts forward vol, which is a
  similarity in NON-predictiveness, not in per-day agreement.
- Metrics compare classification/signs; a single live snapshot compares
  net-dollar-gamma magnitudes, which sign alignment vs. cancellation swings by
  orders of magnitude. Reconcile metrics vs. magnitudes before declaring a bug.

## Reproducing the model-comparison backtest (the "same way we did before")

```bash
# Staged pairs, proxy-friendly, 90d lookback, V5 column included:
bash backtest_v5_batch.sh        # wave1 SPY+QQQ, wave2 AAPL+NVDA, wave3 TSLA+AMD
# or single ticker:
cd Vol_Suite && export THETADATA_HIST_CONCURRENCY=6 && \
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \
  backtest_stage3.py SPY 90 > outputs/backtest_v5_SPY_90d.log 2>&1
```

- Stage-3 report (`format_backtest_report`) prints ALL FIVE models side by side:
  v1 (oi_heuristic), v2 (vol_surface_replication), v3 (oi-change flow),
  v4 (live oi_flow), v5 (direction-5sig) — long/short-gamma days, mean vol,
  t-stat, p-value, reg coef / t / ols p / perm p.
- The old saved baseline `outputs/t98d0f1a2_test/spy_backtest_90d.log` predates
  the v5 column (v1–v4 only) — newer runs include v5; compare like-for-like.
- ThetaData bulk EOD fan-out is the bottleneck: always stage pairs at
  `THETADATA_HIST_CONCURRENCY=6`, never 6-way parallel (502 storms).

## Also found (code defect, fix pending approval)

`dealer_positioning.py:1206` `_gamma_subtitles['direction']` still reads
`'DEALER GAMMA BY STRIKE (V5 direction: whale-bias sign on OTM legs)'` — stale
since 5f9833c: the PRIMARY sign source is per-expiry SABR deviation; whale bias
is only the fallback for unreadable deviations. Comment at L1202-1204 repeats
it. User was offered the rename; no approval yet — do NOT patch dealer-model
code without his explicit OK (trust rules).
