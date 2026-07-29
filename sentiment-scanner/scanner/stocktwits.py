"""StockTwits public API scraper --- no auth needed. Uses urllib for sandbox compat."""
import math
import time
import re
import json
import urllib.request
from datetime import datetime, timezone
from typing import List, Dict, Optional

STOCKTWITS_API = "https://api.stocktwits.com/api/2"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

TICKER_BLACKLIST = {
    "I", "A", "IT", "GO", "NOW", "AI", "ALL", "BIG", "CAN", "CAT",
    "DO", "FOR", "GET", "HAS", "HOT", "MAN", "NEW", "NOT", "ONE",
    "OUT", "RUN", "SAY", "SEE", "SET", "TOP", "USA", "USE", "WAY",
    "ARE", "BE", "BY", "HE", "IF", "IN", "IS", "ME", "MY", "NO",
    "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE"
}

def extract_cashtags(body: str) -> List[str]:
    return [t for t in re.findall(r'\$([A-Z]{1,5})', body) if t not in TICKER_BLACKLIST]

def _fetch_json(url: str, timeout: int = 15) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

def _get_sentiment(msg: dict) -> Optional[str]:
    """Extract Bullish/Bearish sentiment from StockTwits message."""
    entities = msg.get("entities")
    if entities and isinstance(entities, dict):
        sent = entities.get("sentiment")
        if sent and isinstance(sent, dict):
            return sent.get("basic")
    return None

class StockTwitsScraper:
    def __init__(self, rate_limit: float = 1.0):
        self.rate_limit = rate_limit
        self._last_call = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_call = time.time()

    def get_ticker_stream(self, ticker: str, max_pages: int = 3) -> List[Dict]:
        all_messages = []
        cursor = None
        for _ in range(max_pages):
            self._throttle()
            url = f"{STOCKTWITS_API}/streams/symbol/{ticker}.json"
            if cursor:
                url += f"?cursor={cursor}"
            data = _fetch_json(url)
            if not data:
                break
            for msg in data.get("messages", []):
                all_messages.append(self._enrich(msg, ticker))
            cursor = data.get("cursor", {}).get("max")
            if not data.get("cursor", {}).get("more"):
                break
        return all_messages

    def get_trending(self) -> List[Dict]:
        self._throttle()
        data = _fetch_json(f"{STOCKTWITS_API}/trending/symbols.json")
        if not data:
            return []
        return [{"symbol": s["symbol"], "title": s["title"],
                 "watchlist_count": s.get("watchlist_count", 0),
                 "sentiment_change": s.get("sentiment_change"),
                 "volume_change": s.get("volume_change")}
                for s in data.get("symbols", [])]

    def _enrich(self, msg: Dict, ticker: str) -> Dict:
        user = msg.get("user", {})
        join_date = user.get("join_date", "")
        account_age_days = 0
        if join_date:
            try:
                joined = datetime.strptime(join_date, "%Y-%m-%d")
                account_age_days = (datetime.now() - joined).days
            except ValueError:
                pass
        body = msg.get("body", "")
        return {
            "id": msg.get("id"),
            "ticker": ticker,
            "body": body,
            "created_at": msg.get("created_at", ""),
            "sentiment": _get_sentiment(msg),
            "user": {
                "username": user.get("username"),
                "join_date": join_date,
                "account_age_days": account_age_days,
                "followers": user.get("followers", 0),
                "ideas_count": user.get("ideas", 0),
                "like_count": user.get("like_count", 0),
            },
            "likes": msg.get("likes", {}).get("total", 0),
            "cashtags": extract_cashtags(body),
        }

    def close(self):
        pass
