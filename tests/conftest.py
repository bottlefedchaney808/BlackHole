"""Pytest conftest for the root-level orchestrator tests.

Puts the repo root on sys.path so `shared.suite_validation` and `orchestrator`
import the same way they do when orchestrator.py runs as a script.
"""

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
