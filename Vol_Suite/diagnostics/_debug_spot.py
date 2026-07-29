#!/usr/bin/env python3
import httpx, json

headers = {
    "CF-Access-Client-Id": "4185445bed2be507ea70d7db987096e3.access",
    "CF-Access-Client-Secret": "dd22c45b4a1b13cc5b4cf67eff4e5db440887d80d788a2acd700a080397449d7",
}

# Check SPY stock quote endpoint
r = httpx.get("https://api.potatohedge.com/api/theta/snapshot/stock/quote/SPY", headers=headers, timeout=30.0)
print(f"SPY stock quote: {r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"Data: {json.dumps(data, indent=2)[:1000]}")
else:
    print(f"Error: {r.text[:500]}")

# Check GME stock quote for comparison
r3 = httpx.get("https://api.potatohedge.com/api/theta/snapshot/stock/quote/GME", headers=headers, timeout=30.0)
print(f"\nGME stock quote: {r3.status_code}")
if r3.status_code == 200:
    data3 = r3.json()
    print(f"Data: {json.dumps(data3, indent=2)[:1000]}")
else:
    print(f"Error: {r3.text[:500]}")

# Check if SPY expirations exist
r2 = httpx.get("https://api.potatohedge.com/api/theta/list/expirations/SPY", headers=headers, timeout=30.0)
print(f"\nSPY expirations: {r2.status_code}")
if r2.status_code == 200:
    data2 = r2.json()
    print(f"First 10: {data2[:10]}")
    print(f"Count: {len(data2)}")
else:
    print(f"Error: {r2.text[:500]}")

# Also try SPX
for t in ["SPY", "SPX"]:
    r = httpx.get(f"https://api.potatohedge.com/api/theta/snapshot/stock/quote/{t}", headers=headers, timeout=30.0)
    print(f"\n{t} stock quote: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list) and len(data) > 1:
            print(f"  Headers: {data[0]}")
            print(f"  Row 1: {data[1]}")
        else:
            print(f"  Data: {str(data)[:500]}")
    else:
        print(f"  Error: {r.text[:300]}")