# Per-Expiry Direction Bias Backtest Results (2026-08-08)

## Setup
- Tickers: SPY, AAPL
- Lookback: 30 trading days (BT_LOOKBACK_DAYS=30)
- Forward window: 5 days
- Permutations: 2000 (seed=42)
- Ticker fixed effects: yes
- Sign model: direction (v5)
- Accumulation: ON (150-day default, wired into live path)

## v5 (direction-5sig) Comparison — The Key Row

| Variant | coef | t-stat | ols p | perm p (full) | perm p (block) |
|---------|------|--------|-------|--------------|----------------|
| **uniform** (baseline) | -0.0003 | -1.659 | 0.1012 | 0.0335 | 0.1114 |
| **gamma_weighted** (A) | -0.0003 | -1.606 | 0.1125 | 0.0350 | 0.1064 |
| **whale_decomposed** (B) | -0.0003 | -1.733 | 0.0870 | 0.0260 | 0.0890 |
| **sabr_deviation** (C) | -0.0003 | -1.919 | 0.0587 | 0.0135 | **0.0680** |

## Winner: Option C (sabr_deviation)

- **Strongest t-stat**: -1.919 (vs -1.659 baseline, +15.7% improvement)
- **Lowest block perm p**: 0.0680 (closest to 0.05 significance threshold)
- **Lowest full perm p**: 0.0135 (already significant at 0.05)
- **Self-contained**: no Direction package dependency, no new API calls
- **Risk**: noisy on illiquid expiries (same SABR fit concern from debate FM-2)

## Notable Cross-Method Improvements

v4 (live oi_flow) also improved with sabr_deviation:
- Baseline: block p = 0.0850
- sabr_deviation: block p = 0.0710

v1 (oi_heuristic) was consistently significant across all variants:
- All runs: block p < 0.02

## Regime Counts (identical across all 4 variants)

SPY: n=46, v5 short-gamma=39 (85%)
AAPL: n=46, v5 short-gamma=46 (100%)

The per-expiry decomposition affects net_gamma MAGNITUDE but not sign classification.

## Recommendation

Ship sabr_deviation as the default per-expiry direction mode.
Change DEALER_DIRECTION_PER_EXPIRY default from 'uniform' to 'sabr_deviation'.

Rationale:
1. Self-contained (no Direction package dependency)
2. No new API calls (reuses existing SABR fit per-expiry)
3. Strongest signal (t=-1.919 vs -1.659)
4. Closest to block-perm significance (0.0680 vs 0.1114)
5. The illiquid-chain concern (FM-2) is mitigated by the minimum-strike gate already in the code

## Risk Acknowledgment

The backtest used only 2 tickers (SPY, AAPL) with 30-day lookback due to API rate limits.
The 5-ticker / 120-day run hit ThetaData circuit breaker (502 storms).
Results may not generalize to illiquid names or longer windows.
Re-run with all 5 tickers when the proxy stabilizes.

## Implementation Status

The `sabr_deviation` strategy was already implemented and tested; the follow-up change makes it the import-time default only. The other three modes remain explicit comparison modes. The implementation does not alter accumulation, replication weights, the OTM gate, or the primary sign-model default.
