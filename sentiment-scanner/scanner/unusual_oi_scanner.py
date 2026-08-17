"""Unusual OI / Volume Scanner — detects OI outliers vs baseline.

Compares current open interest profile to a stored baseline.  Flags tickers
where total OI has changed by more than a threshold (default 2x) or where
individual strikes have abnormal OI concentration.

The correlation engine uses this for the SMART_MONEY_POSITIONING signal:
when OI surges AND CNS is elevated, it suggests real money is positioning
around a contested narrative.
"""

import json
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import defaultdict

from scanner.options_scanner_base import get_td, strike_from_theta

# OI change multiplier vs baseline to flag as "unusual"
OI_SURGE_MULTIPLIER = 2.0
# Minimum OI at a single strike to be considered meaningful
MIN_OI_FOR_FLAG = 50
# Maximum number of expiries to scan for OI
MAX_EXPIRIES = 5


@dataclass
class OiStrike:
    strike: float
    right: str
    oi: int
    expiry: str


@dataclass
class UnusualOiScan:
    ticker: str
    current_total_oi: int
    baseline_total_oi: int
    oi_change_pct: float
    surge_detected: bool
    top_strikes: List[OiStrike]
    num_expiries: int
    timestamp: str
    error: Optional[str] = None


# ---- Baseline store, persisted to disk so it survives cold starts ----
# Every fresh dashboard/orchestrator process previously started with an empty
# in-memory dict, so the very first scan of a run always reported
# baseline_total_oi=0 / oi_change_pct=0.0 no matter what the ticker's real OI
# history was. Persisting to JSON means only the first-ever scan of a ticker
# is baseline-less; every subsequent process picks up where the last one left off.
_BASELINE_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "oi_baseline.json")

_baselines: Dict[str, int] = {}
_baselines_loaded = False


def _load_baselines() -> None:
    global _baselines_loaded
    if _baselines_loaded:
        return
    _baselines_loaded = True
    try:
        with open(_BASELINE_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _baselines.update({str(k): int(v) for k, v in data.items()})
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError, TypeError):
        pass


def _save_baselines() -> None:
    try:
        os.makedirs(os.path.dirname(_BASELINE_FILE), exist_ok=True)
        with open(_BASELINE_FILE, "w") as f:
            json.dump(_baselines, f)
    except OSError:
        pass


def set_baseline(ticker: str, total_oi: int) -> None:
    _load_baselines()
    _baselines[ticker] = total_oi
    _save_baselines()


def get_baseline(ticker: str) -> int:
    _load_baselines()
    return _baselines.get(ticker, 0)


def _sum_oi_for_expiry(td, ticker: str, exp: str) -> List[OiStrike]:
    """Fetch OI for one expiry, return list of OiStrike + total."""
    try:
        rows = td.option_bulk_oi(ticker, exp)
    except Exception:
        return []
    strikes = []
    for row in rows:
        try:
            k = strike_from_theta(int(row["strike"]))
            right = row["right"]
            oi = int(row["open_interest"])
            if oi > 0:
                strikes.append(OiStrike(strike=k, right=right, oi=oi, expiry=exp))
        except (ValueError, KeyError, TypeError):
            continue
    return strikes


def scan_unusual_oi(
    ticker: str,
    max_expiries: int = MAX_EXPIRIES,
    surge_multiplier: float = OI_SURGE_MULTIPLIER,
) -> UnusualOiScan:
    """Scan OI for one ticker, compare to in-memory baseline.

    Parameters
    ----------
    ticker : str
    max_expiries : int
        Number of nearest expiries to check.
    surge_multiplier : float
        OI multiple above baseline to flag as surge.

    Returns
    -------
    UnusualOiScan
    """
    td = get_td()
    try:
        exps = td.list_expirations(ticker)
    except Exception as e:
        return UnusualOiScan(
            ticker=ticker, current_total_oi=0, baseline_total_oi=0,
            oi_change_pct=0.0, surge_detected=False,
            top_strikes=[], num_expiries=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )

    if not exps:
        return UnusualOiScan(
            ticker=ticker, current_total_oi=0, baseline_total_oi=0,
            oi_change_pct=0.0, surge_detected=False,
            top_strikes=[], num_expiries=0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error="no_expiries",
        )

    all_strikes: List[OiStrike] = []
    for exp in exps[:max_expiries]:
        all_strikes.extend(_sum_oi_for_expiry(td, ticker, exp))

    current_total = sum(s.oi for s in all_strikes)
    baseline = get_baseline(ticker)

    # If no baseline exists, set it now and report no surge
    if baseline == 0:
        set_baseline(ticker, current_total)
        oi_change_pct = 0.0
        surge = False
    else:
        oi_change_pct = (
            ((current_total - baseline) / baseline) * 100.0 if baseline > 0 else 0.0
        )
        surge = current_total >= baseline * surge_multiplier

    # Update baseline
    set_baseline(ticker, current_total)

    # Top OI strikes
    top = sorted(all_strikes, key=lambda s: s.oi, reverse=True)[:10]

    return UnusualOiScan(
        ticker=ticker,
        current_total_oi=current_total,
        baseline_total_oi=baseline,
        oi_change_pct=round(oi_change_pct, 1),
        surge_detected=surge,
        top_strikes=top,
        num_expiries=min(len(exps), max_expiries),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def format_unusual_oi(scan: UnusualOiScan) -> str:
    """One-line summary string for console output."""
    if scan.error:
        return f"  {scan.ticker:6s} | OI: ERROR — {scan.error}"
    surge = " ⚡ SURGE" if scan.surge_detected else ""
    top_str = ", ".join(
        f"${s.strike:.0f}{s.right} ({s.oi:,})" for s in scan.top_strikes[:3]
    )
    return (
        f"  {scan.ticker:6s} | OI: {scan.current_total_oi:,} "
        f"({scan.oi_change_pct:+.1f}%){surge} | "
        f"Top: {top_str}"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    scan = scan_unusual_oi(ticker)
    print(format_unusual_oi(scan))