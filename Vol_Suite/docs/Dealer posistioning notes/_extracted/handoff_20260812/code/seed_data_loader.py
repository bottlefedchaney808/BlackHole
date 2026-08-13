#!/usr/bin/env python3
"""seed_data_loader.py — load a seed_data_*.json payload saved by seed_data_maker.

Lets consumers (seed_flip_compare, a Windows-side reproduction, any analysis)
read the dense EOD greeks / OI-by-day / spot from disk WITHOUT hitting the
PotatoHedge ThetaData proxy. Returns the same (greeks, oi, spot) 3-tuple the
live _build_payload returns.

Usage:
  from seed_data_loader import load_seed_data
  greeks, oi, spot = load_seed_data("seed_data_SPY_20261120_150d.json")

For a directory scan:
  load_all_seed_data("/path/to/dir") -> {ticker: (greeks, oi, spot)}
"""
import glob
import json
import os
from typing import Dict, List, Tuple


def load_seed_data(path: str) -> Tuple[List[dict], List[dict], List[dict]]:
    """Load one saved payload. Returns (greeks, oi, spot) rows verbatim."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["greeks"], data["oi"], data["spot"]


def load_all_seed_data(
        dirpath: str,
) -> Dict[str, Tuple[List[dict], List[dict], List[dict]]]:
    """Scan a directory for seed_data_*.json and return {ticker: payload}.

    Ticker is parsed from the filename (seed_data_<TICKER>_<expiry>_<n>d.json).
    """
    out: Dict[str, Tuple[List[dict], List[dict], List[dict]]] = {}
    for path in sorted(glob.glob(os.path.join(dirpath, "seed_data_*.json"))):
        base = os.path.basename(path)
        try:
            ticker = base.split("_")[2]
        except IndexError:
            continue
        out[ticker] = load_seed_data(path)
    return out


def manifest_of(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["manifest"]


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        g, o, s = load_seed_data(p)
        print(f"{os.path.basename(p)}: greeks={len(g)} oi={len(o)} spot={len(s)} "
              f"manifest={manifest_of(p)}")
