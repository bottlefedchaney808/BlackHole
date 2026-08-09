"""Guards the whole-repo `pytest` invocation CLAUDE.md documents as the
primary way to run tests (`pytest` with no args, using pyproject.toml's
testpaths) against silent collection breakage.

This is a real regression class, not a hypothetical: running each suite's
tests/ standalone (e.g. `pytest Vol_Suite/tests/`) can pass while the
combined root-level run fails collection entirely for that same suite,
because sys.path setup and package identity only get exercised together
here. See docs/PROJECT_AUDIT_AND_SPEC.md's test-collection-gap finding for
the two root causes this was written to catch:
  - a suite's tests/conftest.py adding the repo root to sys.path but not
    the suite's own root, so the suite's flat-module imports 404 only when
    a *different* pytest config (the root pyproject.toml, not the suite's
    own pytest.ini) ends up governing the session;
  - two suites' tests/ directories both lacking a suite-root __init__.py,
    so pytest's package-root walk gives them the same bare dotted name
    ("tests") and only whichever one Python's import system registers
    first in sys.modules is reachable.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.unit
def test_root_pytest_invocation_collects_every_suite_without_error():
    """`pytest --collect-only` from the repo root, using pyproject.toml's
    own testpaths, must not raise a single collection error. A collection
    error here means some suite's tests are silently not running at all
    under the exact command CLAUDE.md tells a human/agent to use."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "errors during collection" not in result.stdout, result.stdout
    assert result.returncode in (0, 1), (  # 1 == deselected-only, still a clean collect
        f"unexpected exit code {result.returncode}\n{result.stdout}\n{result.stderr}"
    )
