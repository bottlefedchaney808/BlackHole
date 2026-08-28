import sys
sys.path.insert(0, ".")
from Direction.indicator import _thread_client
from chart_app.flow_stamp import rows_from_flow_payload, _premium, _trade_ts

client = _thread_client()
env = client.flow.scanner_trades(
    root="SPY",
    start_date="20260817",
    end_date="20260817",
    min_premium=0,
    limit=5,
    offset=0,
)
data = getattr(env, "data", None)
print("DATA TYPE:", type(data))
rows = rows_from_flow_payload(data)
print("N ROWS:", len(rows))
for r in rows[:5]:
    print("ROW:", {k: r.get(k) for k in list(r.keys())[:8]})
    ts = _trade_ts(r)
    prem = _premium(r)
    print("   parsed ts:", ts, "| tzinfo:", getattr(ts, "tzinfo", None), "| premium:", prem)
