"""Shared base for all options scanners — Vol_Suite import bridge + common helpers.

Each scanner in this directory pulls computation-heavy logic from the
Vol_Suite (dealer_positioning, vol_surface_reference, garch_analysis,
correlation_engine, variance_swap_screener, options_chain_scanner) using
the ThetaDataController from Vol_Suite's thetadata_client (which reads
credentials from .env / env vars — no hardcoded secrets).

Usage in a scanner module::

    from scanner.options_scanner_base import get_td, VolSuiteImporter
    vsi = VolSuiteImporter()
    dealer = vsi.dealer_positioning
    td = get_td()
"""

import sys
import os
from typing import Optional

# ---- Vol_Suite path bootstrap ----
_VOL_SUITE_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "Vol_Suite")
)
if _VOL_SUITE_ROOT not in sys.path:
    sys.path.insert(0, _VOL_SUITE_ROOT)

# ---- ThetaDataController cache (one client per session) ----
_td_instance = None


def get_td() -> "ThetaDataController":
    """Return a cached ThetaDataController from Vol_Suite's thetadata_client.

    The client is created once per session and reused.  Caller should NOT
    close it — it lives for the duration of the scan loop.
    """
    global _td_instance
    if _td_instance is None:
        # pylint: disable=import-outside-toplevel
        from thetadata_client import ThetaDataController

        _td_instance = ThetaDataController()
    return _td_instance


def close_td() -> None:
    """Explicitly release the cached ThetaDataController."""
    global _td_instance
    if _td_instance is not None:
        try:
            _td_instance.close()
        except Exception:
            pass
        _td_instance = None


# ---- Convenience re-exports ----
def strike_to_theta(k: float) -> int:
    return int(round(k * 1000))


def strike_from_theta(k: int) -> float:
    return k / 1000.0


# ---- Vol_Suite module lazy-loader ----
class VolSuiteImporter:
    """Lazy accessor for Vol_Suite modules.

    Each attribute access imports the corresponding module on first touch
    and caches it, so you can write::

        vsi = VolSuiteImporter()
        result = vsi.dealer_positioning.compute_dealer_positioning("SPY")
    """

    # pylint: disable=invalid-name
    @property
    def dealer_positioning(self):
        import dealer_positioning  # type: ignore[import-untyped]
        return dealer_positioning

    @property
    def vol_surface_reference(self):
        import vol_surface_reference  # type: ignore[import-untyped]
        return vol_surface_reference

    @property
    def garch_analysis(self):
        import garch_analysis  # type: ignore[import-untyped]
        return garch_analysis

    @property
    def correlation_engine(self):
        import correlation_engine  # type: ignore[import-untyped]
        return correlation_engine

    @property
    def variance_swap_screener(self):
        import variance_swap_screener  # type: ignore[import-untyped]
        return variance_swap_screener

    @property
    def options_chain_scanner(self):
        import options_chain_scanner  # type: ignore[import-untyped]
        return options_chain_scanner

    @property
    def expiry_selector(self):
        import expiry_selector  # type: ignore[import-untyped]
        return expiry_selector

    @property
    def thetadata_client(self):
        import thetadata_client  # type: ignore[import-untyped]
        return thetadata_client
