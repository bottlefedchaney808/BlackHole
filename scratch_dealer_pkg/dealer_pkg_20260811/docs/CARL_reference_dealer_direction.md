# CARL Reference Kit — Dealer Direction: V5 (Direction 5-Signal) vs the Field

**Prepared 2026-08-06 for adversarial review.** Everything CARL needs is here or one path away.
Self-contained: question → artifacts → design → data → findings → open questions.

---

## 1. The question under review

Jason asked for a **new dealer-positioning sign model** whose *direction source* is the
**Direction 5-signal package** (whale flow + Elliott Wave + Bollinger + multi-timeframe trend +
liquidity zones) **in lieu of the ΔOI-flow direction source** (M1/M2) that won last night's debate —
added alongside the existing models (nothing overwritten), so the referee can compare all versions.
Second order: he wanted to test whether **just the whale leg** is enough ("use just whales").

## 2. The artifact chain (all on disk)

| Artifact | Path |
|---|---|
| Original 5-signal method (yfinance doc) | `Direction.py` (committed `0f1b029`, then replaced by launcher) |
| Dealer debate notes (5 voices, CARL-fallback) | `Vol_Suite/dealer_positioning_debate_notes.md` |
| Dealer final debate report | `Vol_Suite/dealer_positioning_debate_report.md` |
| Dealer method v3 spec (M1–M6, empirical referee) | `Vol_Suite/DEALER_METHOD_V3_SPEC.md` |
| oi_flow implementation results (last night's winner, V4) | `Vol_Suite/dealer_positioning_test_results.md` |
| **Direction package (V5 data source)** | `Direction/` (6 modules + tests, 66 passed, ThetaData-only) |
| **V5 sign model (this work)** | `Vol_Suite/dealer_positioning.py` (`'direction'` in `VALID_SIGN_MODELS`) |
| V5 backtest column | `Vol_Suite/backtest_stage3.py` (`net_gamma_v5`, proxy `_net_gamma_v5_direction`, `_build_direction_bias_by_date`) |
| Live comparison sweep | `Vol_Suite/outputs/direction_compare_20260806T050846Z/` + `SUMMARY_v5_comparison.md` |
| Backtest referee @3 | `Vol_Suite/outputs/BACKTEST_v5_90d_SUMMARY.md` + `backtest_v5_<T>_90d.log` |
| Backtest referee whale-only | `Vol_Suite/outputs/BACKTEST_v5_whaleonly_90d_SUMMARY.md` + `backtest_v5_<T>_whaleonly_90d.log` |
| Batch driver (staged pairs) | `backtest_v5_batch.sh` |
| Live sweep driver | `sweep_direction.py` |

**Parallel implementation:** coder profile refactored the same Direction method independently on
kanban task `t_7b88ef39` → worktree branch `wt/direction-coder` (compare for review).

## 3. V5 sign-model design (mirrors oi_flow's structure)

- `sign_model='direction'` in `VALID_SIGN_MODELS` (`dealer_positioning.py`).
- **Bias** (one per snapshot, `_fetch_direction_bias`): runs `Direction.signal_generator.generate(ticker)` →
  bias = +1 if whale ON + bullish + `score ≥ DEALER_DIRECTION_MIN_SCORE` (default 3), −1 for bearish, else 0.
- **Per-strike sign** (`_resolve_sign`): `sign = −bias · right_dir` (+1 call / −1 put), OTM-gated by the shared
  replicating set (keys only). Bias 0 → 0 contribution, NEVER a forced fallback to Layer-1b −1 ("no signal, no trade").
- **Result fields**: `direction_bias`, `direction_signal` (conviction/score/whale_direction) for audit; printed in the report.
- **Threading**: prompt maps (volatility_suite.py + dealer main), `_SIGN_MODEL_LABELS` ×2, README. Sign model is FIXED to V5 Direction (`CANONICAL_SIGN_MODEL='direction'` — Jason's exclusive choice 2026-08-09, backtest winner); per-expiry decomposition `DEALER_DIRECTION_PER_EXPIRY='sabr_deviation'`, 150-day accumulation. No `DEALER_SIGN_MODEL` env override exists.
- **Backtest proxy**: `_build_direction_bias_by_date` (no-lookahead historical whale + price legs from already-fetched data; EOD-volume rows feed the whale leg — zero extra fetches in the single-name path), `_net_gamma_v5_direction` mirrors the live math exactly. `pooled_panel_backtest.py` now passes `hist_eod_rows` (was dead before — one-line fix).

## 4. Live snapshot comparison (2026-08-06, 60d window, net $gamma + call)

| Ticker | v1 oi_heur | v2 replication | v2.1 vol_surface | v4 oi_flow | **v5 @min_score=2** |
|---|---|---|---|---|---|
| SPY | +$777M DAMPEN | −$2,904M AMPLIFY | −$2,681M AMPLIFY | −$265M AMPLIFY | bias −1 → +$161M NO_CALL |
| QQQ | +$314M DAMPEN | −$1,339M AMPLIFY | −$626M AMPLIFY | +$5M NO_CALL | bias −1 → −$71M NO_CALL |
| AAPL | +$236M DAMPEN | −$417M AMPLIFY | +$175M DAMPEN | −$224M AMPLIFY | bias +1 → −$211M AMPLIFY |
| NVDA | +$435M DAMPEN | −$582M AMPLIFY | −$331M AMPLIFY | −$204M AMPLIFY | bias +1 → −$251M AMPLIFY |
| TSLA | +$76M DAMPEN | −$197M AMPLIFY | −$85M AMPLIFY | −$63M AMPLIFY | no whale → NO_CALL |
| AMD | +$17M DAMPEN | −$77M AMPLIFY | −$23M AMPLIFY | +$5M DAMPEN | bias −1 → +$15M DAMPEN |

Live finding: at the faithful min_score=3 the model is silent (score caps at 2/3 — only whale+liquidity fire on a single day);
min_score=2 is the useful live operating point. Coherent logic: bullish whale → dealer short → AMPLIFY; bearish → dealer long → DAMPEN.

## 5. Backtest referee — 90d lookback (coef<0 + perm p<0.05 = evidence FOR dealer-hedge channel)

### v5 @ min_score=3 (5-signal gate)
| Ticker | n | v5 coef | v5 perm p | L/S | verdict |
|---|---|---|---|---|---|
| AAPL | 108 | **−0.0005** | **0.0015** | 15/93 | **SIGNIFICANT, hypothesis-consistent — first in the research line** |
| AMD | 107 | +0.0066 | **0.0020** | 57/50 | significant but WRONG sign (more short → lower vol) |
| NVDA | 108 | −0.0003 | 0.141 | 67/41 | right sign, null (best of the set) |
| TSLA | 108 | −0.0009 | 0.520 | 57/51 | right sign, null |
| SPY | 63 | +0.0001 | 0.311 | 15/48 | null |
| QQQ | 2 | nan | nan | — | ThetaData greeks-history gap for 20261030 (not load) |

### v5 whale-only (min_score=1) — same referee
| Ticker | n | v5 coef | v5 perm p | L/S | verdict |
|---|---|---|---|---|---|
| AAPL | 108 | **−0.0004** | **0.0030** | 20/88 | significant, hypothesis-consistent |
| AMD | 107 | +0.0059 | **0.0055** | 64/43 | significant, wrong sign |
| NVDA | 108 | −0.0002 | 0.159 | 71/37 | right sign, null |
| TSLA | 108 | −0.0008 | 0.553 | 68/40 | right sign, null |
| SPY | 63 | +0.0001 | **0.072** | 13/50 | positive trend approaching significance (wrong direction) |
| QQQ | 2 | nan | nan | — | data gap |

## 6. Key findings (the debate fuel)

1. **AAPL v5 is the first hypothesis-consistent significant result in the entire dealer-hedge line** (perm p 0.0015 @3, 0.0030 whale-only). No other model on any name has done it.
2. **The sign is name-dependent** — AMD is significant *positive* at both gates (0.002/0.0055): the same M2-style identification disease. Not uniformly clean.
3. **"Just whales" ≈ 5-signal.** The confirmation legs (wave3/squeeze/trend/liquidity) add almost nothing — whale-only reproduces every referee outcome. Simpler model, same result.
4. **Classifier property holds**: non-degenerate splits everywhere (v1 was 62/1, v2 5/58).
5. **Live @3 is silent in practice** (score caps 2/3 on single days) while the 90d proxy fires 59–108 days — gate behavior differs live vs historical.
6. **Two data bugs found & fixed during the work** (worth auditing): ThetaData bulk-EOD rows carry `right='CALL'/'PUT'` + dollar-string strikes (normalizer dropped everything → whale always neutral); pooled panel wasn't passing EOD volume rows to the v5 bias builder.

## 7. Open questions for CARL

- Is the AAPL result real signal or a single-name artifact (one expiry, one ticker, 108 days)?
- AMD's wrong-sign disease — same root as M2, or a whale-leg measurement issue (premium = volume×close×100, $25K threshold)?
- Should the whale-only gate replace the 5-signal gate (min_score=1 as default), given equivalence + simplicity?
- SPY's whale-only positive trend (perm p 0.072) — noise or a real dealer-long structure?
- Live silence at @3: is the "no signal, no trade" philosophy the right behavior for a dealer-positioning *market-structure* read (vs a trade signal)?
- v5 uses LEVEL OI magnitude under the flow sign — is that the right magnitude (vs |ΔOI|-weighted like v3)?
- QQQ data gap — accept, or fetch a different expiry for the panel?

## 8. How to run everything

```bash
# Direction package (ThetaData-only 5-signal method)
python Direction/signal_generator.py --ticker NVDA ; python Direction.py SPY
cd Direction && ../findev.sh -m pytest tests -q          # 66 passed

# V5 live dealer snapshot (sign_model is canonical; only threshold knobs vary)
cd Vol_Suite && ../findev.sh -m python -c "import dealer_positioning as d; r=d.compute_dealer_positioning('SPY', max_days=60, direction_min_score=2); print(r.direction_call, r.direction_bias)"

# Backtest referee (90d, per name)
cd Vol_Suite && DEALER_DIRECTION_MIN_SCORE=1 ../findev.sh -m python backtest_stage3.py AAPL 90

# Full staged batch (6 names, pairs)
DEALER_DIRECTION_MIN_SCORE=1 LOG_TAG=_foo bash backtest_v5_batch.sh

# Tests
cd Vol_Suite && ../findev.sh -m pytest tests -q          # 376 passed, 5 skipped
cd .. && ./findev.sh -m pytest Direction/tests -q        # 66 passed
```
