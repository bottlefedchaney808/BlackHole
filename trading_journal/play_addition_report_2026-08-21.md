#!/usr/bin/env python3
"""Play addition report generator — reads morning_scan and produces add summary."""

import re
from pathlib import Path

SCAN = Path("C:/Users/bottl/FinancialDevelopment/trading_journal/morning_scan_2026-08-21.md")
out = []

for line in SCAN.read_text().splitlines():
    if "| **COIN** |" in line or "| **HOOD** |" in line or "| **MSTR** |" in line:
        m = re.match(r'\| \*\*(.+?)\*\* \| (.+?) \| (.+?) \|', line)
        if m:
            out.append(f"{m.group(1)}: {m.group(2)} — {m.group(3)}")
    if "| **COIN 182.5C" in line or "| **HOOD 100C" in line or "| **MSTR 122C" in line:
        m = re.match(r'\| \*\*(.+?)\*\* \| (.+?) \| (.+?) \|', line)
        if m:
            out.append(f"  CONTRACT {m.group(1)}: {m.group(2)} — {m.group(3)}")

print("=" * 60)
print("RH WATCHLIST ADDITIONS — 2026-08-21 Morning Scan")
print("=" * 60)
print()
print("STOCK WATCHLIST (First list / ⚡):")
for o in out:
    if not o.startswith("  CONTRACT"):
        print(f"  {o}")
print()
print("OPTIONS WATCHLIST (💡):")
for o in out:
    if o.startswith("  CONTRACT"):
        print(f"  {o}")
print()
print("=" * 60)
print("DELIVERABLE: Stocks added and why")
print("=" * 60)
print()
print("1. COIN — added to stock + options watchlist")
print("   WHY: After-hours +5.88% (182.49 vs 172.35 close). Crypto complex")
print("   re-rating; COIN is the purest crypto-exchange beta. ATM IV 37.9%")
print("   on 20260828 expiry — rich premium for a momentum name. If crypto")
print("   bid holds into Friday open, this is the highest-conviction crypto")
print("   play. IV can crush if the AH move fades — dual thesis (long momentum")
print("   OR short vol).")
print()
print("2. HOOD — added to stock + options watchlist")
print("   WHY: After-hours +4.73% (99.60 vs 95.10 close). Exchange with direct")
print("   crypto-trading sensitivity (Bitcoin volume beneficiary). ATM IV 33.5%")
print("   on 20260828 — lower IV than COIN but same thesis. More diversified")
print("   revenue base than COIN. IV-rich name → candidate for sell-vol if")
print("   crypto bid stabilizes post-AH.")
print()
print("3. MSTR — added to stock + options watchlist")
print("   WHY: After-hours +8.97% (122.48 vs 112.39 close) — the biggest AH")
print("   mover in the scan. MicroStrategy is the highest-beta BTC proxy in")
print("   the list. ATM IV 31.3% on 20260828. Highest upside if crypto bid")
print("   continues, but also fastest to decay if it reverses. Asymmetric")
print("   risk/reward — high-beta momentum long OR vol play.")
print()
print("OPTION CONTRACTS ADDED TO OPTIONS WATCHLIST (all 2026-08-28 expiry):")
print("  COIN 182.5C — ATM call, IV 37.9%, sellout 2026-08-28T19:30Z")
print("  HOOD 100.0C — ATM call, IV 33.5%, sellout 2026-08-28T19:30Z")
print("  MSTR 122.0C — ATM call, IV 31.3%, sellout 2026-08-28T19:30Z")
print()
print("RATIONALE FOR CONTRACT SELECTION:")
print("  All three are ATM calls on the 2026-08-28 Friday expiry — the same")
print("  expiry ThetaData used for the IV scan. ATM strikes chosen because:")
print("  (a) tightest bid/ask markets for clean entry/exit,")
print("  (b) maximum gamma — most responsive to the AH momentum continuing,")
print("  (c) defined risk (long calls only) given ~$61 buying power constraint")
print("      that makes these unaffordable right now — watchlist queue only.")
print()
print("BUYING POWER NOTE:")
print("  Agentic account BP ~$61 (per 8/20 plan). None of these ATM calls are")
print("  executable at current premium within that constraint — they're queued")
print("  for when stops trigger or capital is added. Courtsey: no orders placed.")
