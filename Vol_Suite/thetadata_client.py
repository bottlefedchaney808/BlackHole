#!/usr/bin/env python3
"""thetadata_client.py

Single, shared ThetaData client for the whole Vol_Suite project, proxied
through api.potatohedge.com (Cloudflare Access).

Every module in this project (dealer_positioning, variance_swap_live,
variance_swap_screener, correlation_engine, garch_analysis) previously carried
its own copy-pasted ThetaDataController class -- four separate copies, each
with the same hardcoded Cloudflare Access credentials committed directly in
source. This module replaces all of them: one client, one place to fix
credentials, one place to fix/extend endpoints.

Credentials are read from THETADATA_CF_ACCESS_CLIENT_ID /
THETADATA_CF_ACCESS_CLIENT_SECRET environment variables (or a local .env file,
gitignored -- see .env.example). They are intentionally NOT hardcoded here.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

import httpx

# How many contracts' history to pull concurrently in option_bulk_hist_greeks
# / option_bulk_hist_oi. Override with THETADATA_HIST_CONCURRENCY.
#
# HISTORY (worth keeping, because the obvious conclusion was the wrong one):
# a full run at concurrency=10 came back with 442/442 contracts reporting
# "never traded", including contracts confirmed by sequential testing to
# have real data. That looked exactly like proxy-side rate limiting, so this
# was dropped to 1 (fully sequential) and the failures went away. The actual
# cause turned out to be a VPN sitting in front of every request the whole
# time -- VPN tunnels routinely fall over under many simultaneous
# connections, which reproduces the identical signature (parallel fails,
# sequential works, retries don't help because the tunnel, not the proxy, is
# the thing saturating). With the VPN off, concurrency is back on.
#
# 8 rather than a bigger number: the proxy is shared infrastructure and
# there's no published rate limit, so this is deliberately moderate. The
# transient-failure retry in _get_with_retry below is what actually makes a
# run robust; concurrency only decides how fast it gets there.
_HIST_GREEKS_CONCURRENCY = int(os.environ.get("THETADATA_HIST_CONCURRENCY", "8"))

# Statuses worth retrying on a per-request basis. 404 is here because this
# proxy demonstrably returns false-negative 404s (see
# option_hist_all_greeks_single's docstring for the non-monotonic repro);
# 502/503/504 are here because the upstream Terminal intermittently times
# out reconstructing history and the proxy surfaces that as a gateway error
# -- a full SPY run produced 434 of them in a row while a VPN was degrading
# the connection, every one of which was transient.
_RETRY_STATUSES = (404, 502, 503, 504)
_RETRY_ATTEMPTS = 3


def strike_to_theta(k: float) -> int:
    return int(round(k * 1000))


def strike_from_theta(k: int) -> float:
    return k / 1000.0


def _load_dotenv_file(env_path: str) -> None:
    """Minimal .env loader (no external dependency). Reads the given .env file
    and populates os.environ for any keys not already set, so real environment
    variables (and anything loaded from an earlier source) always take
    precedence over it."""
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


def _load_dotenv_once(_loaded=[False]):
    """Loads credentials from .env sources into os.environ, in order, without
    ever overwriting a real environment variable or a value found earlier in
    this search:

      1. A .env file sitting next to this module (original, per-suite
         behavior -- kept as-is for any checkout that still keeps its own
         Vol_Suite/.env).
      2. The shared root .env one level up (Path(__file__).resolve().parents[1]
         / '.env'), i.e. FinancialDevelopment/.env -- the single consolidated
         credentials file for all suites (Options_Suite, Vol_Suite,
         VaR_Tools_Simulations, sentiment-scanner). Only consulted if the
         credentials aren't already set by step 1 or the real environment.
    """
    if _loaded[0]:
        return
    _loaded[0] = True
    _load_dotenv_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    if not (os.environ.get("THETADATA_CF_ACCESS_CLIENT_ID") and
            os.environ.get("THETADATA_CF_ACCESS_CLIENT_SECRET")):
        root_env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
        _load_dotenv_file(root_env_path)


class ThetaDataController:
    """ThetaData client proxied through api.potatohedge.com (Cloudflare Access).

    Shared across the whole suite. If credentials are missing, this raises
    immediately -- every call site should wrap construction in a try/except
    that falls back to yfinance (used only as a fallback in this project, per
    project convention: ThetaData is the primary source everywhere it can be).
    """

    def __init__(self, base_url: str = "https://api.potatohedge.com"):
        _load_dotenv_once()
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

    def _get(self, path: str, params: Optional[dict] = None) -> httpx.Response:
        return self.client.get(f"{self.base_url}{path}", params=params, timeout=60.0)

    def _get_with_retry(self, path: str, params: Optional[dict] = None,
                        attempts: int = _RETRY_ATTEMPTS) -> httpx.Response:
        """GET with bounded retry on the transient failures this proxy
        actually produces (_RETRY_STATUSES) and on transport-level errors
        (connection resets / server-disconnects, which show up as
        httpx.TransportError subclasses -- a real SPY run hit
        RemoteProtocolError: "Server disconnected without sending a
        response" mid-pull).

        Returns the LAST response even if it's still an error status --
        callers decide whether a persistent 404 means "genuinely no data
        here" (fine, skip it) or something worth raising on. Transport
        errors have no response to return, so the final one is re-raised.

        Backoff is linear (0.5s, 1.0s, 1.5s) rather than exponential: these
        are per-contract calls inside a pool of hundreds, and the failures
        are brief blips rather than sustained overload, so long sleeps just
        stretch the total run without improving the hit rate.
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
        raise last_exc

    def _parse_rows(self, r: httpx.Response):
        data = r.json()
        if not isinstance(data, list) or len(data) < 2:
            return []
        headers = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]

    @staticmethod
    def _coerce_number(x):
        try:
            v = float(x)
            return None if v != v else v  # reject NaN
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _rows_from_any(data):
        """Normalize any ThetaData JSON shape into a list of dicts. The proxy
        returns three shapes depending on endpoint/params, all seen live:
          1. list-of-lists       [headers_row, data_row, ...]   (EOD w/o use_csv)
          2. hist nested         {"header":{"format":[...]},"response":[[...],...]} (dividends)
          3. columnar            {"close":[...], "open":[...], ...}  (EOD w/ use_csv)
        """
        if isinstance(data, dict) and isinstance(data.get('response'), list):
            resp = data['response']
            fmt = (data.get('header') or {}).get('format') if isinstance(data.get('header'), dict) else None
            if fmt and resp and isinstance(resp[0], list):
                return [dict(zip(fmt, row)) for row in resp]
            if resp and isinstance(resp[0], dict):
                return resp
        if isinstance(data, dict):
            list_cols = {k: v for k, v in data.items() if isinstance(v, list)}
            if list_cols:
                n = min(len(v) for v in list_cols.values())
                return [{k: list_cols[k][i] for k in list_cols} for i in range(n)]
        if isinstance(data, list) and len(data) >= 2 and isinstance(data[0], list):
            return [dict(zip(data[0], row)) for row in data[1:]]
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data
        return []

    # ---------- Options ----------
    def list_expirations(self, root: str) -> List[str]:
        r = self._get(f"/api/theta/list/expirations/{root}")
        r.raise_for_status()
        raw = r.json()
        if isinstance(raw, list):
            return [str(e) for e in raw]
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        return []

    def list_strikes(self, root: str, exp: str) -> List[float]:
        """FIXED (2026-07-23): this used to run every value through
        strike_from_theta() (divide by 1000), which assumes /list/strikes
        returns theta-scaled ints the way most other bulk endpoints do.
        Live testing (diagnose_trade_greeks_endpoint.py's ATM-strike lookup)
        caught this returning 0.95 as the "nearest strike to spot" for a
        stock trading at $747 -- only explainable if every strike in the
        response was already a real dollar value (e.g. 950 for a genuine
        $950 strike) and got wrongly divided down to 0.95. Fixed to use the
        value as-is; if a future response turns out to actually be
        theta-scaled after all, this will show up as absurdly large
        "dollar" strikes (950000 instead of 950), which is easy to spot.
        """
        r = self._get(f"/api/theta/list/strikes/{root}/{exp}")
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

    def option_bulk_greeks(self, root: str, exp: str):
        r = self._get(f"/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_greeks_second_order(self, root: str, exp: str):
        """Native ThetaData 2nd-order greeks (vanna, charm, vomma/volga, veta,
        etc.) for every strike/right at one expiry, in one call -- a raw
        ThetaData passthrough (catalogued in POTATOHEDGE_API_REFERENCE.md as
        recovered-but-never-wired-up), not a from-scratch calculation like
        dealer_positioning.py's gamma. Used by options_chain_scanner.py.
        Exact field names haven't been verified against a live response in
        this environment -- see _SECOND_ORDER_FIELD_CANDIDATES in
        options_chain_scanner.py for the fallback key-name matching this
        feeds into, same pattern as dealer_positioning._extract_greek_field."""
        r = self._get(f"/api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    def option_bulk_oi(self, root: str, exp: str):
        r = self._get(f"/api/theta/bulk_snapshot/option/open_interest/{root}/{exp}")
        r.raise_for_status()
        return self._parse_rows(r)

    # ---------- Range-honoring historical routes (the cheap path) ----------
    #
    # CRITICAL PROPERTY, established 2026-07-24
    # (diagnostics/diagnose_date_collapse.py + diagnose_range_route_hunt.py):
    #
    #   hist/option/all_greeks       returns ONE day  (ignores end_date)
    #   hist/option/open_interest    returns ONE day  (ignores end_date)
    #   hist/option/eod              HONORS the range  <-- the only one that does
    #   bulk_hist/option/open_interest   whole chain, ONE day
    #
    # That asymmetry drives the whole design below. Pulling greeks the
    # obvious way costs 430 contracts x 110 trading days = ~47,000 requests
    # per expiry. Pulling EOD prices per contract over a full range costs
    # ~430, and whole-chain OI one day at a time costs ~110. So we buy prices
    # cheaply and reconstruct IV/gamma ourselves (implied_vol.py) rather than
    # buying vendor greeks at 86x the request cost.

    def option_hist_eod_single(self, root: str, exp: str, strike_theta: int, right: str,
                               start_date: str, end_date: str) -> List[Dict]:
        """Per-contract EOD history over a DATE RANGE -- one row per trading
        day, in a single request.

        Confirmed live: a 20260706-20260723 request returned 14 distinct
        dates. Columns are open/high/low/close/bid/ask/volume/count plus
        strike/right/expiration and `created` (the timestamp this row's date
        comes from -- there is no literal `date` column, which is why
        _last_bar_per_date normalizes it).

        No greeks and no implied_vol here: that's the trade. Prices are what
        this route sells, and prices are enough, because IV inverts out of
        them (implied_vol.implied_vol) and gamma follows from IV
        (dealer_positioning.bs_gamma).

        Still chunked at 28 days, not because this route needs it but because
        hist/stock/eod demonstrably 502s on multi-month ranges (ThetaData's
        own Request-Sizing guidance puts the safe EOD window near a month),
        and there is no reason to assume the option variant is more generous.
        """
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {"start_date": chunk_start.strftime(fmt),
                      "end_date": chunk_end.strftime(fmt)}
            path = f"/api/theta/hist/option/eod/{root}/{exp}/{strike_theta}/{right}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                # Genuine gap for this chunk only -- a contract's listing
                # postdates the start of a long lookback more often than not.
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    def option_bulk_hist_eod(self, root: str, exp: str,
                             start_date: str, end_date: str) -> List[Dict]:
        """EOD price history for a whole expiry's chain over a date range.

        Enumerate the contract universe from today's snapshot, then one
        range-covering request per contract -- ~430 requests for any lookback
        length, because each one carries the entire range. Compare with the
        greeks route, where the same coverage costs ~47,000.
        """
        try:
            universe = self.option_bulk_greeks(root, exp)
        except Exception as e:
            # Raise, never return [] -- see option_bulk_hist_oi.
            print(f"  [option_bulk_hist_eod] could not enumerate {root}/{exp}'s "
                  f"contract universe: {type(e).__name__}: {e}")
            raise

        contracts, seen = [], set()
        for row in universe:
            try:
                key = (int(float(row['strike'])), row['right'])
            except (KeyError, TypeError, ValueError):
                continue
            if key not in seen:
                seen.add(key)
                contracts.append(key)

        all_rows: List[Dict] = []
        total, done, empty, errored = len(contracts), 0, 0, 0
        print(f"  [option_bulk_hist_eod] {root}/{exp}: pulling {total} contracts' "
              f"EOD history for {start_date}-{end_date} "
              f"({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return self.option_hist_eod_single(root, exp, k_theta, right,
                                               start_date, end_date)

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
                    print(f"  [option_bulk_hist_eod] {root}/{exp} {k_theta}/{right}: "
                          f"skipped ({type(e).__name__}: {e})")
                if done % 50 == 0 or done == total:
                    print(f"  [option_bulk_hist_eod] {root}/{exp}: {done}/{total} done "
                          f"({empty} never traded in range, {errored} genuine errors)")
        return all_rows

    def option_bulk_hist_oi_by_day(self, root: str, exp: str,
                                   start_date: str, end_date: str) -> List[Dict]:
        """Whole-chain open interest, one request per calendar day.

        bulk_hist/option/open_interest returns the ENTIRE chain (344 rows in
        the confirmed run) but only for `start_date`. Iterating days is
        therefore ~110 requests for a 5-month lookback, versus 430 contracts
        x 110 days if you go per-contract. Same data, two orders of magnitude
        cheaper.

        Weekends and holidays are requested and come back empty or 404; both
        are treated as "no session", not as errors, because there is no
        market calendar in this project to skip them a priori and guessing
        one wrong would silently drop real trading days.
        """
        fmt = "%Y%m%d"
        day = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        days = []
        while day <= end_dt:
            if day.weekday() < 5:            # cheap win: skip Sat/Sun only
                days.append(day.strftime(fmt))
            day += timedelta(days=1)

        all_rows: List[Dict] = []
        total, done, empty, errored = len(days), 0, 0, 0
        print(f"  [option_bulk_hist_oi_by_day] {root}/{exp}: {total} weekdays "
              f"{start_date}-{end_date} ({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_day(d):
            path = f"/api/theta/bulk_hist/option/open_interest/{root}/{exp}"
            r = self._get_with_retry(path, params={"start_date": d, "end_date": d})
            if r.status_code == 404:
                return []
            r.raise_for_status()
            rows = self._parse_rows(r)
            for row in rows:
                row.setdefault('date', d)     # holiday-proof: stamp what we asked for
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

    def option_hist_open_interest_single(self, root: str, exp: str, strike_theta: int, right: str,
                                          start_date: str, end_date: str) -> List[Dict]:
        """Single-contract historical open interest. OI is a once-daily
        figure (not intraday-bucketed the way greeks are), so no `ivl` param
        -- same path-segment convention confirmed live for
        hist/option/all_greeks and hist/option/trade_greeks. Chunked into
        <=28-day spans like the other per-contract historical pulls in this
        file. A 404 is retried a couple of times before being trusted as a
        genuine data gap (same proven-flaky-proxy fix as
        option_hist_all_greeks_single -- diagnose_wide_range_chunks.py
        showed a 404 on this route family isn't reliably deterministic
        based on the actual date range requested).
        """
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {"start_date": chunk_start.strftime(fmt), "end_date": chunk_end.strftime(fmt)}
            path = f"/api/theta/hist/option/open_interest/{root}/{exp}/{strike_theta}/{right}"
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                # Still 404 after retries -- treat as a genuine gap for this
                # chunk only, not for the whole contract (a contract's strike
                # range grows over its life, so early chunks legitimately
                # have no data while later ones do).
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    def option_bulk_hist_oi(self, root: str, exp: str, start_date: str, end_date: str) -> List[Dict]:
        """Daily OI history for a whole expiry's chain. Powers
        replication_reference.py's seed-plus-accumulate construction (§3's
        Layer 1b/Layer 2 fusion in DEALER_POSITIONING_V2_DESIGN.md): needs
        day-over-day OI *changes* per strike, not just today's level.

        REPLACED (2026-07-23): the original inferred bulk_hist/option/
        open_interest/{root}/{exp} path was NEVER actually verified live and
        turned out to be exactly the same dead route family as
        bulk_hist/option/all_greeks -- confirmed 404 running
        backtest_stage3.py for real (whole-chain bulk_hist routes simply
        aren't wired up on this proxy; see option_bulk_hist_greeks's
        docstring for the fuller history of that discovery). Same fix:
        enumerate the expiry's contracts via the confirmed-working snapshot
        bulk_oi call, then pull each contract's history individually via
        option_hist_open_interest_single, fanned out across a thread pool
        rather than one at a time.
        """
        try:
            universe = self.option_bulk_oi(root, exp)
        except Exception as e:
            # Raise, don't return []. An enumeration failure means "we never
            # found out what's here", which is a completely different thing
            # from "we looked and there's nothing here" -- and returning an
            # empty list makes the two indistinguishable to every caller.
            # This bit hard once already: a transient 502 on this one call
            # was reported upward as a clean empty result and the run
            # carried on producing a confident-looking report built on no
            # data at all.
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
        total = len(contracts)
        done = 0
        empty = 0
        errored = 0
        print(f"  [option_bulk_hist_oi] {root}/{exp}: pulling {total} contracts' "
              f"OI history for {start_date}-{end_date} ({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return k_theta, right, self.option_hist_open_interest_single(
                root, exp, k_theta, right, start_date, end_date)

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {pool.submit(_fetch_one, k_theta, right): (k_theta, right)
                       for k_theta, right in contracts}
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

    def option_hist_all_greeks_single(self, root: str, exp: str, strike_theta: int, right: str,
                                       start_date: str, end_date: str, ivl: int = 900000) -> List[Dict]:
        """Single-contract historical greeks, bucketed into `ivl`-ms
        intervals (default 900000 = 15 min). CONFIRMED LIVE 2026-07-23
        (diagnose_hist_all_greeks_single_contract.py): path-segment style
        matching hist/option/trade_greeks's confirmed-working convention,
        NOT the query-param style ThetaData's own raw docs show -- this
        proxy is path-segment-only everywhere, full stop (every query-param
        attempt tested across every route family 404'd). Without `ivl` this
        502s exactly like the whole-chain bulk_hist/option/all_greeks route
        did -- that route's 502 really was ivl-shaped, it just also failed
        for the unrelated reason that the whole-chain form itself isn't
        wired up on this proxy at all (still 502s regardless of ivl).

        Response already includes vanna/charm/vomma/veta/vera/speed/zomma/
        color/ultima alongside the first-order greeks and implied_vol --
        confirmed real values in the live response, so this single call
        covers what a separate greeks_second_order call would have (that
        route 502s on this proxy regardless of params, but is redundant
        given this).

        Chunks into <=28-day spans, same pattern as the other bulk_hist
        methods in this file, since a single day's response is already the
        header row + one row per interval (~28 rows for a 15-min bucket
        over a 6.5hr session) -- a multi-month single-contract request would
        multiply that by every trading day and risk the same kind of
        proxy-side timeout the whole-chain route hit.

        FIXED (2026-07-23, real bug found running backtest_stage3.py SPY
        90): a contract's strike range GROWS over its life as the
        underlying trades further from the money -- a far-dated expiry's
        current (today's) 400+ strikes did not all exist 5 months ago, so
        an early chunk can legitimately 404 ("no data for this contract
        yet"). Originally this treated every 404 as final and moved on. But
        direct testing (diagnose_wide_range_chunks.py) proved that's wrong:
        the SAME contract, SAME end date, at 10/14/16-day widths all
        returned 200 with real data, while the 12-day width in between
        404'd -- a real size or date-boundary limit can't produce a
        non-monotonic result like that (a wider range containing the exact
        same dates as a failing narrower one succeeding rules it out). This
        proxy is simply flaky: some fraction of requests get a false "no
        data" 404 unrelated to the actual query. A single 404 is therefore
        NOT trustworthy on its own -- retry a couple of times with a short
        delay before accepting it as a genuine data gap. Any other status
        still raises immediately, since that's a real error, not this
        specific flakiness pattern.
        """
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)
        all_rows: List[Dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            params = {"start_date": chunk_start.strftime(fmt),
                      "end_date": chunk_end.strftime(fmt), "ivl": ivl}
            path = f"/api/theta/hist/option/all_greeks/{root}/{exp}/{strike_theta}/{right}"
            # Don't trust a single 404/502 -- retry before concluding this
            # chunk genuinely has no data (see docstring: proven flaky via
            # diagnose_wide_range_chunks.py's non-monotonic results).
            r = self._get_with_retry(path, params=params)
            if r.status_code == 404:
                # Still 404 after retries -- accept as a genuine data gap
                # (this contract/strike really didn't trade in this window)
                # and move to the next chunk, not the whole contract.
                chunk_start = chunk_end + timedelta(days=1)
                continue
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return self._stamp_contract(all_rows, strike_theta, right)

    @staticmethod
    def _stamp_contract(rows: List[Dict], strike_theta: int, right: str) -> List[Dict]:
        """Write the contract's own identity onto every row.

        These are SINGLE-CONTRACT endpoints: root/expiry/strike/right are all
        path segments, so the response body has no reason to echo them back,
        and the confirmed live payload is per-interval market data
        (ms_of_day, the greeks, implied_vol, date) with no strike/right
        column at all. Every downstream consumer, though, is reassembling a
        whole chain out of hundreds of these per-contract pulls and keys
        everything on (strike, right) -- and each one reads them with a
        `try: row['strike'] ... except KeyError: continue`, so a missing
        column doesn't raise, it just silently drops the row on the floor.

        That is exactly the failure mode that makes a run look like it
        worked: hundreds of contracts report "done" with real row counts,
        and the backtest still ends up with a couple of usable days. Stamping
        the identity here -- where it's known for certain, from the loop
        variable that generated the request -- removes the dependency on the
        response shape entirely. Doesn't overwrite anything the API does
        return, so if a route ever does include these, the API's own values
        win.
        """
        for row in rows:
            row.setdefault('strike', strike_theta)
            row.setdefault('right', right)
        return rows

    @staticmethod
    def _normalize_date(row: Dict) -> Optional[str]:
        """Pull a YYYYMMDD date out of a row regardless of which date-shaped
        field the route happens to use. Confirmed live: hist/stock/eod uses
        'created' (a full timestamp), while the option history routes use
        'date' -- so anything consuming both needs this fallback rather than
        assuming one of them.
        """
        for key in ('date', 'Date', 'created', 'datetime'):
            val = row.get(key)
            if not val:
                continue
            digits = str(val)[:10].replace('-', '')
            if len(digits) == 8 and digits.isdigit():
                return digits
        return None

    @classmethod
    def _last_bar_per_date(cls, rows: List[Dict]) -> List[Dict]:
        """Collapse intraday interval bars down to one row per calendar date.
        Keeps the LAST bar of each day (highest ms_of_day) as the
        EOD-equivalent row, closest in spirit to how option_bulk_greeks'
        own snapshot greeks are computed off the closing price.

        Writes the normalized YYYYMMDD back onto the row as 'date' so every
        downstream consumer sees one consistent date format, instead of each
        one separately re-deriving it (and each one silently dropping rows
        whose format it doesn't recognize).
        """
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

    def option_bulk_hist_greeks(self, root: str, exp: str, start_date: str, end_date: str) -> List[Dict]:
        """Daily EOD-equivalent greeks (incl. implied_vol, gamma, and the
        full higher-order set) history for a whole expiry's chain.

        REPLACED (2026-07-23): the original implementation called
        bulk_hist/option/all_greeks/{root}/{exp} directly -- CONFIRMED
        BROKEN on api.potatohedge.com (502 Bad Gateway, with or without
        `ivl`, even for a single day -- not a request-sizing issue, the
        whole-chain route just isn't wired up correctly on this proxy).
        bulk_hist/option/all_trade_greeks (the whole-chain trade-greeks
        alternative) doesn't exist on this proxy at all (clean 404). See
        diagnose_bulk_hist_endpoint.py for the full repro.

        The only confirmed-working historical greeks source on this proxy
        is per-contract (option_hist_all_greeks_single, above). This method
        now enumerates the expiry's full (strike, right) universe via the
        CONFIRMED-working snapshot bulk_greeks call (today's chain -- strikes
        for an already-listed expiration don't change day to day, so this is
        a safe stand-in for "every contract that ever traded in this range"),
        then loops the single-contract call over every one of them, keeping
        only the last intraday bar of each day as that day's EOD-equivalent
        row. Same per-contract call volume as the hist/option/trade_greeks
        fallback (~335 calls/day-range for SPY) but each call returns ~28
        bucketed rows instead of 1000+ raw ticks, so it's far lighter to
        pull and needs no aggregation math (just "keep the last one").

        SPED UP (2026-07-23): the first working version fetched contracts
        one at a time -- for a far-dated expiry (442 contracts here), that's
        thousands of sequential HTTP round-trips through the proxy and took
        long enough that Jason killed a real run with Ctrl+C before it
        finished. These are all independent GETs (no shared state, no
        ordering dependency), so they're now fanned out across a small
        thread pool (_HIST_GREEKS_CONCURRENCY workers) instead of run one
        after another -- same total number of requests, but many in flight
        at once instead of waiting on each round-trip serially.
        """
        try:
            universe = self.option_bulk_greeks(root, exp)
        except Exception as e:
            # Raise, don't return [] -- see option_bulk_hist_oi for the full
            # reasoning. A failed enumeration is a fetch failure, not an
            # empty result, and collapsing the two is how a run ends up
            # reporting a clean pass over nothing.
            print(f"  [option_bulk_hist_greeks] could not enumerate {root}/{exp}'s "
                  f"contract universe via snapshot bulk_greeks: {type(e).__name__}: {e}")
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
        total = len(contracts)
        done = 0
        empty = 0     # 404'd on every chunk -- contract never traded in this range at all
        errored = 0   # a genuine, unexpected failure (not a data gap)
        print(f"  [option_bulk_hist_greeks] {root}/{exp}: pulling {total} contracts' "
              f"history for {start_date}-{end_date} ({_HIST_GREEKS_CONCURRENCY} concurrent)...")

        def _fetch_one(k_theta, right):
            return k_theta, right, self.option_hist_all_greeks_single(
                root, exp, k_theta, right, start_date, end_date)

        with ThreadPoolExecutor(max_workers=_HIST_GREEKS_CONCURRENCY) as pool:
            futures = {pool.submit(_fetch_one, k_theta, right): (k_theta, right)
                       for k_theta, right in contracts}
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

    def option_snapshot_quote(self, root: str, exp: str, strike: float, right: str = "C"):
        k = strike_to_theta(strike)
        r = self._get(f"/api/theta/snapshot/option/quote/{root}/{exp}/{k}/{right}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    # ---------- Stock ----------
    def stock_snapshot_quote(self, root: str):
        r = self._get(f"/api/theta/snapshot/stock/quote/{root}")
        r.raise_for_status()
        rows = self._parse_rows(r)
        return rows[0] if rows else {}

    def fetch_spot_price(self, ticker: str) -> float:
        """Returns 0.0 on any failure (bad symbol, network/auth error, empty
        quote, etc.) so callers like volatility_suite.py's _ticker_exists()
        can treat it as a simple yes/no without crashing the interactive
        flow. IMPORTANT: 0.0 does NOT mean "this ticker doesn't exist" --
        it means "something went wrong," and those are very different
        problems to debug. Exceptions/empty results are printed (not
        silently swallowed) rather than a bare `except: pass`, since "MU
        doesn't resolve" for a massively liquid, decades-listed stock is
        almost never actually a bad-symbol case.

        Confirmed live case (2026-07-23): the snapshot endpoint returned a
        real, well-formed quote row for MU (today's date, a real
        ms_of_day) with bid=ask=0.0000 and zero sizes -- ms_of_day
        (~18.8M ms, ~5:13am) puts this well before regular trading hours
        (9:30am-4:00pm ET), so there's simply no live NBBO yet, not a
        missing/invalid symbol. Falls back to the most recent daily close
        (hist_stock_eod) when the live snapshot has no usable bid/ask/last,
        so a run outside market hours doesn't misreport a perfectly valid,
        liquid ticker as nonexistent.
        """
        try:
            quote = self.stock_snapshot_quote(ticker)
            for key in ['mid', 'bid', 'ask', 'last']:
                if key in quote and quote[key]:
                    val = float(quote[key])
                    if val > 0:
                        return val
            if not quote:
                print(f"  [fetch_spot_price] {ticker}: empty quote response (no rows) -- "
                      f"falling back to last daily close.")
            else:
                print(f"  [fetch_spot_price] {ticker}: live quote has no usable "
                      f"bid/ask/last (likely outside regular trading hours): {quote} -- "
                      f"falling back to last daily close.")
        except Exception as e:
            print(f"  [fetch_spot_price] {ticker}: snapshot request failed "
                  f"({type(e).__name__}: {e}) -- falling back to last daily close.")

        try:
            end = datetime.now()
            start = end - timedelta(days=7)  # a week's pad for weekends/holidays
            rows = self.hist_stock_eod(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            closes = [float(r['close']) for r in rows if r.get('close') and float(r['close']) > 0]
            if closes:
                return closes[-1]
            print(f"  [fetch_spot_price] {ticker}: daily-close fallback returned no usable rows either.")
        except Exception as e:
            print(f"  [fetch_spot_price] {ticker}: daily-close fallback also failed "
                  f"({type(e).__name__}: {e}).")
        return 0.0

    def hist_stock_eod(self, root: str, start_date: str, end_date: str) -> List[Dict]:
        """Daily OHLCV history. start_date/end_date as YYYYMMDD strings.

        RESOLVED (2026-07-19): the earlier "KNOWN ISSUE" note here (502 on this
        route while everything else worked) was diagnosed as a request-sizing
        problem, not a bad path/entitlement issue. ThetaData's own docs
        (Articles/Performance-And-Tuning/Request-Sizing) recommend a MAX of
        ~1 month per request for EOD resolution -- the app was requesting a
        full 2-year range (e.g. 20240719-20260719) in a single call, which the
        Terminal apparently can't service in time (it likely reconstructs each
        day's OHLC from underlying tick data), and the proxy reports that
        upstream timeout as a 502. A manual single-shot test with a ~19-day
        range returned 200 with real data using the exact same path/params/
        credentials, which rules out a routing or entitlement problem and
        confirms it's purely range size.

        Fix: this method now paginates internally into <=1-month chunks and
        concatenates the results, so callers don't need to change anything.
        Endpoint path and parameter format (root as a path segment, dates as
        query params) were supplied by the proxy operator and are unchanged.

        Callers still fall back to yfinance on any remaining failure (see
        correlation_engine.fetch_price_history), so the suite degrades rather
        than breaking.
        """
        fmt = "%Y%m%d"
        start_dt = datetime.strptime(start_date, fmt)
        end_dt = datetime.strptime(end_date, fmt)

        all_rows: List[Dict] = []
        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=28), end_dt)
            r = self._get(f"/api/theta/hist/stock/eod/{root}",
                           params={"start_date": chunk_start.strftime(fmt),
                                   "end_date": chunk_end.strftime(fmt)})
            r.raise_for_status()
            all_rows.extend(self._parse_rows(r))
            chunk_start = chunk_end + timedelta(days=1)
        return all_rows

    def fetch_dividend_yield(self, ticker: str, spot: Optional[float] = None) -> float:
        """Trailing-12-month dividend yield (decimal) from PotatoHedge's stock
        dividend history. The old version read a 'dividend_yield' field off the
        snapshot quote, which the proxy doesn't actually expose there -- so it
        silently returned 0.0 for every ticker, including dividend payers, and
        biased the forward. This uses /api/theta/hist/stock/dividend (verified
        live: dividend_amount column in a header/format+response envelope) and
        sums the trailing year over spot. Returns 0.0 only when there genuinely
        are no dividends or the endpoint is unavailable."""
        try:
            if spot is None or spot <= 0:
                spot = self.fetch_spot_price(ticker)
            if not spot or spot <= 0:
                return 0.0
            end = datetime.now()
            start = end - timedelta(days=366)
            r = self._get(f"/api/theta/hist/stock/dividend/{ticker}",
                          params={'start_date': start.strftime("%Y%m%d"),
                                  'end_date': end.strftime("%Y%m%d")})
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

    def fetch_risk_free_rate(self, T: float = 0.25) -> Optional[float]:
        """Risk-free rate (decimal) for tenor ~T years from PotatoHedge's yield
        curve, replacing the hardcoded RISK_FREE_RATE=0.05 across the suite.
        Verified live shape: a list of dicts with a 'risk_free_rate' field quoted
        in PERCENT (e.g. 3.71). Returns None on any failure so callers keep their
        constant fallback rather than getting a silently-wrong rate."""
        try:
            today = datetime.now()
            target = (today + timedelta(days=int(max(T, 0.01) * 365))).strftime("%Y%m%d")
            start = (today - timedelta(days=7)).strftime("%Y%m%d")
            data = self.get_yield_curve(start_date=start, target_date=target)
            rows = data if isinstance(data, list) else self._rows_from_any(data)
            rate = None
            for row in (rows or []):
                if isinstance(row, dict):
                    for k in ('risk_free_rate', 'rate', 'yield'):
                        if k in row:
                            rate = self._coerce_number(row[k])
                            if rate is not None:
                                break
                if rate is not None:
                    break
            if rate is None:
                return None
            if rate > 1.0:  # quoted in percent
                rate /= 100.0
            return float(rate) if 0.0 <= rate <= 0.25 else None
        except Exception:
            return None

    # ---------- Native PotatoHedge analytics (NOT raw ThetaData passthroughs) ----------
    # Everything above this point mirrors a raw ThetaData endpoint 1:1 (confirmed
    # against the potatohedge package's own recovered source). The two methods below
    # hit PotatoHedge's own /api/db/ value-add analytics layer instead -- a vendor-
    # computed alternative to this project's from-scratch calculations. Endpoint
    # paths/params recovered from potatohedge's git history (PHClient.get_dealer_
    # positioning / get_yield_curve) but NOT yet exercised against a live response in
    # this project -- verify the returned schema before relying on it in a live path.

    def get_dealer_positioning(self, root: str, start_date: str, end_date: str, latest_only: bool = True):
        """Vendor-computed dealer positioning/GEX -- a native cross-check for this
        module's from-scratch dealer_positioning.py calculation. start_date/end_date
        as YYYYMMDD strings; latest_only=True returns one entry per contract instead
        of a time series."""
        r = self._get("/api/db/dealer_positioning", params={
            'root': root, 'start_date': start_date, 'end_date': end_date,
            'latest_only': latest_only, 'use_csv': False,
        })
        r.raise_for_status()
        return r.json()

    def get_yield_curve(self, start_date: str, end_date: Optional[str] = None, target_date: Optional[str] = None):
        """Treasury yield curve -- a candidate replacement for this suite's hardcoded
        RISK_FREE_RATE constants (see garch_analysis.py, variance_swap_live.py, and
        equivalents). If target_date is given, returns the closest applicable forward
        rate for that tenor instead of a full curve. NOT currently wired into any
        RISK_FREE_RATE call site -- that's a follow-up, not done as part of this pass."""
        params = {'start_date': start_date, 'use_csv': False}
        if end_date is not None:
            params['end_date'] = end_date
        if target_date is not None:
            params['target_date'] = target_date
        r = self._get("/api/db/yield_curve", params=params)
        r.raise_for_status()
        return r.json()

    def close(self):
        self.client.close()
