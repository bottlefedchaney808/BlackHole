"""
Pytest conftest for Vol_Suite tests.

Adds the project root (/home/bottl/Financial_Development) to sys.path so that
the shared/ package (shared.thetadata, etc.) is importable from tests that
import modules re-exporting from it (e.g. thetadata_client).

This also covers any other sibling packages (Options_Suite, VaR_Tools, etc.)
that may be referenced via relative-import stubs.
"""

import sys
from pathlib import Path

# The project root is two levels up from tests/ (tests/ -> Vol_Suite/ -> Financial_Development/)
_project_root = Path(__file__).resolve().parent.parent.parent  # Financial_Development/
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))