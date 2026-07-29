#!/usr/bin/env python3
"""index_membership.py

Index constituent / weight lookup for basket building.

Neither ThetaData nor yfinance carry index-membership or constituent-weight
data (ThetaData is options/stock quotes, yfinance's `.info` doesn't reliably
expose this either) -- this is a genuinely separate need, not a workaround of
the "use ThetaData first" rule that applies to price/vol data elsewhere in
this project.

Data source: stockanalysis.com's public holdings JSON endpoint
(https://stockanalysis.com/api/symbol/e/{TICKER}/holdings). Verified live and
reachable during development against SPY, QQQ, and XLK -- returns real,
current constituent weights with no scraping or spreadsheet parsing required.
It's an unofficial/undocumented endpoint (not a published API with a stability
guarantee), so if it ever changes shape, this is the one place to fix it.

Approach: rather than a true reverse index (ticker -> indices), we query a
small, fixed set of major index/sector ETFs and check which ones hold the
target ticker. This covers the S&P 500, Nasdaq 100, Dow 30, Russell 2000, and
all 11 GICS sector SPDRs -- the set most single-name dispersion candidates
will fall into at least one of.
"""
import json
import time
from typing import Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = "https://stockanalysis.com/api/symbol/e"

# stockanalysis.com sits behind a bot filter that answers 404 (not 403) to
# requests that don't look like a real browser. A bare "User-Agent: Mozilla/5.0"
# is itself a well-known scraper signature and gets filtered; sending the full
# header set a browser would send gets a normal 200. If every ETF in the list
# starts 404-ing again, this header block is the first thing to check.
_BROWSER_HEADERS = {
    # Chrome/126 (mid-2024) is what triggered the 404s Jason hit on
    # 2026-07-22 -- current stable is Chrome 150/151 (per
    # chromereleases.googleblog.com, checked 2026-07-23), so a ~2-year-stale
    # version number was itself a plausible bot-detection signature, exactly
    # the failure mode this block's own comment above predicted. Bumped to
    # a current version; this WILL drift again as Chrome keeps shipping on
    # its ~4-week cadence, so treat the version number as perishable, not a
    # one-time fix -- if 404s come back, check the current stable version
    # first before assuming something else broke.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stockanalysis.com/etf/spy/holdings/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}

# NOTE ON COVERAGE: this endpoint returns roughly the top 25 holdings, not the
# full constituent list, regardless of what top_n is requested. So membership
# lookup is really "is this ticker a top-25 weight in the ETF" -- adequate for
# dispersion candidates (which are large weights by definition) but it will
# miss small-weight names. Passing top_n=100000 does not change what the API
# returns; it only affects how much of the response we keep.

# Index/sector ETF -> display name. Kept small and curated on purpose: these
# are the liquid, well-known baskets a dispersion trade would actually use,
# not an arbitrary universe.
CANDIDATE_INDICES: Dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones Industrial Average",
    "IWM": "Russell 2000",
    "XLK": "Technology Select Sector",
    "XLF": "Financial Select Sector",
    "XLE": "Energy Select Sector",
    "XLY": "Consumer Discretionary Select Sector",
    "XLP": "Consumer Staples Select Sector",
    "XLV": "Health Care Select Sector",
    "XLI": "Industrial Select Sector",
    "XLU": "Utilities Select Sector",
    "XLB": "Materials Select Sector",
    "XLC": "Communication Services Select Sector",
    "XLRE": "Real Estate Select Sector",
}

# In-memory cache: one suite run shouldn't refetch the same ETF's holdings
# repeatedly (find_indices_for_ticker alone hits every candidate index once).
_holdings_cache: Dict[str, List[Tuple[str, float]]] = {}


def _fetch_holdings_raw(etf_ticker: str, timeout: float = 15.0, retries: int = 2) -> Optional[dict]:
    url = f"{BASE_URL}/{etf_ticker.upper()}/holdings"
    headers = dict(_BROWSER_HEADERS)
    headers["Referer"] = f"https://stockanalysis.com/etf/{etf_ticker.lower()}/holdings/"
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, TimeoutError, ValueError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))  # brief backoff before retrying
    print(f"  [index_membership] stockanalysis.com fetch failed for {etf_ticker}: {last_err}")
    return None


def _fetch_holdings_yfinance(etf_ticker: str) -> List[Tuple[str, float]]:
    """DISABLED: yahoo purged from this suite. ETF constituent weights are not
    carried by PotatoHedge/ThetaData (options & stock quotes only), so
    stockanalysis.com (_fetch_holdings_raw) is now the sole holdings source. If
    it's unreachable, callers get [] ("no basket data") -- which they already
    handle -- rather than a yahoo fallback. Kept as a no-op stub so nothing that
    references it breaks; re-enable behind an explicit backup flag if desired."""
    return []


def get_index_constituents(etf_ticker: str, top_n: int = 10) -> List[Tuple[str, float]]:
    """Return up to top_n (ticker, weight_pct) pairs for the given index/sector ETF,
    sorted by weight descending (the API already returns them pre-sorted).

    Returns [] only if BOTH the primary (stockanalysis.com) and fallback
    (yfinance) sources fail -- callers should treat [] as "no basket data",
    not as "this ETF has no holdings"."""
    etf_ticker = etf_ticker.upper()
    if etf_ticker in _holdings_cache:
        return _holdings_cache[etf_ticker][:top_n]

    out: List[Tuple[str, float]] = []
    data = _fetch_holdings_raw(etf_ticker)
    if data and isinstance(data.get("data"), dict) and "holdings" in data["data"]:
        for row in data["data"]["holdings"]:
            sym = str(row.get("s", "")).lstrip("$").strip()
            weight_str = str(row.get("as", "0")).rstrip("%").strip()
            if not sym:
                continue
            try:
                weight = float(weight_str)
            except ValueError:
                continue
            out.append((sym, weight))

    if not out:
        # Live endpoint failed (stockanalysis.com's bot filter 404s intermittently).
        # yahoo fallback was purged; use the static snapshot for the major indices.
        from index_constituents_static import get_static_constituents, AS_OF
        static = get_static_constituents(etf_ticker)
        if static:
            print(f"  [index_membership] Live holdings unavailable for {etf_ticker}; "
                  f"using static snapshot (as of {AS_OF.get(etf_ticker, 'n/a')}, {len(static)} names).")
            out = static

    if not out:
        # Don't cache a failure -- a transient network problem shouldn't poison
        # every later lookup for this ETF within the same run.
        return []

    _holdings_cache[etf_ticker] = out
    return out[:top_n]


def find_indices_for_ticker(ticker: str, candidates: Optional[Dict[str, str]] = None) -> List[Dict]:
    """Search the candidate index/sector ETFs for membership of `ticker`.

    Returns a list of dicts sorted by the ticker's weight descending:
        [{"index": "QQQ", "name": "Nasdaq 100", "weight": 8.9}, ...]

    Weight is the decision-support data point: a higher weight means the
    single name is a more meaningful driver of that index's variance, which
    is what makes it a more relevant choice for a dispersion trade against
    that index (vs. one where the name is a rounding error).
    """
    ticker = ticker.upper()
    candidates = candidates or CANDIDATE_INDICES
    results = []
    for idx_ticker, idx_name in candidates.items():
        constituents = get_index_constituents(idx_ticker, top_n=1000)
        for sym, weight in constituents:
            if sym.upper() == ticker:
                results.append({"index": idx_ticker, "name": idx_name, "weight": weight})
                break
    results.sort(key=lambda r: r["weight"], reverse=True)
    return results


def print_index_choices(ticker: str, matches: List[Dict]) -> None:
    if not matches:
        print(f"\n  {ticker} was not found in any of the {len(CANDIDATE_INDICES)} tracked indices/sectors.")
        print(f"  Tracked: {', '.join(CANDIDATE_INDICES.keys())}")
        return
    print(f"\n  {ticker} is a member of {len(matches)} tracked index/sector ETF(s):")
    for i, m in enumerate(matches, 1):
        print(f"    {i}. {m['index']:6s} ({m['name']:35s}) — weight: {m['weight']:.2f}%")


if __name__ == "__main__":
    t = input("Ticker to look up index membership for: ").strip().upper() or "MSFT"
    print(f"\nSearching {len(CANDIDATE_INDICES)} tracked indices for {t}...")
    matches = find_indices_for_ticker(t)
    print_index_choices(t, matches)
    if matches:
        chosen = matches[0]["index"]
        print(f"\nTop constituents of {chosen}:")
        for sym, w in get_index_constituents(chosen, top_n=15):
            print(f"    {sym:8s} {w:.2f}%")
