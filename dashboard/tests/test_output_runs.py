"""test_output_runs.py

Covers the dashboard Output tab redesign
(docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md).

Run via `pytest dashboard/tests/` -- dashboard/tests/ is not currently in
root pyproject.toml's testpaths (a separate, pre-existing gap, out of scope
here), matching how the rest of dashboard/tests/ already has to be run.
"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.output_runs import (
    RunFile, RunInfo, classify_file, extract_timestamp, cluster_by_timestamp,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# classify_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected", [
    ("options_result.json", "json"),
    ("comparison_20260728_093751.csv", "csv"),
    ("SPY_gamma_records_20260716_142448.csv", "csv"),
    ("correlation_heatmap_20260716_142423.png", "png"),
    ("comparison_20260728_093751.pdf", "pdf"),
    ("SOMETHING.PDF", "pdf"),
    ("run_notes.txt", "other"),
    ("no_extension_at_all", "other"),
])
def test_classify_file_by_extension(filename, expected):
    assert classify_file(f"/some/dir/{filename}") == expected


# ---------------------------------------------------------------------------
# extract_timestamp
# ---------------------------------------------------------------------------

def test_extract_timestamp_parses_embedded_token():
    ts = extract_timestamp("comparison_20260728_093751.csv")
    assert ts == datetime(2026, 7, 28, 9, 37, 51)


def test_extract_timestamp_returns_none_when_absent():
    assert extract_timestamp("screener.csv") is None


def test_extract_timestamp_returns_none_on_malformed_token():
    # 8 digits + underscore + 6 digits, but not a real date/time
    assert extract_timestamp("report_99999999_999999.pdf") is None


# ---------------------------------------------------------------------------
# cluster_by_timestamp
# ---------------------------------------------------------------------------

def test_single_file_is_its_own_cluster():
    paths = ["/a/comparison_20260728_093751.csv"]
    assert cluster_by_timestamp(paths) == [paths]


def test_two_files_within_window_cluster_together():
    paths = [
        "/a/comparison_20260728_093751.csv",
        "/a/comparison_20260728_093755.pdf",  # 4s later
    ]
    clusters = cluster_by_timestamp(paths, window_seconds=10)
    assert len(clusters) == 1
    assert set(clusters[0]) == set(paths)


def test_two_files_outside_window_are_separate_clusters():
    paths = [
        "/a/comparison_20260728_093751.csv",
        "/a/comparison_20260728_094500.pdf",  # 7m9s later
    ]
    clusters = cluster_by_timestamp(paths, window_seconds=10)
    assert len(clusters) == 2


def test_transitive_chain_clusters_despite_endpoints_exceeding_window():
    # A-B 5s apart, B-C 5s apart, A-C 10s apart -- still one cluster because
    # clustering chains through consecutive neighbors, not all-pairs.
    paths = [
        "/a/x_20260728_093751.csv",
        "/a/x_20260728_093756.csv",
        "/a/x_20260728_093801.csv",
    ]
    clusters = cluster_by_timestamp(paths, window_seconds=5)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3


def test_undated_files_become_singleton_clusters():
    paths = ["/a/comparison_20260728_093751.csv", "/a/notes.txt"]
    clusters = cluster_by_timestamp(paths, window_seconds=10)
    assert len(clusters) == 2
    assert ["/a/notes.txt"] in clusters


# ---------------------------------------------------------------------------
# RunFile / RunInfo -- just confirm the dataclasses hold what they're told to
# ---------------------------------------------------------------------------

def test_run_file_holds_fields():
    f = RunFile(abs_path="/a/b.csv", rel_path="Vol_Suite/b.csv", kind="csv",
                size_bytes=123, modified=1700000000.0)
    assert f.rel_path == "Vol_Suite/b.csv"
    assert f.kind == "csv"


def test_run_info_holds_fields():
    f = RunFile(abs_path="/a/b.csv", rel_path="Vol_Suite/b.csv", kind="csv",
                size_bytes=123, modified=1700000000.0)
    r = RunInfo(suite="vol", run_id="vsout:20260716_142405", label="2026-07-16 14:24",
                timestamp=1700000000.0, files=[f], sibling_suites=[])
    assert r.suite == "vol"
    assert r.files[0] is f
    assert r.sibling_suites == []
