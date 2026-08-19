# Direction/indicator.py
"""Per-bar price signals for the v2 direction indicator.

Task 2 of the v2 plan: evaluate the three price-series signals -- Elliott
wave3, Bollinger squeeze, multi-TF trend -- on the intraday series as of a
bar timestamp, using ONLY the PURE functions of the Direction modules
(``count_waves``, ``get_bands``/``detect_squeeze``, ``adx``/``ma_alignment``).
The module entry points (``analyze``/``analyze_trend``) are intentionally
NOT used, and ``elliott_wave.py``/``bollinger_analyzer.py``/``trend_engine.py``
are not modified.

Task 3 of the v2 plan: the per-bar intraday whale-flow signal
(``_flow_signal``).  Intraday option-flow data comes from the PH v2 SDK's
``flow.scanner_trades_in_time_range`` (queried via a direct ``PHClient`` --
``shared/thetadata.py`` does not expose the flow namespace yet).  This breaks
the v1 "daily wall": the v1 suite judged whale flow from daily EOD option
volume, whereas v2 judges each bar's own 15-minute window.  ``whale_scanner.py``
is NOT touched; the v1 daily tool keeps its own path.

Task 4 of the v2 plan: the per-bar liquidity signal (``_liquidity_signal``).
Intraday dealer-gamma regime comes from the PH v2 SDK's
``dealer.weighted_greeks`` (``retry_policy=unsafe``, risk MEDIUM), so it is
SAMPLED on a coarse grid (every N bars) rather than called per bar -- one
sequential call per grid point, never fanned out (the 502-storm pitfall).
EOD OI max-pain stays daily (OI settles EOD by nature) and reuses the pure
helpers of ``liquidity_map.py`` (``_pick_expiry``/``_max_pain``), which is
NOT modified; the v1 daily entry point ``get_liquidity`` keeps its own path.

Task 5 of the v2 plan: the per-bar score composition (``bar_eval``).  The
three Task 2-4 legs are merged per bar and scored with the SAME rule
``signal_generator.generate`` uses -- score = number of True signals among
the five (whale, wave3, squeeze, trend, liquidity); HIGH = whale AND wave3
AND (squeeze OR trend) AND score >= 3; MEDIUM = whale AND score >= 3 AND
not HIGH; NONE = otherwise -- replicated VERBATIM from that module (the one
place v2 mirrors the tool, by explicit scope design).  ``signal_generator.py``
is NOT modified.

Every bar of a chart is evaluated with the series as it existed up to that
bar (closed-bar semantics via ``shared.spot_history.intraday_bars_as_of``),
so verdicts move bar-to-bar -- the whole point of v2.

Honest-data contract (what v2 claims -- and does NOT claim):

* Closed-bar semantics: each bar is evaluated with data as of its bar
  timestamp INCLUSIVE (``intraday_bars_as_of``), so a verdict never uses
  information from after the bar closed.
* Whale flow is intraday: the per-bar leg queries PH v2
  ``flow.scanner_trades_in_time_range`` for the bar's own 15-minute window,
  not the v1 daily EOD option-volume wall.
* Dealer gamma is SAMPLED, not per-bar: ``dealer.weighted_greeks`` is heavy
  (retry_policy=unsafe / risk MEDIUM) so it is called once per coarse-grid
  point (every N bars, sequential) and the last computed regime is HELD
  neutral between grid points -- never interpolated as fact, never
  fabricated.
* OI max-pain remains EOD: OI settles daily by nature, so the liquidity
  leg's max-pain input is the bar's YYYYMMDD EOD value, reused per bar.
* Unfetchable per-bar data degrades to neutral, never guesses: any leg that
  cannot fetch its per-bar input (flow timeout, gamma failure, missing
  bars) contributes NONE/0/False to the bar's score instead of a made-up
  value.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

import numpy as np
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

from shared.chart_data import CandleRecord
from shared.config import load_env_once
from shared.spot_history import intraday_bars_as_of

from . import bollinger_analyzer as boll
from . import elliott_wave as ew
from . import trend_engine as te

# Same thresholds the module entry points use:
#   bollinger_analyzer.analyze: detect_squeeze default threshold_pct=0.02
#   trend_engine.analyze_trend: adx_ok = daily["adx"] > 25
_ADX_TREND_THRESHOLD = 25.0

# Below this many bars nothing meaningful computes (count_waves needs >= 3;
# bands need 20; MA50 needs 50) -- degrade to neutral instead of fabricating.
_MIN_BARS = 3

# Whale-flow premium threshold ($ of per-print gross premium) for the
# intraday whale signal.  Reused from the v1 threshold constant --
# Direction.whale_scanner re-exports Vol_Suite's WHALE_THRESHOLD (25k) --
# per the Task-3 brief ("reuse the v1 threshold constant if importable from
# whale_scanner WITHOUT touching it").  If that import ever breaks, fall back
# to the brief's documented default.
try:
    from .whale_scanner import WHALE_THRESHOLD as WHALE_PREMIUM
except Exception:  # pragma: no cover - import path is stable; defensive only
    WHALE_PREMIUM = 100_000.0

OHLCVFn = Callable[[str, str, object], list[CandleRecord]]

# flow_fn contract: flow_fn(root, start_datetime, end_datetime, min_premium)
# -> list[dict] of scanner flow rows ({"premium": $, "timestamp": ISO, ...}).
FlowFn = Callable[[str, object, object, float], list[dict]]

# Candidate timestamp keys on a scanner flow row, most specific first.
_ROW_TS_KEYS = ("timestamp", "detection_timestamp", "execution_timestamp")


def _interval_to_delta(interval: str) -> timedelta:
    """Parse a bar interval like ``"15m"`` / ``"2h"`` into a timedelta.

    Unparseable intervals fall back to 15 minutes (documented default) so a
    weird interval string never crashes per-bar evaluation.
    """
    s = (interval or "").strip().lower()
    if s.endswith("h"):
        try:
            return timedelta(hours=float(s[:-1]))
        except ValueError:
            pass
    if s.endswith("m"):
        try:
            return timedelta(minutes=float(s[:-1]))
        except ValueError:
            pass
    return timedelta(minutes=15)


def _as_datetime(ts) -> datetime:
    """Resolve bar_ts (datetime or ISO string) to a datetime."""
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        return datetime.fromisoformat(ts)
    raise TypeError(f"bar_ts must be a datetime or ISO string, got {type(ts).__name__}")


def _row_ts(row: dict) -> datetime | None:
    """Extract a row's trade timestamp; None when absent/unparseable.

    Rows without a parseable timestamp are treated as in-window downstream
    (the provider was asked for the window; we don't fabricate exclusions).
    """
    for key in _ROW_TS_KEYS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            continue
    return None


def _to_plain(obj):
    """Coerce PHClient FrozenMap/tuple leaves into plain dicts/lists."""
    if isinstance(obj, Mapping):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


# PHClient is not safe to share across threads (see the facade's comment in
# shared/thetadata.py), so each thread gets its own client built from the
# same config -- mirroring the facade's pattern for the flow namespace.
_thread_local = threading.local()


def _thread_client() -> PHClient:
    """Build (once per thread) a PH v2 client with flow.read capability.

    Credentials come from THETADATA_CF_ACCESS_CLIENT_ID/_SECRET env vars (or
    the root .env via shared.config.load_env_once), exactly like the facade.
    Raises RuntimeError when credentials are missing -- _flow_signal's caller
    degrades on that.
    """
    client = getattr(_thread_local, "client", None)
    if client is None:
        load_env_once()
        client_id = os.environ.get("THETADATA_CF_ACCESS_CLIENT_ID")
        client_secret = os.environ.get("THETADATA_CF_ACCESS_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "ThetaData credentials not found. Set THETADATA_CF_ACCESS_CLIENT_ID "
                "and THETADATA_CF_ACCESS_CLIENT_SECRET as environment variables, or "
                "create a .env file in the project root (see .env.example)."
            )
        cred = Credential({
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        })
        config = ClientConfig(
            base_url="https://api.potatohedge.com",
            credentials={"flow.read": cred, "dealer.bulk.read": cred},
            caller_id="zinko",
            client_version="2",
        )
        client = PHClient(config)
        client.__enter__()
        _thread_local.client = client
    return client


def _default_flow_fn(root: str, start_dt, end_dt, min_premium: float) -> list:
    """Production flow provider: PH v2 ``flow.scanner_trades_in_time_range``.

    One sequential call for the bar's window (per the v2 wiki's concurrency
    note -- do NOT fan out flow.* calls).  Returns the envelope's ``.data`` as
    plain dicts.  Failures propagate to _flow_signal, which degrades.
    """
    client = _thread_client()
    env = client.flow.scanner_trades_in_time_range(
        root=root,
        start_datetime=(start_dt.isoformat()
                        if hasattr(start_dt, "isoformat") else str(start_dt)),
        end_datetime=(end_dt.isoformat()
                      if hasattr(end_dt, "isoformat") else str(end_dt)),
        min_premium=min_premium,
    )
    data = getattr(env, "data", None)
    if not isinstance(data, list):
        return []
    return [_to_plain(row) for row in data]


def _flow_signal(ticker, bar_ts, interval: str = "15m",
                 flow_fn: FlowFn | None = None) -> dict:
    """Evaluate the intraday whale-flow signal for the bar closed at ``bar_ts``.

    Returns ``{"whale": bool}`` where ``whale`` is True iff any flow row in
    the bar's own window ``[bar_ts - interval, bar_ts]`` (closed at ``bar_ts``
    -- a trade exactly at the close counts) carries ``premium >=
    WHALE_PREMIUM``.  The window and the premium filter are passed to the
    provider, which returns the window's scanner rows; rows are additionally
    screened by their timestamp so an out-of-window row can never light the
    signal (a row with no parseable timestamp is trusted as in-window since
    the provider was asked for the window).

    ``flow_fn`` is injectable for deterministic, network-free tests and
    defaults to ``_default_flow_fn`` (PH v2 ``flow.scanner_trades_in_time_range``
    via a direct per-thread ``PHClient``).  It is called as
    ``flow_fn(root, start_datetime, end_datetime, min_premium)``.

    Degrades to ``{"whale": False}`` -- NEVER raises, NEVER fabricates -- on
    any provider failure (missing credentials, timeout, PHClientError), empty
    rows, or rows that don't clear the premium bar.
    """
    provider = flow_fn if flow_fn is not None else _default_flow_fn
    try:
        end = _as_datetime(bar_ts)
        start = end - _interval_to_delta(interval)
        rows = provider(ticker, start, end, WHALE_PREMIUM)
    except Exception:
        rows = []

    whale = False
    for row in rows or []:
        try:
            if float(row.get("premium")) < WHALE_PREMIUM:
                continue
            ts = _row_ts(row)
            if ts is not None and not (start <= ts <= end):
                continue
        except (TypeError, ValueError, AttributeError):
            continue
        whale = True
        break
    return {"whale": whale}


def _price_signals(ticker, bar_ts, interval: str = "15m",
                   ohlcv_fn: OHLCVFn | None = None) -> dict:
    """Evaluate the three price-series signals on the intraday series as of
    ``bar_ts``.

    Returns ``{"wave3": bool, "squeeze": bool, "trend": bool}`` where:
      wave3   -- ``count_waves(closes)["wave_type"] == "impulse_wave_3"``
                 (the strongest swing is wave 3; same definition
                 ``elliott_wave.analyze`` uses for its ``signal`` key).
      squeeze -- ``detect_squeeze(get_bands(closes))``: latest band width
                 below 2% of price (default threshold, as the entry point).
      trend   -- ADX > 25 AND ``ma_alignment`` == "bullish" (price >
                 ma20 > ma50); the intraday single-timeframe analogue of
                 ``trend_engine.analyze_trend``'s ``adx_ok`` + alignment.

    ``ohlcv_fn`` is injectable for deterministic, network-free tests and
    defaults to ``shared.spot_history.intraday_bars_as_of`` (bars with
    ``timestamp <= bar_ts``, RTH-filtered, ascending). It is called as
    ``ohlcv_fn(ticker, interval, bar_ts)``.

    Degrades to all-False -- NEVER raises, NEVER fabricates -- when the
    provider fails, returns no bars, or the series is too short for the
    indicator in question (bands need >= 20 bars, MA50 needs >= 50; each
    pure function already reports neutral on short input).
    """
    provider = ohlcv_fn if ohlcv_fn is not None else intraday_bars_as_of
    try:
        bars = provider(ticker, interval, bar_ts)
    except Exception:
        bars = []
    if not bars or len(bars) < _MIN_BARS:
        return {"wave3": False, "squeeze": False, "trend": False}

    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]

    wave3 = ew.count_waves(closes).get("wave_type") == "impulse_wave_3"
    squeeze = boll.detect_squeeze(boll.get_bands(closes))

    adx_value = te.adx(highs, lows, closes)
    ma20 = (np.convolve(closes, np.ones(20) / 20, mode="valid")
            if len(closes) >= 20 else np.array([]))
    ma50 = (np.convolve(closes, np.ones(50) / 50, mode="valid")
            if len(closes) >= 50 else np.array([]))
    alignment = te.ma_alignment(closes, ma20, ma50)
    trend = adx_value > _ADX_TREND_THRESHOLD and alignment == "bullish"

    return {"wave3": wave3, "squeeze": squeeze, "trend": trend}


# ---------------------------------------------------------------------------
# Task 4 -- per-bar liquidity signal (coarse-grid dealer gamma + EOD OI)
# ---------------------------------------------------------------------------
# The intraday dealer-gamma endpoint (dealer.weighted_greeks) is
# retry_policy=unsafe / risk MEDIUM and the shared proxy 502-storms under
# fan-out, so the regime is SAMPLED on a coarse grid: one sequential call
# per grid point, the last computed value reused between grid points, never
# interpolated-as-fact.  Grid state lives in a per-(ticker, date) module
# store guarded by a lock (or a caller-passed dict for deterministic
# multi-pass evaluation).
#
# gamma_fn contract: gamma_fn(root, bar_dt, interval) -> bool  (True =
# dealer-gamma regime is bullish / negative-supply).  Called ONLY on grid
# points (first call of a session, then every gamma_every_n_bars-th session
# bar).  Failures degrade to neutral and the next grid point retries.
#
# oi_fn contract: oi_fn(root, as_of) -> {"max_pain": float, "price": float}
# (EOD OI max-pain as of the bar's YYYYMMDD date).  Called on every bar --
# its default path goes through Direction.data's in-process cache and OI
# settles EOD by nature.

_LIQUIDITY_GRID_LOCK = threading.Lock()
# (ticker, yyyymmdd) -> {"at_index": int|None, "gamma": bool|None}
_LIQUIDITY_GRID_STATE: dict = {}


def _interval_to_ph_interval(interval: str) -> str:
    """Map a bar interval like ``"15m"`` to the PH v2 interval bucket name
    (``"MINUTE15"``, ``"MINUTE30"``, ``"MINUTE5"``, ``"MINUTE"``, ``"HOUR"``,
    ``"DAY"``).  Unparseable intervals fall back to MINUTE15 (documented
    default), mirroring ``_interval_to_delta``'s fallback."""
    s = (interval or "").strip().lower()
    if s.endswith("m"):
        try:
            minutes = float(s[:-1])
        except ValueError:
            return "MINUTE15"
        if minutes <= 1:
            return "MINUTE"
        if minutes <= 5:
            return "MINUTE5"
        if minutes <= 15:
            return "MINUTE15"
        if minutes <= 30:
            return "MINUTE30"
        return "HOUR"
    if s.endswith("h"):
        return "HOUR"
    if s in ("1d", "d", "day", "days"):
        return "DAY"
    return "MINUTE15"


def _session_bar_index(bar_dt: datetime, interval: str) -> int:
    """Ordinal of ``bar_dt``'s bar within its session day: seconds since
    midnight // interval seconds.  This is what the coarse gamma grid is
    indexed by (deterministic from bar_ts alone, independent of evaluation
    order), so grid points are stable across re-evaluations."""
    interval_secs = int(_interval_to_delta(interval).total_seconds()) or 900
    return (bar_dt.hour * 3600 + bar_dt.minute * 60 + bar_dt.second) // interval_secs


def _gamma_regime_from_rows(data) -> bool | None:
    """Net-gamma regime sign from a ``dealer.weighted_greeks`` payload.

    The endpoint returns CSV-shaped list-of-lists by default
    (``use_csv=True``: a header row followed by data rows) with the columns
    ``strike, expiration, right, date, root, ms_of_day`` always included
    plus whatever ``columns=`` requested -- the documented net-gamma fields
    are ``weighted_gamma`` and ``net_positioning``.  Dict rows
    (``use_csv=False``) are also accepted.  Returns True when the summed
    net-gamma is positive (long-gamma / negative-supply regime), False when
    negative, None when nothing parseable is present (neutral, NOT a
    failure)."""
    if data is None:
        return None
    rows = _to_plain(data) if not isinstance(data, (list, tuple)) else data
    if not rows:
        return None

    total = 0.0
    first = rows[0]
    if isinstance(first, (list, tuple)) and first and all(
            isinstance(h, str) for h in first):
        # CSV shape: header row first.
        headers = [str(h).strip().lower() for h in first]
        col = None
        for name in ("weighted_gamma", "net_positioning"):
            if name in headers:
                col = headers.index(name)
                break
        if col is None:
            return None
        for row in rows[1:]:
            try:
                if col < len(row):
                    total += float(row[col])
            except (TypeError, ValueError, IndexError):
                continue
    else:
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value = row.get("weighted_gamma", row.get("net_positioning"))
            try:
                total += float(value)
            except (TypeError, ValueError):
                continue

    if total > 0:
        return True
    if total < 0:
        return False
    return None


def _default_gamma_fn(root: str, bar_dt: datetime, interval: str = "15m") -> bool:
    """Production gamma provider: PH v2 ``dealer.weighted_greeks``.

    One sequential call for the bar's date and time-of-day bucket (per the
    v2 wiki's concurrency note -- do NOT fan out dealer.* calls).  The
    net-gamma regime sign is read from the documented ``weighted_gamma`` /
    ``net_positioning`` columns.  An empty/neutral payload returns False;
    transport failures propagate to ``_liquidity_signal``, which degrades
    to neutral and retries at the next grid point.
    """
    client = _thread_client()
    env = client.dealer.weighted_greeks(
        root=root,
        start_date=bar_dt.strftime("%Y%m%d"),
        end_date=bar_dt.strftime("%Y%m%d"),
        interval=_interval_to_ph_interval(interval),
        ms_of_day=int((bar_dt.hour * 3600 + bar_dt.minute * 60
                       + bar_dt.second) * 1000),
        columns="weighted_gamma,net_positioning",
    )
    regime = _gamma_regime_from_rows(getattr(env, "data", None))
    return bool(regime)  # None -> False (neutral, not a failure)


def _default_oi_fn(root: str, as_of: str) -> dict:
    """Production OI provider: EOD max-pain as of ``as_of`` (YYYYMMDD).

    Reuses the pure helpers of ``liquidity_map.py`` (``_pick_expiry``,
    ``_max_pain``) over the ``Direction.data`` EOD paths (expirations,
    close-as-of, chain OI) -- exactly the v1 ``get_liquidity`` data flow,
    without touching that module.  Imported lazily so the heavy
    ``Direction.data`` -> facade chain loads only when the default is
    actually used.  Returns ``{}`` (neutral) on any missing piece; failures
    propagate to ``_liquidity_signal``, which degrades.
    """
    from . import data as _data
    from . import liquidity_map

    expirations = _data.get_expirations(root) or []
    exp = liquidity_map._pick_expiry(expirations, as_of=as_of)
    price = _data.get_close_asof(root, as_of=as_of)
    if exp is None or price is None or price <= 0:
        return {}
    chain = _data.get_chain_oi(root, exp, as_of=as_of) or []
    if not chain:
        return {}
    max_pain = liquidity_map._max_pain(chain, price)
    return {"max_pain": float(max_pain), "price": float(price)}


def _v1_max_pain_signal(oi: dict) -> bool:
    """The v1 ``get_liquidity()`` rule, read from that module's logic: the
    liquidity signal is True when max pain sits within 2% of spot (gravity
    proximity).  NOTE: this is NOT the brief's literal "price above
    max-pain" phrasing -- per the brief ("match the v1 rule if it's
    readable") the readable v1 rule is the 2% gravity proximity, and that
    is what v2 reuses; the deviation is documented in the Task 4 report.
    Missing/invalid max pain or price -> False (neutral).
    """
    try:
        max_pain = float(oi.get("max_pain"))
        price = float(oi.get("price"))
    except (TypeError, ValueError, AttributeError):
        return False
    if price <= 0 or max_pain <= 0:
        return False
    return abs(max_pain - price) / price <= 0.02


def _grid_state(ticker: str, date_str: str) -> dict:
    """The module-level (ticker, date) grid state, created on first use."""
    key = (ticker, date_str)
    with _LIQUIDITY_GRID_LOCK:
        st = _LIQUIDITY_GRID_STATE.get(key)
        if st is None:
            st = {"at_index": None, "gamma": None}
            _LIQUIDITY_GRID_STATE[key] = st
        return st


def _liquidity_signal(ticker, bar_ts, interval: str = "15m",
                      gamma_fn=None, oi_fn=None, gamma_every_n_bars: int = 4,
                      *, state: dict | None = None) -> dict:
    """Evaluate the per-bar liquidity signal for the bar closed at ``bar_ts``.

    Returns ``{"liquidity": bool}`` where ``liquidity`` is True iff the
    dealer-gamma regime is bullish (negative-supply) OR the v1 max-pain
    rule fires (max pain within 2% of spot -- the rule readable from
    ``liquidity_map.get_liquidity``).

    The dealer-gamma regime is sampled on a coarse grid (every N bars,
    ``gamma_every_n_bars``, default 4) -- one sequential ``gamma_fn`` call
    per grid point (the first call of a session always computes), with the
    last computed value reused between grid points.  Between grid points
    the signal is NEVER re-computed or fabricated: it carries the last
    sampled regime.  A grid-point failure degrades to neutral (False) and
    is retried at the next grid point; ``_liquidity_signal`` never raises.

    ``gamma_fn`` is injectable for deterministic, network-free tests and
    defaults to ``_default_gamma_fn`` (PH v2 ``dealer.weighted_greeks`` via
    a direct per-thread ``PHClient``).  It is called as
    ``gamma_fn(root, bar_dt, interval)``.

    ``oi_fn`` is injectable and defaults to ``_default_oi_fn`` (EOD OI chain
    as of the bar's date via ``liquidity_map._pick_expiry/_max_pain`` over
    the ``Direction.data`` EOD paths).  It is called as
    ``oi_fn(root, as_of_yyyymmdd)`` on every bar -- OI settles EOD by
    nature, so it is daily, not gridded.

    Grid state lives in a per-(ticker, date) module store by default; pass
    ``state`` to own the grid state explicitly for deterministic
    multi-pass evaluation -- create one dict (``state = {}``) and reuse the
    SAME object across every call of the bar sequence.
    """
    gamma_provider = gamma_fn if gamma_fn is not None else _default_gamma_fn
    oi_provider = oi_fn if oi_fn is not None else _default_oi_fn

    bar_dt = _as_datetime(bar_ts)
    date_str = bar_dt.strftime("%Y%m%d")
    st = state if state is not None else _grid_state(ticker, date_str)

    idx = _session_bar_index(bar_dt, interval)
    n = max(1, int(gamma_every_n_bars))
    first = st.get("at_index") is None
    if first or idx % n == 0:
        try:
            gamma_bull = bool(gamma_provider(ticker, bar_dt, interval))
        except Exception:
            gamma_bull = False  # neutral; retried at the next grid point
        st["gamma"] = gamma_bull
        st["at_index"] = idx

    gamma_bull = st.get("gamma")
    if gamma_bull is None:
        gamma_bull = False

    try:
        oi = oi_provider(ticker, date_str)
    except Exception:
        oi = {}

    return {"liquidity": bool(gamma_bull or _v1_max_pain_signal(oi))}


# ---------------------------------------------------------------------------
# Task 5 -- per-bar score composition + bar_eval
# ---------------------------------------------------------------------------
# bar_eval merges the three Task 2-4 legs (_price_signals/_flow_signal/
# _liquidity_signal) per bar and scores them with the SAME rule
# signal_generator.generate() uses -- replicated VERBATIM from that module
# (read-only reference; the v1 tool is untouched):
#   score   = number of True signals among the five (0-5)
#   HIGH    = whale AND wave3 AND (squeeze OR trend) AND score >= 3
#   MEDIUM  = whale AND score >= 3 AND not HIGH
#   NONE    = otherwise
# The liquidity grid state is threaded across the bar sequence via ONE
# caller-owned state dict per bar_eval call (a fresh session each call), so
# the coarse gamma grid samples the whole sequence deterministically and the
# module-level grid store is never touched by bar_eval.

_SIGNAL_NAMES = ("whale", "wave3", "squeeze", "trend", "liquidity")


def _score_conviction(signals: Mapping) -> tuple[int, str]:
    """The v2 score/conviction rule, replicated verbatim from
    ``signal_generator.generate``: score is the number of True signals among
    the five; HIGH/MEDIUM/NONE use that module's exact constants/thresholds.
    """
    score = sum(1 for v in signals.values() if v)
    if (signals.get("whale") and signals.get("wave3")
            and (signals.get("squeeze") or signals.get("trend"))
            and score >= 3):
        conviction = "HIGH"
    elif signals.get("whale") and score >= 3:
        conviction = "MEDIUM"
    else:
        conviction = "NONE"
    return score, conviction


def _compose_signals(ticker, bar_ts, *, state: dict,
                     interval: str = "15m") -> dict:
    """Default per-bar compose path: merge the three Task 2-4 signal legs.

    Each leg already degrades internally (never raises, never fabricates),
    so the merged dict always carries the five canonical boolean keys.
    ``state`` is the caller-owned liquidity grid state (one dict per
    ``bar_eval`` call) so the coarse gamma grid threads across the sequence.
    """
    signals = {}
    signals.update(_price_signals(ticker, bar_ts, interval=interval))
    signals.update(_flow_signal(ticker, bar_ts, interval=interval))
    signals.update(_liquidity_signal(ticker, bar_ts, interval=interval,
                                     state=state))
    return signals


def bar_eval(ticker, bars, generate_fn=None) -> list[dict]:
    """Evaluate every bar of a chart and return the v2 score/conviction.

    ``bars`` is a list of bar timestamps (datetime or ISO strings,
    ascending).  Returns one entry per bar::

        {"ts": <ISO string>, "score": int (0-5),
         "conviction": "HIGH" | "MEDIUM" | "NONE",
         "signals": {"whale", "wave3", "squeeze", "trend", "liquidity": bool}}

    ``generate_fn`` is injectable for deterministic, network-free tests:
    ``generate_fn(ticker, bar_ts) -> dict`` with any of the five boolean
    signal keys (missing keys count as False, extra keys are ignored).  When
    None (the real path), the internal compose runs the three legs
    ``_price_signals`` + ``_flow_signal`` + ``_liquidity_signal`` per bar,
    threading ONE caller-owned liquidity grid state across the whole
    sequence (a fresh session per call).

    A bar whose generation raises degrades to
    ``{"ts": ..., "score": 0, "conviction": "NONE", "signals": {}}`` --
    never raises, never fabricates; the other bars are unaffected.
    """
    state: dict = {}
    results: list[dict] = []
    for bar_ts in bars:
        try:
            ts = _as_datetime(bar_ts).isoformat()
        except (TypeError, ValueError):
            ts = str(bar_ts)
        try:
            if generate_fn is not None:
                raw = generate_fn(ticker, bar_ts)
            else:
                raw = _compose_signals(ticker, bar_ts, state=state)
        except Exception:
            results.append({"ts": ts, "score": 0, "conviction": "NONE",
                            "signals": {}})
            continue
        signals = {name: bool(raw.get(name, False)) for name in _SIGNAL_NAMES}
        score, conviction = _score_conviction(signals)
        results.append({"ts": ts, "score": score, "conviction": conviction,
                        "signals": signals})
    return results
