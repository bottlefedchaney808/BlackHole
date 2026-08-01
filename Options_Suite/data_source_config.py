# Configuration for market data source preference
# Set PREFER_THETADATA = True to prefer the local ThetaData API when available.
PREFER_THETADATA = True

def set_prefer_thetadata(val: bool):
    global PREFER_THETADATA
    PREFER_THETADATA = bool(val)

# If True, when ThetaData fails or returns insufficient data, degrade gracefully
# (flat-vol smile fallback, constant rate/dividend below) instead of prompting.
# NOTE: yfinance has been purged from the active data path -- PotatoHedge/ThetaData
# is now the sole live source. The "fallback" here is no longer yfinance; it is the
# safe constants below, used only when PotatoHedge itself can't supply a value so a
# transient API failure degrades predictably instead of crashing or silently
# corrupting a run. (yfinance may be reintroduced as an explicit backup source later.)
AUTO_FALLBACK_ON_THETADATA_FAILURE = True

def set_auto_fallback(val: bool):
    global AUTO_FALLBACK_ON_THETADATA_FAILURE
    AUTO_FALLBACK_ON_THETADATA_FAILURE = bool(val)

# ---------------------------------------------------------------------------
# Guarded fallbacks -- used ONLY when PotatoHedge can't return a usable value.
# Every PotatoHedge-sourced number is validated/clamped before use; if it fails
# validation or the call errors, these take over and a warning is printed. Edit
# these to match current conditions if you ever run fully offline.
# ---------------------------------------------------------------------------
FALLBACK_RISK_FREE_RATE = 0.046   # ~current 3-6mo T-bill; matches recent live runs
FALLBACK_DIV_YIELD = 0.0          # assume no dividend when unknown (safe for non-payers)
FALLBACK_VOL = 0.30               # only a seed (e.g. Heston V0) -- never a final answer

# The PotatoHedge /api/db/yield_curve and stock-dividend endpoints are NOT yet
# verified against a live response (see POTATOHEDGE_API_REFERENCE.md). The parsers
# in thetadata_controller.py are written defensively and fall back to the constants
# above on any surprise. Flip this to False to skip those calls entirely and always
# use the constants (useful until the endpoints are smoke-tested on a live session).
USE_POTATOHEDGE_RATE = True
USE_POTATOHEDGE_DIVIDEND = True
