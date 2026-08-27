"""swaps_dashboard/app.py -- standalone swap-data browser, split out of
dashboard/app.py so the main dashboard's boot path never touches swaps.db.

See docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.

Routes here are moved verbatim from dashboard/app.py: /swaps, /trades,
/instruments/{upi}, /analytics/cross-source-notional, /analytics/timeseries.
A background task additionally writes cache/overview_snapshot.json every 5
minutes so dashboard/app.py's Overview/Tools cards can show real numbers
without ever querying swaps.db themselves.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# --------------------------------------------------------------------------
# paths / repo imports
# --------------------------------------------------------------------------

SWAPS_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SWAPS_DASHBOARD_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.config import load_env_once  # noqa: E402

load_env_once()

import orchestrator  # noqa: E402  (path is set immediately above)

DB_PATH = orchestrator.DB_PATH

TEMPLATES = Jinja2Templates(directory=os.path.join(SWAPS_DASHBOARD_DIR, 'templates'))

app = FastAPI(title='Swaps Dashboard')


@app.get('/health')
def health() -> Dict[str, Any]:
    return {
        'ok': True,
        'db_path': DB_PATH,
        'db_exists': os.path.exists(DB_PATH),
    }
