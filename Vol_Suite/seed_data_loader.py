#!/usr/bin/env python3
"""seed_data_loader.py — load a seed_data_*.json payload saved by seed_data_maker.

Ported into Vol_Suite/ proper (2026-08-13) from the WSL/Ubuntu-environment
handoff package (docs/Dealer posistioning notes/_extracted/handoff_20260812/
code/seed_data_loader.py) as part of wiring real accumulation into the live
dealer-positioning path -- see the CARL audit + implementation plan. This is
the mechanism the OOS falsifier + lead-lag test harness
(backtest_accumulation_falsifier.py) uses to read the 12-ticker, 150-day
cached dataset already pulled once and saved to disk, so testing runs
offline against saved local pulls instead of hitting the ThetaData proxy on
every run (the proxy-overload problem already hit once).

Usage:
  from seed_data_loader import load_seed_data
  greeks, oi, spot = load_seed_data("seed_data_SPY_20261120_150d.json")

For a directory scan:
  load_all_seed_data("/path/to/dir") -> {ticker: (greeks, oi, spot)}
"""

import glob
import json
import os


def load_seed_data(path: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Load one saved payload. Returns (greeks, oi, spot) rows verbatim."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["greeks"], data["oi"], data["spot"]


def load_all_seed_data(
    dirpath: str,
) -> dict[str, tuple[list[dict], list[dict], list[dict]]]:
    """Scan a directory for seed_data_*.json and return {ticker: payload}.

    Ticker is parsed from the filename (seed_data_<TICKER>_<expiry>_<n>d.json).
    """
    out: dict[str, tuple[list[dict], list[dict], list[dict]]] = {}
    for path in sorted(glob.glob(os.path.join(dirpath, "seed_data_*.json"))):
        base = os.path.basename(path)
        try:
            ticker = base.split("_")[2]
        except IndexError:
            continue
        if ticker in out:
            raise ValueError(
                f"load_all_seed_data: multiple seed files found for ticker {ticker!r} "
                f"in {dirpath!r} (latest: {path!r}); load them individually with "
                f"load_seed_data() instead of scanning the directory."
            )
        out[ticker] = load_seed_data(path)
    return out


def manifest_of(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["manifest"]


if __name__ == "__main__":
    import sys

    for p in sys.argv[1:]:
        g, o, s = load_seed_data(p)
        print(
            f"{os.path.basename(p)}: greeks={len(g)} oi={len(o)} spot={len(s)} "
            f"manifest={manifest_of(p)}"
        )
