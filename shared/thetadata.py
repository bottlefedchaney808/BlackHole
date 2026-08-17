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
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

import httpx
import numpy as _np

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
        self.headers = {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
        self.client = httpx.Client(headers=self.headers, timeout=60.0)

    # ----- low-level HTTP helpers -----

    def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        """GET with optional query params.  Unified version of the old
        _get / _get_params split from Options_Suite."""
        return self.client.get(f"{self.base_url}{path}", params=params, timeout=60.0)

    def _get_params(self, path: str, params: dict) -> httpx.Response:
        """Backward-compat wrapper for callers that used the old separate
        _get_params method.  Delegates to _get."""
        return self._get(path, params=params)

    def _get_with_retry(
        self, path: str, params: Optional[dict] = None,
        attempts: int = _RETRY_ATTEMPTS,
    ) -> httpx.Response:
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

    def _parse_rows(self, r: httpx.Response):
        """Parse a list-of-lists ThetaData response into a list of dicts.

        Expected shape:  [headers_row, data_row, data_row, ...]
        """
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return []
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
        if isinstance(data, dict) and isinstance(data.get('response'), list):
            resp = data['response']
            fmt = (
                (data.get('header') or {}).get('format')
                if isinstance(data.get('header'), dict) else None
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
    def _stamp_contract(rows: List[Dict], strike_theta: int, right: str) -> List[Dict]:
        """Write the contract's own identity onto every row.

        Single-contract endpoints (e.g. hist/option/all_greeks) do not echo
        strike/right in the response body.  This stamps them so downstream
        consumers that key on (strike, right) don't silently drop rows.
        """
        for row in rows:
            row.setdefault('strike', strike_theta)
            row.setdefault('right', right)
        return rows

    @staticmethod
    def _normalize_date(row: Dict) -> Optional[str]:
        """Pull a YYYYMMDD date out of a row regardless of which date-shaped
        field the route happens to use.
        
        Handles all known ThetaData formats:
          - "date": "20250101" (pure date string)
          - "created": "2025-01-01 12:00:00" (ISO timestamp with dashes)
          - "created": "20250101120000" (compact timestamp without dashes)
          - "datetime": "2025-01-01T12:00:00" (ISO datetime)
        """
        for key in ('date', 'Date', 'created', 'datetime'):
            val = row.get(key)
            if not val:
                continue
            s = str(val)
            # Strip dashes and colons to normalize separators
            clean = s.replace('-', '').replace(':', '').replace('T', '').replace(' ', '')
            # Take the first 8 digits if available
            if len(clean) >= 8 and clean[:8].isdigit():
                return clean[:8]
        return None

    @classmethod
    def _last_bar_per_date(cls, rows: List[Dict]) -> List[Dict]:
        """Collapse intraday interval bars down to one row per calendar date.
        Keeps the LAST bar of each day (highest ms_of_day)."""
        best: Dict[str, Dict] = {}
        for row in rows:
            d = cls._normalize_date(row)
            if not d:
                continue
            row['date'] = d
            try:
                ms = int(float(row.get('ms_of_day', 0) or 0))
            except (TypeError, ValueError):
                ms = 0
            if d not in best or ms >= int(float(best[d].get('ms_of_day', -1) or -1)):
                best[d] = row
        return list(best.values())

    # ======================================================================
    # PUBLIC API  —  Options (stock + option endpoints)
    # ======================================================================

    def list_expirations(self, root: str) -> List[str]:
        """List available expiration dates (YYYYMMDD) for a given root."""
        r = self._get_with_retry(f"/api/theta/list/expirations/{root}")
        r.raise_for_status()
        raw = r.json()
        if isinstance(raw, list):
            return [str(e) for e in raw]
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        return []

    def resolve_longest_history_expiry(self, root: str, lookback_days: int = 150,
                                       min_dates: Optional[int] = None) -> str:
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
        far = sorted([e for e in exps if int(e) > int(end_dt.strftime(fmt))],
                     key=int, reverse=True)
        best_exp, best_dates = None, 0
        for exp in far[:14]:  # cap probes to avoid proxy churn
            try:
                path = f"/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}"
                r = self._get_with_retry(path, params={
                    "start_date": earliest,
                    "end_date": probe_end,
                })
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
                print(f"  [resolve_longest_history_expiry] {root}/{exp}: "
                      f"{type(e).__name__}: {str(e)[:60]}", flush=True)
        return best_exp or (far[0] if far else exps[-1])

    def list_strikes(self, root: str, exp: str) -> List[float]:
        """List available strikes for a given root and expiration.

        NOTE: Values are returned as-is (not divided by 1000).  Live testing
        confirmed the /list/strikes endpoint already returns dollar-value
        floats, not theta-scaled ints.
        """
        r = self._get_with_retry(f"/api/theta/list/strikes/{root}/{exp}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        result = []
        for row in rows:
            if 'strike' in row:
                try:
                    result.append(float(row['strike']))
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

    def option_snapshot_quote(self, root: str, exp: str, strike: float, right: str = "C"):
        """Latest option quote snapshot for a single contract."""
        k = strike_to_theta(strike)
        r = self._get_with_retry(f"/api/theta/snapshot/option/quote/{root}/{exp}/{k}/{right}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    # ----- Bulk snapshots -----

    def option_bulk_greeks(self, root: str, exp: str):
        """Snapshot first-order greeks + IV for all strikes/rights at one expiry."""
        r = self._get_with_retry(f"/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_greeks_second_order(self, root: str, exp: str):
        """Snapshot second-order greeks (vanna, charm, vomma, veta, etc.)
        for all strikes/rights at one expiry."""
        r = self._get_with_retry(f"/api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_oi(self, root: str, exp: str):
        """Snapshot open interest for all strikes/rights at one expiry."""
        r = self._get_with_retry(f"/api/theta/bulk_snapshot/option/open_interest/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    # ----- Per-contract historical data -----

    def option_hist_eod_single(
        self, root: str, exp: str, strike_theta: int, right: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
        """Per-contract EOD history over a date range — one row per trading
        day in a single request.  Chunked into <=28-day spans."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
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
        self, root: str, exp: str, strike_theta: int, right: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
        """Single-contract historical open interest.  Chunked into
        <=28-day spans.  404 is retried (flaky proxy) before being
        accepted as a genuine data gap."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
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
        self, root: str, exp: str, strike_theta: int, right: str,
        start_date: str, end_date: str, ivl: int = 900000,
    ) -> List[Dict]:
        """Single-contract historical greeks (incl. IV and second-order
        greeks), bucketed into `ivl`-ms intervals (default 900000 = 15 min).
        Chunked into <=28-day spans.  404/502 are retried (flaky proxy)."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {
                "start_date": chunk_start.strftime(fmt),
                "end_date": chunk_end.strftime(fmt),
                "ivl": ivl,
            }
            path = f"/api/theta/hist/option/all_greeks/{root}/{exp}/{strike_theta}/{right}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    # ----- Whole-chain bulk historical data (thread-pooled) -----

    def _enumerate_contracts(self, root: str, exp: str) -> List[Tuple[int, str]]:
        """Enumerate all (strike_theta, right) pairs for an expiry using
        the snapshot bulk_greeks endpoint."""
        universe = self.option_bulk_greeks(root, exp)
        contracts, seen = [], set()
        for row in universe:
            try:
                k_theta = int(float(row['strike']))
                right = row['right']
            except (KeyError, TypeError, ValueError):
                continue
            key = (k_theta, right)
            if key not in seen:
                seen.add(key)
                contracts.append(key)
        return contracts

    def option_bulk_hist_eod(
        self, root: str, exp: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
        """EOD price history for a whole expiry's chain over a date range."""
        try:
            contracts = self._enumerate_contracts(root, exp)
        except Exception as e:
            print(f"  [option_bulk_hist_eod] could not enumerate {root}/{exp}'s "
                  f"contract universe: {type(e).__name__}: {e}")
            raise

        all_rows: List[Dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(f"  [option_bulk_hist_eod] {root}/{exp}: pulling {total} contracts' "
              f"EOD history for {start_date}-{end_date} "
              f"({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return self.option_hist_eod_single(
                root, exp, k_theta, right, start_date, end_date,
            )

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {
                pool.submit(_fetch_one, k, rt): (k, rt)
                for k, rt in contracts
            }
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
                    print(f"  [option_bulk_hist_eod] {root}/{exp} {k_theta}/{right}: "
                          f"skipped ({type(e).__name__}: {e})")
                if done % 50 == 0 or done == total:
                    print(f"  [option_bulk_hist_eod] {root}/{exp}: {done}/{total} done "
                          f"({empty} never traded in range, {errored} genuine errors)")
        return all_rows

    def option_bulk_hist_eod_greeks(
        self, root: str, exp: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
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
        all_rows: List[Dict] = []
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
        self, root: str, exp: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
        """Daily EOD-equivalent greeks (incl. implied_vol, gamma, and full
        higher-order set) history for a whole expiry's chain.

        Enumerates the chain via snapshot bulk_greeks, then fans out per-contract
        hist/all_greeks calls across a thread pool.
        """
        try:
            contracts = self._enumerate_contracts(root, exp)
        except Exception as e:
            print(f"  [option_bulk_hist_greeks] could not enumerate {root}/{exp}'s "
                  f"contract universe via snapshot bulk_greeks: {type(e).__name__}: {e}")
            raise

        all_rows: List[Dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(f"  [option_bulk_hist_greeks] {root}/{exp}: pulling {total} contracts' "
              f"history for {start_date}-{end_date} "
              f"({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return k_theta, right, self.option_hist_all_greeks_single(
                root, exp, k_theta, right, start_date, end_date,
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
                    print(f"  [option_bulk_hist_greeks] {root}/{exp} {k_theta}/{right}: "
                          f"skipped ({type(e).__name__}: {e})")
                if done % 50 == 0 or done == total:
                    print(f"  [option_bulk_hist_greeks] {root}/{exp}: {done}/{total} contracts done "
                          f"({empty} never traded in range, {errored} genuine errors)")
        return all_rows

    def option_bulk_hist_oi(
        self, root: str, exp: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
        """Daily OI history for a whole expiry's chain.

        Enumerates the chain via snapshot bulk_oi, then fans out per-contract
        hist/open_interest calls across a thread pool.
        """
        try:
            universe = self.option_bulk_oi(root, exp)
        except Exception as e:
            print(f"  [option_bulk_hist_oi] could not enumerate {root}/{exp}'s "
                  f"contract universe via snapshot bulk_oi: {type(e).__name__}: {e}")
            raise

        contracts = []
        seen = set()
        for row in universe:
            try:
                k_theta = int(float(row['strike']))
                right = row['right']
            except (KeyError, TypeError, ValueError):
                continue
            key = (k_theta, right)
            if key not in seen:
                seen.add(key)
                contracts.append(key)

        all_rows: List[Dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(f"  [option_bulk_hist_oi] {root}/{exp}: pulling {total} contracts' "
              f"OI history for {start_date}-{end_date} "
              f"({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return k_theta, right, self.option_hist_open_interest_single(
                root, exp, k_theta, right, start_date, end_date,
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
                    print(f"  [option_bulk_hist_oi] {root}/{exp} {k_theta}/{right}: "
                          f"skipped ({type(e).__name__}: {e})")
                if done % 50 == 0 or done == total:
                    print(f"  [option_bulk_hist_oi] {root}/{exp}: {done}/{total} contracts done "
                          f"({empty} never traded in range, {errored} genuine errors)")
        return all_rows

    def option_bulk_hist_oi_by_day(
        self, root: str, exp: str,
        start_date: str, end_date: str,
    ) -> List[Dict]:
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

        all_rows: List[Dict] = []
        total, done, empty, errored = len(days), 0, 0, 0
        print(f"  [option_bulk_hist_oi_by_day] {root}/{exp}: {total} weekdays "
              f"{start_date}-{end_date} ({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_day(d):
            path = f"/api/theta/bulk_hist/option/open_interest/{root}/{exp}"
            r = self._get_with_retry(
                path, params={"start_date": d, "end_date": d},
            )
            if r.status_code == 404:
                return []
            r.raise_for_status()
            rows = self._parse_rows(r)
            for row in rows:
                row.setdefault('date', d)
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
                    print(f"  [option_bulk_hist_oi_by_day] {root}/{exp} {d}: "
                          f"skipped ({type(e).__name__}: {e})")
                if done % 25 == 0 or done == total:
                    print(f"  [option_bulk_hist_oi_by_day] {root}/{exp}: {done}/{total} days "
                          f"({empty} no session, {errored} genuine errors)")
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
        """
        try:
            quote = self.stock_snapshot_quote(ticker)
            for key in ['mid', 'bid', 'ask', 'last']:
                v = quote.get(key)
                if v not in (None, '') and float(v) > 0:
                    return float(v)
            if not quote:
                print(f"  [fetch_spot_price] {ticker}: empty quote response "
                      f"(no rows) -- trying last trade print.")
            else:
                print(f"  [fetch_spot_price] {ticker}: live quote has no usable "
                      f"bid/ask/last (likely outside regular trading hours): "
                      f"{quote} -- trying last trade print.")
        except Exception as e:
            print(f"  [fetch_spot_price] {ticker}: snapshot request failed "
                  f"({type(e).__name__}: {e}) -- trying last trade print.")

        try:
            trade = self.stock_snapshot_trade(ticker)
            price = trade.get('price')
            if price not in (None, '') and float(price) > 0:
                print(f"  [fetch_spot_price] {ticker}: no live bid/ask quote right now "
                      f"(outside regular trading hours, most likely) -- using last trade "
                      f"print {float(price):.4f} instead of a live mid. Not a live quote.")
                return float(price)
            print(f"  [fetch_spot_price] {ticker}: no usable last trade print either -- "
                  f"falling back to last daily close.")
        except Exception as e:
            print(f"  [fetch_spot_price] {ticker}: trade snapshot request failed "
                  f"({type(e).__name__}: {e}) -- falling back to last daily close.")

        try:
            end = datetime.now()
            start = end - timedelta(days=7)
            rows = self.hist_stock_eod(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
            )
            closes = [
                float(r['close']) for r in rows
                if r.get('close') and float(r['close']) > 0
            ]
            if closes:
                return closes[-1]
            print(f"  [fetch_spot_price] {ticker}: daily-close fallback "
                  f"returned no usable rows either.")
        except Exception as e:
            print(f"  [fetch_spot_price] {ticker}: daily-close fallback also "
                  f"failed ({type(e).__name__}: {e}).")
        return 0.0

    def hist_stock_eod(self, root: str, start_date: str, end_date: str) -> List[Dict]:
        """Daily OHLCV history.  Paginates into <=28-day chunks to avoid
        proxy-side 502 on long lookbacks."""
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)

        all_rows: List[Dict] = []
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

    def hist_stock_ohlc(self, root: str, start_date: str, end_date: str) -> List[Dict]:
        """One-minute stock OHLCV history from the verified stock/ohlc route."""
        r = self._get_with_retry(
            f"/api/theta/hist/stock/ohlc/{root}",
            params={"start_date": start_date, "end_date": end_date},
        )
        r.raise_for_status()
        return self._parse_rows(r)

    def fetch_option_iv(
        self, ticker: str, strike: float, T: float,
        option_type: str = "call",
        exp: Optional[str] = None,
    ) -> Tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
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
                expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime("%Y%m%d")
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
            right = "C" if option_type == 'call' else "P"

            greeks = self.option_bulk_greeks(ticker, nearest_exp)
            k_theta = strike_to_theta(strike)
            for row in greeks:
                if int(row['strike']) == k_theta and row['right'] == right:
                    iv = row.get('implied_vol')
                    bid = row.get('bid')
                    ask = row.get('ask')
                    price = (
                        (float(bid) + float(ask)) / 2.0
                        if bid and ask and float(bid) > 0
                        else None
                    )
                    if not price:
                        price = float(bid) if bid and float(bid) > 0 else None
                    ts = row.get('underlying_timestamp', '')
                    if not ts and 'date' in row:
                        ts = str(row['date'])
                    return (float(iv), price, nearest_exp, ts) if iv else (None, price, nearest_exp, ts)
        except Exception as e:
            print(f"[ThetaData] fetch_option_iv error: {e}")
        return None, None, None, None

    def fetch_risk_free_rate(self, T: float = 1.0):
        """Risk-free rate for tenor ~T (years) from PotatoHedge's yield curve.

        Returns a decimal (e.g. 0.046) or None if unavailable/implausible.
        """
        try:
            today = datetime.now()
            target = (today + timedelta(days=int(max(T, 0.01) * 365))).strftime("%Y%m%d")
            start = (today - timedelta(days=7)).strftime("%Y%m%d")
            data = self.get_yield_curve(start_date=start, target_date=target)
            rate = self._first_numeric_field(data, ['risk_free_rate', 'rate', 'yield'])
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

    def fetch_dividend_yield(self, ticker: str, spot: Optional[float] = None) -> float:
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
                    'start_date': start.strftime("%Y%m%d"),
                    'end_date': end.strftime("%Y%m%d"),
                },
            )
            r.raise_for_status()
            rows = self._rows_from_any(r.json())
            total = 0.0
            for row in rows:
                amt = self._coerce_number(row.get('dividend_amount'))
                if amt is None:
                    for k, v in row.items():
                        kl = str(k).lower()
                        if 'dividend' in kl and 'amount' in kl:
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
                    'start_date': start.strftime("%Y%m%d"),
                    'end_date': end.strftime("%Y%m%d"),
                },
            )
            r.raise_for_status()
            rows = self._rows_from_any(r.json())
            closes = []
            for row in rows:
                num = self._coerce_number(row.get('close'))
                if num and num > 0:
                    closes.append(num)
            if len(closes) < 5:
                return None
            closes = _np.array(closes[-(window + 1):], dtype=float)
            logret = _np.log(closes[1:] / closes[:-1])
            vol = float(_np.std(logret) * _np.sqrt(252))
            if 0.0 < vol < 5.0:
                return vol
            return None
        except Exception:
            return None

    def fetch_beta(
        self, ticker: str, market_proxy: str = 'SPY', window: int = 252,
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
                        'start_date': start.strftime("%Y%m%d"),
                        'end_date': end.strftime("%Y%m%d"),
                    },
                )
                r.raise_for_status()
                rows = self._rows_from_any(r.json())
                out = []
                for row in rows:
                    num = self._coerce_number(row.get('close'))
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
        self, root: str, start_date: str, end_date: str,
        latest_only: bool = True,
    ):
        """PotatoHedge's own vendor-computed dealer positioning/GEX.

        This is NOT a raw ThetaData passthrough — it lives under /api/db/,
        PotatoHedge's own value-add analytics layer.
        """
        r = self._get("/api/db/dealer_positioning", params={
            'root': root,
            'start_date': start_date,
            'end_date': end_date,
            'latest_only': latest_only,
            'use_csv': False,
        })
        r.raise_for_status()
        return r.json()

    def get_yield_curve(
        self, start_date: str,
        end_date: Optional[str] = None,
        target_date: Optional[str] = None,
    ):
        """PotatoHedge's own Treasury yield curve endpoint.

        If target_date is given, returns the closest applicable forward
        risk-free rate for that tenor instead of a full curve.
        """
        params = {'start_date': start_date, 'use_csv': False}
        if end_date is not None:
            params['end_date'] = end_date
        if target_date is not None:
            params['target_date'] = target_date
        r = self._get("/api/db/yield_curve", params=params)
        r.raise_for_status()
        return r.json()

    def close(self):
        """Close the underlying httpx client."""
        self.client.close()