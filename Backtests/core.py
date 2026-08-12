"""core.py — shared evaluation core for the Backtests tournament suite.

Pure helpers: market-row normalization, moneyness/TTE bucketing, BS closed
forms (with known-answer fixtures in tests), error metrics, report writers,
and the network-free FakeController used by every test.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GREEK_FIELDS_L1 = ["delta", "gamma", "theta", "vega", "rho"]
GREEK_FIELDS_L2 = ["vanna", "charm", "vomma", "speed", "color"]
ALL_GREEK_FIELDS = GREEK_FIELDS_L1 + GREEK_FIELDS_L2

# Market-row numeric fields that may arrive as strings from the proxy.
_NUMERIC_FIELDS = (
    "delta", "gamma", "theta", "vega", "rho",
    "vanna", "charm", "vomma", "speed", "color",
    "veta", "vera", "ultima", "zomma", "epsilon", "lambda", "dual_delta",
    "dual_gamma", "d1", "d2", "iv_error",
    "implied_vol", "iv", "mid", "bid", "ask", "close", "last", "volume", "oi",
    "underlying_price",
)


def strike_from_theta(k: float) -> float:
    """Theta-scaled int strike (e.g. 727000) -> dollar strike (727.0)."""
    return float(k) / 1000.0


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a vendor greeks row to floats + 'C'/'P' right + 'date'.

    ThetaData bulk rows carry theta-scaled integer strikes, single-letter or
    full-word rights ('CALL'/'PUT'), and every greek/price field as a STRING.
    Non-numeric extras (date, created, ms_of_day, ...) are preserved so
    downstream date filtering keeps working (shared normalization contract).
    """
    if not isinstance(row, dict):
        return None
    strike = None
    if row.get("strike") is not None:
        strike = _to_float(row["strike"])
        if strike is not None and strike > 1000:
            strike = strike_from_theta(strike)
    if strike is None and row.get("strike_theta") is not None:
        strike = _to_float(row["strike_theta"])
        if strike is not None:
            strike = strike_from_theta(strike)
    if strike is None or strike <= 0:
        return None
    right = str(row.get("right", "")).upper()
    if right.startswith("C"):
        right = "C"
    elif right.startswith("P"):
        right = "P"
    else:
        cp = str(row.get("call_put", row.get("cp", ""))).upper()
        right = "C" if cp.startswith("C") else "P" if cp.startswith("P") else "?"
    if right == "?":
        return None
    rec: Dict[str, Any] = dict(row)
    rec["strike"] = strike
    rec["right"] = right
    for f in _NUMERIC_FIELDS:
        if row.get(f) is not None:
            v = _to_float(row[f])
            if v is not None:
                rec[f] = v
    if rec.get("implied_vol") is None:
        for alias in ("iv", "impliedVolatility", "impliedvol"):
            v = _to_float(row.get(alias))
            if v is not None:
                rec["implied_vol"] = v
                break
    return rec


def normalize_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in (normalize_row(r) for r in rows) if r is not None]


def rows_by_date(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        d = str(r.get("date", ""))
        out.setdefault(d, []).append(r)
    return out


def parse_yyyymmdd(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    s = str(value).strip()
    if len(s) >= 8 and s[:8].isdigit():
        try:
            return datetime.strptime(s[:8], "%Y%m%d")
        except ValueError:
            pass
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt)
        except ValueError:
            continue
    return None


def tte_years(expiration: Any, date: Any) -> Optional[float]:
    """Calendar-day years from `date` to `expiration` (both YYYYMMDD-ish)."""
    e = parse_yyyymmdd(expiration)
    d = parse_yyyymmdd(date)
    if e is None or d is None:
        return None
    return max((e - d).total_seconds() / (365.0 * 86400.0), 1e-6)


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def moneyness_bucket(strike: float, spot: float, right: str) -> str:
    """ATM (<3% from spot), ITM/OTM (3-15%), DeepITM/DeepOTM (>15%).

    'ITM'/'OTM' are relative to the option's own right: a call with K < S is
    ITM, a put with K > S is ITM.
    """
    if spot <= 0:
        return "?"
    dist = strike / spot - 1.0
    if right == "C":
        side = "ITM" if dist < 0 else "OTM"
    else:
        side = "ITM" if dist > 0 else "OTM"
    mag = abs(dist)
    if mag <= 0.03:
        return "ATM"
    if mag <= 0.15:
        return side
    return "Deep" + side


def tte_bucket(T: Optional[float]) -> str:
    if T is None:
        return "?"
    if T < 0.1:
        return "<0.1y"
    if T <= 0.3:
        return "0.1-0.3y"
    return ">0.3y"


# ---------------------------------------------------------------------------
# Black-Scholes closed forms (carry-adjusted, European)
# ---------------------------------------------------------------------------

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float) -> Tuple[float, float]:
    if sigma <= 1e-9 or T <= 0 or S <= 0 or K <= 0:
        return 0.0, 0.0
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    return d1, d1 - sigma * sqrt_t


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, cp: bool) -> float:
    """Black-Scholes-with-carry price. cp=True -> call."""
    d1, d2 = bs_d1_d2(S, K, T, r, q, sigma)
    if cp:
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, q: float, sigma: float, cp: bool) -> float:
    d1, _ = bs_d1_d2(S, K, T, r, q, sigma)
    nd1 = _norm_cdf(d1)
    return nd1 * math.exp(-q * T) if cp else (nd1 - 1.0) * math.exp(-q * T)


def strike_for_forward_delta(F: float, sigma: float, T: float, target_delta: float) -> float:
    """Strike K such that FORWARD call delta = target_delta (0..1).

    Mirror of VannaVolga._forward_delta_strike: N(d1)=target_delta so
    K = F*exp(-N^{-1}(target)*sigma*sqrt(T) + 0.5*sigma^2*T).
    """
    from scipy.stats import norm
    d1 = norm.ppf(target_delta)
    return F * math.exp(-d1 * sigma * math.sqrt(T) + 0.5 * sigma * sigma * T)


# ---------------------------------------------------------------------------
# Error metrics
# ---------------------------------------------------------------------------

def mae(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    vals = [(a, b) for a, b in pairs if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if not vals:
        return None
    return sum(abs(a - b) for a, b in vals) / len(vals)


def rmse(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    vals = [(a, b) for a, b in pairs if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if not vals:
        return None
    return math.sqrt(sum((a - b) ** 2 for a, b in vals) / len(vals))


def bias(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    """Mean (model - market); positive = model overprices/overstates."""
    vals = [(a, b) for a, b in pairs if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if not vals:
        return None
    return sum(a - b for a, b in vals) / len(vals)


def pearson(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    vals = [(a, b) for a, b in pairs if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if len(vals) < 3:
        return None
    n = len(vals)
    mx = sum(a for a, _ in vals) / n
    my = sum(b for _, b in vals) / n
    cov = sum((a - mx) * (b - my) for a, b in vals)
    vx = sum((a - mx) ** 2 for a, b in vals)
    vy = sum((b - my) ** 2 for a, b in vals)
    if vx <= 0 or vy <= 0:
        return None
    return cov / math.sqrt(vx * vy)


def sign_agreement(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Optional[float]:
    vals = [(a, b) for a, b in pairs if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if not vals:
        return None
    agree = sum(1 for a, b in vals if (a >= 0) == (b >= 0))
    return agree / len(vals)


def summarize_errors(pairs: Sequence[Tuple[Optional[float], Optional[float]]]) -> Dict[str, Optional[float]]:
    return {
        "n": sum(1 for a, b in pairs if a is not None and b is not None),
        "mae": mae(pairs),
        "rmse": rmse(pairs),
        "bias": bias(pairs),
        "corr": pearson(pairs),
        "sign_agreement": sign_agreement(pairs),
    }


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

def _fmt(v: Optional[float], nd: int = 4) -> str:
    if v is None:
        return "-"
    return f"{v:.{nd}f}"


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.1f}%"


def write_json(path: str, obj: Any) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
    return path


def write_txt(path: str, lines: Iterable[str]) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def render_metrics_table(
    title: str,
    models: Sequence[str],
    metrics: Dict[str, Dict[str, Dict[str, Optional[float]]]],  # model -> greek -> summary
    greek_fields: Sequence[str],
) -> List[str]:
    lines = [f"== {title} ==", ""]
    header = ["model"] + [g for g in greek_fields]
    widths = [max(len(m), 6) for m in models] + [max(len(g), 6) for g in greek_fields]
    # per-greek: show MAE, corr, sign-agreement compactly as "mae|corr|sa"
    lines.append("metric: MAE | corr | sign-agree (per greek)")
    lines.append("")
    lines.append("  " + "  ".join(f"{h:>{w}}" for h, w in zip(["model"] + list(greek_fields), widths)))
    for m in models:
        cells = [m]
        for g in greek_fields:
            s = metrics.get(m, {}).get(g, {})
            cells.append(f"{_fmt(s.get('mae'))}|{_fmt(s.get('corr'), 2)}|{_fmt_pct(s.get('sign_agreement'))}")
        lines.append("  " + "  ".join(f"{c:>{w}}" for c, w in zip(cells, widths)))
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Network-free fake controller (tests)
# ---------------------------------------------------------------------------

class FakeController:
    """Canned ThetaDataController stand-in.

    `snapshot` -> rows returned by option_bulk_greeks / option_bulk_hist_greeks
    (optionally keyed by date for per-day calls). `eod` -> rows returned by
    hist_stock_eod.
    """

    def __init__(
        self,
        snapshot: Optional[List[Dict[str, Any]]] = None,
        eod: Optional[List[Dict[str, Any]]] = None,
        expirations: Optional[List[str]] = None,
        spot: float = 100.0,
        rate: float = 0.05,
        div: float = 0.0,
    ):
        self._snapshot = snapshot or []
        self._eod = eod or []
        self._expirations = expirations or []
        self.spot_price = spot
        self.rate = rate
        self.div = div
        self.snapshot_calls = 0
        self.hist_calls: List[Tuple[str, str, str]] = []

    def option_bulk_greeks(self, root: str, exp: str):
        self.snapshot_calls += 1
        return list(self._snapshot)

    def option_bulk_greeks_second_order(self, root: str, exp: str):
        return list(self._snapshot)

    def option_bulk_hist_greeks(self, root: str, exp: str, start_date: str, end_date: str):
        self.hist_calls.append((root, exp, start_date))
        rows = [r for r in self._snapshot if str(r.get("date", "")) == start_date]
        return rows or list(self._snapshot)

    def option_bulk_hist_eod(self, root: str, exp: str, start_date: str, end_date: str):
        return [r for r in self._eod if start_date <= str(r.get("date", "")) <= end_date]

    def hist_stock_eod(self, root: str, start_date: str, end_date: str):
        def _day(r):
            return str(r.get("date") or r.get("created", ""))[:10].replace("-", "")
        def _matches_root(r):
            row_root = r.get("symbol", r.get("root"))
            return row_root is None or str(row_root).upper() == root.upper()
        return [r for r in self._eod
                if _matches_root(r) and start_date <= _day(r) <= end_date]

    def list_expirations(self, root: str):
        return list(self._expirations)

    def fetch_spot_price(self, root: str) -> float:
        return self.spot_price

    def fetch_risk_free_rate(self) -> float:
        return self.rate

    def fetch_dividend_yield(self, root: str) -> float:
        return self.div

    def close(self):
        pass


def make_contract_row(
    strike: float,
    right: str,
    iv: float,
    spot: float,
    delta: Optional[float] = None,
    gamma: Optional[float] = None,
    theta: Optional[float] = None,
    vega: Optional[float] = None,
    rho: Optional[float] = None,
    vanna: Optional[float] = None,
    charm: Optional[float] = None,
    vomma: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    date: str = "20260720",
    expiration: str = "20260918",
) -> Dict[str, Any]:
    """Build a vendor-shaped row (string numerics, theta-scaled strike)."""
    row: Dict[str, Any] = {
        "root": "TST",
        "expiration": int(expiration),
        "strike": int(round(strike * 1000)),
        "right": right,
        "date": date,
        "implied_vol": f"{iv:.6f}",
        "underlying_price": f"{spot:.2f}",
    }
    if delta is not None:
        row["delta"] = f"{delta:.6f}"
    if gamma is not None:
        row["gamma"] = f"{gamma:.6f}"
    if theta is not None:
        row["theta"] = f"{theta:.6f}"
    if vega is not None:
        row["vega"] = f"{vega:.6f}"
    if rho is not None:
        row["rho"] = f"{rho:.6f}"
    if vanna is not None:
        row["vanna"] = f"{vanna:.6f}"
    if charm is not None:
        row["charm"] = f"{charm:.6f}"
    if vomma is not None:
        row["vomma"] = f"{vomma:.6f}"
    if bid is not None:
        row["bid"] = f"{bid:.4f}"
    if ask is not None:
        row["ask"] = f"{ask:.4f}"
    return row


def default_report_header(tickers: str, expiry: str, lookback_days: int, harnesses: List[str]) -> List[str]:
    return [
        "Backtest Tournament Report",
        f"generated: {datetime.now().isoformat(timespec='seconds')}",
        f"tickers: {tickers} | expiry: {expiry} | lookback_days: {lookback_days}",
        f"harnesses: {', '.join(harnesses)}",
        "",
    ]
