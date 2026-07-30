# C:\Users\bottl\FinancialDevelopment\Vol_Suite\thetadata_client.py
#
# REDIRECT STUB -- This file previously contained a standalone
# ThetaDataController implementation.  Both implementations (this file and
# Options_Suite/thetadata_controller.py) have been merged into
# shared/thetadata.py for maintainability and so v3-API migration only needs
# to happen in one place. All fixes and endpoint coverage from both original
# files were carried over (union of methods; retry/concurrency logic and the
# fetch_spot_price truthy-check + trade-fallback fix are all present in the
# shared client).
#
# All public names are re-exported from the single shared source;
# imports from `Vol_Suite.thetadata_client` continue to work without
# modification.

import sys
import os
import time  # re-exported: tests monkeypatch tc.time.sleep to skip retry backoff.
             # `time` is a singleton module object, so this patches the same
             # object shared.thetadata's own `import time` uses internally.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.thetadata import (
    ThetaDataController,
    strike_to_theta,
    strike_from_theta,
)

# Re-export for tests that monkeypatch the old module-level functions
from shared.config import load_env_once as _load_dotenv_once

__all__ = [
    "ThetaDataController",
    "strike_to_theta",
    "strike_from_theta",
]

# Legacy: expose module-level constants any Vol_Suite callers might reference.
# These are still used inside shared/thetadata.py, so they remain available.
_HIST_GREEKS_CONCURRENCY = int(__import__("os").environ.get("THETADATA_HIST_CONCURRENCY", "8"))
_RETRY_STATUSES = (404, 502, 503, 504)
_RETRY_ATTEMPTS = 3
__all__.extend(["_HIST_GREEKS_CONCURRENCY", "_RETRY_STATUSES", "_RETRY_ATTEMPTS"])
