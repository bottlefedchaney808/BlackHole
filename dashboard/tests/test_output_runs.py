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


import json
import os

from dashboard.output_runs import (
    claim_files_for_suite, label_from_marker, discover_rundir_runs,
)


# ---------------------------------------------------------------------------
# claim_files_for_suite -- ownership filter for a shared unified-run directory
# ---------------------------------------------------------------------------

def test_claim_files_for_suite_options_claims_its_own_marker_and_context():
    names = ["options_result.json", "suite_context_options.json",
             "var_result.json", "suite_context.json", "SPY_gamma_records.csv"]
    claimed = claim_files_for_suite(names, "options")
    assert set(claimed) == {"options_result.json", "suite_context_options.json"}


def test_claim_files_for_suite_var_claims_its_own_marker_and_context():
    names = ["options_result.json", "var_result.json", "suite_context_var.json"]
    assert set(claim_files_for_suite(names, "var")) == {"var_result.json", "suite_context_var.json"}


def test_claim_files_for_suite_sentiment_claims_its_own_marker_and_context():
    names = ["sentiment_result.json", "suite_context_sentiment.json", "var_result.json"]
    assert set(claim_files_for_suite(names, "sentiment")) == \
        {"sentiment_result.json", "suite_context_sentiment.json"}


def test_claim_files_for_suite_vol_claims_everything_not_claimed_by_others():
    names = ["options_result.json", "var_result.json", "sentiment_result.json",
             "suite_context.json", "suite_context_options.json",
             "SPY_gamma_records_20260716.csv", "volatility_suite_20260716.pdf"]
    claimed = claim_files_for_suite(names, "vol")
    assert set(claimed) == {"SPY_gamma_records_20260716.csv", "volatility_suite_20260716.pdf"}


def test_claim_files_for_suite_excludes_bare_suite_context_everywhere():
    names = ["suite_context.json"]
    for suite in ("options", "var", "sentiment", "vol"):
        assert claim_files_for_suite(names, suite) == []


# ---------------------------------------------------------------------------
# label_from_marker
# ---------------------------------------------------------------------------

def test_label_from_marker_options(tmp_path):
    p = tmp_path / "options_result.json"
    p.write_text(json.dumps({"suite": "options", "status": "ok",
                              "ticker": "AAPL", "method": "CRR"}))
    assert label_from_marker(str(p), "options") == "AAPL · CRR · ok"


def test_label_from_marker_var(tmp_path):
    p = tmp_path / "var_result.json"
    p.write_text(json.dumps({"suite": "var", "status": "ok", "module": "corr_sim"}))
    assert label_from_marker(str(p), "var") == "corr_sim · ok"


def test_label_from_marker_returns_none_on_missing_file():
    assert label_from_marker("/does/not/exist.json", "options") is None


def test_label_from_marker_returns_none_on_malformed_json(tmp_path):
    p = tmp_path / "options_result.json"
    p.write_text("not json{{{")
    assert label_from_marker(str(p), "options") is None


# ---------------------------------------------------------------------------
# discover_rundir_runs
# ---------------------------------------------------------------------------

def test_discover_rundir_runs_finds_options_marker_and_labels_it(tmp_path):
    run_dir = tmp_path / "20260729T055306Z"
    run_dir.mkdir()
    (run_dir / "options_result.json").write_text(json.dumps(
        {"suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR"}))
    (run_dir / "suite_context_options.json").write_text("{}")
    (run_dir / "var_result.json").write_text(json.dumps({"suite": "var", "status": "ok"}))

    runs = discover_rundir_runs(str(tmp_path / "*"), "options", "orch")
    assert len(runs) == 1
    run = runs[0]
    assert run.suite == "options"
    assert run.run_id == "orch:20260729T055306Z"
    assert run.label == "AAPL · CRR · ok"
    claimed_names = {os.path.basename(f.abs_path) for f in run.files}
    assert claimed_names == {"options_result.json", "suite_context_options.json"}
    # var_result.json belongs to a different suite -- must not leak in
    assert "var_result.json" not in claimed_names


def test_discover_rundir_runs_skips_directories_with_nothing_for_this_suite(tmp_path):
    run_dir = tmp_path / "20260729T055306Z"
    run_dir.mkdir()
    (run_dir / "var_result.json").write_text(json.dumps({"suite": "var", "status": "ok"}))

    runs = discover_rundir_runs(str(tmp_path / "*"), "options", "orch")
    assert runs == []


def test_discover_rundir_runs_falls_back_to_dirname_label_without_a_marker(tmp_path):
    run_dir = tmp_path / "20260716_142405"
    run_dir.mkdir()
    (run_dir / "SPY_gamma_records_20260716.csv").write_text("a,b\n1,2\n")

    runs = discover_rundir_runs(str(tmp_path / "*"), "vol", "vsout")
    assert len(runs) == 1
    assert runs[0].run_id == "vsout:20260716_142405"
    assert "20260716" in runs[0].label


def test_discover_rundir_runs_sorts_newest_first(tmp_path):
    older = tmp_path / "20260101T000000Z"
    newer = tmp_path / "20260201T000000Z"
    older.mkdir()
    newer.mkdir()
    (older / "options_result.json").write_text(json.dumps({"suite": "options", "status": "ok",
                                                              "ticker": "A", "method": "CRR"}))
    (newer / "options_result.json").write_text(json.dumps({"suite": "options", "status": "ok",
                                                              "ticker": "B", "method": "CRR"}))
    import os as _os
    old_time = _os.path.getmtime(str(older / "options_result.json")) - 1000
    _os.utime(str(older / "options_result.json"), (old_time, old_time))

    runs = discover_rundir_runs(str(tmp_path / "*"), "options", "orch")
    assert [r.run_id for r in runs] == ["orch:20260201T000000Z", "orch:20260101T000000Z"]


from dashboard.output_runs import discover_loose_bucket


def test_vol_standalone_rundir_is_discovered_via_discover_rundir_runs(tmp_path):
    # Vol_Suite/outputs/<ts>/ already has one directory per run -- no new
    # function needed, discover_rundir_runs (Task 2) handles it directly.
    run_dir = tmp_path / "20260716_142405"
    run_dir.mkdir()
    (run_dir / "SPY_gamma_records_20260716_142448.csv").write_text("a,b\n1,2\n")
    (run_dir / "volatility_suite_20260716_142527.pdf").write_bytes(b"%PDF-1.4")

    runs = discover_rundir_runs(str(tmp_path / "*"), "vol", "vsout")
    assert len(runs) == 1
    assert {os.path.basename(f.abs_path) for f in runs[0].files} == \
        {"SPY_gamma_records_20260716_142448.csv", "volatility_suite_20260716_142527.pdf"}


def test_discover_loose_bucket_wraps_a_flat_directory_as_one_run(tmp_path):
    (tmp_path / "correlation_matrix_20260801_171619.csv").write_text("a,b\n1,2\n")
    (tmp_path / "INTC_gamma_records_20260801_171652.csv").write_text("a,b\n1,2\n")

    run = discover_loose_bucket(str(tmp_path), "vol", "loose:vs_output", "Legacy vs_output (ungrouped)")
    assert run is not None
    assert run.run_id == "loose:vs_output"
    assert run.label == "Legacy vs_output (ungrouped)"
    assert len(run.files) == 2


def test_discover_loose_bucket_returns_none_for_empty_or_missing_directory(tmp_path):
    assert discover_loose_bucket(str(tmp_path / "does_not_exist"), "vol",
                                  "loose:vs_output", "x") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert discover_loose_bucket(str(empty), "vol", "loose:vs_output", "x") is None


def test_discover_loose_bucket_ignores_subdirectories(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "b.csv").write_text("y")

    run = discover_loose_bucket(str(tmp_path), "vol", "loose:vs_output", "x")
    assert len(run.files) == 1


from dashboard.output_runs import discover_clustered_runs


def test_discover_clustered_runs_groups_csv_and_pdf_pair(tmp_path):
    csv_path = tmp_path / "comparison_20260728_093751.csv"
    pdf_path = tmp_path / "comparison_20260728_093753.pdf"  # 2s later
    csv_path.write_text("a,b\n1,2\n")
    pdf_path.write_bytes(b"%PDF-1.4")

    runs = discover_clustered_runs([str(csv_path), str(pdf_path)], "options", "cmp")
    assert len(runs) == 1
    run = runs[0]
    assert run.run_id == "cmp:20260728_093751"
    assert len(run.files) == 2
    assert run.label == "2026-07-28 09:37:51"


def test_discover_clustered_runs_separates_files_outside_the_window(tmp_path):
    a = tmp_path / "comparison_20260728_093751.csv"
    b = tmp_path / "comparison_20260728_120000.csv"
    a.write_text("x")
    b.write_text("x")

    runs = discover_clustered_runs([str(a), str(b)], "options", "cmp")
    assert len(runs) == 2


def test_discover_clustered_runs_sorts_newest_first(tmp_path):
    a = tmp_path / "comparison_20260101_000000.csv"
    b = tmp_path / "comparison_20260201_000000.csv"
    a.write_text("x")
    b.write_text("x")

    runs = discover_clustered_runs([str(a), str(b)], "options", "cmp")
    assert [r.run_id for r in runs] == ["cmp:20260201_000000", "cmp:20260101_000000"]


def test_discover_clustered_runs_skips_missing_files(tmp_path):
    a = tmp_path / "comparison_20260728_093751.csv"
    a.write_text("x")
    missing = str(tmp_path / "comparison_20260728_093755.pdf")  # never created

    runs = discover_clustered_runs([str(a), missing], "options", "cmp")
    assert len(runs) == 1
    assert len(runs[0].files) == 1


from dashboard.output_runs import discover_date_bucket_runs


def test_discover_date_bucket_runs_one_run_per_date_folder(tmp_path):
    day1 = tmp_path / "20260727"
    day2 = tmp_path / "20260728"
    day1.mkdir()
    day2.mkdir()
    (day1 / "pack-a.json").write_text("{}")
    (day2 / "pack-b.json").write_text("{}")
    (day2 / "pack-c.json").write_text("{}")

    runs = discover_date_bucket_runs(str(tmp_path / "*"), "sentiment", "pack")
    assert len(runs) == 2
    by_id = {r.run_id: r for r in runs}
    assert "pack:20260728" in by_id
    assert len(by_id["pack:20260728"].files) == 2
    assert "2026-07-28" in by_id["pack:20260728"].label


def test_discover_date_bucket_runs_ignores_manifest_file(tmp_path):
    day = tmp_path / "20260728"
    day.mkdir()
    (day / "pack-a.json").write_text("{}")
    # latest_manifest.json lives one level up (a sibling of the date
    # folders), not inside one -- glob on tmp_path/* wouldn't match it, but
    # confirm a same-named file INSIDE a date folder still just shows up as
    # a normal file (no special-casing needed/wanted here).
    (day / "latest_manifest.json").write_text("{}")

    runs = discover_date_bucket_runs(str(tmp_path / "*"), "sentiment", "pack")
    assert len(runs[0].files) == 2


def test_discover_date_bucket_runs_skips_empty_date_folders(tmp_path):
    empty = tmp_path / "20260728"
    empty.mkdir()
    runs = discover_date_bucket_runs(str(tmp_path / "*"), "sentiment", "pack")
    assert runs == []


def test_discover_date_bucket_runs_sorts_newest_first(tmp_path):
    for d in ("20260101", "20260201"):
        folder = tmp_path / d
        folder.mkdir()
        (folder / "pack.json").write_text("{}")
    runs = discover_date_bucket_runs(str(tmp_path / "*"), "sentiment", "pack")
    assert [r.run_id for r in runs] == ["pack:20260201", "pack:20260101"]


from dashboard.output_runs import (
    discover_runs, get_run, read_json_view, read_csv_table, build_file_view,
    SUITE_LABELS,
)


def test_suite_labels_covers_all_five_keys():
    assert set(SUITE_LABELS) == {'options', 'vol', 'var', 'sentiment', 'swaps', 'unified'}


def test_discover_runs_combines_sources_and_detects_unified_siblings(tmp_path, monkeypatch):
    orch = tmp_path / "orchestrator_output"
    orch.mkdir()
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir()
    (run_dir / "options_result.json").write_text(json.dumps(
        {"suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR"}))
    (run_dir / "var_result.json").write_text(json.dumps({"suite": "var", "status": "ok"}))

    import dashboard.output_runs as output_runs_mod
    monkeypatch.setattr(output_runs_mod, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs_mod, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"),
        'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })

    runs = discover_runs('options')
    assert len(runs) == 1
    assert runs[0].run_id == "orch:20260729T055306Z"
    assert runs[0].sibling_suites == ['var']


def test_get_run_finds_a_run_by_id(tmp_path, monkeypatch):
    orch = tmp_path / "orchestrator_output"
    orch.mkdir()
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir()
    (run_dir / "options_result.json").write_text(json.dumps(
        {"suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR"}))

    import dashboard.output_runs as output_runs_mod
    monkeypatch.setattr(output_runs_mod, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs_mod, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })

    run = get_run('options', 'orch:20260729T055306Z')
    assert run is not None
    assert run.run_id == 'orch:20260729T055306Z'
    assert get_run('options', 'orch:nonexistent') is None


def test_read_json_view_pairs_kind(tmp_path):
    p = tmp_path / "options_result.json"
    p.write_text(json.dumps({"ticker": "AAPL", "status": "ok"}))
    view = read_json_view(str(p))
    assert view['kind'] == 'pairs'
    assert ('ticker', 'AAPL') in view['pairs']


def test_read_csv_table_table_kind(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("a,b\n1,2\n3,4\n")
    view = read_csv_table(str(p))
    assert view['kind'] == 'table'
    assert view['headers'] == ['a', 'b']
    assert view['rows'] == [['1', '2'], ['3', '4']]


def test_build_file_view_dispatches_by_kind(tmp_path):
    json_file = RunFile(abs_path=str(tmp_path / "a.json"), rel_path="a.json",
                        kind="json", size_bytes=2, modified=0.0)
    (tmp_path / "a.json").write_text("{}")
    csv_file = RunFile(abs_path=str(tmp_path / "b.csv"), rel_path="b.csv",
                       kind="csv", size_bytes=2, modified=0.0)
    (tmp_path / "b.csv").write_text("a\n1\n")
    png_file = RunFile(abs_path=str(tmp_path / "c.png"), rel_path="c.png",
                       kind="png", size_bytes=2, modified=0.0)
    pdf_file = RunFile(abs_path=str(tmp_path / "d.pdf"), rel_path="d.pdf",
                       kind="pdf", size_bytes=2, modified=0.0)
    other_file = RunFile(abs_path=str(tmp_path / "e.txt"), rel_path="e.txt",
                         kind="other", size_bytes=2, modified=0.0)

    assert build_file_view(json_file)['kind'] == 'pairs'
    assert build_file_view(csv_file)['kind'] == 'table'
    assert build_file_view(png_file) == {'kind': 'image'}
    assert build_file_view(pdf_file) == {'kind': 'pdf'}
    assert build_file_view(other_file) == {'kind': 'download'}
