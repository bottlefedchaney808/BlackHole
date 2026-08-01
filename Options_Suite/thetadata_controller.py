# C:\Users\bottl\FinancialDevelopment\Options_Suite\thetadata_controller.py
#
# REDIRECT STUB -- This file previously contained a standalone
# ThetaDataController implementation.  Both implementations (this file and
# Vol_Suite/thetadata_client.py) have been merged into shared/thetadata.py
# for maintainability and so v3-API migration only needs to happen in one
# place. All fixes from both original files (including this file's
# fetch_spot_price truthy-check fix and stock_snapshot_trade fallback) were
# carried over.
#
# All public names are re-exported from the single shared source;
# imports from `Options_Suite.thetadata_controller` continue to work
# without modification.

from shared.thetadata import (
    ThetaDataController,
    strike_to_theta,
    strike_from_theta,
)

__all__ = [
    "ThetaDataController",
    "strike_to_theta",
    "strike_from_theta",
]

# Legacy: expose _load_dotenv_once as a no-op for any code that
# imported it directly.
_load_dotenv_once = lambda: None  # noqa: E731
__all__.append("_load_dotenv_once")
