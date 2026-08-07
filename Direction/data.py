# Direction/data.py
"""Direction/data.py -- the package's single data-access layer.

Everything in the Direction package gets its market data through this
module, which wraps the project's shared ThetaData client
(``shared.thetadata``). **No yfinance anywhere.**

Contract for consumers:
- Every public fetcher returns ``None`` / empty containers on missing data
  or network failure -- callers must degrade gracefully, never crash.
- OHLCV comes back as a small dict of numpy arrays, oldest -> newest.
- Option-chain helpers return normalized rows: ``{"strike": float,
  "right": "C"|"P", ...}``.
- All fetch results are cached in-process with a short TTL so the five
  modules can share one data pull per run.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

from shared.thetadata import ThetaDataController, strike_from_theta

# ---------------------------------------------------------------------------
# Controller + caching
# ---------------------------------------------------------------------------

_controller: Optional[ThetaDataController] = None

# {cache_key: (expires_at, value)}
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = int(os.environ.get("DIRECTION_DATA_TTL", "300"))  # 5 min


def _get_controller() -> Optional[ThetaDataController]:
    """Build (once) and return the ThetaData controller.

    Returns None if credentials are missing so every public fetcher can
    degrade to an empty result instead of crashing the whole signal run.
    """
    global _controller
    if _controller is None:
        try:
            _controller = ThetaDataController()
        except Exception:
            _controller = None
    return _controller


def _cached(key: str, producer):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = producer()
    _CACHE[key] = (now + _CACHE_TTL, value)
    return value


def _clear_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Equity price data
# ---------------------------------------------------------------------------


def get_price(ticker: str) -> Optional[float]:
    """Latest spot price (last trade fallback handled by the client)."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            return float(c.fetch_spot_price(ticker))
        except Exception:
            return None

    return _cached(f"price:{ticker}", _produce)


def get_ohlcv(ticker: str, lookback_days: int = 180) -> Optional[Dict]:
    """Daily OHLCV history.

    Returns ``{"date": np.ndarray[str], "high": np.ndarray[float],
    "low": ..., "close": ..., "volume": ...}`` oldest -> newest, or None.
    ``lookback_days`` is calendar days; the client paginates internally.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
            start = end - timedelta(days=lookback_days)
            rows = c.hist_stock_eod(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception:
            return None
        if not rows:
            return None
        return _ohlcv_from_rows(rows)

    return _cached(f"ohlcv:{ticker}:{lookback_days}", _produce)


def _date_key(row: Dict) -> str:
    """Pull an ISO YYYY-MM-DD date string out of an EOD row.

    The ThetaData proxy returns the trading date under different keys/formats
    depending on route: 'created' (ISO timestamp) on hist/stock/eod, 'date'
    (YYYYMMDD or ISO) elsewhere.
    """
    raw = str(row.get("created") or row.get("date") or row.get("Date") or "").strip()
    if not raw:
        return ""
    clean = raw.replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    if len(clean) < 8 or not clean[:8].isdigit():
        return ""
    ymd = clean[:8]
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _ohlcv_from_rows(rows: List[Dict]) -> Optional[Dict]:
    """Normalize EOD rows into per-day OHLCV arrays (dedup by date)."""
    by_date: Dict[str, dict] = {}
    for row in rows:
        d = _date_key(row)
        if not d:
            continue
        rec = by_date.setdefault(d, {"high": None, "low": None, "close": None, "volume": 0})
        for f in ("high", "low", "close"):
            try:
                v = float(row.get(f))
            except (TypeError, ValueError):
                v = rec[f]
            if v is not None and (rec[f] is None or f == "close" or (f == "volume")):
                rec[f] = v
        try:
            rec["volume"] = float(row.get("volume", 0) or 0)
        except (TypeError, ValueError):
            pass
    if not by_date:
        return None
    dates = sorted(by_date.keys())
    out = {
        "date": np.array(dates),
        "high": np.array([by_date[d]["high"] for d in dates], dtype=float),
        "low": np.array([by_date[d]["low"] for d in dates], dtype=float),
        "close": np.array([by_date[d]["close"] for d in dates], dtype=float),
        "volume": np.array([by_date[d]["volume"] for d in dates], dtype=float),
    }
    mask = ~np.isnan(out["close"])
    if not mask.any():
        return None
    return {k: v[mask] for k, v in out.items()}


def resample_ohlcv(daily: Dict, freq: str = "W") -> Optional[Dict]:
    """Resample daily OHLCV to weekly ('W') or monthly ('M').

    Pure-numpy date bucketing -- no pandas required. Requires
    ``daily["date"]`` as ISO strings.
    """
    if daily is None or len(daily["date"]) == 0:
        return None
    dates = [datetime.strptime(d, "%Y-%m-%d") for d in daily["date"]]
    buckets = []
    for dt in dates:
        if freq == "W":
            key = (dt - timedelta(days=dt.weekday())).date()
        elif freq == "M":
            key = dt.date().replace(day=1)
        else:
            key = dt.date()
        buckets.append(key)
    out_dates, out_high, out_low, out_close, out_vol = [], [], [], [], []
    i = 0
    while i < len(dates):
        key = buckets[i]
        j = i
        hi, lo = daily["high"][i], daily["low"][i]
        vol = 0.0
        while j < len(dates) and buckets[j] == key:
            hi = max(hi, daily["high"][j])
            lo = min(lo, daily["low"][j])
            vol += float(daily["volume"][j])
            j += 1
        out_dates.append(str(key))
        out_high.append(hi)
        out_low.append(lo)
        out_close.append(daily["close"][j - 1])
        out_vol.append(vol)
        i = j
    return {
        "date": np.array(out_dates),
        "high": np.array(out_high, dtype=float),
        "low": np.array(out_low, dtype=float),
        "close": np.array(out_close, dtype=float),
        "volume": np.array(out_vol, dtype=float),
    }


# ---------------------------------------------------------------------------
# Option chain data
# ---------------------------------------------------------------------------


def get_expirations(ticker: str) -> List[str]:
    """Sorted list of available expiry strings (YYYYMMDD)."""
    c = _get_controller()
    if c is None:
        return []

    def _produce():
        try:
            exps = [str(e) for e in c.list_expirations(ticker)]
            return sorted(exps)
        except Exception:
            return []

    return _cached(f"exps:{ticker}", _produce)


def _normalize_chain_rows(rows: List[Dict]) -> List[Dict]:
    """Normalize bulk rows to ``{"strike": float, "right": "C"|"P"}`` + extras."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        strike = None
        if row.get("strike") is not None:
            try:
                strike = float(row["strike"])
            except (TypeError, ValueError):
                strike = None
            if strike is not None and strike > 1000:  # theta-scaled int
                strike = strike_from_theta(strike)
        if strike is None and row.get("strike_theta") is not None:
            try:
                strike = strike_from_theta(float(row["strike_theta"]))
            except (TypeError, ValueError):
                strike = None
        if strike is None:
            continue
        right = str(row.get("right", "")).upper()
        if right.startswith("C"):
            right = "C"
        elif right.startswith("P"):
            right = "P"
        else:
            cp = str(row.get("call_put", row.get("cp", ""))).upper()
            right = "C" if cp.startswith("C") else "P" if cp.startswith("P") else "?"
        if right == "?":
            continue
        rec = dict(row)
        rec["strike"] = strike
        rec["right"] = right
        for f in ("oi", "open_interest", "volume", "close", "last", "bid", "ask"):
            if row.get(f) is not None:
                try:
                    rec[f] = float(row[f])
                except (TypeError, ValueError):
                    pass
        out.append(rec)
    return out


def get_chain_oi(ticker: str, exp: str) -> Optional[List[Dict]]:
    """Per-strike open interest for one expiry.

    Returns normalized rows ``{"strike", "right", "oi"}`` or None.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            rows = c.option_bulk_oi(ticker, exp)
        except Exception:
            return None
        rows = _normalize_chain_rows(rows)
        for r in rows:
            if "oi" not in r:
                r["oi"] = 0.0
        return rows or None

    return _cached(f"oi:{ticker}:{exp}", _produce)


def _latest_day_rows(rows: List[Dict]) -> List[Dict]:
    """Keep only the rows of the most recent date present."""
    dates = [str(r.get("date", "")) for r in rows if r.get("date")]
    if not dates:
        return []
    latest = max(dates)
    return [r for r in rows if str(r.get("date", "")) == latest]


def get_chain_eod_volume(ticker: str, exp: str, lookback_days: int = 35) -> Optional[List[Dict]]:
    """Latest trading day's volume + close for every contract in one expiry."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
            start = end - timedelta(days=lookback_days)
            rows = c.option_bulk_hist_eod(
                ticker, exp, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception:
            return None
        if not rows:
            return None
        rows = _normalize_chain_rows(rows)
        rows = _latest_day_rows(rows)
        rows = [r for r in rows if "volume" in r]
        return rows or None

    return _cached(f"voleod:{ticker}:{exp}", _produce)


def get_chain_quote(ticker: str, exp: str, strike: float, right: str = "C") -> Optional[Dict]:
    """Latest quote for a single contract (bid/ask) or None."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            row = c.option_snapshot_quote(ticker, exp, strike, right)
        except Exception:
            return None
        if not row:
            return None
        rec = {"strike": strike, "right": right}
        for f in ("bid", "ask", "last", "volume", "open_interest"):
            if row.get(f) is not None:
                try:
                    rec[f] = float(row[f])
                except (TypeError, ValueError):
                    pass
        return rec

    return _cached(f"quote:{ticker}:{exp}:{strike}:{right}", _produce)


def get_dealer_gamma(ticker: str) -> Optional[Dict]:
    """PotatoHedge's vendor dealer-positioning snapshot (latest day)."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
            start = end - timedelta(days=10)
            payload = c.get_dealer_positioning(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                latest_only=True,
            )
        except Exception:
            return None
        if isinstance(payload, dict) and payload:
            return payload
        if isinstance(payload, list) and payload:
            return {"rows": payload}
        return None

    return _cached(f"dealer:{ticker}", _produce)


def clear_cache() -> None:
    """Drop all cached data (used by tests and long-running loops)."""
    _clear_cache()
