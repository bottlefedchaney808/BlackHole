"""Local FastAPI surface for the native chart window.

Phase 1 semantics (honest):
- Scores are **price-only** (wave3 / squeeze / trend from cached OHLCV).
- Whale is ONE `flow.scanner_trades_in_time_range` for the loaded window,
  stamped onto bars. Never per-bar PH. Liquidity still off.
- No order route. Robinhood is display-only via POST /api/rh.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

_STATIC_DIR = Path(__file__).resolve().parent / "static"

from chart_app.bar_cache import BarCache
from chart_app.ingest import refresh_cache
from chart_app.snapshot import build_state
from shared.chart_data import SUPPORTED_INTERVALS, ChartDataError
from shared.spot_history import (
    fetch_daily_candles,
    fetch_intraday_candles,
    validate_ticker,
)

# Mirrors the LOOKBACK map in static/index.html -- how much history to pull
# per interval so a background refresh keeps enough bars to backfill any gap
# since the last poll, not just a rolling window.
_LOOKBACK = {
    "3m": "5d",
    "5m": "5d",
    "10m": "10d",
    "15m": "5d",
    "30m": "20d",
    "1h": "60d",
    "4h": "1y",
    "1d": "1y",
}


class _SymbolBody(BaseModel):
    ticker: str
    interval: str


class _PositionBody(BaseModel):
    qty: float
    avg_price: float


class _RhBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: _PositionBody | None = None
    fills: list[Any] = []


class _RefreshBody(BaseModel):
    lookback: str


def create_app(
    cache: BarCache,
    *,
    default_ticker: str = "SPY",
    default_interval: str = "15m",
    daily_fn=fetch_daily_candles,
    intrad_fn=fetch_intraday_candles,
    flow_fn=None,
    background_refresh_seconds: float | None = None,
) -> FastAPI:
    app = FastAPI()
    saved_session = cache.get_session()
    session: dict[str, Any] = {
        "ticker": saved_session[0] if saved_session else default_ticker,
        "interval": saved_session[1] if saved_session else default_interval,
        "rh": None,
        "flow_cache": {},
    }
    _refresh_task: asyncio.Task | None = None

    def _refresh_now() -> None:
        interval = session["interval"]
        refresh_cache(
            cache,
            session["ticker"],
            interval,
            _LOOKBACK.get(interval, "5d"),
            daily_fn=daily_fn,
            intrad_fn=intrad_fn,
        )
        session["flow_cache"].clear()

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        payload = build_state(
            cache,
            session["ticker"],
            session["interval"],
            session["rh"],
            flow_fn=flow_fn,
            flow_cache=session["flow_cache"],
        )
        payload["ok"] = True
        return payload

    @app.post("/api/symbol")
    def post_symbol(body: _SymbolBody) -> dict[str, bool]:
        try:
            ticker = validate_ticker(body.ticker)
        except ChartDataError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.interval not in SUPPORTED_INTERVALS:
            raise HTTPException(status_code=400, detail="unsupported interval")
        session["ticker"] = ticker
        session["interval"] = body.interval
        session["flow_cache"].clear()
        cache.set_session(ticker, body.interval)
        return {"ok": True}

    @app.post("/api/rh")
    def post_rh(body: _RhBody) -> dict[str, bool]:
        position = None
        if body.position is not None:
            position = {
                "qty": float(body.position.qty),
                "avg_price": float(body.position.avg_price),
            }
        session["rh"] = {"position": position, "fills": list(body.fills)}
        return {"ok": True}

    @app.post("/api/refresh")
    def post_refresh(body: _RefreshBody) -> dict[str, Any]:
        upserted = refresh_cache(
            cache,
            session["ticker"],
            session["interval"],
            body.lookback,
            daily_fn=daily_fn,
            intrad_fn=intrad_fn,
        )
        session["flow_cache"].clear()
        return {"ok": True, "upserted": upserted}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    if background_refresh_seconds:
        # Opt-in only (param is None by default) so plain create_app() calls
        # in tests never hit the network -- only the module-level `app`
        # instance below, used by the actual uvicorn process, enables this.

        def _next_delay() -> float:
            # Daily bars don't move intrabar the way intraday ones do --
            # polling them every ~45s would just burn ThetaData calls for no
            # new information, so back off hard whenever "1d" is selected.
            if session["interval"] == "1d":
                return max(background_refresh_seconds, 900.0)
            return background_refresh_seconds

        async def _refresh_loop() -> None:
            loop = asyncio.get_event_loop()
            while True:
                try:
                    await loop.run_in_executor(None, _refresh_now)
                except Exception:
                    pass  # transient data-source failure; retry next tick
                await asyncio.sleep(_next_delay())

        @app.on_event("startup")
        async def _on_startup() -> None:
            nonlocal _refresh_task
            loop = asyncio.get_event_loop()
            try:
                await loop.run_in_executor(None, _refresh_now)
            except Exception:
                pass
            _refresh_task = asyncio.create_task(_refresh_loop())

        @app.on_event("shutdown")
        async def _on_shutdown() -> None:
            if _refresh_task is not None:
                _refresh_task.cancel()

    return app


def _production_flow_fn(root, start_dt, end_dt, min_premium):
    """One scanner_trades call (paginated) per session date — not per bar.

    Always full tape (min_premium=0 internally; passed arg ignored).
    Date-only calls (start_date/end_date). Pagination limit=10000 + offset loop
    until short/empty page per day. 5-day cap. except:continue (degrade).
    """
    from datetime import datetime, timedelta

    from chart_app.flow_stamp import rows_from_flow_payload
    from Direction.indicator import _thread_client

    def _as_dt(value):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))

    start = _as_dt(start_dt)
    end = _as_dt(end_dt)
    days = []
    cursor = start.date()
    last = end.date()
    while cursor <= last:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    if not days or len(days) > 5:
        return []
    client = _thread_client()
    rows: list = []
    LIMIT = 10000
    for day in days:
        ymd = day.strftime("%Y%m%d")
        offset = 0
        while True:
            try:
                env = client.flow.scanner_trades(
                    root=root,
                    start_date=ymd,
                    end_date=ymd,
                    min_premium=0,  # full tape always (ignore the passed arg)
                    limit=LIMIT,
                    offset=offset,
                )
                page = rows_from_flow_payload(getattr(env, "data", None))
                rows.extend(page)
                if len(page) < LIMIT:
                    break
                offset += LIMIT
            except Exception:
                # per-day degrade (keep existing contract); do not raise
                break
    return rows


def _refresh_seconds_from_env(default: float = 45.0) -> float:
    raw = os.environ.get("CHART_APP_REFRESH_SECONDS")
    if not raw:
        return default
    try:
        return max(5.0, float(raw))
    except ValueError:
        return default


_DEFAULT_CACHE = Path("artifacts/chart_app_bars.db")
_DEFAULT_CACHE.parent.mkdir(parents=True, exist_ok=True)
app = create_app(
    BarCache(_DEFAULT_CACHE),
    flow_fn=_production_flow_fn,
    background_refresh_seconds=_refresh_seconds_from_env(),
)
