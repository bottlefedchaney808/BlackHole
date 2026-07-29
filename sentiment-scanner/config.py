"""Configuration for the sentiment scanner."""
import os

SCAN_INTERVAL_MINUTES = 15
TICKER_WATCHLIST = []
MAX_TICKERS_TO_SCAN = 20

WAR_SCORE_THRESHOLD = 0.3
CNS_THRESHOLD = 50
VOLUME_THRESHOLD = 20

THETADATA_BASE_URL = "https://api.potatohedge.com"
THETADATA_CLIENT_ID = os.getenv("THETADATA_CF_ACCESS_CLIENT_ID")
THETADATA_CLIENT_SECRET = os.getenv("THETADATA_CF_ACCESS_CLIENT_SECRET")

SWAP_LOOKBACK_DAYS = 3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "data")
HIGHLIGHT_PACKS_DIR = os.path.join(OUTPUT_DIR, "exports", "highlighted_ticker_packs")
HIGHLIGHT_PACK_MANIFEST = os.path.join(HIGHLIGHT_PACKS_DIR, "latest_manifest.json")
HIGHLIGHT_PACK_TTL_DAYS = 14
HIGHLIGHT_PACK_MAX_MANIFEST_ENTRIES = 100
