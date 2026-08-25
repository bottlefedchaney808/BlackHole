#!/usr/bin/env bash
"""Re-run the 4-arm seed/flow comparison OFFLINE from saved seed_data_*.json
(no ThetaData proxy needed). Reads the payloads via seed_data_loader.py and
feeds them into _accumulate_from_history.

Usage (from Vol_Suite/):
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \\
    seed_flip_compare_offline.py ../handoff_20260812/seed_data 150
"""

import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import replication_reference as rr
from seed_data_loader import load_all_seed_data, manifest_of


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    data_dir = sys.argv[1]
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    payloads = load_all_seed_data(data_dir)
    if not payloads:
        print(f"[offline] no seed_data_*.json in {data_dir}")
        return 1

    # Read each payload's expiry from its manifest.
    expiries = {}
    for path in sorted(glob.glob(os.path.join(data_dir, "seed_data_*.json"))):
        try:
            m = manifest_of(path)
            expiries[m["ticker"]] = m["expiry"]
        except Exception:
            pass

    arms = [
        ("live(repl)", "replication", "0", "0"),
        ("vanna_seed", "vanna", "0", "0"),
        ("live+vannaflow", "replication", "0", "1"),
        ("svi_rp_seed", "svi_rp", "0", "0"),
    ]
    summary = {}
    for ticker, (greeks, oi, spot) in sorted(payloads.items()):
        print(f"\n===== {ticker} (offline, {lookback}d) =====")
        expiry = expiries.get(ticker, "20261120")
        results = {}
        for label, seed_mode, ssign, vflow in arms:
            os.environ["DEALER_SEED_SIGN"] = ssign
            os.environ["DEALER_VANNA_FLOW"] = vflow
            try:
                acc = rr._accumulate_from_history(
                    ticker, expiry, lookback, seed_mode, greeks, oi, spot
                )
            finally:
                os.environ.pop("DEALER_SEED_SIGN", None)
                os.environ.pop("DEALER_VANNA_FLOW", None)
            book = acc.position_by_strike
            end_book = sum(book.values())
            regime = "SHORT" if end_book < 0 else ("LONG" if end_book > 0 else "FLAT")
            results[label] = {
                "end_book": end_book,
                "regime": regime,
                "n_days": len(acc.daily_trace),
            }
            print(
                f"  {label:<16} end_book={end_book:,.0f} ({regime}) n_days={len(acc.daily_trace)}"
            )
        summary[ticker] = results

    print("\n===== CONSOLIDATED (offline) =====")
    print(
        f"{'Ticker':<7} {'live':>12} {'vanna_seed':>12} {'vannaflow':>12} {'svi_rp_seed':>12}"
    )
    for ticker, results in sorted(summary.items()):
        live = results["live(repl)"]["end_book"]
        vs = results["vanna_seed"]["end_book"]
        vf = results["live+vannaflow"]["end_book"]
        sr = results["svi_rp_seed"]["end_book"]
        print(f"{ticker:<7} {live:>12,.0f} {vs:>12,.0f} {vf:>12,.0f} {sr:>12,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
