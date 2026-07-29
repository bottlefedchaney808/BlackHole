"""Reddit scanner - connects via MCP server when available.
Currently Reddit API is blocked for unauthenticated access (403).
When Docker MCP is set up with Reddit credentials, set MCP_AVAILABLE=True.
"""
from typing import List, Dict, Optional

MCP_AVAILABLE = False  # Set to True when Docker MCP is configured

TICKER_BLACKLIST = {
    "I","A","IT","GO","NOW","AI","ALL","BIG","CAN","CAT","DO","FOR","GET",
    "HAS","HOT","MAN","NEW","NOT","ONE","OUT","RUN","SAY","SEE","SET","TOP",
    "USA","USE","WAY","ARE","BE","BY","HE","IF","IN","IS","ME","MY","NO",
    "OF","ON","OR","SO","TO","UP","US","WE"
}

SUBREDDITS = ["wallstreetbets", "stocks", "investing", "options", "smallstreetbets"]

class RedditScraper:
    """Reddit scraper that connects through MCP when credentials are configured."""

    def __init__(self):
        self.available = MCP_AVAILABLE

    def get_hot_posts(self, subreddit: str = "wallstreetbets", limit: int = 25) -> List[Dict]:
        if not self.available:
            return []
        # When MCP is configured, this will call the Reddit MCP tools
        # Example: mcp__reddit__browse_subreddit(subreddit=subreddit, sort="hot", limit=limit)
        return []

    def scan_all(self) -> Dict[str, List[Dict]]:
        if not self.available:
            return {}
        return {}

    def close(self):
        pass
