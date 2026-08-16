# Start Here

One shared environment (`.venv`), four analysis suites, a DTCC swap data
pipeline, an orchestrator that ties them together, and a live dashboard.
Everything below is a double-click `.bat` file — nothing needs a terminal
left open except the two long-running ones (dashboard, scheduler).

## First time only

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
python setup_db.py
```

Credentials (ThetaData) live in one place: the root `.env` file. Nothing
else needs its own `.env` anymore.

## The pieces

| What | Run it with | What it does |
|---|---|---|
| **Dashboard** | `dashboard.bat` | Opens `http://127.0.0.1:8787` — live swap data, ingestion status, and buttons to trigger orchestrator runs. This is the main control surface; start here. Only run one at a time — double-clicking it again while one's already up just opens your existing dashboard instead of starting a duplicate. |
| **DTCC scheduler** | `run_scheduler.bat` | Leave running. Polls DTCC's public API every 5 minutes and loads new swap trades — no manual downloads, ever. Only run one at a time — a second instance fights the first for database writes and silently stalls ingestion progress. It'll refuse to start if one's already running. |
| **DTCC backfill** | `python backfill.py` | One-time (or re-run anytime): pulls the full available history for SEC + CFTC equity swaps. Real volume is large (roughly 1.5M rows/day for SEC alone) — expect this to run for hours the first time. Safe to interrupt and re-run; it resumes where it left off. |
| **Orchestrator** | `orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16` | Runs sentiment-scanner → Vol_Suite → Options_Suite + VaR_Tools_Simulations in dependency order, or a single suite with `--suite options\|vol\|var\|sentiment`. Also reachable from the dashboard's "Trigger a run" panel. |
| **Individual suites** | `Options_Suite\options_suite.bat`, `Vol_Suite\vol_suite.bat`, `VaR_Tools_Simulations\var.bat`, `sentiment-scanner\sentiment.bat` | Run any suite standalone and interactively, same as before — they now share the one root `.venv` instead of their own. |
| **Backtest tournament** | `.venv\Scripts\python.exe Backtests\main.py --harness all --ticker SPY,QQQ --lookback-days 1` | Runs the transferred standalone Backtests package for pricing, greeks, and signal evaluation. Writes text/JSON artifacts to `Backtests\outputs\` and keeps existing `Vol_Suite` backtests separate. |

## Query swap data directly

```bash
python swaps_query.py
```

Or from Python:

```python
from swaps_query import SwapsQuery
q = SwapsQuery()
q.get_database_stats()
q.top_notional_products()          # top instruments by notional, most recent day
q.query_by_upi("QZBTF5S5TCDR")     # trades identify instruments by UPI, not ticker
```

See `DTCC_LOCAL_SETUP.md` for how the DTCC pipeline itself works (it pulls
straight from DTCC's public API — no manual downloads).

## Typical order of operations

1. `dashboard.bat` — leave it open, this is your window into everything.
2. `run_scheduler.bat` in a second window — keeps swap data current.
3. `python backfill.py` once, if you want the full historical swap dataset
   (can also be kicked off and left running unattended).
4. Trigger orchestrator runs from the dashboard, or `orchestrator.bat`
   directly, whenever you want a cross-suite analysis for a ticker.


## Workflow helpers

Run these from Git Bash at the repo root when you need a quick workflow check:

- `bash scripts/burst_checkpoint.sh vol` - prints `git diff --stat` and runs the narrow Vol_Suite checkpoint slice before another burst of changes.
- `git config core.hooksPath scripts/hooks` - installs `scripts/hooks/commit-msg`, which enforces the repo's commit-subject policy on each commit.
- `bash scripts/verify_tradingview_submodule.sh` - verifies the `tradingview-mcp` checkout (gitlink when configured, source entrypoint always) and treats the live Hermes/CDP probe as informational on this Windows repo.

## Sharing the dashboard publicly

The dashboard has a built-in **Share** control in the top bar. Start the
dashboard, click **Start sharing**, and confirm — a public HTTPS link
(e.g. `https://<random-words>.trycloudflare.com`) appears that proxies to your
local dashboard for as long as your machine and the dashboard stay running.
Click **Stop sharing** (or close the dashboard) to take it down.

Under the hood it runs Cloudflare's free, account-less "quick tunnel"
(`cloudflared tunnel --url http://127.0.0.1:8787`). The URL changes each time
you start a new one, and `cloudflared` must be installed and on PATH.

**There is no password on the dashboard.** Anyone with the link can see all
swap data and trigger orchestrator runs (which call real, billed ThetaData
API requests). Only share the link with people you trust, and stop the
tunnel when you're done.
