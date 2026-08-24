"""
Pytest configuration and shared test infrastructure for Options_Suite.

Handles sys.path so that both flat-name imports and package imports
within Options_Suite stay importable while avoiding conflicts with other
projects in the same parent (e.g. sentiment-scanner's config.py).
Provides a FakeThetaDataController for network-free testing.
"""

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path: add the Options_Suite root so flat-name imports like
# "from american_binomial import crr_american_price" resolve correctly.
# Keep the parent out to avoid shadow conflicts (sentiment-scanner/config.py).
# ---------------------------------------------------------------------------
_OPTIONS_SUITE = str(Path(__file__).resolve().parent.parent)
_PROJECT_ROOT = str(
    Path(__file__).resolve().parent.parent.parent
)  # Financial_Development

if _OPTIONS_SUITE not in sys.path:
    sys.path.insert(0, _OPTIONS_SUITE)
# _PROJECT_ROOT is needed for package-style imports like "import Options_Suite.MC",
# but must be appended (never inserted at 0) so it can never shadow Options_Suite's
# own flat modules (e.g. a project-root config.py shadowing Options_Suite/config.py).
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Enforce CPU-only numpy path so tests don't try to import cupy (which is
# unlikely to be available in a test venv).
# ---------------------------------------------------------------------------
os.environ.setdefault("OPTIONS_SUITE_FORCE_CPU", "1")


# ---------------------------------------------------------------------------
# FakeThetaDataController — canned data stand-in
# ---------------------------------------------------------------------------
class FakeThetaDataController:
    """Drop-in stub for shared.thetadata.ThetaDataController that returns
    pre-configured canned data without any network calls.

    Usage in a test::

        import shared.thetadata as td_mod
        monkeypatch.setattr(td_mod, "ThetaDataController", FakeThetaDataController)
    """

    def __init__(self, spot=495.0, expirations=None, bulk_greeks=None):
        self.spot = spot
        self._expirations = expirations or ["20261016"]
        self._bulk_greeks = bulk_greeks or []
        self._closed = False

    def fetch_spot_price(self, ticker):
        return self.spot

    def list_expirations(self, ticker):
        return self._expirations

    def option_bulk_greeks(self, ticker, expiry):
        return self._bulk_greeks

    def close(self):
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ---------------------------------------------------------------------------
# Auto-use fixtures
# ---------------------------------------------------------------------------

import pytest


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Prevent real time.sleep in retry loops during tests."""
    monkeypatch.setattr("time.sleep", lambda _: None)


@pytest.fixture(autouse=True)
def _suppress_cupy(monkeypatch):
    """Prevent MC.py's module-level cupy detection from trying to import cupy
    at test collection time.  The env-var set above already blocks it, but
    this guard is belt-and-suspenders."""
    import importlib

    # Force-reload either import style so the GPU_ACTIVE check re-runs with
    # FORCE_CPU set before tests import package-qualified modules.
    for name in ("MC", "Options_Suite.MC"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
