#!/usr/bin/env python3
"""F8 Investigation: Determine if ThetaData OI endpoint is sparse or full-coverage.

Pulls 3 months of historical OI for SPY and checks if strikes disappear/reappear
across consecutive dates (sparse) or are always present (full coverage).
"""
import sys
from datetime import datetime, timedelta
from collections import defaultdict
from thetadata_client import ThetaDataController
import expiry_selector

ticker = 'SPY'
end_date = datetime.now().date()
start_date = end_date - timedelta(days=90)  # 3 months back

print(f"Fetching OI history for {ticker} from {start_date} to {end_date}...")

td = ThetaDataController()
target_T = 0.25
expiry_str, actual_T = expiry_selector.resolve_expiration(td, ticker, None, target_T)
print(f'Using expiry: {expiry_str} (T={actual_T:.3f} years)\n')

oi_rows = td.option_bulk_hist_oi(ticker, expiry_str, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
td.close()

# Group by date
oi_by_date = defaultdict(dict)
for row in oi_rows:
    date = row.get('date')
    strike = int(row.get('strike', 0))
    right = row.get('right')
    oi = int(row.get('open_interest', 0))
    if date and strike and right:
        oi_by_date[date][(strike, right)] = oi

dates = sorted(oi_by_date.keys())
print(f'Found {len(dates)} dates of OI data\n')

if len(dates) < 2:
    print('ERROR: Only 1 or 0 dates found — inconclusive. Need at least 2 consecutive dates.')
    sys.exit(1)

# Analyze consecutive days
print('Strike presence across consecutive dates:')
print('-' * 100)

total_disappeared = 0
total_appeared = 0

for i in range(min(10, len(dates)-1)):  # Show first 10 transitions
    d1, d2 = dates[i], dates[i+1]
    s1 = set(oi_by_date[d1].keys())
    s2 = set(oi_by_date[d2].keys())
    disappeared = s1 - s2
    appeared = s2 - s1

    total_disappeared += len(disappeared)
    total_appeared += len(appeared)

    print(f'{d1} → {d2}:', end='  ')
    print(f'{len(s1):3d} → {len(s2):3d} strikes', end='')
    if disappeared:
        print(f'  ({len(disappeared):2d} disappeared: {list(disappeared)[:2]})', end='')
    if appeared:
        print(f'  ({len(appeared):2d} appeared: {list(appeared)[:2]})', end='')
    print()

print('-' * 100)
print(f'\nSummary: Across {min(10, len(dates)-1)} date transitions:')
print(f'  - Total strikes that disappeared: {total_disappeared}')
print(f'  - Total strikes that appeared: {total_appeared}')
print()

if total_disappeared > 50 or total_appeared > 50:
    print('✓ VERDICT: SPARSE ENDPOINT')
    print('  Many strikes disappear/reappear across dates.')
    print('  This confirms F8 is a REAL BUG — endpoint only returns nonzero-OI rows.')
    print('  A strike with 0 OI today becomes indistinguishable from a missing endpoint row.')
    sys.exit(0)
elif total_disappeared == 0 and total_appeared == 0:
    print('✓ VERDICT: FULL COVERAGE ENDPOINT')
    print('  All strikes present every day — no disappear/reappear.')
    print('  F8 is NOT a bug — endpoint returns every strike every day.')
    sys.exit(0)
else:
    print('? VERDICT: INCONCLUSIVE')
    print(f'  Small number of disappears/appears ({total_disappeared}/{total_appeared}).')
    print('  Need to investigate more dates or look at OI=0 handling specifically.')
    sys.exit(1)
