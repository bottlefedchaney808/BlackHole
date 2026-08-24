"""
sentiment_backtest.py

Measure whether CNS / war_score signals from sentiment-scanner's highlighted
ticker packs actually predict subsequent price moves.

Workflow:
  1. Scan data_dir / highlighted_ticker_packs / <YYYYMMDD> / *.json for pack files.
  2. For each pack date + ticker, fetch forward price returns from ThetaData.
  3. Bucket signals by CNS quartile, compute hit rates, Sharpe of a long-only
     strategy (top CNS quartile), and CNS-return correlation.

Network-free testing is supported via FakeThetaDataController (see tests).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SignalResult:
    """One sentiment signal paired with its subsequent forward return."""

    date: str  # pack date YYYY-MM-DD
    ticker: str  # symbol
    cns_score: int  # contested narrative score (0-100)
    war_score: float  # war score (0-1)
    forward_5d_return: float | None  # fractional return over 5 trading days
    forward_10d_return: float | None  # fractional return over 10 trading days


@dataclass
class SentimentBacktestResult:
    """Aggregated backtest statistics."""

    start_date: str
    end_date: str
    total_packs_analyzed: int
    total_signals: int
    top_quartile_cns_names: list[str]  # tickers in top CNS quartile
    bottom_quartile_cns_names: list[str]  # tickers in bottom CNS quartile
    hit_rate_top_vs_bottom: (
        float  # top quartile mean return - bottom quartile mean return
    )
    sharpe_long_only: float | None  # Sharpe of long-only top-quartile strategy
    cns_return_correlation: float | None  # Pearson corr(CNS, forward return)
    forward_days: int  # forward window used
    details: list[SignalResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compact_datelike(name: str) -> bool:
    """Return True if *name* looks like a compact date directory (YYYYMMDD)."""
    return len(name) == 8 and name.isdigit()


def _parse_pack_date(pack: dict) -> str | None:
    """Extract pack creation date as YYYY-MM-DD string from ISO timestamp."""
    raw = pack.get("created_at", "")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _to_trading_dates(
    close_rows: list, reference_date: str, forward_days: int
) -> tuple[float | None, float | None]:
    """From sorted close_rows (list of dicts with 'date' and 'close'),
    find the forward close *forward_days* and *forward_days+5* trading days
    after *reference_date* and return fractional returns.

    Returns (forward_5d_return, forward_10d_return) where the second uses
    forward_days+5 if the caller wanted 10.  Both are None when insufficient
    data exists.
    """
    # Normalise reference_date to compact YYYYMMDD for comparison
    ref_compact = reference_date.replace("-", "")
    closes = [
        r
        for r in close_rows
        if r.get("date") and r.get("close") is not None and float(r["close"]) > 0
    ]
    closes.sort(key=lambda r: str(r["date"]))

    # Find index of reference date (or first date >= it)
    ref_idx: int | None = None
    for i, r in enumerate(closes):
        d = str(r["date"]).replace("-", "")
        if d >= ref_compact:
            ref_idx = i
            break
    if ref_idx is None:
        return None, None

    ref_close = float(closes[ref_idx]["close"])

    def _forward_return(offset: int) -> float | None:
        idx = ref_idx + offset
        if idx >= len(closes):
            return None
        fwd_close = float(closes[idx]["close"])
        return (fwd_close - ref_close) / ref_close

    f5 = _forward_return(forward_days)
    f10 = _forward_return(forward_days + 5) if forward_days + 5 >= 0 else None
    return f5, f10


def _compute_quantile_boundaries(cns_values: list[int]) -> tuple[float, float, float]:
    """Return the lower quartile, median, and upper quartile thresholds for a list of CNS scores.

    Returns (q1, median, q3) where q1/q3 are the 25th/75th percentiles.
    """
    if not cns_values:
        return 0.0, 0.0, 0.0
    sorted_vals = sorted(cns_values)
    n = len(sorted_vals)
    q1_idx = int(n * 0.25)
    q2_idx = n // 2
    q3_idx = int(n * 0.75)
    # Use inclusive nearest-rank for quartiles
    median_val = (
        float(sorted_vals[q2_idx])
        if n % 2 == 1
        else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    )
    q1_val = float(sorted_vals[min(q1_idx, n - 1)])
    q3_val = float(sorted_vals[min(q3_idx, n - 1)])
    return q1_val, median_val, q3_val


def _pearson_correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient.  Returns None if insufficient data."""
    if len(xs) < 3:
        return None
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(v * v for v in xs)
    syy = sum(v * v for v in ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = ((n * sxx - sx * sx) * (n * syy - sy * sy)) ** 0.5
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def _sharpe_ratio(returns: list[float], rf: float = 0.0) -> float | None:
    """Non-annualised (per-period) Sharpe ratio from a list of fractional returns.

    The caller is responsible for annualising this using the actual holding
    period (e.g. periods_per_year ** 0.5).
    """
    if len(returns) < 2:
        return None
    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)
    if std_r == 0:
        return None
    # Daily-ish return, annualise by sqrt(252); adjust if forward_days != 1
    return (mean_r - rf) / std_r  # caller annualises as needed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_sentiment_backtest(
    data_dir: str,
    forward_days: int = 5,
    theta_controller_factory: Callable[[], object] | None = None,
) -> SentimentBacktestResult:
    """Scan *data_dir* for historical ticker packs and measure CNS predictive power.

    Parameters
    ----------
    data_dir :
        Path to ``sentiment-scanner/data`` directory.  Packs are expected under
        ``data_dir/exports/highlighted_ticker_packs/<YYYYMMDD>/*.json``.
    forward_days :
        Number of trading days forward to measure returns from the pack date.
        Default 5 (one trading week).
    theta_controller_factory :
        Optional factory that returns a ThetaDataController-like object.
        Defaults to building a real ``ThetaDataController``.  Tests pass a
        factory that returns a fake.

    Returns
    -------
    SentimentBacktestResult with aggregated statistics.
    """
    packs_dir = os.path.join(data_dir, "exports", "highlighted_ticker_packs")

    if not os.path.isdir(packs_dir):
        return SentimentBacktestResult(
            start_date="",
            end_date="",
            total_packs_analyzed=0,
            total_signals=0,
            top_quartile_cns_names=[],
            bottom_quartile_cns_names=[],
            hit_rate_top_vs_bottom=0.0,
            sharpe_long_only=None,
            cns_return_correlation=None,
            forward_days=forward_days,
        )

    # Collect all pack JSON files sorted by date
    pack_files: list[tuple[str, str]] = []  # (date_dir, file_path)
    for entry in sorted(os.listdir(packs_dir)):
        entry_path = os.path.join(packs_dir, entry)
        if os.path.isdir(entry_path) and _compact_datelike(entry):
            for fname in sorted(os.listdir(entry_path)):
                if fname.endswith(".json"):
                    pack_files.append((entry, os.path.join(entry_path, fname)))

    if not pack_files:
        return SentimentBacktestResult(
            start_date="",
            end_date="",
            total_packs_analyzed=0,
            total_signals=0,
            top_quartile_cns_names=[],
            bottom_quartile_cns_names=[],
            hit_rate_top_vs_bottom=0.0,
            sharpe_long_only=None,
            cns_return_correlation=None,
            forward_days=forward_days,
        )

    details: list[SignalResult] = []

    # All signals by (date_dir, ticker) for dedup and CNS quantile computation
    seen: set = set()
    total_packs = 0
    td = None  # lazily created when first ticker is encountered

    for date_dir, pack_path in pack_files:
        try:
            with open(pack_path, "r", encoding="utf-8") as f:
                pack = json.load(f)
        except Exception:
            continue

        if not isinstance(pack, dict):
            continue

        pack_date = _parse_pack_date(pack)
        if pack_date is None:
            continue

        tickers_data = pack.get("tickers", [])
        if not tickers_data:
            continue

        total_packs += 1

        # Lazily create the theta-data controller on first pack with tickers
        if td is None:
            if theta_controller_factory is not None:
                td = theta_controller_factory()
            else:
                from shared.thetadata import ThetaDataController

                td = ThetaDataController()

        for td_entry in tickers_data:
            symbol = str(td_entry.get("symbol", "")).upper()
            if not symbol:
                continue
            key = (date_dir, symbol)
            if key in seen:
                continue
            seen.add(key)

            cns_val = int(td_entry.get("cns", 0))
            war_val = float(td_entry.get("war_score", 0.0))

            # Fetch forward price data
            try:
                # Start from pack date, fetch enough history to cover forward_days + buffer
                start_compact = date_dir
                start_dt = datetime.strptime(start_compact, "%Y%m%d")
                end_dt = start_dt + timedelta(days=forward_days * 2 + 10)
                end_compact = end_dt.strftime("%Y%m%d")

                rows = td.hist_stock_eod(symbol, start_compact, end_compact)  # type: ignore[union-attr]
                f5, f10 = _to_trading_dates(rows, pack_date, forward_days)
            except Exception:
                f5, f10 = None, None

            details.append(
                SignalResult(
                    date=pack_date,
                    ticker=symbol,
                    cns_score=cns_val,
                    war_score=war_val,
                    forward_5d_return=f5,
                    forward_10d_return=f10,
                )
            )

    # Close controller if it has a close() method
    if td is not None and hasattr(td, "close"):
        try:
            td.close()  # type: ignore[union-attr]
        except Exception:
            pass

    # ---- Compute aggregated metrics ----
    if not details:
        return SentimentBacktestResult(
            start_date="",
            end_date="",
            total_packs_analyzed=total_packs,
            total_signals=0,
            top_quartile_cns_names=[],
            bottom_quartile_cns_names=[],
            hit_rate_top_vs_bottom=0.0,
            sharpe_long_only=None,
            cns_return_correlation=None,
            forward_days=forward_days,
        )

    # Use forward_5d_return as the primary return metric (rename to match forward_days)
    all_cns = [s.cns_score for s in details]
    q1_cns, median_cns, q3_cns = _compute_quantile_boundaries(all_cns)

    top_quartile = [
        s for s in details if s.cns_score >= q3_cns and s.forward_5d_return is not None
    ]
    bottom_quartile = [
        s for s in details if s.cns_score < q1_cns and s.forward_5d_return is not None
    ]

    top_names = sorted(set(s.ticker for s in top_quartile))
    bottom_names = sorted(set(s.ticker for s in bottom_quartile))

    top_mean = (
        statistics.mean([s.forward_5d_return for s in top_quartile])
        if top_quartile
        else 0.0
    )
    bottom_mean = (
        statistics.mean([s.forward_5d_return for s in bottom_quartile])
        if bottom_quartile
        else 0.0
    )
    hit_rate = top_mean - bottom_mean

    # Long-only Sharpe (top quartile)
    if len(top_quartile) >= 2:
        top_returns = [s.forward_5d_return for s in top_quartile]  # type: ignore[union-attr]
        period_sharpe = _sharpe_ratio(top_returns)
        periods_per_year = 252.0 / max(forward_days, 1)
        sharpe_long_only = (
            period_sharpe * (periods_per_year**0.5)
            if period_sharpe is not None
            else None
        )
    else:
        sharpe_long_only = None

    # CNS-return correlation
    valid = [
        (s.cns_score, s.forward_5d_return)
        for s in details
        if s.forward_5d_return is not None
    ]
    if len(valid) >= 3:
        cns_corr = _pearson_correlation([v[0] for v in valid], [v[1] for v in valid])
    else:
        cns_corr = None

    # Date range
    all_dates = sorted(set(s.date for s in details))
    start_date = all_dates[0] if all_dates else ""
    end_date = all_dates[-1] if all_dates else ""

    return SentimentBacktestResult(
        start_date=start_date,
        end_date=end_date,
        total_packs_analyzed=total_packs,
        total_signals=len(details),
        top_quartile_cns_names=top_names,
        bottom_quartile_cns_names=bottom_names,
        hit_rate_top_vs_bottom=hit_rate,
        sharpe_long_only=sharpe_long_only,
        cns_return_correlation=cns_corr,
        forward_days=forward_days,
        details=details,
    )
