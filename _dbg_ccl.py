"""Debug: inspect raw CCL historical ATM IV data."""
import sys
from datetime import datetime, timedelta
sys.path.insert(0, '.')
from shared.thetadata import ThetaDataController
import collections

td = ThetaDataController()
exp = '20260918'
rows = td.option_bulk_hist_eod_greeks('CCL', exp, '20260801', '20260827')
print('total rows:', len(rows))
if rows:
    print('keys:', sorted(rows[0].keys()))
    byday = collections.defaultdict(list)
    for r in rows:
        if r.get('right') == 'C':
            byday[str(r.get('date'))].append(r)
    print('days:', len(byday))
    for d in sorted(byday)[:6]:
        rs = sorted(byday[d], key=lambda x: abs(float(x.get('strike', 0)) - 24990))[:4]
        for r in rs:
            print(d, 'strike=', r.get('strike'), 'iv=', r.get('implied_vol'))
td.close()
