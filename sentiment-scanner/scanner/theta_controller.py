"""ThetaData client proxied through api.potatohedge.com."""
import json
import httpx
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

BASE_URL = "https://api.potatohedge.com"
HEADERS = {"CF-Access-Client-Id": "4185445bed2be507ea70d7db987096e3.access",
           "CF-Access-Client-Secret": "dd22c45b4a1b13cc5b4cf67eff4e5db440887d80d788a2acd700a080397449d7"}

def strike_to_theta(k: float) -> int: return int(round(k * 1000))
def strike_from_theta(k: int) -> float: return k / 1000.0

class ThetaDataController:
    def __init__(self):
        self.client = httpx.Client(headers=dict(HEADERS), timeout=30.0)
    def _get(self, path: str):
        r = self.client.get(f"{BASE_URL}{path}", timeout=30.0)
        r.raise_for_status()
        return r.json()
    def _parse_rows(self, data):
        if not isinstance(data, list) or len(data) < 2: return []
        headers = data[0]
        return [dict(zip(headers, row)) for row in data[1:]]
    def list_expirations(self, root: str) -> List[str]:
        return [str(e) for e in self._get(f"/api/theta/list/expirations/{root}")]
    def list_strikes(self, root: str, exp: str) -> List[float]:
        """Returns strikes in dollar format (not theta cents*1000)."""
        rows = self._parse_rows(self._get(f"/api/theta/list/strikes/{root}/{exp}"))
        return [float(row['strike']) for row in rows if 'strike' in row]
    def stock_snapshot_quote(self, root: str):
        rows = self._parse_rows(self._get(f"/api/theta/snapshot/stock/quote/{root}"))
        return rows[0] if rows else {}
    def option_bulk_greeks(self, root: str, exp: str):
        return self._parse_rows(self._get(f"/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}"))
    def fetch_spot_price(self, ticker: str) -> float:
        try:
            quote = self.stock_snapshot_quote(ticker)
            for key in ['mid', 'bid', 'ask', 'last']:
                if key in quote and quote[key]: return float(quote[key])
        except: pass
        return 0.0
    def close(self): self.client.close()
