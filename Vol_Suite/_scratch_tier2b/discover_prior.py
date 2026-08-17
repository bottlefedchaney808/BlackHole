#!/usr/bin/env python3
"""Discover deterministic candidate short expiries for a prior as_of.

For each as_of, candidate expiries = every calendar date in [as_of+2 .. as_of+7]
(a genuine 2-7 DTE window end). We probe each candidate via explicit-expiry
ThetaData greeks pull; whichever returns nonempty real rows is a genuine listed
short expiry at that as_of. We return the ordered list of valid candidates so
the puller can select the nearest.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from thetadata_client import ThetaDataController  # noqa: E402

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_env = os.path.join(_root, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def dte(expiry, as_of):
    return max((expiry - as_of).days, 0)


def discover(td, ticker, as_of, lookback_days=14):
    """Return sorted list of (dte, expiry_YYYYMMDD) with genuine data in 2-7 DTE."""
    valid = []
    for offset in range(2, 8):
        exp_date = as_of + datetime.timedelta(days=offset)
        exp_s = exp_date.strftime("%Y%m%d")
        # calendar day is a weekend -> not a trading expiry, skip fast
        if exp_date.weekday() >= 5:
            continue
        start = as_of - datetime.timedelta(days=lookback_days)
        try:
            gr = td.option_bulk_hist_eod_greeks(ticker, exp_s,
                                                start.strftime("%Y%m%d"),
                                                as_of.strftime("%Y%m%d"))
        except Exception as e:
            print(f"    {exp_s} greeks ERR {type(e).__name__}: {e}", flush=True)
            continue
        if len(gr) == 0:
            continue
        # nonempty -> genuine listed expiry with data in the lookback
        valid.append((dte(exp_date, as_of), exp_s))
    valid.sort()
    return valid


def main():
    td = ThetaDataController()
    prior = ["2026-07-31", "2026-07-17", "2026-07-03", "2026-06-19", "2026-06-05"]
    sample = ["AAPL", "AMD", "AMZN", "GOOGL", "JPM", "META",
              "MSFT", "NFLX", "NVDA", "QQQ", "SPY", "TSLA"]
    for asof_s in prior:
        as_of = datetime.date.fromisoformat(asof_s)
        print(f"\n=== as_of {asof_s} ===", flush=True)
        # probe all tickers to capture ticker-specific expiry patterns
        for tk in sample:
            v = discover(td, tk, as_of)
            print(f"  {tk:5s}: valid 2-7DTE expiries={v}", flush=True)
    td.close()


if __name__ == "__main__":
    main()
