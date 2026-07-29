"""data_loader.py
Single data-access layer for the VaR engine.

CACHE-FIRST: every fetch checks .cache/<ticker>_<start>_<end>.json before
hitting the network.  ThetaData (api.potatohedge.com) is the ONLY source.
No yfinance.  No fallbacks that cost money.
"""
import os
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np

# ── cache directory ─────────────────────────────────────────────────────────
_CACHE_DIR = Path(__file__).parent.parent / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)

# ── lazy ThetaData client (imported from local copy) ─────────────────────────
def _theta_client():
    """Return a ThetaDataController, loading .env from THIS project root."""
    import sys, importlib
    _root = Path(__file__).parent.parent
    env_path = _root / ".env"
    # load .env into os.environ if present
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    # import from sibling Variance_Swap_Module
    vsm = str(Path(__file__).parent.parent.parent / "Variance_Swap_Module")
    if vsm not in sys.path:
        sys.path.insert(0, vsm)
    from thetadata_client import ThetaDataController
    return ThetaDataController()


def _cache_key(tag: str) -> Path:
    h = hashlib.md5(tag.encode()).hexdigest()[:12]
    return _CACHE_DIR / f"{h}.json"


def _cache_load(tag: str):
    p = _cache_key(tag)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _cache_save(tag: str, data):
    if not data:          # never cache empty — force retry next call
        return
    try:
        _cache_key(tag).write_text(json.dumps(data))
    except Exception:
        pass


def _default_sentiment_manifest() -> Path:
    return Path(__file__).parent.parent.parent / "sentiment-scanner" / "data" / "exports" / "highlighted_ticker_packs" / "latest_manifest.json"


def load_highlighted_ticker_pack(
    manifest_path: Optional[str] = None,
    pack_index: int = 0,
    top_n: Optional[int] = None,
) -> List[str]:
    """Load ticker symbols from the sentiment-scanner highlighted pack manifest.

    Returns a ranked uppercase symbol list. Empty list on any load/parse issue.
    """
    manifest_file = Path(manifest_path) if manifest_path else _default_sentiment_manifest()
    if not manifest_file.exists():
        return []
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    packs = manifest.get("packs", []) if isinstance(manifest, dict) else []
    if not isinstance(packs, list) or not packs:
        return []
    idx = min(max(int(pack_index), 0), len(packs) - 1)
    selected = packs[idx] if isinstance(packs[idx], dict) else {}
    pack_json = selected.get("json_path")
    if not pack_json:
        return []
    pack_file = Path(pack_json)
    if not pack_file.exists():
        return []
    try:
        pack = json.loads(pack_file.read_text(encoding="utf-8"))
    except Exception:
        return []
    rows = pack.get("tickers", []) if isinstance(pack, dict) else []
    if not isinstance(rows, list):
        return []
    ranked = sorted(
        rows,
        key=lambda t: (int((t or {}).get("rank", 9999) or 9999), -float((t or {}).get("cns", 0) or 0)),
    )
    symbols = []
    for row in ranked:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).upper().strip()
        if sym and sym not in symbols:
            symbols.append(sym)
    if top_n is not None:
        try:
            n = int(top_n)
            if n > 0:
                symbols = symbols[:n]
        except (TypeError, ValueError):
            pass
    return symbols


# ── public API ───────────────────────────────────────────────────────────────

def fetch_eod_prices(ticker: str,
                     start_date: str,
                     end_date: str,
                     field: str = "close") -> Dict[str, float]:
    """Return {date_str: price} dict for *ticker* between YYYYMMDD dates.
    Cached to disk; never calls ThetaData twice for the same range.
    """
    tag = f"eod|{ticker}|{start_date}|{end_date}|{field}"
    cached = _cache_load(tag)
    if cached is not None:
        return cached

    td = _theta_client()
    rows = td.hist_stock_eod(ticker, start_date, end_date)
    td.close()

    result = {}
    for row in rows:
        if not row:
            continue
        # ThetaData returns 'created' (ISO datetime) — extract date
        raw_dt = str(row.get("created") or row.get("date") or "")
        date_str = raw_dt[:10].replace("-", "") if raw_dt else ""
        val = row.get(field) or row.get("close") or row.get("last")
        try:
            if date_str and len(date_str) == 8:
                result[date_str] = float(val)
        except (TypeError, ValueError):
            pass

    _cache_save(tag, result)
    return result


def fetch_price_series(ticker: str,
                       start_date: str,
                       end_date: str) -> np.ndarray:
    """Sorted numpy array of closing prices (chronological)."""
    d = fetch_eod_prices(ticker, start_date, end_date)
    if not d:
        raise RuntimeError(f"No price data for {ticker} {start_date}→{end_date}")
    return np.array([v for _, v in sorted(d.items())])


def fetch_log_returns(ticker: str,
                      start_date: str,
                      end_date: str) -> np.ndarray:
    """Log returns array from EOD prices."""
    px = fetch_price_series(ticker, start_date, end_date)
    return np.diff(np.log(px))


def fetch_spot(ticker: str) -> float:
    """Live spot price — cached for 60 s only (stale-if-recent)."""
    tag = f"spot|{ticker}|{datetime.now().strftime('%Y%m%d_%H%M')[:13]}"  # 10-min bucket
    cached = _cache_load(tag)
    if cached is not None:
        return float(cached)
    td = _theta_client()
    spot = td.fetch_spot_price(ticker)
    td.close()
    if spot > 0:
        _cache_save(tag, spot)
    return spot


def fetch_risk_free_rate(T: float = 0.25) -> float:
    """Risk-free rate for tenor T (years). Cached daily."""
    tag = f"rfr|{datetime.now().strftime('%Y%m%d')}|T{T:.3f}"
    cached = _cache_load(tag)
    if cached is not None:
        return float(cached)
    td = _theta_client()
    r = td.fetch_risk_free_rate(T)
    td.close()
    if r is None:
        r = 0.05  # fallback constant — NOT a network call
    _cache_save(tag, r)
    return r


def default_date_range(lookback_days: int = 504):
    """Return (start_YYYYMMDD, end_YYYYMMDD) for *lookback_days* trading days
    (approximately — uses calendar days * 1.4 as a safe over-fetch)."""
    end = datetime.now()
    start = end - timedelta(days=int(lookback_days * 1.4))
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def estimate_garch_vol(ticker: str,
                       lookback_days: int = 504,
                       trading_days: float = 252.0) -> float:
    """Quick GARCH(1,1) estimate: return annualized conditional vol for *ticker*.
    Uses the same scipy-based fit as hist_sim.py (no arch/statsmodels dep).
    Returns 0.0 if fit fails.
    """
    from .hist_sim import _garch_fit
    start, end = default_date_range(lookback_days)
    try:
        rets = fetch_log_returns(ticker, start, end)
        if len(rets) < 60:
            return 0.0
        g = _garch_fit(rets)
        # current_vol is daily — annualize
        return float(g["current_vol"] * np.sqrt(trading_days))
    except Exception:
        return 0.0


def estimate_geometric_return(ticker: str,
                              lookback_days: int = 504,
                              trading_days: float = 252.0) -> float:
    """Annualized geometric mean return from historical prices.
    Computed as (P_T / P_0)^(252/T) - 1.
    """
    start, end = default_date_range(lookback_days)
    try:
        px = fetch_price_series(ticker, start, end)
        if len(px) < 60:
            return 0.0
        r = (px[-1] / px[0]) ** (trading_days / len(px)) - 1.0
        return float(r)
    except Exception:
        return 0.0


def estimate_dividend_yield(ticker: str,
                            spot: float = None) -> float:
    """Trailing-12-month dividend yield (decimal) from ThetaData.
    Cached daily per ticker.  Returns 0.0 on failure.
    """
    today = datetime.now().strftime("%Y%m%d")
    tag = f"div|{ticker}|{today}"
    cached = _cache_load(tag)
    if cached is not None:
        return float(cached)
    try:
        td = _theta_client()
        dy = td.fetch_dividend_yield(ticker, spot=spot)
        td.close()
        if dy and dy > 0:
            _cache_save(tag, float(dy))
            return float(dy)
    except Exception:
        pass
    return 0.0
