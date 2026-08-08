# Direction -- 5-Input Market Direction Signal Engine

Unified direction signal built from **five independent inputs** (whale flow,
Elliott Wave, Bollinger Bands, multi-timeframe trend, liquidity zones).
ThetaData-backed only.

Ported from an external devnotes research package (`Dealer_pos_devnotes.tar.gz`,
sourced from a separate `/home/bottl/Financial_Development` environment) --
see `docs/superpowers/plans/2026-08-06-direction-tools-suite.md` for the full
port plan, what changed vs. the source package, and why.

## Package layout

| File | Role |
|---|---|
| `data.py` | Single ThetaData access layer (price, OHLCV, chains, GEX) |
| `whale_scanner.py` | Live whale-flow scanner (`scan`) -- wraps `Vol_Suite.whale_scanner.classify_whale_bias` |
| `elliott_wave.py` | Wave counting / wave-3 momentum (`analyze`) |
| `bollinger_analyzer.py` | Squeeze + band-thrust regime (`analyze`) |
| `trend_engine.py` | ADX + MA20/MA50 multi-timeframe trend (`analyze_trend`) |
| `liquidity_map.py` | Max pain (proper payout-minimization, not spot-proximity) / OI strike walls / PCR / GEX (`get_liquidity`) |
| `signal_generator.py` | **Unified layer** -- combines all five into one conviction |

## Relationship to Vol_Suite's whale-flow backtest port

`Vol_Suite/whale_scanner.py` (added 2026-08-06, see
`docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`) is a
**pure, network-free classifier** (`classify_whale_bias`) used by
`backtest_stage3.py`'s historical backtest. `Direction/whale_scanner.py` here
is its **live** sibling: it fetches a fresh chain via `Direction.data` and
calls that exact same classifier, so both paths agree on what counts as
"whale" flow -- no second copy of the threshold/ratio math.

## Standalone module usage

Every module runs on its own (ThetaData only):

```bash
python -m Direction.whale_scanner SPY
python -m Direction.elliott_wave SPY
python -m Direction.bollinger_analyzer SPY
python -m Direction.trend_engine SPY
python -m Direction.liquidity_map SPY
```

Each module returns a dict contract ending in a boolean `signal`:
`whale_scanner.scan(ticker)`, `elliott_wave.analyze(ticker)`,
`bollinger_analyzer.analyze(ticker)`, `trend_engine.analyze_trend(ticker)`,
`liquidity_map.get_liquidity(ticker)`.

## Unified usage

Combine all five inputs into a single conviction call:

```bash
python Direction.py NVDA
python -m Direction.signal_generator --ticker NVDA
python Direction.py   # default ticker SPY
```

Programmatic:

```python
from Direction import generate

r = generate("NVDA")
# {
#   "ticker": "NVDA",
#   "price": 130.55,
#   "signals": {"whale": True, "wave3": True, "squeeze": False,
#               "trend": True, "liquidity": True},
#   "score": 4,                      # number of signals firing (0-5)
#   "conviction": "HIGH",            # HIGH | MEDIUM | NONE
#   "details": {"whale": {...}, "elliott": {...}, "bollinger": {...},
#               "trend": {...}, "liquidity": {...}},
# }
```

## Conviction rules

| Conviction | Requirement | Tradeable |
|---|---|---|
| **HIGH** | whale **AND** wave3 **AND** (squeeze **OR** trend) **AND** score >= 3 | Yes |
| **MEDIUM** | whale **AND** score >= 3, and not HIGH | Yes (smaller) |
| **NONE** | anything else -- **no whale signal = no trade** | No |

- `score` = number of the five signals firing (0-5).
- Whale flow is the mandatory gate: without it, conviction is always NONE.
- Module outputs are mapped defensively (`result.get("signal", False)`), so a
  module returning `None`/`{}` on a data hiccup degrades to "off" instead of
  crashing the run.

## Platform integration (Tools registry)

All six modules are wired into the `Tools` plugin platform as registered
`ToolSpec` adapters (see `docs/superpowers/plans/2026-08-06-direction-tools-suite.md`
Tasks 10-16), invocable from the dashboard's `/tools` page or programmatically:

| Slug | Adapter | Backing module |
|---|---|---|
| `whale-flow` | `Tools/tools/whale_flow_tool.py` | `Direction.whale_scanner.scan` |
| `elliott-wave` | `Tools/tools/elliott_wave_tool.py` | `Direction.elliott_wave.analyze` |
| `bollinger` | `Tools/tools/bollinger_tool.py` | `Direction.bollinger_analyzer.analyze` |
| `trend-engine` | `Tools/tools/trend_engine_tool.py` | `Direction.trend_engine.analyze_trend` |
| `liquidity-map` | `Tools/tools/liquidity_map_tool.py` | `Direction.liquidity_map.get_liquidity` |
| `direction-signal` | `Tools/tools/direction_signal_tool.py` | `Direction.signal_generator.generate` |

These are **live** ThetaData tools (not artifact readers), so they require a
suite context with a ticker:

```python
from Tools.registry import get_tool

result = get_tool("direction-signal").run({"focus": {"ticker": "NVDA"}})
result = get_tool("whale-flow").run({"focus": {"ticker": "NVDA"}})
```

The unified `direction-signal` tool is the efficient group entry point --
one process, one shared 300s data cache (`DIRECTION_DATA_TTL` env var)
across all five inputs.

## Scope note

These tools are standalone -- **none of the five signals is wired into
`Vol_Suite/dealer_positioning.py`'s live `VALID_SIGN_MODELS`**. That stays
scoped exactly as decided in
`docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`:
the source package's own numbers don't survive proper multiple-comparisons
correction, so nothing here feeds a live sign convention without a
separate, deliberate decision to do so.

## Data note

All market data comes from **ThetaData** through `Direction.data` (spot,
EOD OHLCV, option chains, dealer positioning). If credentials are missing or
a fetch fails, fetchers return `None` and modules degrade gracefully.
