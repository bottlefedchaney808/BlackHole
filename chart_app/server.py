"""Local FastAPI surface for the native chart window. No live PH, no orders."""

from __future__ import annotations

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
from shared.spot_history import fetch_daily_candles, fetch_intraday_candles, validate_ticker


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
) -> FastAPI:
    app = FastAPI()
    session: dict[str, Any] = {
        "ticker": default_ticker,
        "interval": default_interval,
        "rh": None,
    }

    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        payload = build_state(
            cache, session["ticker"], session["interval"], session["rh"]
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
        return {"ok": True}

    @app.post("/api/rh")
    def post_rh(body: _RhBody) -> dict[str, bool]:
        position = None
        if body.position is not None:
            position = {"qty": float(body.position.qty), "avg_price": float(body.position.avg_price)}
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
        return {"ok": True, "upserted": upserted}

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app


_DEFAULT_CACHE = Path("artifacts/chart_app_bars.db")
_DEFAULT_CACHE.parent.mkdir(parents=True, exist_ok=True)
app = create_app(BarCache(_DEFAULT_CACHE))
