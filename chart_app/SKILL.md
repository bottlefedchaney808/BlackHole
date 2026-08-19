---
name: native-chart-app
description: Use when launching native chart or live tape.
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [quant, chart, tape, robinhood]
    related_skills: [native-quant-chart-development, launch-quant-tools]
---

# Native Chart App

Use when Jason says launch the chart, native chart, live tape, or "what's on the chart".

Launch: from repo root
`env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791`
Then open http://127.0.0.1:8791/

Read: GET http://127.0.0.1:8791/api/state
Switch: POST /api/symbol {"ticker":"SPY","interval":"15m"}
Refresh bars: POST /api/refresh {"lookback":"30d"}
Push RH: after mcp__robinhood__get_equity_positions / get_equity_orders, POST /api/rh
  {"position": {"qty": <float>, "avg_price": <float>} or null, "fills": [...]}

Orders: NEVER call a chart URL to place. Place only via mcp__robinhood__place_equity_order
and only when Jason explicitly says to in chat. Then refresh RH snapshot.

Do not fan out PH per bar. Do not use dashboard :8787.
