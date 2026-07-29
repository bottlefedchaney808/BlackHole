import sys
from datetime import datetime, timedelta
from thetadata_client import ThetaDataController
import expiry_selector

# Pull 5 consecutive trading days of OI history for SPY
ticker = 'SPY'
end_date = datetime.now().date()
start_date = end_date - timedelta(days=14)  # 2 weeks back, should span 10 trading days

# Find the nearest available expiry (next ~3 months out)
td = ThetaDataController()
target_T = 0.25  # 3 months
expiry_str, actual_T = expiry_selector.resolve_expiration(td, ticker, None, target_T)
print(f"Using expiry: {expiry_str} (T={actual_T:.3f} years)")

try:
    oi_rows = td.option_bulk_hist_oi(ticker, expiry_str, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
except Exception as e:
    print(f"Error fetching OI: {e}")
    print("Trying nearer expiry...")
    # Fall back to even nearer expiry
    target_T = 0.08  # 1 month
    expiry_str, actual_T = expiry_selector.resolve_expiration(td, ticker, None, target_T)
    print(f"Using fallback expiry: {expiry_str} (T={actual_T:.3f} years)")
    oi_rows = td.option_bulk_hist_oi(ticker, expiry_str, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))

td.close()

# Group by date and analyze strike presence
from collections import defaultdict
oi_by_date = defaultdict(dict)
for row in oi_rows:
    date = row.get('date')
    strike = int(row.get('strike', 0))
    right = row.get('right')
    oi = int(row.get('open_interest', 0))
    if date and strike and right:
        oi_by_date[date][(strike, right)] = oi

# Analyze strike consistency across consecutive days
dates = sorted(oi_by_date.keys())
print(f'Found OI data for {len(dates)} dates: {dates[:3]}...{dates[-3:]}')

for i in range(len(dates)-1):
    d1, d2 = dates[i], dates[i+1]
    s1 = set(oi_by_date[d1].keys())
    s2 = set(oi_by_date[d2].keys())
    disappeared = s1 - s2
    appeared = s2 - s1
    
    print(f'  {d1} → {d2}: {len(s1)} strikes → {len(s2)} strikes', end='')
    if disappeared:
        print(f', {len(disappeared)} disappeared (samples: {list(disappeared)[:2]})', end='')
    if appeared:
        print(f', {len(appeared)} appeared (samples: {list(appeared)[:2]})', end='')
    print()

print()
print('VERDICT: If many strikes disappear/reappear → SPARSE (only nonzero-OI rows returned)')
print('         If all strikes present every day → FULL (every strike, every day)')