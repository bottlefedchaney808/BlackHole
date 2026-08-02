"""Pytest conftest for shared/tests.

Puts the repo root on sys.path so `shared.summary` and `shared.schemas`
import the same way they do for every other suite/tool in this repo — mirrors
the sibling `tests/conftest.py`.
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
