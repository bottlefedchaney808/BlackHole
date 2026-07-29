"""CME Swap Data Repository scraper - swap positioning signals."""
import json, re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.request import urlopen, Request

CME_URL = "https://www.cmegroup.com/market-data/repository/data.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

class CMESwapScraper:
    def fetch_swap_page(self) -> Optional[str]:
        try:
            req = Request(CME_URL, headers={"User-Agent": UA})
            with urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except: return None

    def parse_recent_swaps(self, html: str, max_rows: int = 50) -> List[Dict]:
        swaps = []
        # Find the equity tab table
        # CME uses a table with Dissemination Identifier rows
        rows = re.findall(
            r'<tr[^>]*>\s*<td[^>]*>(SDR\w+)</td>\s*<td[^>]*>(\w+)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>(NEWT|CORR|MODI|EROR)</td>\s*<td[^>]*>(TRAD|OTHR)</td>',
            html
        )
        for match in rows[:max_rows]:
            swaps.append({
                "dissemination_id": match[0],
                "upi": match[1],
                "fisn": match[2],
                "action": match[3],
                "event": match[4],
                "underlier": match[2],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        return swaps

    def get_equity_swap_by_ticker(self, ticker: str) -> List[Dict]:
        html = self.fetch_swap_page()
        if not html: return []
        swaps = self.parse_recent_swaps(html)
        return [s for s in swaps if ticker.upper() in s["underlier"].upper()]

def build_swap_snapshot(ticker: str, lookback_days: int = 7) -> Dict:
    scraper = CMESwapScraper()
    matches = scraper.get_equity_swap_by_ticker(ticker)
    return {"ticker": ticker, "swap_activity": len(matches),
            "var_swap_count": len([m for m in matches if any(kw in m["underlier"].lower() for kw in ["var","vol","variance","volatility"])]),
            "total_notional_usd": 0,
            "large_positions_near": 0,
            "lookback_days": lookback_days,
            "timestamp": datetime.now(timezone.utc).isoformat()}
