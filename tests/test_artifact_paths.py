"""test_artifact_paths.py

Tests for shared/artifact_paths.py portable path helpers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.artifact_paths import repo_root, to_rel, from_rel


pytestmark = pytest.mark.unit


class TestRepoRoot:
    """Test repo_root() derivation."""

    def test_repo_root_derived_from_file(self):
        """repo_root() should be derived from __file__, not cwd."""
        root = repo_root()
        assert root.is_absolute()
        assert root.name == "FinancialDevelopment"
        # The artifact_paths module lives at <repo>/shared/artifact_paths.py
        assert (root / "shared" / "artifact_paths.py").exists()

    def test_repo_root_is_stable(self):
        """repo_root() should be cached after first call."""
        root1 = repo_root()
        root2 = repo_root()
        assert root1 == root2


class TestToRel:
    """Test to_rel() conversion to repo-relative paths."""

    def test_roundtrip_under_repo(self, tmp_path):
        """Paths under repo root should become relative."""
        root = repo_root()
        # Create a test file under the repo
        test_file = root / "outputs" / "r1" / "chart.png"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        rel = to_rel(str(test_file))
        assert rel == "outputs/r1/chart.png"
        assert "/" in rel  # Should use forward slashes

    def test_windows_style_path_stripped(self, tmp_path):
        """Windows-style paths should have FinancialDevelopment segment stripped."""
        root = repo_root()
        # Simulate a Windows path where the drive letter and Users path are different
        # but it still points to the FinancialDevelopment repo
        # Example: C:\Users\bottl\FinancialDevelopment\outputs\r1\c.png
        test_file = root / "outputs" / "r1" / "chart.png"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()
        
        # Convert to Windows-style path format
        win_path = f"C:\\Users\\bottl\\FinancialDevelopment{str(test_file)[len(str(root)):]}"
        win_path = win_path.replace("/", "\\")
        
        rel = to_rel(win_path)
        # Should strip C:\Users\bottl\FinancialDevelopment and keep repo-relative part
        assert rel == "outputs/r1/chart.png"

    def test_outside_root_unchanged(self, tmp_path):
        """Paths outside repo root should be returned unchanged."""
        outside = "/elsewhere/x.png"
        assert to_rel(outside) == outside

        # Also test a path that looks absolute but is outside
        assert to_rel("/tmp/test.png") == "/tmp/test.png"

    def test_backslash_normalized(self):
        """Backslashes should be normalized to forward slashes."""
        root = repo_root()
        test_file = root / "outputs" / "r1" / "chart.png"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.touch()

        # Use backslash in input
        backslash_path = str(test_file).replace("/", "\\")
        rel = to_rel(backslash_path)
        assert "/" in rel
        assert "\\" not in rel


class TestFromRel:
    """Test from_rel() resolution to absolute paths."""

    def test_from_rel_reconstructs_absolute(self):
        """from_rel should join with repo root."""
        rel = "outputs/r1/chart.png"
        abs_path = from_rel(rel)
        assert abs_path.is_absolute()
        expected = repo_root() / "outputs/r1/chart.png"
        assert abs_path == expected

    def test_from_rel_handles_forward_slashes(self):
        """from_rel should work with forward slashes."""
        rel = "outputs/r1/chart.png"
        abs_path = from_rel(rel)
        assert abs_path.is_absolute()

    def test_from_rel_normalizes_separators(self):
        """from_rel should normalize backslashes to forward slashes."""
        rel = "outputs\\r1\\chart.png"
        abs_path = from_rel(rel)
        assert abs_path.is_absolute()
        # Path should use forward slashes internally
        assert "r1" in str(abs_path)


class TestIntegration:
    """Integration tests for roundtrip scenarios."""

    def test_roundtrip_consistency(self, tmp_path):
        """to_rel(from_rel(x)) should equal x for repo-relative paths."""
        rel = "outputs/r1/chart.png"
        abs_path = from_rel(rel)
        rel_back = to_rel(str(abs_path))
        assert rel_back == rel
