# Native chart app

Standalone window **http://127.0.0.1:8791/**. Not TradingView, not Hermes preview, not dashboard `:8787`.

Jason sees pixels. Agent reads `GET /api/state`. Same numbers.

## Launch

```bash
cd C:/Users/bottl/FinancialDevelopment
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
```

Windows: `chart_app.bat`. Agent: load skill `native-chart-app` (Edge `--new-window`, not `cmd start`).

## What is on the glass (parked 2026-08-19)

- Ticker box + interval select + Load. Default **SPY 15m / 5d**.
- Wheel zoom + bottom slider (lookback). 1d caches **1y**, opens last ~180 bars.
- Overlays: EMA20 / EMA50 / VWAP / Bollinger (legend toggles).
- Direction **legs always visible**: W3 SQ TR WH LQ pills (last bar) + amber whale dots on stamped bars.
- Buy/sell/hold/add still **position-gated** (`apply_position_gate`). Window starts flat.

## Whale (WH)

- **Not** per-bar `scanner_trades_in_time_range` (times out on multi-hour windows).
- Production: `flow.scanner_trades` **once per session date**, cap **5 days**, stamp prints ≥ $25k onto closed bars. Cached until symbol/refresh changes.
- Row time key is `datetime`. Payload is header-first tuples (`chart_app/flow_stamp.py`).
- LQ still off. Sampled dealer / S/R lines next.

## API

```
GET  /api/state
POST /api/symbol   {"ticker":"SPY","interval":"15m"}
POST /api/refresh  {"lookback":"5d"}   # 1d lookback "1y" from the UI
POST /api/rh       {"position": {...} | null, "fills": [...]}
```

No `/api/order`. RH live only when Jason says so in chat (`mcp__robinhood__place_equity_order`), then POST `/api/rh`.

Daily EOD fetch ends at **last complete weekday** (`shared.spot_history._complete_eod_date`) so today's empty session does not 500.

## Next (do not auto-start)

1. LQ: one `dealer.weighted_greeks_summary` + `support_resistance.snapshot` lines.
2. Per-scale indicator knobs.
3. Paint W3/SQ/TR on the tape (not just last-bar pills).
4. `flow.recent(window=15)` for the live last bar between daily stamps.
