"""Sector Rotation / Factor Momentum Scanner — ranks sector ETFs by
momentum, relative strength, and correlation drift.

Uses the Vol_Suite correlation_engine (ThetaData-backed) for price history
so no yfinance dependency is required.  The scanner covers 15 major
sector/index ETFs:
    SPY  QQQ  DIA  IWM  XLK  XLF  XLE  XLY  XLP
    XLV  XLI  XLU  XLB  XLC  XLRE

For each ETF, it computes:
  1. 1m / 3m / 6m total return momentum
  2. Relative strength vs SPY (ratio of 6m returns)
  3. Correlation drift: change between 30-day and 90-day rolling corr vs SPY
  4. Trend strength (price momentum consistency)

A composite score is then calculated, and sectors are ranked from strongest
to weakest momentum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import numpy as np

from scanner.options_scanner_base import VolSuiteImporter
# Direct import of fetch_price_history (via Vol_Suite path bootstrap above)
# so the function can be easily patched in tests.
from correlation_engine import fetch_price_history

vsi = VolSuiteImporter()

# ---------------------------------------------------------------------------
# Sector / Index ETF universe
# ---------------------------------------------------------------------------

CANDIDATE_INDICES: List[Tuple[str, str]] = [
    ("SPY",  "S&P 500"),
    ("QQQ",  "Nasdaq-100"),
    ("DIA",  "Dow Jones Industrial"),
    ("IWM",  "Russell 2000"),
    ("XLK",  "Technology"),
    ("XLF",  "Financials"),
    ("XLE",  "Energy"),
    ("XLY",  "Consumer Discretionary"),
    ("XLP",  "Consumer Staples"),
    ("XLV",  "Health Care"),
    ("XLI",  "Industrials"),
    ("XLU",  "Utilities"),
    ("XLB",  "Materials"),
    ("XLC",  "Communication Svcs"),
    ("XLRE", "Real Estate"),
]

TRADING_DAYS_1M = 21
TRADING_DAYS_3M = 63
TRADING_DAYS_6M = 126

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SectorFactors:
    """Factor readings for a single sector ETF.

    All return values are in decimal form (e.g. 0.05 = 5%).
    """

    ticker: str
    name: str
    mom_1m: float      # 1-month total return
    mom_3m: float      # 3-month total return
    mom_6m: float      # 6-month total return
    rs_vs_spy: float   # Relative strength vs SPY (ticker 6m ret / SPY 6m ret)
    corr_change: float # 30d rolling corr(SPY) minus 90d rolling corr(SPY)
    volume_trend: float # Trend strength (mean daily return / std daily return over 1m)
    error: Optional[str] = None


@dataclass
class SectorRank:
    """Ranked sector with composite score and directional signal."""

    ticker: str
    name: str
    composite_score: float  # 0-100 (higher = stronger momentum)
    mom_1m: float
    mom_3m: float
    mom_6m: float
    rs_vs_spy: float
    corr_change: float
    volume_trend: float
    signal: str  # "BULLISH" | "NEUTRAL" | "BEARISH"


# ---------------------------------------------------------------------------
# Factor computation
# ---------------------------------------------------------------------------


def _compute_momentum(prices: np.ndarray, lookback: int) -> float:
    """Total return over *lookback* periods (most recent -> least recent).

    ``prices`` is sorted OLDEST first.  The most recent ``lookback + 1``
    prices are used so we have a start and end value.
    """
    if len(prices) < lookback + 1:
        return 0.0
    start = prices[-(lookback + 1)]
    end = prices[-1]
    if start <= 0 or end <= 0:
        return 0.0
    return float((end - start) / start)


def _correlation(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation between two equal-length arrays."""
    if len(x) < 3 or len(y) < 3:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def compute_factors(etf_ticker: str, period: str = "6mo") -> SectorFactors:
    """Compute momentum factors for a single sector ETF.

    Parameters
    ----------
    etf_ticker : str
        Ticker symbol (e.g. "XLK").
    period : str
        Price history period (default ``"6mo"``).  At least 6 months of data
        are needed for a full factor read.

    Returns
    -------
    SectorFactors
    """
    name = next(
        (n for t, n in CANDIDATE_INDICES if t == etf_ticker),
        etf_ticker,
    )

    # Fetch prices for the ETF + SPY (needed for RS and correlation drift).
    try:
        df = fetch_price_history(
            [etf_ticker, "SPY"], period=period,
        )
    except Exception as exc:
        return SectorFactors(
            ticker=etf_ticker, name=name,
            mom_1m=0.0, mom_3m=0.0, mom_6m=0.0,
            rs_vs_spy=0.0, corr_change=0.0, volume_trend=0.0,
            error=f"fetch_price_history failed: {exc}",
        )

    if etf_ticker not in df.columns or "SPY" not in df.columns:
        missing = [t for t in (etf_ticker, "SPY") if t not in df.columns]
        return SectorFactors(
            ticker=etf_ticker, name=name,
            mom_1m=0.0, mom_3m=0.0, mom_6m=0.0,
            rs_vs_spy=0.0, corr_change=0.0, volume_trend=0.0,
            error=f"Missing ticker(s) in price data: {missing}",
        )

    etf_prices = df[etf_ticker].values.flatten()
    spy_prices = df["SPY"].values.flatten()

    if len(etf_prices) < 2:
        return SectorFactors(
            ticker=etf_ticker, name=name,
            mom_1m=0.0, mom_3m=0.0, mom_6m=0.0,
            rs_vs_spy=0.0, corr_change=0.0, volume_trend=0.0,
            error=f"Insufficient price data ({len(etf_prices)} rows)",
        )

    # -- Momentums --
    mom_1m = _compute_momentum(etf_prices, TRADING_DAYS_1M)
    mom_3m = _compute_momentum(etf_prices, TRADING_DAYS_3M)
    mom_6m = _compute_momentum(etf_prices, TRADING_DAYS_6M)

    # -- Relative strength vs SPY (ratio of 6m returns) --
    spy_6m = _compute_momentum(spy_prices, TRADING_DAYS_6M)
    rs_vs_spy = (mom_6m / spy_6m) if abs(spy_6m) > 1e-10 else 0.0

    # -- Returns (for correlation and trend calculations) --
    etf_rets = np.array([])
    spy_rets = np.array([])
    if len(etf_prices) >= 3 and len(spy_prices) >= 3:
        etf_rets = np.diff(np.log(etf_prices))
        spy_rets = np.diff(np.log(spy_prices))
        min_len = min(len(etf_rets), len(spy_rets))
        etf_rets = etf_rets[-min_len:]
        spy_rets = spy_rets[-min_len:]

    # -- Correlation drift (30d rolling corr vs SPY minus 90d rolling corr) --
    corr_change = 0.0
    if len(etf_rets) >= TRADING_DAYS_3M:
        corr_30 = _correlation(
            etf_rets[-TRADING_DAYS_1M:], spy_rets[-TRADING_DAYS_1M:],
        )
        corr_90 = _correlation(
            etf_rets[-TRADING_DAYS_3M:], spy_rets[-TRADING_DAYS_3M:],
        )
        corr_change = corr_30 - corr_90

    # -- Trend strength (annualised Sharpe of 1m daily returns) --
    volume_trend = 0.0
    if len(etf_rets) >= TRADING_DAYS_1M:
        recent_rets = etf_rets[-TRADING_DAYS_1M:]
        std_ret = float(np.std(recent_rets, ddof=1))
        mean_ret = float(np.mean(recent_rets))
        if std_ret > 1e-10:
            volume_trend = (mean_ret / std_ret) * np.sqrt(252)

    return SectorFactors(
        ticker=etf_ticker, name=name,
        mom_1m=round(mom_1m, 6),
        mom_3m=round(mom_3m, 6),
        mom_6m=round(mom_6m, 6),
        rs_vs_spy=round(rs_vs_spy, 4),
        corr_change=round(corr_change, 4),
        volume_trend=round(volume_trend, 4),
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _z_normalise(values: List[float]) -> List[float]:
    """Z-score normalise a list; return 0 for constant/empty lists."""
    arr = np.array(values, dtype=float)
    if len(arr) < 2:
        return [0.0] * len(arr)
    std = float(np.std(arr, ddof=1))
    if std < 1e-10:
        return [0.0] * len(arr)
    mean = float(np.mean(arr))
    return [float((v - mean) / std) for v in arr]


def rank_sectors(period: str = "6mo") -> List[SectorRank]:
    """Compute factors for all candidate sectors and rank them.

    The composite score (0-100) is a weighted blend of:

      - 1m momentum  (25%)
      - 3m momentum  (25%)
      - 6m momentum  (25%)
      - Relative strength vs SPY  (15%)
      - Correlation change        (5%)
      - Trend strength             (5%)

    The scores are z-normalised per factor, summed, then min-max scaled to
    0-100.  The top third of sectors get ``"BULLISH"``, the bottom third get
    ``"BEARISH"``, and the middle third are ``"NEUTRAL"``.

    Parameters
    ----------
    period : str
        Price history period passed to ``fetch_price_history``.

    Returns
    -------
    List[SectorRank]
        Sorted by composite score descending.
    """
    factors: List[SectorFactors] = []
    for ticker, name in CANDIDATE_INDICES:
        f = compute_factors(ticker, period=period)
        factors.append(f)

    # Filter out errors
    valid = [f for f in factors if f.error is None]
    if not valid:
        return []

    # Z-normalise each numeric factor across the valid set
    mom_1m_vals = [f.mom_1m for f in valid]
    mom_3m_vals = [f.mom_3m for f in valid]
    mom_6m_vals = [f.mom_6m for f in valid]
    rs_vals = [f.rs_vs_spy for f in valid]
    corr_vals = [f.corr_change for f in valid]
    trend_vals = [f.volume_trend for f in valid]

    z_mom_1m = _z_normalise(mom_1m_vals)
    z_mom_3m = _z_normalise(mom_3m_vals)
    z_mom_6m = _z_normalise(mom_6m_vals)
    z_rs = _z_normalise(rs_vals)
    z_corr = _z_normalise(corr_vals)
    z_trend = _z_normalise(trend_vals)

    # Weights
    W_M1, W_M3, W_M6 = 0.25, 0.25, 0.25
    W_RS, W_CORR, W_TREND = 0.15, 0.05, 0.05

    raw_scores: List[float] = []
    for i in range(len(valid)):
        score = (
            W_M1 * z_mom_1m[i]
            + W_M3 * z_mom_3m[i]
            + W_M6 * z_mom_6m[i]
            + W_RS * z_rs[i]
            + W_CORR * z_corr[i]
            + W_TREND * z_trend[i]
        )
        raw_scores.append(score)

    # Min-max to 0-100
    score_arr = np.array(raw_scores, dtype=float)
    smin, smax = float(score_arr.min()), float(score_arr.max())
    if smax - smin > 1e-10:
        normalised = (score_arr - smin) / (smax - smin) * 100.0
    else:
        normalised = np.full_like(score_arr, 50.0)

    # Build ranks
    ranks = []
    for i, f in enumerate(valid):
        ranks.append(SectorRank(
            ticker=f.ticker,
            name=f.name,
            composite_score=round(float(normalised[i]), 1),
            mom_1m=f.mom_1m,
            mom_3m=f.mom_3m,
            mom_6m=f.mom_6m,
            rs_vs_spy=f.rs_vs_spy,
            corr_change=f.corr_change,
            volume_trend=f.volume_trend,
            signal="PENDING",
        ))

    # Sort descending by composite score
    ranks.sort(key=lambda r: r.composite_score, reverse=True)

    # Assign signal: top third BULLISH, bottom third BEARISH, rest NEUTRAL
    n = len(ranks)
    third = max(1, n // 3)
    for i, r in enumerate(ranks):
        if i < third:
            r.signal = "BULLISH"
        elif i >= n - third:
            r.signal = "BEARISH"
        else:
            r.signal = "NEUTRAL"

    return ranks


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _pct(v: float) -> str:
    """Format a decimal as a percentage string."""
    return f"{v * 100:.1f}%"


def _sign_str(v: float) -> str:
    """Format a signed float as percentage with explicit '+' sign."""
    return f"{v * 100:+.1f}%"


def format_rotation(ranks: List[SectorRank]) -> str:
    """Render ranked sectors as a formatted table.

    Parameters
    ----------
    ranks : List[SectorRank]
        Output of ``rank_sectors()`` or ``scan_all()``.

    Returns
    -------
    str
    """
    if not ranks:
        return "  No sector rotation data available."

    lines = [
        "=" * 110,
        "  SECTOR ROTATION — Factor Momentum Scan",
        f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "=" * 110,
        "",
        f"  {'Rank':<6} {'Ticker':<6} {'Name':<25} {'Score':<8} {'Signal':<10} "
        f"{'1m Mom':<10} {'3m Mom':<10} {'6m Mom':<10} "
        f"{'RS vs SPY':<12} {'Corr Δ':<10} {'Trend':<8}",
        "  " + "-" * 108,
    ]

    for i, r in enumerate(ranks, 1):
        lines.append(
            f"  {i:<6} {r.ticker:<6} {r.name:<25} "
            f"{r.composite_score:<8.1f} {r.signal:<10} "
            f"{_pct(r.mom_1m):<10} {_pct(r.mom_3m):<10} {_pct(r.mom_6m):<10} "
            f"{r.rs_vs_spy:<12.2f} {_sign_str(r.corr_change):<10} "
            f"{r.volume_trend:<8.2f}"
        )

    lines.append("")
    lines.append("  Legend:")
    lines.append("    Score  | 0-100 composite (higher = stronger momentum)")
    lines.append("    Signal| Top ⅓ → BULLISH  |  Middle ⅓ → NEUTRAL  |  Bottom ⅓ → BEARISH")
    lines.append("    RS    | 6m return / SPY 6m return ( >1.0 = outperforming)")
    lines.append("    Corr Δ| 30d rolling r minus 90d rolling r (positive = rising beta)")
    lines.append("    Trend | Annualised Sharpe of 1m daily returns (consistency)")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def scan_all() -> List[SectorRank]:
    """Convenience wrapper — run ``rank_sectors()`` with default options.

    Returns
    -------
    List[SectorRank]
    """
    return rank_sectors(period="6mo")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    period = sys.argv[1] if len(sys.argv) > 1 else "6mo"
    ranks = rank_sectors(period=period)
    print(format_rotation(ranks))

    # Summary line for quick consumption
    if ranks:
        top3 = ", ".join(f"{r.ticker} ({r.composite_score:.0f})" for r in ranks[:3])
        bot3 = ", ".join(f"{r.ticker} ({r.composite_score:.0f})" for r in ranks[-3:])
        print(f"\n  Top 3: {top3}")
        print(f"  Bot 3: {bot3}")