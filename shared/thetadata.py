#!/usr/bin/env python3
"""shared/thetadata.py — unified ThetaData client for the entire project.

Merges the two previous separate implementations:
  - Options_Suite/thetadata_controller.py
  - Vol_Suite/thetadata_client.py

This single class replaces both. Old locations are redirect stubs that import
from here for full backward compatibility.

Design decisions:
  - Class name: ThetaDataController (unchanged for backward compat)
  - base_url defaults to https://api.potatohedge.com
  - Uses load_env_once() from shared.config (replaces per-module _load_dotenv_once)
  - Includes ALL methods from both implementations (union of endpoints)
  - Includes Vol_Suite's retry logic: 3 attempts on 404/502/503/504
  - Includes Vol_Suite's concurrency support (_HIST_GREEKS_CONCURRENCY from env)
  - strike_to_theta() and strike_from_theta() are module-level functions
  - Timeout: 60.0s (Vol_Suite's more generous value)
  - _get(path, params=None) is unified (accepts optional params)
  - _get_params() retained as backward-compat wrapper

V2 -> V3 migration note: every public method here wraps exactly one vendor
endpoint behind a stable Python signature (fetch_spot_price, fetch_dividend_yield,
etc.) -- callers across all suites go through this class, never construct
endpoint paths themselves. When the v3 API lands, the endpoint path/parsing
inside each method is the only thing that should need to change; the calling
convention across the project should not. Endpoint-specific quirk-handling
comments below (retry patterns, field-name fallbacks, scaling fixes) are
v2-proxy-specific and are expected to be revisited wholesale at that point,
not preserved for their own sake.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

import httpx
import numpy as _np
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential
from potatohedge.errors import PHClientError

from shared.config import load_env_once

# ---------------------------------------------------------------------------
# Concurrency & retry constants  (from Vol_Suite)
# ---------------------------------------------------------------------------

_HIST_GREEKS_CONCURRENCY = int(os.environ.get("THETADATA_HIST_CONCURRENCY", "8"))

_RETRY_STATUSES = (404, 502, 503, 504)
_RETRY_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def strike_to_theta(k: float) -> int:
    """Convert a dollar strike (e.g. 450.0) to theta integer (e.g. 450000)."""
    return int(round(k * 1000))


def strike_from_theta(k: int) -> float:
    """Convert a theta integer (e.g. 450000) back to a dollar strike (e.g. 450.0)."""
    return k / 1000.0


def _as_of_date(as_of):
    """Resolve an optional as_of (YYYY-MM-DD or YYYYMMDD) to a date (default: today)."""
    if not as_of:
        return datetime.now().date()
    clean = str(as_of).replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        return datetime.now().date()
    try:
        return datetime.strptime(clean, "%Y%m%d").date()
    except ValueError:
        return datetime.now().date()


# ---------------------------------------------------------------------------
# v2 transport shims
# ---------------------------------------------------------------------------


class _Credential(Credential):
    pass


class _ClientConfig(ClientConfig):
    pass


def _freeze_to_python(obj):
    """Recursively convert PHClient FrozenMap/tuple leaves into plain Python
    dicts/lists so downstream callers see the same shapes they got from
    httpx.Response.json()."""
    from collections.abc import Mapping

    if isinstance(obj, Mapping):
        return {k: _freeze_to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_freeze_to_python(v) for v in obj]
    return obj


class _V2Response:
    """Minimal stand-in for the v2 client's ResponseEnvelope payload."""

    def __init__(self, data, status_code=None):
        self._data = data
        # Explicit override for the retry-exhausted-on-PHClientError path,
        # where there is no real payload but the underlying error DID carry
        # a real status (e.g. a persistent 404) that callers like
        # option_hist_eod_single's `if r.status_code == 404: continue` need
        # to see. Without this, every retry-exhausted PHClientError reported
        # status_code=500 regardless of the real status, so a genuine
        # "no data for this contract" 404 fell through the 404 check, then
        # through the no-op raise_for_status(), and only surfaced when
        # .json() finally raised a generic TypeError -- misclassifying an
        # expected data gap as a "genuine error" (see option_bulk_hist_eod's
        # empty/errored split, and the desk-note "v2 payload is None" reports
        # this was masking as dealer-positioning being broken).
        self._status_override = status_code

    def json(self):
        if self._data is None:
            raise TypeError("v2 payload is None")
        return _freeze_to_python(self._data)

    def raise_for_status(self):
        return None

    @property
    def status_code(self):
        if self._status_override is not None:
            return self._status_override
        return 200 if self._data is not None else 500


def _map_ph_error(exc: PHClientError):
    mapped = httpx.HTTPStatusError(
        f"[PHClient {exc.status}] {exc.message}",
        request=None,
        response=None,
    )
    mapped.status_code = exc.status or 500
    return mapped


# Cash index roots -- these have no equity listing, so the stock-quote
# endpoints ThetaDataController otherwise routes everything through always
# come back empty/erroring for one. fetch_spot_price checks this set first
# and routes to the dedicated index endpoints (index_snapshot_quote /
# hist_index_eod) instead. Not exhaustive -- add a root here if a caller
# needs another CBOE-style cash index that isn't listed yet.
_INDEX_ROOTS = frozenset({"SPX", "SPXW", "NDX", "VIX", "RUT", "DJX", "XSP", "OEX"})

_PATH_ALIASES = {
    # exact match first
    "/api/db/dealer_positioning": ("dealer", "positioning", {"use_csv": True}),
    "/api/db/yield_curve": ("market", "yield_curve", {}),
    "/api/volatility/surface_change/{root}": (
        "volatility",
        "surface_change",
        {},
    ),
    "/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}": (
        "options",
        "bulk_hist_option_eod_greeks",
        {},
    ),
    "/api/theta/bulk_hist/option/open_interest/{root}/{exp}": (
        "options",
        "bulk_hist_option_open_interest",
        {},
    ),
    "/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}": (
        "options",
        "bulk_snapshot_option_all_greeks",
        {},
    ),
    "/api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}": (
        "options",
        "bulk_snapshot_option_all_greeks",
        {},
    ),
    "/api/theta/bulk_snapshot/option/open_interest/{root}/{exp}": (
        "options",
        "bulk_snapshot_option_open_interest",
        {},
    ),
    "/api/theta/bulk_snapshot/option/quote/{root}/{exp}": (
        "options",
        "bulk_snapshot_option_quote",
        {},
    ),
    "/api/flow/analysis/{root}": (
        "flow",
        "analysis",
        {},
    ),
    "/api/theta/hist/option/all_greeks/{root}/{exp}/{strike}/{right}": (
        "options",
        "hist_option_all_greeks",
        {},
    ),
    "/api/theta/hist/option/eod/{root}/{exp}/{strike}/{right}": (
        "options",
        "hist_option_eod",
        {},
    ),
    "/api/theta/hist/option/open_interest/{root}/{exp}/{strike}/{right}": (
        "options",
        "hist_option_open_interest",
        {},
    ),
    "/api/theta/hist/stock/dividend/{ticker}": ("market", "stock_dividends", {}),
    "/api/theta/hist/stock/eod/{ticker}": ("market", "stock_eod", {}),
    "/api/theta/hist/stock/eod/{root}": ("market", "stock_eod", {}),
    "/api/theta/hist/stock/ohlc/{ticker}": ("market", "stock_ohlc", {}),
    "/api/theta/hist/stock/ohlc/{root}": ("market", "stock_ohlc", {}),
    "/api/theta/snapshot/index/price/{root}": ("market", "last_index_price", {}),
    "/api/theta/hist/index/eod/{root}": ("market", "index_eod", {}),
    "/api/theta/list/expirations/{root}": ("options", "options_chain", {}),
    "/api/theta/list/strikes/{root}/{exp}": ("options", "options_chain", {}),
    "/api/theta/snapshot/option/quote/{root}/{exp}/{k}/{right}": (
        "options",
        "snapshot_option_quote",
        {},
    ),
    "/api/theta/snapshot/stock/quote/{root}": (
        "market",
        "bulk_snapshot_stock_quote",
        {},
    ),
    "/api/theta/snapshot/stock/trade/{root}": ("market", "snapshot_stock_trade", {}),
}

_compiled_aliases = {}
for pattern, (ns, method, defaults) in _PATH_ALIASES.items():
    regex = "^" + __import__("re").sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", pattern) + "$"
    _compiled_aliases[__import__("re").compile(regex)] = (ns, method, defaults)

_PARAM_ALIASES = {
    "root": "root",
    "exp": "exp",
    "k": "strike",
    "strike_theta": "strike",
    "right": "right",
    "ticker": "root",
    "start_date": "start_date",
    "end_date": "end_date",
    "start_datetime": "start_datetime",
    "end_datetime": "end_datetime",
    "date": "date",
    "use_csv": "use_csv",
    "adjusted": "adjusted",
    "venue": "venue",
    "annual_div": "annual_div",
    "rate": "rate",
    "rate_value": "rate_value",
    "under_price": "under_price",
    "signal_date": "signal_date",
    "level": "level",
    "latest_only": "latest_only",
    "positioning_days": "positioning_days",
    "max_dte": "max_dte",
    "interval_type": "interval_type",
    "ms_of_day": "ms_of_day",
    "force_refresh": "force_refresh",
    "min_score": "min_score",
    "category": "category",
    "sentiment": "sentiment",
    "has_large_blocks": "has_large_blocks",
    "has_sweep_activity": "has_sweep_activity",
    "limit": "limit",
    "sort_by": "sort_by",
    "min_premium": "min_premium",
    "min_contracts": "min_contracts",
    "min_exchanges": "min_exchanges",
    "min_sweeps": "min_sweeps",
    "min_size": "min_size",
    "max_size": "max_size",
    "trade_type": "trade_type",
    "option_type": "option_type",
    "moneyness": "moneyness",
    "min_dte": "min_dte",
    "max_strike_distance": "max_strike_distance",
    "min_score_delta": "min_score_delta",
    "sort_order": "sort_order",
    "offset": "offset",
    "lookback": "lookback",
    "min_severity": "min_severity",
    "min_unusual_days": "min_unusual_days",
    "window": "window",
    "direction": "direction",
    "lookback_sessions": "lookback_sessions",
    "source_roots": "source_roots",
    "min_volume": "min_volume",
    "signal_type": "signal_type",
    "aggregate_by": "aggregate_by",
    "include_execution_metrics": "include_execution_metrics",
    "include_variants": "include_variants",
    "include_oi": "include_oi",
    "normalize": "normalize",
    "columns": "columns",
    "use_cache": "use_cache",
    "use_calculated_greeks": "use_calculated_greeks",
    "positioning_start_date": "positioning_start_date",
    "positioning_end_date": "positioning_end_date",
    "rth": "rth",
    "start_time": "start_time",
    "end_time": "end_time",
    "exclusive": "exclusive",
    "req": "req",
    "data_type": "data_type",
    "occ": "occ",
    "symbol": "root",
    "days_back": "days_back",
    "trade_right": "trade_right",
    "strike_window_pct": "strike_window_pct",
    "max_contracts": "max_contracts",
    "mode": "mode",
    "expiry": "expiration",
    "include_chart_data": "include_chart_data",
    "session_date": "session_date",
    "tickers": "tickers",
    "series_transform": "series_transform",
    "min_threshold": "min_threshold",
    "method": "method",
    "metric": "metric",
    "from_date": "from_date",
    "lookback_hours": "lookback_hours",
    "category": "category",
    "include_history": "include_history",
    "min_strength": "min_strength",
    "greek_type": "greek_type",
    "history_contract_version": "history_contract_version",
    "level_type": "level_type",
    "as_of_date": "as_of_date",
    "price_range_pct": "price_range_pct",
    "horizon_min": "horizon_min",
    "analysis_mode": "analysis_mode",
    "baseline_date": "baseline_date",
    "asof_date": "asof_date",
    "min_history": "min_history",
    "expiration": "exp",
    "positioning_days": "positioning_days",
    "interval": "interval",
    "min_ftd_quantity": "min_ftd_quantity",
    "min_volume": "min_volume",
    "include_neutral": "include_neutral",
    "min_premium": "min_premium",
    "min_size": "min_size",
    "max_premium": "max_premium",
    "max_size": "max_size",
    "min_dte": "min_dte",
    "max_dte": "max_dte",
    "min_score_delta": "min_score_delta",
    "sort_order": "sort_order",
    "limit": "limit",
    "offset": "offset",
    "use_csv": "use_csv",
}


def _translate_path(path: str, params: dict):
    p = path.split("?")[0]
    for pattern, (ns, method, defaults) in _compiled_aliases.items():
        m = pattern.match(p)
        if m:
            # Build raw params from path segments (already v2-named thanks
            # to named regex groups), then apply legacy→v2 aliasing + defaults.
            raw = dict(m.groupdict())
            raw.update(params)
            v2_params = _rewrite_params(raw, defaults)
            # Method-specific renames that the generic alias table can't
            # express without breaking other callers.
            if (
                method == "options_chain"
                and "root" in v2_params
                and "ticker" not in v2_params
            ):
                v2_params["ticker"] = v2_params.pop("root")
            if (
                method == "options_chain"
                and "exp" in v2_params
                and "expiration" not in v2_params
            ):
                v2_params["expiration"] = v2_params.pop("exp")
            return ns, method, v2_params
    # option_quote_at_time / option_trade_at_time not yet in v2 client;
    # route to snapshot fallback for now.
    if "/at_time/option/quote/" in p:
        return (
            "options",
            "snapshot_option_quote",
            _rewrite_params(
                params,
                {
                    "root": _param(params, "root"),
                    "exp": _param(params, "exp"),
                    "strike": _param(params, "strike"),
                    "right": _param(params, "right"),
                    "start_date": _param(params, "start_date"),
                    "end_date": _param(params, "end_date"),
                    "ivl": _param(params, "ivl"),
                    "use_csv": False,
                },
            ),
        )
    if "/at_time/option/trade/" in p:
        return (
            "options",
            "snapshot_option_trade",
            _rewrite_params(
                params,
                {
                    "root": _param(params, "root"),
                    "exp": _param(params, "exp"),
                    "strike": _param(params, "strike"),
                    "right": _param(params, "right"),
                    "start_date": _param(params, "start_date"),
                    "end_date": _param(params, "end_date"),
                    "ivl": _param(params, "ivl"),
                    "use_csv": False,
                },
            ),
        )
    if "/at_time/stock/quote/" in p:
        return (
            "market",
            "stock_quote_at_time",
            _rewrite_params(
                params,
                {
                    "root": _param(params, "root"),
                    "start_date": _param(params, "start_date"),
                    "end_date": _param(params, "end_date"),
                    "ivl": _param(params, "ivl"),
                    "use_csv": False,
                },
            ),
        )
    if "/at_time/stock/trade/" in p:
        return (
            "market",
            "stock_trade_at_time",
            _rewrite_params(
                params,
                {
                    "root": _param(params, "root"),
                    "start_date": _param(params, "start_date"),
                    "end_date": _param(params, "end_date"),
                    "ivl": _param(params, "ivl"),
                    "use_csv": False,
                },
            ),
        )
    raise ValueError(f"no v2 mapping for legacy path: {path}")


def _param(params: dict, key: str):
    return params.get(key)


def _rewrite_params(params: dict, defaults: dict) -> dict:
    out = dict(defaults)
    for old, new in _PARAM_ALIASES.items():
        if old in params and new not in out:
            out[new] = params[old]
    # passthrough anything already in v2 form
    for k, v in params.items():
        if k not in out and k in {
            "root",
            "exp",
            "strike",
            "right",
            "start_date",
            "end_date",
            "start_datetime",
            "end_datetime",
            "date",
            "interval",
            "use_csv",
            "adjusted",
            "venue",
            "annual_div",
            "rate",
            "rate_value",
            "under_price",
            "signal_date",
            "level",
            "latest_only",
            "positioning_days",
            "max_dte",
            "interval_type",
            "ms_of_day",
            "force_refresh",
            "data_type",
            "req",
            "occ",
            "mode",
            "max_contracts",
            "strike_window_pct",
            "include_chart_data",
            "session_date",
            "tickers",
            "series_transform",
            "min_threshold",
            "method",
            "metric",
            "from_date",
            "lookback_hours",
            "category",
            "include_history",
            "expiration",
            "min_strength",
            "greek_type",
            "history_contract_version",
            "level_type",
            "as_of_date",
            "price_range_pct",
            "horizon_min",
            "analysis_mode",
            "baseline_date",
            "asof_date",
            "min_history",
            "positioning_start_date",
            "positioning_end_date",
            "rth",
            "start_time",
            "end_time",
            "exclusive",
            "days_back",
            "trade_right",
            "min_ftd_quantity",
            "min_volume",
            "include_neutral",
            "min_premium",
            "min_size",
            "max_premium",
            "max_size",
            "min_dte",
            "max_strike_distance",
            "min_score_delta",
            "sort_order",
            "limit",
            "offset",
            "lookback",
            "min_severity",
            "min_unusual_days",
            "window",
            "direction",
            "lookback_sessions",
            "source_roots",
            "signal_type",
            "aggregate_by",
            "include_execution_metrics",
            "include_variants",
            "include_oi",
            "normalize",
            "columns",
            "use_cache",
            "use_calculated_greeks",
            "ivl",
        }:
            out[k] = v
    return {k: v for k, v in out.items() if v is not None and v != ""}


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ThetaDataController:
    """ThetaData client proxied through api.potatohedge.com (Cloudflare Access).

    Credentials are read from THETADATA_CF_ACCESS_CLIENT_ID /
    THETADATA_CF_ACCESS_CLIENT_SECRET environment variables (or a .env file
    via shared.config.load_env_once).  They are intentionally NOT hardcoded
    here so they can't end up committed to source control.

    If credentials are missing, this raises RuntimeError immediately.  Every
    call site in this project already wraps ThetaDataController() construction
    in a try/except that falls back to yfinance, so a missing/misconfigured
    credential degrades gracefully instead of crashing the app.
    """

    def __init__(self, base_url: str = "https://api.potatohedge.com"):
        load_env_once()
        self.base_url = base_url
        client_id = os.environ.get("THETADATA_CF_ACCESS_CLIENT_ID")
        client_secret = os.environ.get("THETADATA_CF_ACCESS_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise RuntimeError(
                "ThetaData credentials not found. Set THETADATA_CF_ACCESS_CLIENT_ID and "
                "THETADATA_CF_ACCESS_CLIENT_SECRET as environment variables, or create a "
                ".env file in the project root (see .env.example)."
            )
        self._cred = _Credential(
            {
                "CF-Access-Client-Id": client_id,
                "CF-Access-Client-Secret": client_secret,
            }
        )
        self._v2_config = _ClientConfig(
            base_url=base_url,
            credentials={
                "market.read": self._cred,
                "market.bulk.read": self._cred,
                "market.refresh": self._cred,
                "options.read": self._cred,
                "options.bulk.read": self._cred,
                "dealer.read": self._cred,
                "dealer.bulk.read": self._cred,
                "volatility.read": self._cred,
                "volatility.compute": self._cred,
                "flow.read": self._cred,
                "flow.bulk.read": self._cred,
                "signals.read": self._cred,
                "regsho.read": self._cred,
                "support_resistance.read": self._cred,
                "recipes.read": self._cred,
                "news.read": self._cred,
            },
            caller_id="zinko",
            client_version="2",
        )
        # PHClient is not safe to share across threads (confirmed live: concurrent
        # calls on one instance intermittently raise PHRequestValidationError even
        # with valid, individually-working arguments -- looks like shared mutable
        # state in the SDK's request-binding path). The suites fan out per-contract
        # hist calls across a ThreadPoolExecutor (see option_bulk_hist_greeks /
        # option_bulk_hist_oi / _enumerate_contracts), so give each thread its own
        # PHClient built from the same config/credentials instead of one shared
        # instance.
        self._v2_local = threading.local()
        self._v2_all: list[PHClient] = []
        self._v2_lock = threading.Lock()
        self._v2 = self._get_thread_client()

    def _get_thread_client(self) -> PHClient:
        client = getattr(self._v2_local, "client", None)
        if client is None:
            client = PHClient(self._v2_config)
            client.__enter__()
            self._v2_local.client = client
            with self._v2_lock:
                self._v2_all.append(client)
        return client

    # ----- low-level HTTP helpers -----

    def _get(self, path: str, params: dict | None = None) -> "_V2Response":
        """Route a legacy path/params pair through PHClient."""
        ns, method, v2_params = _translate_path(path, params or {})
        client = getattr(self._get_thread_client(), ns)
        env = getattr(client, method)(**v2_params)
        return _V2Response(env.data)

    def _get_params(self, path: str, params: dict) -> "_V2Response":
        """Backward-compat wrapper for callers that used the old separate
        _get_params method.  Delegates to _get."""
        return self._get(path, params=params)

    def _get_with_retry(
        self,
        path: str,
        params: dict | None = None,
        attempts: int = _RETRY_ATTEMPTS,
    ) -> "_V2Response":
        """GET with bounded retry on transient failures.

        Retries on _RETRY_STATUSES and on transport-level errors (connection
        resets / server-disconnects).  Returns the LAST response even if it is
        still an error status — callers decide what a persistent 404 means.

        Backoff is linear (0.5 s, 1.0 s, 1.5 s) rather than exponential:
        these are per-contract calls inside a pool of hundreds, and the
        failures are brief blips rather than sustained overload.
        """
        last_exc = None
        last_response = None
        for attempt in range(attempts):
            if attempt:
                time.sleep(0.5 * attempt)
            try:
                r = self._get(path, params=params)
            except PHClientError as e:
                if (
                    getattr(e, "retryable", False)
                    or getattr(e, "status", None) in _RETRY_STATUSES
                ):
                    last_exc = e
                    last_response = _V2Response(
                        None, status_code=getattr(e, "status", None)
                    )
                    continue
                raise
            except httpx.TransportError as e:
                last_exc = e
                continue
            if r.status_code not in _RETRY_STATUSES:
                return r
            last_response = r
        if last_response is not None:
            return last_response
        raise last_exc  # type: ignore[misc]

    # ----- response parsing -----

    def _parse_rows(self, r: "_V2Response"):
        """Parse a ThetaData response into a list of dicts.

        Handles both legacy list-of-lists and v2/v3 list-of-dicts shapes.
        Also handles plain list-of-strings (ticker/expiry lists).
        """
        data = r.json()
        if isinstance(data, dict):
            return [data] if data else []
        if not isinstance(data, list) or not data:
            return []
        # Plain list-of-strings (tickers, expirations)
        if isinstance(data[0], str):
            return [{"value": e} for e in data]
        # v2/v3 JSON decode returns list-of-dicts
        if isinstance(data[0], dict):
            return data
        # legacy ThetaData list-of-lists: [headers_row, data_row, ...]
        headers = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]

    @staticmethod
    def _coerce_number(x):
        """Try to convert a value to float; return None on failure or NaN."""
        try:
            v = float(x)
            return None if v != v else v  # reject NaN
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_rows_from_lists(data):
        """Convert a list-of-lists [headers, row, ...] into list-of-dicts."""
        headers = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]

    @staticmethod
    def _rows_from_any(data):
        """Normalize any ThetaData JSON shape into a list of dicts.

        Verified live against three real shapes:
          1. Snapshot list-of-lists:  [headers_row, data_row, ...]
          2. Hist nested:             {"header":{"format":[...]}, "response":[[...],...]}
          3. Columnar (EOD w/ use_csv):  {"close":[...], "open":[...], ...}
        """
        # 2) hist nested with an explicit column format
        if isinstance(data, dict) and isinstance(data.get("response"), list):
            resp = data["response"]
            fmt = (
                (data.get("header") or {}).get("format")
                if isinstance(data.get("header"), dict)
                else None
            )
            if fmt and resp and isinstance(resp[0], list):
                return [dict(zip(fmt, row)) for row in resp]
            if resp and isinstance(resp[0], dict):
                return resp
        # 3) columnar dict of parallel arrays
        if isinstance(data, dict):
            list_cols = {k: v for k, v in data.items() if isinstance(v, list)}
            if list_cols:
                n = min(len(v) for v in list_cols.values())
                return [{k: list_cols[k][i] for k in list_cols} for i in range(n)]
        # 1) snapshot list-of-lists
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[0], list):
            return [dict(zip(data[0], row)) for row in data[1:]]
        # already list-of-dicts
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
        return []

    def _first_numeric_field(self, obj, name_hints):
        """Pull the first plausible numeric value out of an arbitrarily-shaped
        JSON response (dict, list-of-dicts, or the /api/theta list-of-lists),
        preferring keys whose name matches one of name_hints."""
        hints = [h.lower() for h in name_hints]
        # list-of-lists (theta convention): [headers, row, ...]
        if isinstance(obj, list) and obj and isinstance(obj[0], list):
            try:
                rows = self._parse_rows_from_lists(obj)
                obj = rows
            except Exception:
                pass
        candidates = obj if isinstance(obj, list) else [obj]
        for entry in candidates:
            if isinstance(entry, dict):
                for k, v in entry.items():
                    if any(h in str(k).lower() for h in hints):
                        num = self._coerce_number(v)
                        if num is not None:
                            return num
        # last resort: any numeric anywhere
        for entry in candidates:
            if isinstance(entry, dict):
                for v in entry.values():
                    num = self._coerce_number(v)
                    if num is not None:
                        return num
            else:
                num = self._coerce_number(entry)
                if num is not None:
                    return num
        return None

    # ----- helpers for historical-data stitching (from Vol_Suite) -----

    @staticmethod
    def _stamp_contract(rows: list[dict], strike_theta: int, right: str) -> list[dict]:
        """Write the contract's own identity onto every row.

        Single-contract endpoints (e.g. hist/option/all_greeks) do not echo
        strike/right in the response body.  This stamps them so downstream
        consumers that key on (strike, right) don't silently drop rows.
        """
        for row in rows:
            row.setdefault("strike", strike_theta)
            row.setdefault("right", right)
        return rows

    @staticmethod
    def _normalize_date(row: dict) -> str | None:
        """Pull a YYYYMMDD date out of a row regardless of which date-shaped
        field the route happens to use.

        Handles all known ThetaData formats:
          - "date": "20250101" (pure date string)
          - "created": "2025-01-01 12:00:00" (ISO timestamp with dashes)
          - "created": "20250101120000" (compact timestamp without dashes)
          - "datetime": "2025-01-01T12:00:00" (ISO datetime)
        """
        for key in ("date", "Date", "created", "datetime"):
            val = row.get(key)
            if not val:
                continue
            s = str(val)
            # Strip dashes and colons to normalize separators
            clean = (
                s.replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
            )
            # Take the first 8 digits if available
            if len(clean) >= 8 and clean[:8].isdigit():
                return clean[:8]
        return None

    @classmethod
    def _last_bar_per_date(cls, rows: list[dict]) -> list[dict]:
        """Collapse intraday interval bars down to one row per calendar date.
        Keeps the LAST bar of each day (highest ms_of_day)."""
        best: dict[str, dict] = {}
        for row in rows:
            d = cls._normalize_date(row)
            if not d:
                continue
            row["date"] = d
            try:
                ms = int(float(row.get("ms_of_day", 0) or 0))
            except (TypeError, ValueError):
                ms = 0
            if d not in best or ms >= int(float(best[d].get("ms_of_day", -1) or -1)):
                best[d] = row
        return list(best.values())

    # ======================================================================
    # PUBLIC API  —  Options (stock + option endpoints)
    # ======================================================================

    def list_expirations(self, root: str) -> list[str]:
        """List available expiration dates (YYYYMMDD) for a given root.

        v2 has no direct `list_expirations` endpoint. We route to
        `options_chain(ticker=root, date="latest", max_contracts=2000)` and
        extract unique `expiration` values from the chain rows.
        """
        r = self._get_with_retry(
            f"/api/theta/list/expirations/{root}",
            params={"date": "latest", "max_contracts": 2000},
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            # legacy plain-list shape
            return [str(e) for e in data]
        # v2/v3 shape: {ticker, expiration, available_expirations: [...], ...}
        # available_expirations entries are dicts (call_count/expiration/...);
        # normalize the ISO 'YYYY-MM-DD' expiration field to compact YYYYMMDD
        # to match this method's documented return format and every other
        # facade method's `exp` convention.
        exps = []
        seen: set = set()
        for e in data.get("available_expirations") or []:
            raw = e.get("expiration") if isinstance(e, dict) else e
            exp = str(raw).replace("-", "").strip()
            if exp and exp not in seen:
                seen.add(exp)
                exps.append(exp)
        if exps:
            return exps
        # last resort: single expiration returned as the field
        exp = str(data.get("expiration", "")).replace("-", "").strip()
        return [exp] if exp else []

    def resolve_longest_history_expiry(
        self, root: str, lookback_days: int = 150, min_dates: int | None = None
    ) -> str:
        """Pick the expiry with the LONGEST available option-chain history —
        the right expiry for a full lookback-window backtest (a recently-listed
        expiry like the nearest 0.25-year one has only a few weeks of history).

        Probes each far-dated expiry's EARLIEST 28-day window via a small
        bulk_hist/option/eod_greeks call (the full-range call 502s on
        LARGE_REQUEST, so we probe one small chunk at the window's start: if it
        returns rows, the expiry reaches back that far). Returns the furthest-
        reaching expiry with >= the required date count; falls back to the
        latest-listed expiry if nothing reaches the full window.
        """
        fmt = "%Y%m%d"
        end_dt = datetime.now().date()
        start_dt = end_dt - timedelta(days=int(lookback_days * 1.6) + 20)
        # probe just the first ~28d of the window (small request, no LARGE_REQUEST)
        earliest = start_dt.strftime(fmt)
        probe_end = (start_dt + timedelta(days=27)).strftime(fmt)
        need = min_dates or int(lookback_days * 0.9)

        exps = self.list_expirations(root)
        far = sorted(
            [e for e in exps if int(e) > int(end_dt.strftime(fmt))],
            key=int,
            reverse=True,
        )
        best_exp, best_dates = None, 0
        for exp in far[:14]:  # cap probes to avoid proxy churn
            try:
                path = f"/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}"
                r = self._get_with_retry(
                    path,
                    params={
                        "start_date": earliest,
                        "end_date": probe_end,
                    },
                )
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                rows = self._parse_rows(r)
                n = len({self._normalize_date(row) for row in rows})
                if n > best_dates:
                    best_exp, best_dates = exp, n
                if n >= need:
                    return exp
            except Exception as e:
                print(
                    f"  [resolve_longest_history_expiry] {root}/{exp}: "
                    f"{type(e).__name__}: {str(e)[:60]}",
                    flush=True,
                )
        return best_exp or (far[0] if far else exps[-1])

    def list_strikes(self, root: str, exp: str) -> list[float]:
        """List available strikes for a given root and expiration.

        NOTE: Values are returned as-is (not divided by 1000).
        """
        r = self._get_with_retry(f"/api/theta/list/strikes/{root}/{exp}")
        r.raise_for_status()
        data = r.json()
        # v2/v3 maps this legacy route to options_chain. A single-expiration
        # request returns a chain-summary dict with the per-strike rows
        # nested under 'chain' (each row already has a dollar-float 'strike').
        if isinstance(data, dict) and isinstance(data.get("chain"), list):
            result = []
            for row in data["chain"]:
                try:
                    result.append(float(row["strike"]))
                except (KeyError, ValueError, TypeError):
                    pass
            return sorted(set(result))
        # Some callers/mocks may still see a flat list of contract dicts.
        if isinstance(data, list) and data and isinstance(data[0], dict):
            result = []
            for row in data:
                try:
                    result.append(float(row["strike"]))
                except (KeyError, ValueError, TypeError):
                    pass
            return sorted(set(result))
        # Fallback: old list-of-lists shape
        rows = self._parse_rows(r)
        result = []
        for row in rows:
            if "strike" in row:
                try:
                    result.append(float(row["strike"]))
                except (ValueError, TypeError):
                    pass
        return result

    # ----- Snapshots -----

    def stock_snapshot_quote(self, root: str):
        """Latest stock quote snapshot."""
        r = self._get_with_retry(f"/api/theta/snapshot/stock/quote/{root}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    def index_snapshot_quote(self, root: str):
        """Latest index price snapshot (v2 market.last_index_price) -- SPX,
        NDX, VIX, RUT etc. have no equity listing, so stock_snapshot_quote's
        /api/theta/snapshot/stock/quote/{root} always returns empty/errors
        for them; this is the correct endpoint for an index root."""
        r = self._get_with_retry(f"/api/theta/snapshot/index/price/{root}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    def stock_snapshot_trade(self, root: str):
        """Last actual print (price/size/timestamp), independent of the live
        bid/ask NBBO quote above. Used by fetch_spot_price as a fallback when
        the quote feed has nothing posted -- outside regular trading hours,
        stock_snapshot_quote's bid/ask can come back '0.0000' for every
        ticker, not just illiquid ones."""
        r = self._get_with_retry(f"/api/theta/snapshot/stock/trade/{root}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    def option_snapshot_quote(
        self, root: str, exp: str, strike: float, right: str = "C"
    ):
        """Latest option quote snapshot for a single contract."""
        k = strike_to_theta(strike)
        r = self._get_with_retry(
            f"/api/theta/snapshot/option/quote/{root}/{exp}/{k}/{right}"
        )
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    # ----- Bulk snapshots -----

    def option_bulk_greeks(self, root: str, exp: str):
        """Snapshot first-order greeks + IV for all strikes/rights at one expiry."""
        r = self._get_with_retry(
            f"/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}"
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_greeks_second_order(self, root: str, exp: str):
        """Snapshot second-order greeks (vanna, charm, vomma, veta, etc.)
        for all strikes/rights at one expiry."""
        r = self._get_with_retry(
            f"/api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}"
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_oi(self, root: str, exp: str):
        """Snapshot open interest for all strikes/rights at one expiry."""
        r = self._get_with_retry(
            f"/api/theta/bulk_snapshot/option/open_interest/{root}/{exp}"
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_quote(self, root: str, exp: str):
        """Snapshot NBBO + size + volume for all strikes/rights at one expiry."""
        r = self._get_with_retry(f"/api/theta/bulk_snapshot/option/quote/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    def option_flow_analysis(
        self,
        root: str,
        date: str | None = None,
        exp: str | None = None,
        aggregate_by: str = "strike",
    ) -> list[dict]:
        """flow.analysis. Do NOT pass exp — v2 500s. Filter expiry client-side."""
        params: dict[str, Any] = {
            "aggregate_by": aggregate_by,
            "include_execution_metrics": True,
            "use_csv": False,
        }
        if date:
            params["date"] = date
        r = self._get_with_retry(f"/api/flow/analysis/{root}", params=params)
        r.raise_for_status()
        rows = self._parse_rows(r)
        out = []
        for row in rows:
            item = dict(row) if not isinstance(row, dict) else row
            out.append(item)
        return out

    def option_session_trades(
        self, root: str, start_date: str, end_date: str | None = None
    ) -> list[dict]:
        """flow.scanner_trades — per-print size/bid/ask/right/expiry (wiki)."""
        env = self._get_thread_client().flow.scanner_trades(
            root=root,
            start_date=start_date,
            end_date=end_date or start_date,
        )
        data = env.data
        if not data:
            return []
        rows = list(data)
        first = rows[0]
        if isinstance(first, (tuple, list)) and first and isinstance(first[0], str):
            headers = [str(h) for h in first]
            return [dict(zip(headers, row)) for row in rows[1:]]
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(row)
            elif hasattr(row, "keys"):
                out.append(dict(row))
        return out

    def option_bulk_oi_latest(
        self, root: str, exp: str, lookback_days: int = 7, as_of: str | None = None
    ) -> list[dict]:
        """Fallback OI fetch when the snapshot endpoint 404s.

        ``bulk_snapshot/option/open_interest`` sometimes returns 404 for
        expiries that have no current OI snapshot (e.g. a newly-listed
        expiry that hasn't settled yet).  The historical endpoint
        ``bulk_hist/option/open_interest`` *does* have OI for those
        expiries -- it just takes a single date at a time, so we probe
        backwards from ``as_of`` (default: today) until we hit a trading
        day with data.

        Returns the same list-of-dicts shape as ``option_bulk_oi``.
        Returns ``[]`` if no recent trading day has OI for this expiry.
        """
        fmt = "%Y%m%d"
        end_dt = _as_of_date(as_of) if as_of else datetime.now().date()
        for offset in range(lookback_days):
            day = end_dt - timedelta(days=offset)
            if day.weekday() >= 5:  # skip Sat/Sun
                continue
            d = day.strftime(fmt)
            path = f"/api/theta/bulk_hist/option/open_interest/{root}/{exp}"
            r = self._get_with_retry(path, params={"start_date": d, "end_date": d})
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rows = self._parse_rows(r)
            if rows:
                return rows
        return []

    # ----- Per-contract historical data -----

    def option_hist_eod_single(
        self,
        root: str,
        exp: str,
        strike_theta: int,
        right: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Per-contract EOD history over a date range — one row per trading
        day in a single request.  Chunked into <=28-day spans."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: list[dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {
                "start_date": chunk_start.strftime(fmt),
                "end_date": chunk_end.strftime(fmt),
            }
            path = f"/api/theta/hist/option/eod/{root}/{exp}/{strike_theta}/{right}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    def option_hist_open_interest_single(
        self,
        root: str,
        exp: str,
        strike_theta: int,
        right: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Single-contract historical open interest.  Chunked into
        <=28-day spans.  404 is retried (flaky proxy) before being
        accepted as a genuine data gap."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: list[dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {
                "start_date": chunk_start.strftime(fmt),
                "end_date": chunk_end.strftime(fmt),
            }
            path = f"/api/theta/hist/option/open_interest/{root}/{exp}/{strike_theta}/{right}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    def option_hist_all_greeks_single(
        self,
        root: str,
        exp: str,
        strike_theta: int,
        right: str,
        start_date: str,
        end_date: str,
        ivl: int = 900000,
    ) -> list[dict]:
        """Single-contract historical greeks (incl. IV and second-order
        greeks), bucketed into `ivl`-ms intervals (default 900000 = 15 min).
        Chunked into <=28-day spans.  404/502 are retried (flaky proxy)."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: list[dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {
                "start_date": chunk_start.strftime(fmt),
                "end_date": chunk_end.strftime(fmt),
                "ivl": ivl,
            }
            path = (
                f"/api/theta/hist/option/all_greeks/{root}/{exp}/{strike_theta}/{right}"
            )
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    # ----- Whole-chain bulk historical data (thread-pooled) -----

    def _enumerate_contracts(self, root: str, exp: str) -> list[tuple[int, str]]:
        """Enumerate all (strike_theta, right) pairs for an expiry using
        the snapshot bulk_greeks endpoint."""
        universe = self.option_bulk_greeks(root, exp)
        contracts, seen = [], set()
        for row in universe:
            try:
                k_theta = int(float(row["strike"]))
                right = row["right"]
            except (KeyError, TypeError, ValueError):
                continue
            key = (k_theta, right)
            if key not in seen:
                seen.add(key)
                contracts.append(key)
        return contracts

    def option_bulk_hist_eod(
        self,
        root: str,
        exp: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """EOD price history for a whole expiry's chain over a date range."""
        try:
            contracts = self._enumerate_contracts(root, exp)
        except Exception as e:
            print(
                f"  [option_bulk_hist_eod] could not enumerate {root}/{exp}'s "
                f"contract universe: {type(e).__name__}: {e}"
            )
            raise

        all_rows: list[dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(
            f"  [option_bulk_hist_eod] {root}/{exp}: pulling {total} contracts' "
            f"EOD history for {start_date}-{end_date} "
            f"({_HIST_GREEKS_CONCURRENCY} concurrent)..."
        )

        def _fetch_one(k_theta, right):
            return self.option_hist_eod_single(
                root,
                exp,
                k_theta,
                right,
                start_date,
                end_date,
            )

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {pool.submit(_fetch_one, k, rt): (k, rt) for k, rt in contracts}
            for future in as_completed(futures):
                k_theta, right = futures[future]
                done += 1
                try:
                    rows = future.result()
                    if rows:
                        all_rows.extend(self._last_bar_per_date(rows))
                    else:
                        empty += 1
                except Exception as e:
                    errored += 1
                    print(
                        f"  [option_bulk_hist_eod] {root}/{exp} {k_theta}/{right}: "
                        f"skipped ({type(e).__name__}: {e})"
                    )
                if done % 50 == 0 or done == total:
                    print(
                        f"  [option_bulk_hist_eod] {root}/{exp}: {done}/{total} done "
                        f"({empty} never traded in range, {errored} genuine errors)"
                    )
        return all_rows

    def option_bulk_hist_eod_greeks(
        self,
        root: str,
        exp: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Dense whole-chain EOD OHLC + implied_vol + FULL greeks over a date
        range — the untapped route that avoids local IV inversion.

        `bulk_hist/option/eod_greeks/{root}/{exp}` returns every contract's EOD
        bar (17:15 ET close) with implied_vol, gamma, vanna, delta, theta, and
        the full higher-order set, in ONE request per expiry (vs the sparse
        per-contract hist/all_greeks fan-out). Verified live 2026-08-13:
        HTTP 200, dense rows (~8.5k/month on QQQ).

        The proxy LARGE_REQUEST-limits a full-year single call (502/timeout),
        so this chunks into <=28-day windows (the client's established safe
        span for hist routes). Verified live 2026-08-13: HTTP 200, dense rows
        (~8.5k/month on QQQ), strike already theta-scaled cents-int ($459.78 ->
        459780), right already 'C'/'P', date an int YYYYMMDD — the SAME
        convention as option_bulk_hist_oi_by_day (NOT the dollar-string/'CALL'
        shape of option_bulk_hist_eod). Stamps a string YYYYMMDD 'date' for
        downstream consumers. Returns [] if the expiry has no history in range.
        """
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: list[dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {
                "start_date": chunk_start.strftime(fmt),
                "end_date": chunk_end.strftime(fmt),
            }
            path = f"/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            rows = self._parse_rows(r)
            for row in rows:
                # strike/right come back already in suite convention; just
                # normalize the right to single-char and stamp a string date
                rt = str(row.get("right", ""))[:1].upper()
                if rt not in ("C", "P"):
                    continue
                row["right"] = rt
                d = self._normalize_date(row)
                if d:
                    row["date"] = d
            all_rows.extend(rows)
            chunk_start = chunk_end + timedelta(days=1)
        return all_rows

    def option_bulk_hist_greeks(
        self,
        root: str,
        exp: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Daily EOD-equivalent greeks (incl. implied_vol, gamma, and full
        higher-order set) history for a whole expiry's chain.

        Enumerates the chain via snapshot bulk_greeks, then fans out per-contract
        hist/all_greeks calls across a thread pool.
        """
        try:
            contracts = self._enumerate_contracts(root, exp)
        except Exception as e:
            print(
                f"  [option_bulk_hist_greeks] could not enumerate {root}/{exp}'s "
                f"contract universe via snapshot bulk_greeks: {type(e).__name__}: {e}"
            )
            raise

        all_rows: list[dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(
            f"  [option_bulk_hist_greeks] {root}/{exp}: pulling {total} contracts' "
            f"history for {start_date}-{end_date} "
            f"({_HIST_GREEKS_CONCURRENCY} concurrent)..."
        )

        def _fetch_one(k_theta, right):
            return (
                k_theta,
                right,
                self.option_hist_all_greeks_single(
                    root,
                    exp,
                    k_theta,
                    right,
                    start_date,
                    end_date,
                ),
            )

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {
                pool.submit(_fetch_one, k_theta, right): (k_theta, right)
                for k_theta, right in contracts
            }
            for future in as_completed(futures):
                k_theta, right = futures[future]
                done += 1
                try:
                    _, _, rows = future.result()
                    if rows:
                        all_rows.extend(self._last_bar_per_date(rows))
                    else:
                        empty += 1
                except Exception as e:
                    errored += 1
                    print(
                        f"  [option_bulk_hist_greeks] {root}/{exp} {k_theta}/{right}: "
                        f"skipped ({type(e).__name__}: {e})"
                    )
                if done % 50 == 0 or done == total:
                    print(
                        f"  [option_bulk_hist_greeks] {root}/{exp}: {done}/{total} contracts done "
                        f"({empty} never traded in range, {errored} genuine errors)"
                    )
        return all_rows

    def option_bulk_hist_oi(
        self,
        root: str,
        exp: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Daily OI history for a whole expiry's chain.

        Enumerates the chain via snapshot bulk_oi, then fans out per-contract
        hist/open_interest calls across a thread pool.
        """
        try:
            universe = self.option_bulk_oi(root, exp)
        except Exception as e:
            print(
                f"  [option_bulk_hist_oi] could not enumerate {root}/{exp}'s "
                f"contract universe via snapshot bulk_oi: {type(e).__name__}: {e}"
            )
            raise

        contracts = []
        seen = set()
        for row in universe:
            try:
                k_theta = int(float(row["strike"]))
                right = row["right"]
            except (KeyError, TypeError, ValueError):
                continue
            key = (k_theta, right)
            if key not in seen:
                seen.add(key)
                contracts.append(key)

        all_rows: list[dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(
            f"  [option_bulk_hist_oi] {root}/{exp}: pulling {total} contracts' "
            f"OI history for {start_date}-{end_date} "
            f"({_HIST_GREEKS_CONCURRENCY} concurrent)..."
        )

        def _fetch_one(k_theta, right):
            return (
                k_theta,
                right,
                self.option_hist_open_interest_single(
                    root,
                    exp,
                    k_theta,
                    right,
                    start_date,
                    end_date,
                ),
            )

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {
                pool.submit(_fetch_one, k_theta, right): (k_theta, right)
                for k_theta, right in contracts
            }
            for future in as_completed(futures):
                k_theta, right = futures[future]
                done += 1
                try:
                    _, _, rows = future.result()
                    if rows:
                        all_rows.extend(self._last_bar_per_date(rows))
                    else:
                        empty += 1
                except Exception as e:
                    errored += 1
                    print(
                        f"  [option_bulk_hist_oi] {root}/{exp} {k_theta}/{right}: "
                        f"skipped ({type(e).__name__}: {e})"
                    )
                if done % 50 == 0 or done == total:
                    print(
                        f"  [option_bulk_hist_oi] {root}/{exp}: {done}/{total} contracts done "
                        f"({empty} never traded in range, {errored} genuine errors)"
                    )
        return all_rows

    def option_bulk_hist_oi_by_day(
        self,
        root: str,
        exp: str,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Whole-chain open interest, one request per calendar day.

        bulk_hist/option/open_interest returns the entire chain for
        `start_date` only.  Iterating days (~110 for a 5-month lookback)
        is much cheaper than per-contract pulls (~430 x 110).
        """
        fmt = "%Y%m%d"
        day = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        days = []
        while day <= end_dt:
            if day.weekday() < 5:  # skip Sat/Sun
                days.append(day.strftime(fmt))
            day += timedelta(days=1)

        all_rows: list[dict] = []
        total, done, empty, errored = len(days), 0, 0, 0
        print(
            f"  [option_bulk_hist_oi_by_day] {root}/{exp}: {total} weekdays "
            f"{start_date}-{end_date} ({_HIST_GREEKS_CONCURRENCY} concurrent)..."
        )

        def _fetch_day(d):
            path = f"/api/theta/bulk_hist/option/open_interest/{root}/{exp}"
            r = self._get_with_retry(
                path,
                params={"start_date": d, "end_date": d},
            )
            if r.status_code == 404:
                return []
            r.raise_for_status()
            rows = self._parse_rows(r)
            for row in rows:
                row.setdefault("date", d)
            return rows

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {pool.submit(_fetch_day, d): d for d in days}
            for future in as_completed(futures):
                d = futures[future]
                done += 1
                try:
                    rows = future.result()
                    if rows:
                        all_rows.extend(rows)
                    else:
                        empty += 1
                except Exception as e:
                    errored += 1
                    print(
                        f"  [option_bulk_hist_oi_by_day] {root}/{exp} {d}: "
                        f"skipped ({type(e).__name__}: {e})"
                    )
                if done % 25 == 0 or done == total:
                    print(
                        f"  [option_bulk_hist_oi_by_day] {root}/{exp}: {done}/{total} days "
                        f"({empty} no session, {errored} genuine errors)"
                    )
        return all_rows

    # ======================================================================
    # PUBLIC API  —  Macro / underlying inputs
    # ======================================================================

    def fetch_spot_price(self, ticker: str) -> float:
        """Latest spot price for `ticker`.

        Returns 0.0 on any failure.  Three-layer fallback for when the live
        NBBO quote has nothing posted (e.g. outside regular trading hours,
        where bid/ask can come back the STRING '0.0000' for every ticker,
        not just illiquid ones):
          1. live quote (mid/bid/ask/last)
          2. last actual trade print (stock_snapshot_trade)
          3. most recent daily close (hist_stock_eod)

        NOTE: the truthy check below is `v not in (None, '') and float(v) > 0`,
        not `if key in quote and quote[key]` -- the latter treats the STRING
        '0.0000' as truthy (non-empty string) and returns 0.0 as if it were a
        found price.

        A cash index (SPX, NDX, VIX, ...) has no equity listing, so the
        stock-quote path below always comes back empty/erroring for one --
        route those through the dedicated index endpoints instead, before
        any of the stock-specific fallback logic runs.
        """
        if ticker.strip().upper() in _INDEX_ROOTS:
            return self._fetch_index_spot_price(ticker.strip().upper())

        try:
            quote = self.stock_snapshot_quote(ticker)
            for key in ["mid", "bid", "ask", "last"]:
                v = quote.get(key)
                if v not in (None, "") and float(v) > 0:
                    return float(v)
            if not quote:
                print(
                    f"  [fetch_spot_price] {ticker}: empty quote response "
                    f"(no rows) -- trying last trade print."
                )
            else:
                print(
                    f"  [fetch_spot_price] {ticker}: live quote has no usable "
                    f"bid/ask/last (likely outside regular trading hours): "
                    f"{quote} -- trying last trade print."
                )
        except Exception as e:
            print(
                f"  [fetch_spot_price] {ticker}: snapshot request failed "
                f"({type(e).__name__}: {e}) -- trying last trade print."
            )

        try:
            trade = self.stock_snapshot_trade(ticker)
            price = trade.get("price")
            if price not in (None, "") and float(price) > 0:
                print(
                    f"  [fetch_spot_price] {ticker}: no live bid/ask quote right now "
                    f"(outside regular trading hours, most likely) -- using last trade "
                    f"print {float(price):.4f} instead of a live mid. Not a live quote."
                )
                return float(price)
            print(
                f"  [fetch_spot_price] {ticker}: no usable last trade print either -- "
                f"falling back to last daily close."
            )
        except Exception as e:
            print(
                f"  [fetch_spot_price] {ticker}: trade snapshot request failed "
                f"({type(e).__name__}: {e}) -- falling back to last daily close."
            )

        try:
            end = datetime.now()
            start = end - timedelta(days=7)
            rows = self.hist_stock_eod(
                ticker,
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
            )
            closes = [
                float(r["close"])
                for r in rows
                if r.get("close") and float(r["close"]) > 0
            ]
            if closes:
                return closes[-1]
            print(
                f"  [fetch_spot_price] {ticker}: daily-close fallback "
                f"returned no usable rows either."
            )
        except Exception as e:
            print(
                f"  [fetch_spot_price] {ticker}: daily-close fallback also "
                f"failed ({type(e).__name__}: {e})."
            )
        return 0.0

    def _fetch_index_spot_price(self, root: str) -> float:
        """fetch_spot_price's index path: live index_snapshot_quote, falling
        back to the last daily close via hist_index_eod. Two layers, not
        three -- an index has no separate "last trade print" concept
        distinct from its live price snapshot the way a stock does."""
        try:
            quote = self.index_snapshot_quote(root)
            price = quote.get("price")
            if price not in (None, "") and float(price) > 0:
                return float(price)
            print(
                f"  [fetch_spot_price] {root}: index snapshot has no usable "
                f"price ({quote}) -- falling back to last daily close."
            )
        except Exception as e:
            print(
                f"  [fetch_spot_price] {root}: index snapshot request failed "
                f"({type(e).__name__}: {e}) -- falling back to last daily close."
            )

        try:
            end = datetime.now()
            start = end - timedelta(days=7)
            rows = self.hist_index_eod(
                root, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
            closes = [
                float(r["close"])
                for r in rows
                if r.get("close") and float(r["close"]) > 0
            ]
            if closes:
                return closes[-1]
            print(
                f"  [fetch_spot_price] {root}: daily-close fallback returned no usable rows."
            )
        except Exception as e:
            print(
                f"  [fetch_spot_price] {root}: daily-close fallback also failed "
                f"({type(e).__name__}: {e})."
            )
        return 0.0

    def hist_stock_eod(self, root: str, start_date: str, end_date: str) -> list[dict]:
        """Daily OHLCV history.  Paginates into <=28-day chunks to avoid
        proxy-side 502 on long lookbacks."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)

        all_rows: list[dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            r = self._get_with_retry(
                f"/api/theta/hist/stock/eod/{root}",
                params={
                    "start_date": chunk_start.strftime(fmt),
                    "end_date": chunk_end.strftime(fmt),
                },
            )
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return all_rows

    def hist_stock_ohlc(self, root: str, start_date: str, end_date: str) -> list[dict]:
        """One-minute stock OHLCV history from the verified stock/ohlc route."""
        r = self._get_with_retry(
            f"/api/theta/hist/stock/ohlc/{root}",
            params={"start_date": start_date, "end_date": end_date},
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def hist_index_eod(self, root: str, start_date: str, end_date: str) -> list[dict]:
        """Daily index close history -- the fetch_spot_price fallback for an
        index root when index_snapshot_quote has nothing live posted.
        Unlike hist_stock_eod, no 28-day chunking: only ever called with a
        short (~7 day) lookback here, so a single request is enough."""
        r = self._get_with_retry(
            f"/api/theta/hist/index/eod/{root}",
            params={"start_date": start_date, "end_date": end_date},
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def fetch_option_iv(
        self,
        ticker: str,
        strike: float,
        T: float,
        option_type: str = "call",
        exp: str | None = None,
    ) -> tuple[float | None, float | None, str | None, str | None]:
        """Fetch option IV and price from ThetaData.

        Returns (chain_iv, chain_price, expiry_used, data_timestamp).

        `exp`: pre-resolved YYYYMMDD expiry.  If given it is used directly
        so the whole run shares ONE expiry/T derived from real dates
        instead of each call re-deriving a "nearest".
        """
        try:
            if exp:
                nearest_exp = str(exp)
            else:
                expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime(
                    "%Y%m%d"
                )
                exps = self.list_expirations(ticker)
                if not exps:
                    return None, None, None, None
                target = datetime.strptime(expiry_date, "%Y%m%d")
                parsed = [
                    (abs((datetime.strptime(e, "%Y%m%d") - target).days), e)
                    for e in exps
                ]
                parsed.sort()
                nearest_exp = parsed[0][1]
            right = "C" if option_type == "call" else "P"

            greeks = self.option_bulk_greeks(ticker, nearest_exp)
            k_theta = strike_to_theta(strike)
            for row in greeks:
                if int(row["strike"]) == k_theta and row["right"] == right:
                    iv = row.get("implied_vol")
                    bid = row.get("bid")
                    ask = row.get("ask")
                    price = (
                        (float(bid) + float(ask)) / 2.0
                        if bid and ask and float(bid) > 0
                        else None
                    )
                    if not price:
                        price = float(bid) if bid and float(bid) > 0 else None
                    ts = row.get("underlying_timestamp", "")
                    if not ts and "date" in row:
                        ts = str(row["date"])
                    return (
                        (float(iv), price, nearest_exp, ts)
                        if iv
                        else (None, price, nearest_exp, ts)
                    )
        except Exception as e:
            print(f"[ThetaData] fetch_option_iv error: {e}")
        return None, None, None, None

    def fetch_risk_free_rate(self, T: float = 1.0):
        """Risk-free rate for tenor ~T (years) from PotatoHedge's yield curve.

        Returns a decimal (e.g. 0.046) or None if unavailable/implausible.
        """
        try:
            today = datetime.now()
            target = (today + timedelta(days=int(max(T, 0.01) * 365))).strftime(
                "%Y%m%d"
            )
            start = (today - timedelta(days=7)).strftime("%Y%m%d")
            data = self.get_yield_curve(start_date=start, target_date=target)
            rate = self._first_numeric_field(data, ["risk_free_rate", "rate", "yield"])
            if rate is None:
                return None
            if rate > 1.0:
                rate = rate / 100.0
            if 0.0 <= rate <= 0.25:
                return float(rate)
            return None
        except Exception as e:
            print(f"[PotatoHedge] yield_curve unavailable ({e}).")
            return None

    def fetch_dividend_yield(self, ticker: str, spot: float | None = None) -> float:
        """Trailing-12-month dividend yield (decimal) from PotatoHedge's stock
        dividend history.  Returns 0.0 when no dividends are found (a real
        answer, not a failure).
        """
        try:
            if spot is None or spot <= 0:
                spot = self.fetch_spot_price(ticker)
            if not spot or spot <= 0:
                return 0.0
            end = datetime.now()
            start = end - timedelta(days=366)
            r = self._get(
                f"/api/theta/hist/stock/dividend/{ticker}",
                params={
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": end.strftime("%Y%m%d"),
                },
            )
            r.raise_for_status()
            rows = self._rows_from_any(r.json())
            total = 0.0
            for row in rows:
                amt = self._coerce_number(row.get("dividend_amount"))
                if amt is None:
                    for k, v in row.items():
                        kl = str(k).lower()
                        if "dividend" in kl and "amount" in kl:
                            amt = self._coerce_number(v)
                            break
                if amt and amt > 0:
                    total += amt
            q = total / spot
            return float(q) if 0.0 <= q < 0.20 else 0.0
        except Exception:
            return 0.0

    def fetch_realized_vol(self, ticker: str, window: int = 30):
        """Annualized close-to-close realized vol from PotatoHedge daily EOD.

        Returns a decimal or None.  Only used as a seed (e.g. Heston V0),
        never as a final pricing vol, so None is harmless.
        """
        try:
            end = datetime.now()
            start = end - timedelta(days=int(window * 2 + 10))
            r = self._get(
                f"/api/theta/hist/stock/eod/{ticker}",
                params={
                    "start_date": start.strftime("%Y%m%d"),
                    "end_date": end.strftime("%Y%m%d"),
                },
            )
            r.raise_for_status()
            rows = self._rows_from_any(r.json())
            closes = []
            for row in rows:
                num = self._coerce_number(row.get("close"))
                if num and num > 0:
                    closes.append(num)
            if len(closes) < 5:
                return None
            closes = _np.array(closes[-(window + 1) :], dtype=float)
            logret = _np.log(closes[1:] / closes[:-1])
            vol = float(_np.std(logret) * _np.sqrt(252))
            if 0.0 < vol < 5.0:
                return vol
            return None
        except Exception:
            return None

    def fetch_beta(
        self,
        ticker: str,
        market_proxy: str = "SPY",
        window: int = 252,
    ):
        """Equity beta vs `market_proxy` (default SPY): cov(stock logret,
        market logret) / var(market logret) over the trailing `window`
        trading days.  Returns None if either series can't be fetched or
        there isn't enough overlapping history.
        """
        if ticker.upper() == market_proxy.upper():
            return 1.0
        try:
            end = datetime.now()
            days_back = min(int(window * 1.6 + 15), 355)
            start = end - timedelta(days=days_back)

            def _closes(root):
                r = self._get(
                    f"/api/theta/hist/stock/eod/{root}",
                    params={
                        "start_date": start.strftime("%Y%m%d"),
                        "end_date": end.strftime("%Y%m%d"),
                    },
                )
                r.raise_for_status()
                rows = self._rows_from_any(r.json())
                out = []
                for row in rows:
                    num = self._coerce_number(row.get("close"))
                    if num and num > 0:
                        out.append(num)
                return out

            stock_closes = _closes(ticker)
            market_closes = _closes(market_proxy)

            n = min(len(stock_closes), len(market_closes))
            if n < 30:
                return None

            stock_arr = _np.array(stock_closes[-n:], dtype=float)
            market_arr = _np.array(market_closes[-n:], dtype=float)
            stock_ret = _np.diff(_np.log(stock_arr))
            market_ret = _np.diff(_np.log(market_arr))
            if len(market_ret) < 2:
                return None
            var_m = _np.var(market_ret, ddof=1)
            if var_m <= 0:
                return None
            cov = _np.cov(stock_ret, market_ret, ddof=1)[0, 1]
            beta = float(cov / var_m)
            if beta != beta:  # NaN guard
                return None
            return beta
        except Exception:
            return None

    # ======================================================================
    # PUBLIC API  —  Native PotatoHedge analytics (NOT raw ThetaData
    #                passthroughs).  These hit /api/db/ value-add layer.
    # ======================================================================

    def get_dealer_positioning(
        self,
        root: str,
        start_date: str,
        end_date: str,
        latest_only: bool = True,
    ):
        """PotatoHedge's own vendor-computed dealer positioning/GEX.

        This is NOT a raw ThetaData passthrough — it lives under /api/db/,
        PotatoHedge's own value-add analytics layer.
        """
        r = self._get(
            "/api/db/dealer_positioning",
            params={
                "root": root,
                "start_date": start_date,
                "end_date": end_date,
                "latest_only": latest_only,
                "use_csv": False,
            },
        )
        r.raise_for_status()
        return r.json()

    def get_iv_surface_change(
        self,
        root: str,
        expiration: str,
        baseline_date: str,
        asof_date: str | None = None,
    ):
        """PH v2 volatility.surface_change — vendor ΔIV, no local IV solve.

        expiration/baseline/asof are YYYYMMDD or YYYY-MM-DD. Omit asof_date
        to use the vendor EOD-safe prior trading day.
        """

        def _iso(d: str) -> str:
            digits = "".join(ch for ch in str(d) if ch.isdigit())
            if len(digits) == 8:
                return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            return str(d)

        params = {
            "expiration": _iso(expiration),
            "baseline_date": _iso(baseline_date),
            "interval_type": "DAY",
        }
        if asof_date:
            params["asof_date"] = _iso(asof_date)
        r = self._get_with_retry(
            f"/api/volatility/surface_change/{root}",
            params=params,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_yield_curve(
        self,
        start_date: str,
        end_date: str | None = None,
        target_date: str | None = None,
    ):
        """PotatoHedge's own Treasury yield curve endpoint.

        If target_date is given, returns the closest applicable forward
        risk-free rate for that tenor instead of a full curve.
        """
        params = {"start_date": start_date, "use_csv": False}
        if end_date is not None:
            params["end_date"] = end_date
        if target_date is not None:
            params["target_date"] = target_date
        r = self._get("/api/db/yield_curve", params=params)
        r.raise_for_status()
        return r.json()

    def close(self):
        """Close every thread-local v2 client created by this controller."""
        with self._v2_lock:
            clients, self._v2_all = self._v2_all, []
        for client in clients:
            client.close()
