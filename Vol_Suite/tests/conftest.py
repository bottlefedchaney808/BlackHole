"""
Pytest conftest for Vol_Suite tests.

Adds the project root (/home/bottl/Financial_Development) to sys.path so that
the shared/ package (shared.thetadata, etc.) is importable from tests that
import modules re-exporting from it (e.g. thetadata_client).

This also covers any other sibling packages (Options_Suite, VaR_Tools, etc.)
that may be referenced via relative-import stubs.

Also adds Vol_Suite/ itself, since its own tests import its modules as flat
top-level names (e.g. `import whale_scanner`). Vol_Suite/pytest.ini already
does this via `pythonpath = .`, but that config only takes effect when
pytest's rootdir search lands on it -- true for a standalone `pytest
Vol_Suite/tests/` invocation, but NOT when running from the repo root
alongside other suites' tests/, where the root pyproject.toml governs the
session instead and its own `pythonpath = "."` resolves relative to the
repo root, not here. Without this, Vol_Suite's tests silently fail to
collect under the repo-root-wide `pytest` command CLAUDE.md documents as
the primary way to run tests.
"""

import importlib.util
import sys
from pathlib import Path

# The project root is two levels up from tests/ (tests/ -> Vol_Suite/ -> Financial_Development/)
_project_root = Path(__file__).resolve().parent.parent.parent  # Financial_Development/
_vol_suite_root = Path(__file__).resolve().parent.parent  # Vol_Suite/
for _p in (_project_root, _vol_suite_root):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Options_Suite/ also has its own, unrelated expiry_selector.py -- and its
# tests/conftest.py puts Options_Suite/ on sys.path too. When both suites'
# tests are collected in the same process (the repo-root-wide `pytest` run),
# whichever suite's conftest happens to run first wins the bare
# `import expiry_selector` race for the rest of the session, silently
# breaking the other suite's dealer_positioning.py/variance_swap_live.py/
# variance_swap_screener.py/vrp_term_structure.py (all of which read
# expiry_selector.DEFAULT_A, an attribute Options_Suite's file doesn't have).
# Force Vol_Suite's own copy into sys.modules now, before any Vol_Suite test
# module gets a chance to import the wrong one.
_spec = importlib.util.spec_from_file_location(
    "expiry_selector", _vol_suite_root / "expiry_selector.py")
_expiry_selector = importlib.util.module_from_spec(_spec)
sys.modules["expiry_selector"] = _expiry_selector
_spec.loader.exec_module(_expiry_selector)