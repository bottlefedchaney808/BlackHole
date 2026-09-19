# Native chart app

Standalone window **http://127.0.0.1:8791/**. Not TradingView, not Hermes preview, not dashboard `:8787`.

Jason sees pixels. An agent reads `GET /api/state`. Same numbers.

**Deep handoff: [`HANDOFF.md`](HANDOFF.md)** — read that before changing anything. It
carries the measured numbers behind every design decision here, and the honest
verdict on the algo.

## Launch

```bash
cd E:/BlackHole_Investments/BlackHole
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
```

Agent: load skill `native-chart-app` (Edge `--new-window`, not `cmd start`).
After editing anything in `static/js/`, **hard-reload the page** (Ctrl+Shift+R) —
the browser caches it.

## What is on the glass

**Three modes** (buttons in the header, or keys `1` / `2` / `3`):

| mode | price pane | lower panes |
|---|---|---|
| **Clean** | candles + EMA20/50 only | volume |
| **Signals** | candles + EMA20/50 + ALMA, the ATR stop, whale dots, big labelled BUY/SELL marks and shaded held periods | conviction line **plus whatever you have checked** |
| **Full** | every overlay in the checkbox row, each with a `⚙` settings gear | same |

Signals mode simplifies the *price* pane; it does not throw away your lower
panels. Press `r` to refit the window.

**Symbols**: equities (`SPY`, `NVDA`) and crypto perps (`BTC-PERP`, `ETH-PERP`,
`SOL-PERP`, …) or spot (`BTC-USD`). Perps route to OKX, not ThetaData — see
below.

**Direction legs**: `W3 SQ TR WH LQ` pills for the last bar. `WH` is coloured by
flow direction (green = call-side, red = put-side) because "a whale printed" and
"that whale was bullish" are different claims. `LQ` is ELMo's Hui-Heubel
liquidity and is finally live — it was hardwired `False` since the app was written.

**Conviction readout**: a signed −100…+100 score, a bipolar bar, and the current
position. The score is the same number plotted in the `algo` pane.

## The algo, honestly

`signal_engine.py` combines five weighted components (trend 26, momentum 22,
whale 22, ELMo 18, breakout 12) into one signed line, gated on liquidity, and
turns it into buy/add/trim/sell with an ATR trailing stop.

**It has no demonstrated out-of-sample edge.** Measured on 8 equities × 3y daily
plus 3 intraday series, split-adjusted, 2bp a side:

- walk-forward (4 folds, refit per fold): **3/11 series positive OOS, 33% fold hit rate**
- permutation test (60 shuffled surrogates each): **median p = 0.598, 0/12 beat their null**

The defaults are calibrated so the chart is *legible*, not because they make
money. Treat the line as a fast read of where trend, momentum, order and flow
agree — not as an autotrader. Full numbers and method in `HANDOFF.md` §6.

Where it does help is drawdowns: on BTC-PERP daily it returned **+4.1%** against
buy-and-hold **−30.3%**.

## ELMo

**E**ntropy, **L**iquidity, **M**omentum — after the TradingView strategy of the
same name (source protected; nothing copied, each leg implemented from the
literature).

Entropy is bucketed in **rolling-sigma** units and consumed as a **trailing
percentile rank**; so is the Hui-Heubel liquidity ratio. That is what removes
the per-symbol, per-timeframe lookback tuning the original needs. Verified on 14
series from $113 to $80,701 and 15m to 1d, with identical settings:
`ordered` fires on 30.5% of bars (sd 4.1), `liquid` on 45.9% (sd 8.6).

No volume → `liquidity = None` and the header says "no volume: LQ unavailable".
It will not invent a share count.

## Whale

- One `flow.scanner_trades` sweep **per session date**, never per bar, on a
  background thread so it cannot block `/api/state`.
- Provider-side premium floor (`CHART_APP_FLOW_MIN_PREMIUM`, default $10k). SPY
  prints **1.39M contracts a day**; the unfiltered tape took 402s per day and
  never finished. The floor keeps 82% of premium for 2% of the rows.
- Threshold adapts to mark ~1 dot per 2 bars, floored at $25k. Dots are **sized
  by premium** and coloured by net call/put.
- `whale.coverage` reports sessions-stamped vs sessions-charted, so a partly
  covered chart says so instead of just looking empty.

## Crypto / perps

ThetaData is equities-only and **will answer for `BTC` with the Grayscale
Bitcoin Mini Trust ETF** ($33.82). So routing is on ticker shape and a bare
`BTC` deliberately stays an equity. Use `BTC-PERP`.

Venues (probed 2026-09-18): OKX swaps **OK** (used first, real perpetuals),
Coinbase spot **OK** (fallback), Binance and Bybit **geo-blocked (451/403)**.
`CandlePayload.source` names which venue answered.

## API

```
GET  /api/state
POST /api/symbol   {"ticker":"BTC-PERP","interval":"1h"}
POST /api/refresh  {"lookback":"30d"}
POST /api/rh       {"position": {...} | null, "fills": [...]}
```

No `/api/order`, and there must never be one. Live RH only when Jason says so in
chat (`mcp__robinhood__place_equity_order`), then `POST /api/rh` to display it.

## Live strategy tester and the gears

Two ways to retune without leaving the chart. **Both run against the bars
already cached** -- no refetch, no provider call, nothing billed. Measured
round trip: 0.02-0.03s.

**The tester** (`Test` button, or key `t`, or the gear on `conviction`).
A strip of sliders for the algo's levels and ELMo's windows, with a live stat
line underneath:

    return  buy&hold  vs b&h  trades  win  PF  max DD  sharpe  expo  avg hold

`vs b&h` sits next to the return deliberately: for a long-only timing rule a
green +30% against a +90% hold is a losing strategy, and burying that
comparison is how a tester flatters itself.

It scores with `backtest.run_backtest` -- **next-bar-open fills, costs
charged** -- while the arrows it draws come from the live state machine, which
records a decision on the bar that triggered it. Two runs on purpose:
conflating them would either put the marks a bar late or assume a fill nobody
could get.

**The gears** (the little `⚙` beside each overlay in Full mode). One popover
per indicator -- EMA periods, Bollinger window and sigma, VWAP sigma, ATR
period and multiple, RSI, CCI, MACD fast/slow/signal, and all nine ELMo
windows. A gear turns **amber** when that indicator is off its shipped value,
so you can always tell a default chart from one you retuned an hour ago and
forgot.

Slider values are seeded from `GET /api/indicator-defaults`, never hardcoded
client-side, so a slider cannot say 14 while the engine uses 20. Series keys
stay fixed (`ema20` is still `ema20` at period 5) because the key is the wire
contract the renderer draws against, not a description of the period.

```
POST /api/backtest          {"config": {...}, "elmo": {...}, "cost_bps": 2.0}
POST /api/indicators        {"params": {...}, "elmo": {...}}
GET  /api/indicator-defaults
```

## Backtests

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m chart_app.backtest_runner --stage all
```

Stages: `pull` / `base` / `sweep` / `wf` (walk-forward) / `perm` (permutation).
Next-bar-open fills, costs on by default, warm-up bars seeded but never traded
or scored. Caches to `artifacts/chart_app_backtest.db` — separate from the live
chart's DB on purpose.

## Tests

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests -q   # 167 passing
```

The load-bearing one is `test_conviction_is_causal`: it recomputes the score on
a truncated series and demands bit-identical earlier values. Every backtest
number is meaningless without it. If you add a component, cover it there.

## Known data-integrity issue

`shared/spot_history.fetch_daily_candles` serves **unadjusted** prices. NVDA's
2024 10-for-1 arrives as a real −89.9% session. `split_guard.py` handles it for
this app; `Vol_Suite/correlation_engine.py` uses the same path and does **not**.
See `HANDOFF.md` §4.
