# Native chart app (phase 1)

Standalone local chart window on **127.0.0.1:8791**. Not the dashboard (`:8787`).
One ticker, cached OHLCV, local price scores. No per-bar PotatoHedge fan-out.

## Launch

From repo root (clean interpreter — do not inherit `PYTHONPATH` / `VIRTUAL_ENV`):

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
```

Then open http://127.0.0.1:8791/

Windows: `chart_app.bat`. POSIX: `chart_app.sh`.

## Surfaces

| Who | How |
|---|---|
| Human | Browser window (vendored ECharts, no CDN) |
| Agent | `GET /api/state` — same bars / scores / markers / overlays. Do not OCR. |

```text
GET  /api/state
POST /api/symbol   {"ticker":"SPY","interval":"15m"}
POST /api/refresh  {"lookback":"30d"}
POST /api/rh       {"position": {"qty": <float>, "avg_price": <float>} | null, "fills": [...]}
```

There is **no** `/api/order`. Place only via `mcp__robinhood__place_equity_order`
when Jason says so in chat, then `POST /api/rh` to display the snapshot.

## What is computed (phase 1)

- ThetaData / PH v2 **spot OHLCV** into `artifacts/chart_app_bars.db` (`1d` or
  supported intraday). Never yfinance.
- Direction **price legs** on cached bars: wave3, squeeze, trend.
- Classic overlays: EMA20, EMA50, VWAP, Bollinger mid/upper/lower.
- Position-gated markers (`apply_position_gate`). Window starts flat.
- Live stamp uses the same HIGH/MEDIUM/NONE rule as `signal_generator.generate`.
  Whale is False, so HIGH/MEDIUM from whale **will not fire**. Expect
  `NONE (n/5)` until a later sampled-PH phase.

## What is not computed

| Off / later | Why |
|---|---|
| Whale / liquidity | Not fetched. Stay `False`. Do not fabricate. |
| Per-bar flow / dealer | Forbidden. Cost and honesty. |
| Sampled PH stamps | Later phase — not wired. |
| Orders | Server never calls Robinhood. Display-only. |
| Replay, watchlist, dealer overlays | Out of scope for phase 1. |
| Dashboard merge | Do not bind `:8787` or add routes to `dashboard/app.py`. |

## Bounded smoke

30 trading-calendar days of **daily** SPY is the live check — not a 1y job.
If today's EOD chunk returns `v2 payload is None`, fetch through the last
weekday only.
