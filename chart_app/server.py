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
import threading
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

# Mirrors the LOOKBACK map in static/js/theme.js -- how much history to pull
# per interval so a background refresh keeps enough bars to backfill any gap
# since the last poll, not just a rolling window.
# THE 30-DAY CEILING IS GONE (2026-09-19). What follows used to say "30 days is
# the most the provider path will serve" -- and that was never the provider. It
# was `httpx`'s default 5.0s timeout inside PHClient: every one-minute span from
# 10d to 25d failed at exactly 5.0s and then served in 0.3-0.5s once the timeout
# was raised. `shared/spot_history.py::validate_intraday_lookback` no longer caps
# intraday at all, and `hist_stock_ohlc` chunks at 21d. See CLAUDE.md and the
# `phclient-v2` skill.
#
# So 1h and 4h can finally seed their own indicators instead of being propped up
# by the EMA 200->100 cascade, which existed ONLY because 30d of hourly data is
# ~162 bars and EMA200 never defined. The cascade stays as a safety net for a
# short tape; it is no longer load-bearing for these two timeframes.
#
# Windows are sized by what the INDICATORS need, not by what the fetch tolerated:
#   1h  ~7 bars/session  -> 60d  ~ 42 sessions ~ 294 bars   (EMA200 seeds)
#   4h  ~2 bars/session  -> 180d ~126 sessions ~ 252 bars   (EMA200 seeds)
# Each is one chunked pull, cached after, and only on that timeframe's first
# load. Nothing else changed: the shorter frames are unaffected.
_MAX_INTRADAY_LOOKBACK = "180d"
_LOOKBACK = {
    "3m": "5d",
    "5m": "5d",
    "10m": "10d",
    # 20d, not 5d. The default chart is 15m; 5d is ~130 bars, so EMA200
    # never defined and the 200-bar ELMo ranks never filled. 20d is ~520
    # bars -- enough to seed both -- and matches 30m's window.
    "15m": "20d",
    # 30d, not 20d. 20d of 30m is ~179 bars -- short of the 200 EMA200 and the
    # ELMo ranks need, and it had been that way unnoticed because the old test
    # asserted lookback STRINGS rather than the bar count they produce.
    "30m": "30d",
    "1h": "60d",                    # ~7 bars/session * ~42 sessions
    "4h": "180d",                   # ~2 bars/session * ~126 sessions
    "1d": "1y",                     # daily path, not the intraday validator
}


class _SymbolBody(BaseModel):
    ticker: str
    interval: str


class _ProfileBody(BaseModel):
    """Save a parameter set for a (ticker, interval).

    `ticker`/`interval` omitted or "*" store a wider default -- "*"/"15m" is
    the structurally-justified one (windows are counted in bars, so a horizon
    is timeframe-specific), while a symbol-specific profile is the kind the
    walk-forward warned about and is stamped accordingly.
    """

    ticker: str | None = None
    interval: str | None = None
    config: dict[str, Any] = {}
    elmo: dict[str, Any] = {}
    capital: float | None = None
    # Anything stronger than `in_sample` has to be earned by actually running
    # folds -- the tester cannot claim it on its own.
    validation: str = "in_sample"
    metrics: dict[str, Any] = {}
    note: str = ""


class _PositionBody(BaseModel):
    qty: float
    avg_price: float


class _RhBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: _PositionBody | None = None
    fills: list[Any] = []


class _RefreshBody(BaseModel):
    lookback: str


class _BacktestBody(BaseModel):
    """Live parameter test against the bars already on screen.

    `config` overrides `signal_engine.DEFAULTS`, `elmo` overrides
    `elmo.DEFAULTS`. Both are free-form dicts rather than a fixed schema so the
    tester panel can expose a new knob without a server change; unknown keys
    are simply merged and ignored by whichever engine does not use them.
    """

    config: dict[str, Any] = {}
    elmo: dict[str, Any] = {}
    # Notional the percent metrics are scaled by for the dollar figures the
    # tester shows. Not an account balance -- see `backtest.DEFAULT_CAPITAL`.
    capital: float = 100_000.0
    # Inclusive ISO dates (YYYY-MM-DD) bounding the bars to score. Omitted =
    # the whole loaded window. This is what makes an out-of-sample check
    # possible by hand: fit on one span, score another. It can only ever narrow
    # the bars ALREADY cached -- it never triggers a provider pull, so a slider
    # drag stays free and a date outside the cache is reported, not fetched.
    start: str | None = None
    end: str | None = None
    cost_bps: float = 2.0


class _IndicatorBody(BaseModel):
    """Retuned indicator periods for the per-pane gear panels.

    `params` overrides `score_engine.INDICATOR_DEFAULTS` (EMA/BB/ATR/RSI/CCI/
    MACD); `elmo` overrides `elmo.DEFAULTS`. Unknown keys are ignored by the
    engines rather than rejected, so a panel can offer a knob the server has
    not shipped yet without 422-ing the whole request.
    """

    params: dict[str, Any] = {}
    elmo: dict[str, Any] = {}


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

    # --- deferred flow ----------------------------------------------------
    # A full options tape is five session dates paginated 10k rows at a time.
    # Called inline from GET /api/state it timed the request out (measured:
    # >60s on SPY 15m), and the page polls that endpoint every 5 seconds.
    # So the first ask starts a background thread and returns `None`, which
    # `snapshot.build_state` reads as "loading" and does not cache; a later
    # poll finds the finished rows. One fetch per window, never a stampede --
    # `_flow_pending` is what stops each 5-second poll launching another.
    _flow_lock = threading.Lock()
    _flow_done: dict[tuple, list] = {}
    _flow_pending: set[tuple] = set()

    def _deferred_flow(root, start_dt, end_dt, min_premium):
        if flow_fn is None:
            return []
        key = (root, str(start_dt), str(end_dt))
        with _flow_lock:
            if key in _flow_done:
                return _flow_done[key]
            if key in _flow_pending:
                return None
            _flow_pending.add(key)

        def _work() -> None:
            try:
                rows = flow_fn(root, start_dt, end_dt, min_premium)
            except Exception:  # noqa: BLE001 — degrade; never fabricate flow
                rows = []
            with _flow_lock:
                _flow_done[key] = list(rows or [])
                _flow_pending.discard(key)
                # Bound the memory: a long session switching symbols would
                # otherwise retain every tape it ever pulled.
                if len(_flow_done) > 12:
                    for stale in list(_flow_done)[:-12]:
                        _flow_done.pop(stale, None)

        threading.Thread(target=_work, daemon=True, name="chart-flow").start()
        return None

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
            flow_fn=_deferred_flow if flow_fn is not None else None,
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
        """Pull history for the current symbol/interval into the bar cache.

        Returns the failure instead of raising it. A provider timeout (a
        coarse interval is aggregated from a lot of one-minute rows, so it
        can genuinely time out) used to escape as a bare 500 "Internal
        Server Error", which the page had no way to distinguish from an
        interval that simply has no data -- both left an empty chart with no
        explanation. Now the caller gets the reason and can say so.
        """
        try:
            upserted = refresh_cache(
                cache,
                session["ticker"],
                session["interval"],
                body.lookback,
                daily_fn=daily_fn,
                intrad_fn=intrad_fn,
            )
        except Exception as exc:  # noqa: BLE001 -- reported to the caller
            return {
                "ok": False,
                "upserted": 0,
                "error": f"{type(exc).__name__}: {exc}",
                "ticker": session["ticker"],
                "interval": session["interval"],
            }
        session["flow_cache"].clear()
        # Report the span the cache now holds, so a "load history" caller can
        # rebind its date pickers without a second round trip -- and can see
        # that a deeper pull actually reached further back rather than just
        # topping up the recent end.
        held = cache.load(session["ticker"], session["interval"])
        return {
            "ok": True,
            "upserted": upserted,
            "ticker": session["ticker"],
            "interval": session["interval"],
            "cache_span": {
                "start": held[0].timestamp.date().isoformat() if held else None,
                "end": held[-1].timestamp.date().isoformat() if held else None,
                "bars": len(held),
            },
        }

    @app.post("/api/backtest")
    def post_backtest(body: _BacktestBody) -> dict[str, Any]:
        """Re-run the engine on the loaded bars with one-off overrides.

        This is the live tester behind the chart's parameter panel: move a
        slider, see the marks and the P&L move. It reads the SAME bar cache
        the chart is drawing, so what you are scoring is exactly what is on
        screen -- no refetch, no provider call, nothing billed.

        Two different runs are returned on purpose:

        - `metrics` / `trades` come from `backtest.run_backtest`, which fills
          at the NEXT bar's open and charges costs. That is the honest P&L.
        - `actions` / `score` / `stop` come from the live state machine, which
          records a decision on the bar that triggered it. That is what the
          chart should draw.

        Conflating them would mean either the marks sit a bar late or the P&L
        assumes a fill nobody could get.
        """
        from chart_app.backtest import run_backtest
        from chart_app.elmo import compute_elmo
        from chart_app.flow_pane import bin_flow
        from chart_app.flow_stamp import flow_observed_bars
        from chart_app.signal_engine import evaluate

        records = cache.load(session["ticker"], session["interval"])
        loaded = len(records)
        if loaded < 60:
            return {"ok": False, "error": f"need >=60 bars, have {loaded}"}

        # The span that EXISTS, before any narrowing. Published so the date
        # pickers can bound themselves to it: typing a wider range silently
        # intersects back to this, which reads as "my dates were ignored".
        cache_span = {
            "start": records[0].timestamp.date().isoformat(),
            "end": records[-1].timestamp.date().isoformat(),
            "bars": loaded,
            "interval": session["interval"],
            # No longer capped: the old 30-day intraday ceiling was httpx's
            # 5s default, not the provider (CLAUDE.md). Kept as a flag only to
            # note that intraday still CHUNKS, so a deep pull is slow.
            "intraday_chunked": session["interval"] != "1d",
        }

        # Narrow to the requested dates. Reported back as `window` either way,
        # because "I asked for 2024 and got what the cache had" has to be
        # visible -- silently scoring a different span than the one typed is
        # how a tuning result stops meaning anything.
        if body.start or body.end:
            lo = body.start or "0000-01-01"
            hi = body.end or "9999-12-31"
            records = [r for r in records if lo <= r.timestamp.date().isoformat() <= hi]
            if len(records) < 60:
                first = cache.load(session["ticker"], session["interval"])
                span = (
                    f"{first[0].timestamp.date()} to {first[-1].timestamp.date()}"
                    if first else "empty"
                )
                return {
                    "ok": False,
                    "error": (
                        f"need >=60 bars in {lo}..{hi}, have {len(records)}. "
                        f"Cache holds {loaded} bars, {span}."
                    ),
                }

        # Reuse whatever flow the chart already has; never trigger a pull from
        # a slider drag.
        flow_net = None
        flow_observed = None
        for (tkr, itv, _a, _b), rows in list(session["flow_cache"].items()):
            if tkr == session["ticker"] and itv == session["interval"] and rows:
                flow_net = bin_flow(records, rows)["net"]
                # Which bars the flow pull actually covered. Required: the pull
                # reaches a handful of session dates while the scored window
                # can be a year, so without this the whale weight dilutes every
                # uncovered bar (see backtest.run_backtest).
                flow_observed = flow_observed_bars(records, rows)
                break

        # Inherit the active profile, then let the request override it. Without
        # this, an empty `elmo` fell back to SHIPPED defaults -- so a chart
        # running a saved profile was scored against different indicator reads
        # than the ones it was drawing, and the P&L belonged to a chart nobody
        # was looking at. Indicator periods come from the gears, levels from
        # the tester; both land here already merged over the profile.
        from chart_app.profiles import resolve as resolve_profile

        active = resolve_profile(session["ticker"], session["interval"])
        elmo_cfg = {**(active.get("elmo") or {}), **body.elmo}
        signal_cfg = {**(active.get("config") or {}), **body.config}

        try:
            elmo = compute_elmo(records, **elmo_cfg)
            conv, run = evaluate(
                records,
                elmo=elmo,
                flow_net=flow_net,
                flow_observed=flow_observed,
                config=signal_cfg,
            )
            result = run_backtest(
                records,
                interval=session["interval"],
                config=signal_cfg,
                flow_net=flow_net,
                flow_observed=flow_observed,
                cost_bps=body.cost_bps,
                elmo_overrides=elmo_cfg,
                capital=body.capital,
            )
        except Exception as exc:  # noqa: BLE001 -- reported to the caller, never a 500
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        return {
            "ok": True,
            "ticker": session["ticker"],
            "interval": session["interval"],
            "metrics": result.metrics,
            "window": {
                "start": records[0].timestamp.date().isoformat(),
                "end": records[-1].timestamp.date().isoformat(),
                "bars": len(records),
                "loaded_bars": loaded,
                "narrowed": len(records) != loaded,
            },
            # NOT "available" -- that key is already the conviction engine's
            # per-component availability dict in this same response, and a
            # duplicate silently clobbered one of the two.
            "cache_span": cache_span,
            "trades": result.trades,
            "equity": result.equity,
            # Drawn by the chart.
            "score": conv.score,
            "actions": run.actions,
            "position": run.position,
            "stop": run.stop,
            "atr": conv.atr,
            "components": conv.components,
            "available": conv.available,
            "elmo": elmo.as_dict(),
            "config": signal_cfg,
            # Provenance: which profile fed this run, and the indicator reads it
            # scored against. The panel shows levels only, so without this there
            # is no way to tell which ELMo settings produced the number.
            "elmo_config": elmo_cfg,
            "profile_source": active.get("source"),
        }

    @app.post("/api/indicators")
    def post_indicators(body: _IndicatorBody) -> dict[str, Any]:
        """Recompute the overlays/oscillators with retuned periods.

        Backs the per-pane gear panels. Like `/api/backtest` it reads the bar
        cache the chart is already drawing, so a slider drag is pure CPU --
        no refetch, no provider call, nothing billed.
        """
        from chart_app.elmo import compute_elmo
        from chart_app.score_engine import (
            INDICATOR_DEFAULTS,
            classic_overlays,
            oscillators,
        )

        records = cache.load(session["ticker"], session["interval"])
        if not records:
            return {"ok": False, "error": "no bars loaded"}
        try:
            payload: dict[str, Any] = {
                "ok": True,
                "ticker": session["ticker"],
                "interval": session["interval"],
                "overlays": classic_overlays(records, body.params),
                "oscillators": oscillators(records, body.params),
                "defaults": INDICATOR_DEFAULTS,
                "params": body.params,
            }
            if body.elmo:
                payload["elmo"] = compute_elmo(records, **body.elmo).as_dict()
        except Exception as exc:  # noqa: BLE001 -- reported, never a 500
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return payload

    @app.get("/api/profiles")
    def get_profiles() -> dict[str, Any]:
        """Every stored profile, plus the one active for the loaded symbol."""
        from chart_app import profiles

        return {
            "ok": True,
            "profiles": profiles.load_all(),
            "active": profiles.resolve(session["ticker"], session["interval"]),
            "validations": list(profiles.VALIDATIONS),
        }

    @app.post("/api/profiles")
    def post_profile(body: _ProfileBody) -> dict[str, Any]:
        """Save a profile. Takes effect on the LIVE chart at the next poll.

        No provider call: the next `/api/state` rebuilds from cached bars with
        the profile applied, so saving is as free as dragging the slider was.
        """
        from chart_app import profiles

        try:
            record = profiles.save(
                body.ticker if body.ticker else session["ticker"],
                body.interval if body.interval else session["interval"],
                config=body.config,
                elmo=body.elmo,
                capital=body.capital,
                validation=body.validation,
                metrics=body.metrics,
                note=body.note,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "saved": record}

    @app.delete("/api/profiles")
    def delete_profile(ticker: str | None = None, interval: str | None = None) -> dict[str, Any]:
        from chart_app import profiles

        removed = profiles.delete(
            ticker if ticker else session["ticker"],
            interval if interval else session["interval"],
        )
        return {"ok": True, "removed": removed}

    @app.get("/api/indicator-defaults")
    def get_indicator_defaults() -> dict[str, Any]:
        """Every knob the gear panels can show, with its shipped value.

        Served rather than duplicated in JavaScript so a new tunable appears
        in the UI the moment it exists on the server -- the panel cannot drift
        out of sync with what the engine actually reads.
        """
        from chart_app.elmo import DEFAULTS as ELMO_DEFAULTS
        from chart_app.score_engine import INDICATOR_DEFAULTS
        from chart_app.signal_engine import DEFAULTS as SIGNAL_DEFAULTS
        from chart_app.signal_engine import WEIGHTS

        return {
            "indicators": INDICATOR_DEFAULTS,
            "elmo": {k: v for k, v in ELMO_DEFAULTS.items() if not isinstance(v, str)},
            "signal": {
                k: v for k, v in SIGNAL_DEFAULTS.items() if not isinstance(v, str)
            },
            "weights": WEIGHTS,
        }

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


def _flow_days_from_env(default: int = 5) -> int:
    """How many session dates one refresh may pull flow for.

    Each day is a paginated `scanner_trades` sweep of the full tape, so this
    is the knob that trades billed provider calls against how far back the
    whale dots reach. Raise it when you want deeper coverage on a 30d chart
    and are willing to pay for the pulls.
    """
    raw = os.environ.get("CHART_APP_FLOW_DAYS")
    if not raw:
        return default
    try:
        return max(1, min(30, int(raw)))
    except ValueError:
        return default


def _flow_min_premium_from_env(default: float = 10_000.0) -> float:
    """Server-side premium floor for the flow pull.

    This is the single most important number in the whale path. Measured on
    SPY for 2026-09-17: the full tape is **1,387,630 prints in one day**, and
    the distribution is overwhelmingly dust.

        floor        rows kept   premium kept
        >=   $1,000    18.78%       94.98%
        >=  $10,000     1.94%       82.36%
        >=  $25,000     0.69%       77.32%

    So a $10k floor discards 98% of the rows and keeps 82% of the premium,
    and every whale-sized print is still in the result. Lower it toward $1,000
    when you want the flow pane's net-premium series to be near-exact and are
    willing to pay ~10x the rows for the last 13 percentage points.
    """
    raw = os.environ.get("CHART_APP_FLOW_MIN_PREMIUM")
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


# One cache entry per (root, session date). Keyed by DAY, not by chart window:
# the window key includes the last bar's timestamp, so every new bar used to
# invalidate the whole cache and re-pull all five sessions. A day, once
# fetched, never changes -- except today's, which is dropped on each refresh
# by `_flow_day_cache_drop_today`.
_FLOW_DAY_CACHE: dict[tuple, list] = {}
_FLOW_DAY_CACHE_MAX = 40


def _production_flow_fn(root, start_dt, end_dt, min_premium):
    """`scanner_trades`, paginated, once per session date — never per bar.

    Two bugs fixed here on 2026-09-18, which together are the whole of "whale
    symbols hardly ever show up":

    1. **`min_premium=0`.** The docstring used to read "always full tape
       (passed arg ignored)". On SPY that is 1.39M prints a day and **402
       seconds** to page down; five sessions is over half an hour, so the
       fetch never finished inside any poll on ANY timeframe. It now passes a
       real floor (`_flow_min_premium_from_env`).

    2. **`if not days or len(days) > 5: return []`.** Every chart lookback
       except 5d spans more than five weekdays (10m=10d, 30m=20d, 1h/4h=30d,
       1d=1y), so on five of the eight timeframes this returned an empty list
       before making a single call — not "no whales today", but "never
       asked". It now takes the most recent `CHART_APP_FLOW_DAYS` sessions of
       the window, and `flow_stamp.whale_coverage` reports sessions-covered
       against sessions-charted so a partly-stamped chart says so on screen.

    Still never called per bar, and still single-threaded: the repo rule is
    that ThetaData pulls are serialised, and a concurrent second caller here
    reliably drove the first into `PHTimeoutError` while this was measured.
    """
    from datetime import datetime, timedelta

    from chart_app.crypto_source import is_crypto
    from chart_app.flow_stamp import rows_from_flow_payload
    from Direction.indicator import _thread_client

    # There is no US options tape for a perp. Asking anyway burns a provider
    # call per session date to be told so.
    if is_crypto(root):
        return []

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
    if not days:
        return []
    # Most recent sessions first-priority: a partly-covered chart should be
    # stamped at the right-hand edge, where the user is looking.
    days = days[-_flow_days_from_env() :]
    client = _thread_client()
    floor = _flow_min_premium_from_env()
    today = datetime.now().date()
    rows: list = []
    LIMIT = 10000
    for day in days:
        key = (root, day.isoformat(), floor)
        # Today's session is still accumulating prints, so it is never served
        # from cache. Every earlier day is final.
        if day != today and key in _FLOW_DAY_CACHE:
            rows.extend(_FLOW_DAY_CACHE[key])
            continue
        ymd = day.strftime("%Y%m%d")
        offset = 0
        day_rows: list = []
        while True:
            try:
                env = client.flow.scanner_trades(
                    root=root,
                    start_date=ymd,
                    end_date=ymd,
                    min_premium=floor,
                    limit=LIMIT,
                    offset=offset,
                )
                page = rows_from_flow_payload(getattr(env, "data", None))
                day_rows.extend(page)
                if len(page) < LIMIT:
                    break
                offset += LIMIT
            except Exception:  # noqa: BLE001 — per-day degrade; never raise
                # Keeps the original contract.
                # A partial day is still stamped, and `whale_coverage` is what
                # tells the user the chart is only partly covered.
                break
        if day != today:
            _FLOW_DAY_CACHE[key] = day_rows
            if len(_FLOW_DAY_CACHE) > _FLOW_DAY_CACHE_MAX:
                for stale in list(_FLOW_DAY_CACHE)[:-_FLOW_DAY_CACHE_MAX]:
                    _FLOW_DAY_CACHE.pop(stale, None)
        rows.extend(day_rows)
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
