"""
Pytest conftest for VaR_Tools_Simulations tests.

Adds VaR_Tools_Simulations/ itself to sys.path so that flat imports like
`from var_engine.copulas import ...` resolve.

Also gives VaR_Tools_Simulations/ its own __init__.py (added alongside this
file) so its tests/ package gets a unique dotted module name
(`VaR_Tools_Simulations.tests.test_X`) rather than the bare `tests.test_X`
every suite without a suite-root __init__.py collapses to. Without that,
this suite's tests/ collided with sentiment-scanner/tests/ (the only other
suite in the same situation) under a combined root-level `pytest` run:
whichever one Python's import system registered first under
sys.modules['tests'] made the other's same-named submodules unreachable,
raising ModuleNotFoundError for every test file in the suite that lost the
race -- despite `pytest VaR_Tools_Simulations/tests/` on its own working
fine. Mirrors the pattern Options_Suite/tests/conftest.py already uses.
"""

import sys
from pathlib import Path

_var_tools_root = Path(__file__).resolve().parent.parent  # VaR_Tools_Simulations/
_project_root = _var_tools_root.parent  # Financial_Development/

for _p in (_var_tools_root, _project_root):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
