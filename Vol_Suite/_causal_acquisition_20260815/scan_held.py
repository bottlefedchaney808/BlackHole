#!/usr/bin/env python3
"""Scan every seed_data_*.json in the repo to enumerate the EXACT set of dates
already present in any corpus (held dates). Avoid all of these when choosing
new acquisition days."""
import json, glob, os, sys

ROOT = r"C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev"
held = set()
seen_files = []
for pat in [
    os.path.join(ROOT, "Vol_Suite", "_scratch_tier2", "seed_data_*.json"),
    os.path.join(ROOT, "Vol_Suite", "_scratch_tier2b", "window_*", "seed_data_*.json"),
    os.path.join(ROOT, "Vol_Suite", "seed_data_*.json"),
    os.path.join(ROOT, "Vol_Suite", "_causal_acquisition_20260815", "seed_data_*.json"),
]:
    for f in sorted(glob.glob(pat)):
        seen_files.append(f)
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception as e:
            print(f"[ERR] {os.path.basename(f)}: {e}")
            continue
        for key in ("greeks", "spot", "oi"):
            for row in d.get(key, []):
                v = row.get("date") or row.get("Date") or row.get("created")
                if v:
                    digits = "".join(ch for ch in str(v) if ch.isdigit())[:8]
                    if len(digits) == 8:
                        held.add(digits)
        # manifest obs ranges
        m = d.get("manifest", {})
        for rng in (m.get("obs_date_range") or []):
            dig = "".join(ch for ch in str(rng) if ch.isdigit())[:8]
            if len(dig) == 8:
                held.add(dig)

print(f"scanned {len(seen_files)} seed files")
print(f"HELD DATES ({len(sorted(held))}):")
print(" ".join(sorted(held)))
