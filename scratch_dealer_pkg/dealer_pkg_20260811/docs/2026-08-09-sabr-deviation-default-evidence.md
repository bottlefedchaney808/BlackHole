# SABR Deviation Default — Evidence Report

## Decision

`DEALER_DIRECTION_PER_EXPIRY` now defaults to `sabr_deviation`.

Explicit overrides remain supported:

- `uniform`
- `gamma_weighted`
- `whale_decomposed`
- `sabr_deviation`

The primary `compute_dealer_positioning()` default remains `sign_model="vol_surface_replication"`.

## What Was Tested

The four per-expiry direction modes were compared through the existing pooled panel backtest:

- Tickers: SPY, AAPL
- `BT_LOOKBACK_DAYS=30`
- Forward window: 5 days
- Permutations: 2,000
- Permutation seed: 42
- Ticker fixed effects: enabled
- Sign model: v5 `direction`
- Accumulation: enabled with its 150-day default
- Controls: continuous net-gamma signal, ATM IV, time to expiry, ticker fixed effects
- Strict null: within-ticker block permutation

## Results

| Mode | Coefficient | t-stat | OLS p | Full permutation p | Block permutation p |
|---|---:|---:|---:|---:|---:|
| `uniform` baseline | -0.0003 | -1.659 | 0.1012 | 0.0335 | 0.1114 |
| `gamma_weighted` | -0.0003 | -1.606 | 0.1125 | 0.0350 | 0.1064 |
| `whale_decomposed` | -0.0003 | -1.733 | 0.0870 | 0.0260 | 0.0890 |
| `sabr_deviation` | -0.0003 | -1.919 | 0.0587 | 0.0135 | 0.0680 |

## Why SABR Deviation Won

1. It had the strongest v5 t-statistic: `-1.919` versus `-1.659` for `uniform`.
2. It had the lowest full permutation p-value: `0.0135`.
3. It had the lowest strict within-ticker block permutation p-value: `0.0680`, closest to the `0.05` threshold.
4. It reuses each expiry's existing SABR fit, adds no Direction-package dependency, and makes no new API calls.
5. It also improved the v4 row's block p-value from `0.0850` to `0.0710` in the same comparison.
6. The implementation preserves the existing no-signal contract: a zero run-level Direction bias remains silent rather than being replaced by a SABR-derived bias.

## Implementation Details

The existing helper at `Vol_Suite/dealer_positioning.py:679-698` computes the per-expiry raw bias from the sum of `deviation_by_strike`:

- Positive total deviation: net rich, return `-1.0` (dealer short).
- Negative total deviation: net cheap, return `+1.0` (dealer long).
- Missing/empty fit or total within the configured deadband: return the run-level fallback bias.
- Zero run-level fallback bias: return `0.0`, preserving the Direction model's no-signal/no-trade behavior.

The shipped default is parsed at `Vol_Suite/dealer_positioning.py:631-634` from `DEALER_DIRECTION_PER_EXPIRY`, with `sabr_deviation` as the default string. Explicit environment overrides remain available.

## Verification Completed

Focused command:

```bash
env -u PYTHONPATH -u VIRTUAL_ENV \
  /home/bottl/Financial_Development/Financial_Dev_Env/bin/python3 \
  -m pytest Vol_Suite/tests/test_dealer_positioning_direction.py -q
```

Observed result: **35 passed**.

The focused tests cover:

- Positive aggregate SABR deviation.
- Negative aggregate SABR deviation.
- Deadband fallback.
- Missing and empty fit fallback.
- Zero fallback remaining silent.
- Shipped default source contract.
- Preservation of all four explicit modes.
- Explicit `uniform` import-time override.
- Existing direction-sign, no-bias, labeling, and report behavior.

## What Was Not Proven

The strict block permutation p-value was `0.0680`, not below `0.05`. The evidence supports selecting SABR deviation as the best tested comparative mode, not claiming definitive predictive significance.

The comparison used only SPY and AAPL over a 30-trading-day lookback. The 5-ticker/120-day attempt hit ThetaData 502/circuit-breaker saturation and is excluded from the comparison. The result should be re-run on the broader ticker panel when the data proxy is stable.

## Scope Preserved

This change does not alter:

- The primary `vol_surface_replication` sign-model default.
- Replication-weight magnitude handling.
- The OTM replication gate.
- The 150-day accumulation implementation.
- The `gamma_weighted` or `whale_decomposed` formulas.
- SABR all-strikes ingestion.

## Conclusion

Ship `sabr_deviation` as the default per-expiry direction mode, retain all explicit alternatives for controlled comparison, and revisit the choice after a larger stable panel is available.

**Evidence level:** comparative and promising; not yet definitive.
**Current implementation status:** default flip implemented; focused direction suite green.
