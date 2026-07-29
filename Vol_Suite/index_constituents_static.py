#!/usr/bin/env python3
"""index_constituents_static.py

Static fallback for ETF constituent weights, used ONLY when the live
stockanalysis.com holdings endpoint is unreachable (it sits behind a bot filter
that intermittently 404s otherwise-valid requests -- see index_membership.py).
This replaced the yfinance funds_data fallback, which was removed when yahoo was
purged from the suite.

These are real top-holdings snapshots (weights in percent) captured from
stockanalysis.com. Weights drift over time, so this is a FALLBACK, not the
primary source -- the live endpoint is always tried first. Refresh by re-pulling
`/api/symbol/e/{ETF}/holdings` and pasting the top ~25 names/weights here.

Coverage: the broad large-cap indices (SPY, QQQ, DIA) that most single-name
dispersion candidates fall into. Sector SPDRs and IWM are intentionally left to
the live endpoint (their top-25 shift more and matter less for dispersion); add
them here the same way if you want static coverage for them too.
"""
from typing import Dict, List, Tuple

# As-of date of each snapshot, for transparency in logs.
AS_OF: Dict[str, str] = {"SPY": "2026-06-18", "QQQ": "2026-06-04", "DIA": "2026-06-05"}

STATIC_CONSTITUENTS: Dict[str, List[Tuple[str, float]]] = {
    "SPY": [
        ("NVDA", 7.95), ("AAPL", 6.79), ("MSFT", 4.37), ("AMZN", 3.71), ("GOOGL", 3.35),
        ("AVGO", 3.03), ("GOOG", 2.70), ("MU", 1.98), ("META", 1.96), ("TSLA", 1.75),
        ("BRK.B", 1.39), ("JPM", 1.36), ("AMD", 1.36), ("LLY", 1.35), ("INTC", 0.98),
        ("XOM", 0.89), ("JNJ", 0.85), ("V", 0.85), ("WMT", 0.80), ("AMAT", 0.76),
        ("LRCX", 0.75), ("CSCO", 0.73), ("CAT", 0.71), ("COST", 0.66), ("MA", 0.62),
    ],
    "QQQ": [
        ("NVDA", 8.42), ("AAPL", 7.24), ("MSFT", 5.04), ("MU", 4.90), ("AMZN", 4.32),
        ("AMD", 3.73), ("GOOGL", 3.43), ("TSLA", 3.31), ("GOOG", 3.18), ("AVGO", 3.15),
        ("META", 2.93), ("WMT", 2.52), ("INTC", 2.44), ("CSCO", 2.25), ("COST", 1.89),
        ("LRCX", 1.84), ("AMAT", 1.74), ("NFLX", 1.51), ("PLTR", 1.42), ("KLAC", 1.22),
        ("TXN", 1.21), ("MRVL", 1.21), ("SNDK", 1.14), ("QCOM", 1.13), ("LIN", 1.03),
    ],
    "DIA": [
        ("GS", 12.54), ("CAT", 10.92), ("MSFT", 5.03), ("UNH", 4.82), ("AMGN", 4.22),
        ("V", 3.91), ("JPM", 3.77), ("HD", 3.75), ("AXP", 3.75), ("AAPL", 3.71),
        ("SHW", 3.69), ("TRV", 3.66), ("IBM", 3.44), ("MCD", 3.38), ("AMZN", 2.97),
        ("JNJ", 2.81), ("BA", 2.60), ("HON", 2.58), ("NVDA", 2.48), ("CVX", 2.26),
        ("CRM", 2.24), ("MMM", 1.86), ("PG", 1.77), ("CSCO", 1.47), ("MRK", 1.46),
    ],
}


def get_static_constituents(etf_ticker: str) -> List[Tuple[str, float]]:
    """Return the static top-holdings list for an ETF, or [] if not covered."""
    return list(STATIC_CONSTITUENTS.get(etf_ticker.upper(), []))
