"""Local FastAPI surface for the native chart window. No live PH, no orders."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from chart_app.bar_cache import BarCache
from chart_app.snapshot import build_state
from shared.chart_data import SUPPORTED_INTERVALS, ChartDataError
from shared.spot_history import validate_ticker


class _SymbolBody(BaseModel):
    ticker: str
    interval: str


class _PositionBody(BaseModel):
    qty: float
    avg_price: float


class _RhBody(BaseModel):
    position: _PositionBody | None = None
    fills: list[Any] = []


def create_app(
    cache: BarCache,
    *,
    default_ticker: str = "SPY",
    default_interval: str = "15m",
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

    @app.get("/", response_class=HTMLResponse)
    def root() -> str:
        return "chart-app"

    return app
