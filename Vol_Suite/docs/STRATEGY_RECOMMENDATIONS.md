# Strategy Recommendations Format & Contract

## Overview

The options chain scanner automatically generates multi-leg options strategy recommendations based on detected volatility edges and Greeks positioning. This document specifies the data format, assumptions, and contract for these recommendations.

## Data Format Assumptions

### Delta Sign Convention

- **Call deltas**: Positive (0 to +1), representing exposure to underlying price moves
- **Put deltas**: Negative (-1 to 0), representing inverse exposure to underlying price moves
- **Net strategy delta**: Sum of all leg deltas (can be positive, negative, or near-zero)

**Important**: This is the standard market convention. When combining legs, ensure sign consistency.

Example:
```
Long 1 call (strike 100): delta = +0.6
Short 1 call (strike 105): delta = -0.3
Net delta = +0.6 - 0.3 = +0.3 (directional bullish bias)
```

### Greeks Units

- **delta, gamma, theta, vega, vanna**: Per dollar of underlying
- **bid_ask_spread**: In dollars (e.g., 0.05 = $0.05 spread)
- **open_interest**: Number of contracts
- **iv_deviation**: In basis points (1.5 = 15bps deviation from fit smile)

### Edge Kind Mapping

The chain scanner identifies edges using the following mapping:

| edge_kind | edge_type | Interpretation |
|-----------|-----------|-----------------|
| 'rich' | 'SELL' | IV too high vs fit smile; sell premium |
| 'cheap' | 'BUY' | IV too low vs fit smile; buy premium |

**iv_residual_pts** (basis points): Deviation from smile fit curve. Positive = rich (overpriced), negative = cheap (underpriced).

## Output Format: chain_strategies.json

Every chain scan writes `chain_strategies.json` with this structure:

### Top-Level Schema

```json
{
  "version": "1.0",
  "timestamp": "2026-07-30T14:35:22.123456",
  "chain_verdict": "EDGE DETECTED",
  "vol_regime": "RICH",
  "current_price": 450.5,
  "expiration_date": "2026-08-21",
  "strategies": [...],
  "summary": {
    "total_recommendations": 1,
    "by_type": {"call_spread": 1},
    "by_regime": "RICH"
  }
}
```

### Strategy Object Schema

```json
{
  "strategy_type": "call_spread",
  "legs": [
    {
      "instrument_type": "call",
      "strike": 450.0,
      "quantity": -1
    },
    {
      "instrument_type": "call",
      "strike": 455.0,
      "quantity": 1
    }
  ],
  "vol_regime": "RICH",
  "rationale": "Sell rich 450.0 call, buy 455.0 call for protection",
  "edge_strikes_used": [450.0],
  "greeks_summary": {
    "delta": -0.3,
    "gamma": -0.01,
    "theta": 0.05,
    "vega": -0.2,
    "vanna": 0.01
  },
  "rank_score": 0.85
}
```

### Field Descriptions

#### Top Level
- **version**: Format version (always "1.0")
- **timestamp**: ISO 8601 UTC timestamp of generation
- **chain_verdict**: "EDGE DETECTED" or "NO CLEAR EDGE"
- **vol_regime**: "RICH", "CHEAP", or "FAIR"
- **current_price**: Spot price of underlying at scan time
- **expiration_date**: ISO 8601 date of option expiration
- **strategies**: Array of strategy recommendations (empty if no edges)
- **summary**: Summary statistics

#### Strategy
- **strategy_type**: Type of multi-leg strategy (see below)
- **legs**: Array of option legs (each leg is a transaction)
- **vol_regime**: Which vol regime drove this recommendation
- **rationale**: Natural-language explanation of why this strategy is recommended
- **edge_strikes_used**: Array of edge strikes incorporated into this strategy
- **greeks_summary**: Net Greeks exposure across all legs
- **rank_score**: Numeric score (higher = better match to vol regime)

#### Leg
- **instrument_type**: "call" or "put"
- **strike**: Strike price (must exist in chain)
- **quantity**: Number of contracts
  - Positive = long (buy)
  - Negative = short (sell)
  - Quantity semantics: 1 contract = 100 shares

#### Greeks Summary
- **delta**: Net directional exposure
- **gamma**: Curvature exposure (profits from realized moves)
- **theta**: Time decay (positive = collect as time passes)
- **vega**: IV exposure (positive = profits from vol expansion)
- **vanna**: Vega/delta cross-exposure (sensitivity of delta to IV changes)

All Greeks are per contract (adjusted for quantity).

## Strategy Types

### Recommended Strategies by Vol Regime

#### RICH Regime (High IV)

Strategies designed to collect theta (time decay) while limiting directional risk:

1. **call_spread**
   - Short high-IV call, long upside protection call
   - Netposition: Short gamma, positive theta
   - Use when: Expect IV compression, near-term stays flat

2. **put_spread**
   - Short high-IV put, long downside protection put
   - Net position: Short gamma, positive theta
   - Use when: Bullish outlook, expect IV compression

3. **iron_condor**
   - Sell call spread + sell put spread (both sides)
   - Net position: Highly short gamma, max theta collection
   - Use when: Neutral outlook, max premium harvesting

4. **collar**
   - Sell calls, buy puts (protective)
   - Net position: Limited downside, capped upside
   - Use when: Defensive with income generation

#### CHEAP Regime (Low IV)

Strategies designed to benefit from IV expansion and directional moves:

1. **straddle**
   - Long ATM call + put
   - Net position: Long gamma, long vega, delta ≈ 0
   - Use when: Expect big move (earnings, events), IV likely to rise

2. **strangle**
   - Long OTM call + put
   - Net position: Long gamma, long vega, delta ≈ 0, cheaper than straddle
   - Use when: Expect move but at lower cost

3. **call_spread**
   - Long call, short higher call (directional with defined risk)
   - Net position: Long gamma, positive vega
   - Use when: Bullish, want directional exposure at low cost

4. **put_spread**
   - Long put, short lower put (directional with defined risk)
   - Net position: Long gamma, positive vega
   - Use when: Bearish, want directional exposure at low cost

#### FAIR Regime (Balanced IV)

Neutral strategies emphasizing realized volatility exposure:

1. **straddle**
   - Long ATM call + put (gamma play)
   - Use when: Expect realized vol > implied vol

2. **strangle**
   - Long OTM call + put (cheaper gamma exposure)
   - Use when: Expect realized vol > implied vol

3. **call_spread** / **put_spread**
   - Neutral spreads with delta management
   - Use when: Hedge exposure while collecting premium

## Ranking Philosophy

Strategies are ranked by **rank_score**, which quantifies alignment with the vol regime:

### RICH Regime Scoring
```
Score = 2.0 * theta - |gamma| - |vega|
```

**Rationale**: High IV is expected to compress (mean reversion). Prefer:
- High **theta**: Collects time decay as vol compresses
- Low **|gamma|**: Limits directional risk (premium-selling exposure)
- Low **|vega|**: Limits vol risk (protected from IV expansion)

### CHEAP Regime Scoring
```
Score = 1.5 * gamma + 1.5 * vega - |theta|
```

**Rationale**: Low IV is expected to expand. Prefer:
- High **gamma**: Profits from realized moves
- High **vega**: Profits from IV expansion (main alpha source)
- Low **|theta|**: Willing to pay time decay for convexity

### FAIR Regime Scoring
```
Score = |gamma| - |theta|
```

**Rationale**: Balanced IV environment. Prefer:
- High **|gamma|**: Profits from realized volatility
- Low **|theta|**: Avoid paying excessive time decay

### Interpretation

- **Higher rank_score**: Strategy is more aligned with vol regime expectations
- **Ordered by rank_score**: First recommendation is best match
- **Scores are relative**: Compare within same regime, not across regimes

## Integration with Options_Suite

Options_Suite consumes strategies from `suite_context.json`:

```json
{
  "strategies": [...],  // From chain_strategies.json strategies array
  "focus": {"ticker": "SPY", ...},
  ...
}
```

### Options_Suite Usage

Options_Suite imports strategies for:

1. **Pricing**: Calculate current P&L of recommended strategies
2. **Greeks monitoring**: Track net Greeks exposure
3. **Portfolio comparison**: Compare strategies to current holdings
4. **Backtesting**: Analyze historical recommendation performance

### API Contract

Options_Suite expects:
- Each leg to have: `instrument_type`, `strike`, `quantity` (required)
- All numeric values to be JSON-serializable (no NaN/Infinity)
- All strikes to exist in the underlying chain (validated at generation time)

**Validation**: The _json_safe() function ensures NaN/Infinity are converted to null before serialization.

## Data Quality Checks

### Consistency Checks

1. **Strike validity**: All leg strikes must exist in chain_data
2. **Quantity semantics**: Positive = long, negative = short
3. **Greeks summation**: Reported greeks_summary matches sum of leg Greeks
4. **Delta convention**: Calls positive, puts negative

### JSON Serialization

1. **No NaN/Infinity**: All floats are valid JSON numbers or null
2. **Type correctness**: 
   - strike: numeric (int or float)
   - quantity: integer
   - instrument_type: string ("call" or "put")
3. **Roundtrip test**: JSON serialize → deserialize preserves values

## Example Workflows

### Workflow 1: RICH Regime Detection and Trading

```python
# Chain scan detects rich IV at 450 strike
# Recommendation: Sell call spread
strategy = {
    "strategy_type": "call_spread",
    "legs": [
        {"instrument_type": "call", "strike": 450.0, "quantity": -1},  # Sell
        {"instrument_type": "call", "strike": 455.0, "quantity": 1},   # Buy protection
    ],
    "vol_regime": "RICH",
    "greeks_summary": {"delta": -0.3, "theta": 0.05, ...}
}

# Interpretation:
# - Short 1 call @ 450 (sell premium, collect theta)
# - Long 1 call @ 455 (pay for protection, limit upside risk)
# - Net: Short 0.3 delta (slightly bearish), collect 0.05 theta per day
# - Best for: IV compression, sideways market
```

### Workflow 2: CHEAP Regime and Vol Expansion

```python
# Chain scan detects cheap IV at 420 strike
# Recommendation: Long strangle
strategy = {
    "strategy_type": "strangle",
    "legs": [
        {"instrument_type": "call", "strike": 460.0, "quantity": 1},   # Buy upside
        {"instrument_type": "put", "strike": 400.0, "quantity": 1},    # Buy downside
    ],
    "vol_regime": "CHEAP",
    "greeks_summary": {"delta": 0.0, "gamma": 0.02, "vega": 0.4, ...}
}

# Interpretation:
# - Long 1 call @ 460 + long 1 put @ 400 (bet on big move)
# - Net: Delta ≈ 0 (neutral direction), long 0.02 gamma (profits from moves)
# - Long 0.4 vega (profits from vol expansion)
# - Cost: Pay theta for convexity
# - Best for: IV expansion expected, earnings events
```

## Troubleshooting

### Issue: Empty strategies array despite EDGE DETECTED

**Cause**: Edges detected but no liquid strikes nearby for strategy construction.

**Solution**: Check liquidity thresholds (MIN_OPEN_INTEREST=50, MAX_BID_ASK_SPREAD=0.5). Adjust if needed.

### Issue: JSON parsing error (NaN/Infinity)

**Cause**: Numeric Greeks contain invalid JSON values.

**Solution**: Ensure _json_safe() is called during serialization. Should be automatic in run_chain_scanner().

### Issue: Options_Suite can't parse leg

**Cause**: Missing required field or type mismatch.

**Solution**: Verify each leg has: `instrument_type` (string), `strike` (numeric), `quantity` (integer).

## References

- Task 1: Strategy recommender core module
- Task 5: End-to-end integration tests
- options_chain_scanner.py: Chain scan integration
- strategy_recommender.py: Scoring logic (see _rank_strategies())
