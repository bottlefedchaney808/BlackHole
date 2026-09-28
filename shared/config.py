"""Shared configuration constants and .env loader for all Financial_Development suites.

Every suite in this project (Options_Suite, Vol_Suite, sentiment-scanner,
VaR_Tools_Simulations) previously duplicated its own _load_dotenv_once()
implementation, each reading .env from its own directory.  This module
consolidates them into one canonical loader that reads .env from the
PROJECT_ROOT, so credentials stay in sync and one env file is sufficient.

Usage::

    from shared.config import load_env_once, PROJECT_ROOT, THETADATA_CF_ID

    load_env_once()
    client_id = os.environ.get(THETADATA_CF_ID)  # "THETADATA_CF_ACCESS_CLIENT_ID"
"""

import os
from pathlib import Path

# ── Project root -----------------------------------------------------------
# Resolves to the repo root (C:\Users\bottl\FinancialDevelopment on this host)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Credential env-var key constants ---------------------------------------
# These are the *names* of environment variables that hold credentials.
# Use them with os.environ.get() after calling load_env_once().
THETADATA_CF_ID = "THETADATA_CF_ACCESS_CLIENT_ID"
THETADATA_CF_SECRET = "THETADATA_CF_ACCESS_CLIENT_SECRET"
POTATOHEDGE_BASE_URL = "POTATOHEDGE_BASE_URL"
# The one Robinhood account the stock sleeves may trade ("Agentic"). Kept in
# `.env`, never in code: the repo is public and account numbers do not belong
# in its history.
ROBINHOOD_AGENTIC_ACCOUNT = "ROBINHOOD_AGENTIC_ACCOUNT"


def robinhood_agentic_account() -> str:
    """The agentic account number from `.env`, or "" when it is not set.

    Callers must treat "" as "refuse to trade", never as a default."""
    load_env_once()
    return os.environ.get(ROBINHOOD_AGENTIC_ACCOUNT, "").strip()

# ── Fallback constants (used when ThetaData / PotatoHedge can't supply a value)
FALLBACK_RISK_FREE_RATE = 0.046  # ~current 3-6mo T-bill
FALLBACK_DIV_YIELD = 0.0         # assume no dividend when unknown
FALLBACK_VOL = 0.30              # seed volatility (e.g. Heston V0)


# ── Cached .env loader (idempotent, reads from PROJECT_ROOT) ---------------
def load_env_once(_loaded: list = None) -> None:
    """Read ``PROJECT_ROOT/.env`` into ``os.environ`` exactly once.

    Existing environment variables always take precedence over values in the
    file (real env vars → .env file), so this is safe to call before every
    credential fetch without worrying about stale overrides.

    The ``_loaded`` sentinel is a private mutable-default argument that tracks
    whether the file has already been read in this process — it is *not* a
    public API and callers should never pass a value for it.
    """
    # Initialise the sentinel on first call.
    if _loaded is None:
        _loaded = [False]

    if _loaded[0]:
        return

    _loaded[0] = True

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    try:
        text = env_path.read_text(encoding="utf-8")
    except Exception:
        return

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("\"").strip("'")
        if key and key not in os.environ:
            os.environ[key] = val