#!/usr/bin/env python3
"""Standalone launcher for the Sector Rotation / Factor Momentum scanner.

Runs independently of main.py's per-ticker loop since sector rotation
scores 15 sector ETFs (not a single ticker) and is comparatively
expensive (16 price-history fetches per run).

Usage:
    python sector_rotation_launcher.py              # single run
    python sector_rotation_launcher.py --loop        # repeat on an interval
    python sector_rotation_launcher.py --period 3mo   # shorter lookback
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from scanner.sector_rotation import rank_sectors, format_rotation
from scanner.earnings_scanner import EarningsScanner, format_earnings
from scanner.options_scanner_base import get_td


def _parse_args(argv: List[str]) -> argparse.Namespace:
    """Parse command-line arguments for the sector rotation launcher."""
    parser = argparse.ArgumentParser(
        description="Sector Rotation / Factor Momentum launcher."
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Repeat the scan every SCAN_INTERVAL_MINUTES until interrupted.",
    )
    parser.add_argument(
        "--period", default="6mo",
        help="Price history period passed to rank_sectors (default 6mo).",
    )
    return parser.parse_args(argv)


def _run_earnings_check(ranks, top_n: int = 3) -> None:
    """Run an earnings-calendar check for the top-ranked tickers that
    sector rotation just identified.

    Reuses ``EarningsScanner`` (scanner/earnings_scanner.py) rather than
    reimplementing any earnings-date/IV logic. Prefers tickers ranked
    "BULLISH"; falls back to the top *top_n* by composite score if none
    are BULLISH (e.g. all-NEUTRAL/BEARISH universe).
    """
    if not ranks:
        return

    top_tickers = [r.ticker for r in ranks if r.signal == "BULLISH"][:top_n]
    if not top_tickers:
        top_tickers = [r.ticker for r in ranks[:top_n]]

    print(f"\n  Earnings check for top sectors: {', '.join(top_tickers)}")
    try:
        td = get_td()
        scanner = EarningsScanner(td)
        results = scanner.scan_earnings(top_tickers)
        print(format_earnings(results))
    except Exception as e:
        print(f"  Earnings check failed: {e}")


def run_once(period: str = "6mo") -> None:
    """Run one sector-rotation pass, print the ranked table, and follow up
    with an earnings-calendar check for the top-ranked tickers."""
    ranks = rank_sectors(period=period)
    print(format_rotation(ranks))
    _run_earnings_check(ranks)


def main(argv: Optional[List[str]] = None) -> None:
    """Entry point: run once, or loop on an interval if --loop is passed."""
    # Windows consoles default to cp1252, which can't encode characters
    # this module (and several other scanner modules) print, e.g. em-dashes,
    # arrows, and box-drawing separators — causing a UnicodeEncodeError.
    # Reconfigure stdout to UTF-8 up front so any such output is safe,
    # rather than patching each individual string.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    run_once(args.period)
    if not args.loop:
        return
    try:
        while True:
            print(f"\n--- Next sector scan in {config.SCAN_INTERVAL_MINUTES} min ---")
            time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
            run_once(args.period)
    except KeyboardInterrupt:
        print("\nShutting down sector rotation launcher...")


if __name__ == "__main__":
    main()
