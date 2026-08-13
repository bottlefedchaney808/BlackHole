#!/usr/bin/env python3
"""seed_data_maker.py — fetch a ticker's chain payload ONCE and save to disk.

The seed/accumulation comparison (`seed_flip_compare.py`) normally re-pulls the
dense EOD greeks + OI-by-day + spot from the PotatoHedge ThetaData proxy every
run. This tool fetches that payload a single time and writes it to a JSON file,
so the comparison (and any consumer) can run offline from disk without hitting
the proxy — essential when migrating between machines (WSL -> Windows) or when
the proxy is saturated.

Usage (from Vol_Suite/, scrubbed venv):
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \
    seed_data_maker.py SPY 150 /path/to/out_dir

Writes <out_dir>/seed_data_<TICKER>_<expiry>_<lookback>d.json with the greeks /
oi / spot rows verbatim, plus a manifest. Consume with seed_data_loader.py.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.thetadata import ThetaDataController
import expiry_selector
from seed_flip_compare import _build_payload


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ticker = sys.argv[1].upper()
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(out_dir, exist_ok=True)

    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, None, 0.25)
        greeks, oi, spot = _build_payload(td, ticker, expiry)
    finally:
        td.close()

    if not greeks:
        print(f"[seed_data_maker] no IV-history for {ticker}/{expiry}; nothing saved")
        return 1

    out = os.path.join(out_dir, f"seed_data_{ticker}_{expiry}_{lookback}d.json")
    manifest = {
        "ticker": ticker, "expiry": expiry, "lookback_days": lookback,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_greeks": len(greeks), "n_oi": len(oi), "n_spot": len(spot),
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"manifest": manifest, "greeks": greeks, "oi": oi, "spot": spot}, fh)
    print(f"[seed_data_maker] saved {len(greeks)} greeks, {len(oi)} oi, "
          f"{len(spot)} spot for {ticker}/{expiry} -> {out}")
    print(f"  manifest: {json.dumps(manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
