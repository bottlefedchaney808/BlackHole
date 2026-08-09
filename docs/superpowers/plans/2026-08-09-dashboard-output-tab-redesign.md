# Dashboard Output Tab Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's "newest single file, CSV/JSON only" Output tab with a real per-suite, per-run browser that shows every asset a run produced (JSON/CSV/PNG/PDF/other), works for dashboard-triggered and standalone runs alike, and gets a dark/bold visual reskin.

**Architecture:** A new pure-ish module `dashboard/output_runs.py` discovers "runs" by scanning the filesystem fresh on every request (no DB changes) -- each suite has its own discovery strategy (shared `orchestrator_output/<run_id>/` directories with suite-ownership filtering, Vol_Suite's own timestamped subdirectories, Options_Suite's flat files clustered by embedded timestamp, sentiment's date-bucketed exports) but all funnel into the same `RunInfo`/`RunFile` shapes. `dashboard/app.py` wires this into an enhanced `/suites/{suite}` route (run-picker dropdown, live-run banner, unified 5th tab) plus a new path-validated `/suites/{suite}/asset` route for serving images/PDFs. `dashboard/templates/suite.html` is rebuilt to render every file in the selected run; `dashboard/templates/base.html`'s shared CSS gets a dark/bold reskin with gradient suite-tab pills, which lifts every other page (Overview, Quant Console, Tools) for free since they reuse the same classes.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, pytest, no new dependencies.

## Global Constraints

- No database schema changes -- `orchestrator_runs` is not touched. See the spec's "Architecture" section for why.
- Tools/registry output (Direction's tools, backtest tool, options-strategy tool) is out of scope -- they keep their existing `/tools` page.
- The run-picker dropdown shows the last 10 runs per suite, no pagination.
- "Everything inline, no CDN, minimal vanilla JS" -- this dashboard runs offline on localhost (see `base.html`'s own header comment). No JS frameworks, no external assets. Plain `<form>` submission over `onchange`, matching the existing convention.
- New route `/suites/{suite}/asset` must never trust its `rel_path` query param directly against the filesystem -- it must only ever serve a path that server-side discovery itself already enumerated for the named `run_id`.
- Full spec: `docs/superpowers/specs/2026-08-09-dashboard-output-tab-redesign-design.md`.

---

## Task 1: `output_runs.py` -- data model, file classification, timestamp clustering

**Files:**
- Create: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Produces: `RunFile` (dataclass: `abs_path: str`, `rel_path: str`, `kind: str`, `size_bytes: int`, `modified: float`), `RunInfo` (dataclass: `suite: str`, `run_id: str`, `label: str`, `timestamp: float`, `files: List[RunFile]`, `sibling_suites: List[str]`), `classify_file(path: str) -> str` (returns `'json'|'csv'|'png'|'pdf'|'other'`), `extract_timestamp(filename: str) -> Optional[datetime]`, `cluster_by_timestamp(paths: List[str], window_seconds: int = 10) -> List[List[str]]`. Task 2+ import and use all of these.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/tests/test_output_runs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.output_runs'`

- [ ] **Step 3: Write the implementation**

Create `dashboard/output_runs.py`:

```python
"""dashboard/output_runs.py -- filesystem-based run discovery for the Output
tab (docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md).

No database involved: orchestrator_runs does not durably carry output_dir
for suite-kind runs (see dashboard/app.py::_execute_run and migration
004_add_quant_alerts.sql's own comment on this), and a real chunk of what
needs to show up here -- Options_Suite's interactively-generated
comparison_*.csv/.pdf, standalone Vol_Suite/outputs/<ts>/ runs -- was never
triggered through the dashboard and has no DB row at all. Every suite's own
result JSON is already self-describing and schema-validated
(shared/schemas.py), so reading it directly gives the same rich labels a DB
join would, for every run regardless of how it was launched.

This module is intentionally filesystem-path-agnostic where it can be: the
pure logic (classify_file, cluster_by_timestamp) takes plain paths/strings
and has no repo-layout knowledge at all. Later tasks add discovery functions
that DO know real repo layout (ORCH_OUTPUT, SUITE_ROOTS below), but even
those accept the root paths as parameters with real-repo defaults, so tests
can point them at a tmp_path fixture instead.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))   # dashboard/
ROOT = os.path.dirname(_MODULE_DIR)                          # repo root
ORCH_OUTPUT = os.path.join(ROOT, 'orchestrator_output')
SUITE_ROOTS = {
    'options': os.path.join(ROOT, 'Options_Suite'),
    'vol': os.path.join(ROOT, 'Vol_Suite'),
    'var': os.path.join(ROOT, 'VaR_Tools_Simulations'),
    'sentiment': os.path.join(ROOT, 'sentiment-scanner'),
}


@dataclass(frozen=True)
class RunFile:
    abs_path: str
    rel_path: str      # relative to ROOT; used for display and as the asset-route key
    kind: str            # 'json' | 'csv' | 'png' | 'pdf' | 'other'
    size_bytes: int
    modified: float      # epoch seconds


@dataclass(frozen=True)
class RunInfo:
    suite: str
    run_id: str                              # e.g. "orch:20260729T055306Z", "cmp:20260728_093751"
    label: str
    timestamp: float
    files: List[RunFile] = field(default_factory=list)
    sibling_suites: List[str] = field(default_factory=list)


_KIND_BY_EXT = {
    '.json': 'json',
    '.csv': 'csv',
    '.png': 'png',
    '.pdf': 'pdf',
}


def classify_file(path: str) -> str:
    """Classifies a file purely by extension -- good enough for every kind
    this repo's suites actually produce (json/csv/png/pdf); anything else
    (txt, unknown) is 'other' and gets a plain download link."""
    ext = os.path.splitext(path)[1].lower()
    return _KIND_BY_EXT.get(ext, 'other')


_TIMESTAMP_RE = re.compile(r'(\d{8}_\d{6})')


def extract_timestamp(filename: str) -> Optional[datetime]:
    """Pulls the first YYYYMMDD_HHMMSS token out of a filename, e.g.
    'comparison_20260728_093751.csv' -> datetime(2026,7,28,9,37,51). Returns
    None if no such token is present, or if it doesn't parse as a real
    date/time (e.g. month 99)."""
    m = _TIMESTAMP_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')
    except ValueError:
        return None


def cluster_by_timestamp(paths: List[str], window_seconds: int = 10) -> List[List[str]]:
    """Groups file paths into runs by proximity of the timestamp embedded in
    each filename (via extract_timestamp). Two files land in the same
    cluster if, once all dated files are sorted by timestamp, they are
    consecutive and within `window_seconds` of each other -- this chains
    transitively, so A-B-C each 5s apart cluster together even though A and
    C are 10s apart.

    A file with no parseable timestamp becomes its own singleton cluster.
    Needed for Options_Suite's comparison_*.csv/.pdf pairs: each is saved by
    an independent datetime.now() call, so a CSV+PDF pair saved together via
    the 'both' choice can differ by a second or two -- exact-string matching
    on the timestamp is not reliable.
    """
    dated: List[tuple] = []
    undated: List[str] = []
    for p in paths:
        ts = extract_timestamp(os.path.basename(p))
        if ts is None:
            undated.append(p)
        else:
            dated.append((p, ts))
    dated.sort(key=lambda pair: pair[1])

    clusters: List[List[tuple]] = []
    for path, ts in dated:
        if clusters and (ts - clusters[-1][-1][1]).total_seconds() <= window_seconds:
            clusters[-1].append((path, ts))
        else:
            clusters.append([(path, ts)])

    result = [[p for p, _ in cluster] for cluster in clusters]
    result.extend([u] for u in undated)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add output_runs data model and timestamp clustering"
```

---

## Task 2: `output_runs.py` -- rundir discovery for shared `orchestrator_output/` directories

**Files:**
- Modify: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Consumes: `RunFile`, `RunInfo`, `classify_file` from Task 1.
- Produces: `SUITE_MARKER_FILES: Dict[str, Tuple[str, ...]]`, `claim_files_for_suite(filenames: List[str], suite: str) -> List[str]`, `label_from_marker(marker_path: str, suite: str) -> Optional[str]`, `discover_rundir_runs(root_glob: str, suite: str, run_id_prefix: str) -> List[RunInfo]`. Task 3/6 call `discover_rundir_runs` directly (it's suite-agnostic given the right marker-claim rule); Task 6's public `discover_runs` calls it with `orch_output`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_output_runs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'claim_files_for_suite'`

- [ ] **Step 3: Write the implementation**

Append to `dashboard/output_runs.py`:

```python
import glob
import json


# ---------------------------------------------------------------------------
# Shared orchestrator_output/<run_id>/ directories -- one directory can hold
# multiple suites' files at once (a unified run writes all four). Ownership
# of a file is inferred from naming convention, since there is no per-file
# suite tag.
# ---------------------------------------------------------------------------

#: Marker/context files a suite claims for itself out of a shared directory.
SUITE_MARKER_FILES = {
    'options': ('options_result.json', 'suite_context_options.json'),
    'var': ('var_result.json', 'suite_context_var.json'),
    'sentiment': ('sentiment_result.json', 'suite_context_sentiment.json'),
}

#: Filenames no suite claims as "its own result to review" -- cross-suite
#: plumbing, excluded from every suite's per-file list.
_UNCLAIMED_FILES = {'suite_context.json'}

#: The full set of filenames options/var/sentiment could ever claim, used by
#: vol's exclusion rule below.
_NON_VOL_MARKERS = {name for names in SUITE_MARKER_FILES.values() for name in names}


def claim_files_for_suite(filenames: List[str], suite: str) -> List[str]:
    """Given every filename in one shared run directory, returns the subset
    'suite' claims as its own. options/var/sentiment claim their own named
    marker+context files; vol claims everything NOT claimed by one of the
    other three and not in _UNCLAIMED_FILES (Vol_Suite writes its rich
    CSV/PNG/PDF assets straight into the shared directory via VS_OUTPUT_DIR,
    with no marker file of its own to key off)."""
    if suite in SUITE_MARKER_FILES:
        wanted = set(SUITE_MARKER_FILES[suite])
        return [f for f in filenames if f in wanted]
    if suite == 'vol':
        return [f for f in filenames if f not in _NON_VOL_MARKERS and f not in _UNCLAIMED_FILES]
    return []


def label_from_marker(marker_path: str, suite: str) -> Optional[str]:
    """Reads a suite's own result JSON and builds a human-readable label.
    Returns None if the file is missing/unreadable/malformed -- callers fall
    back to a directory-name-derived label in that case."""
    try:
        with open(marker_path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None

    status = data.get('status', '?')
    if suite == 'options':
        return f"{data.get('ticker', '?')} · {data.get('method', '?')} · {status}"
    if suite == 'var':
        return f"{data.get('module', '?')} · {status}"
    if suite == 'sentiment':
        return f"sentiment · {status}"
    return status


#: Only the newest this many candidate directories are ever inspected per
#: source, per suite -- keeps a page load cheap even as orchestrator_output/
#: grows into the hundreds of run directories. Comfortably covers "last 10
#: runs that have files for this suite" with margin for interleaved
#: unified/other-suite-only runs in between.
_MAX_CANDIDATES = 40


def discover_rundir_runs(root_glob: str, suite: str, run_id_prefix: str) -> List[RunInfo]:
    """Each directory matching `root_glob` is one candidate run. A directory
    contributes a RunInfo for `suite` only if claim_files_for_suite finds at
    least one file it owns there. Labeled from that suite's own marker file
    when present, falling back to the directory's own name (typically an
    embedded timestamp) otherwise."""
    candidate_dirs = [d for d in glob.glob(root_glob) if os.path.isdir(d)]
    candidate_dirs.sort(key=os.path.getmtime, reverse=True)
    candidate_dirs = candidate_dirs[:_MAX_CANDIDATES]

    runs: List[RunInfo] = []
    for d in candidate_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        claimed_names = claim_files_for_suite(names, suite)
        if not claimed_names:
            continue

        files: List[RunFile] = []
        for name in claimed_names:
            abs_path = os.path.join(d, name)
            try:
                stat = os.stat(abs_path)
            except OSError:
                continue
            files.append(RunFile(
                abs_path=abs_path,
                rel_path=os.path.relpath(abs_path, ROOT),
                kind=classify_file(abs_path),
                size_bytes=stat.st_size,
                modified=stat.st_mtime,
            ))
        if not files:
            continue

        label = None
        if suite in SUITE_MARKER_FILES:
            marker_name = SUITE_MARKER_FILES[suite][0]
            if marker_name in claimed_names:
                label = label_from_marker(os.path.join(d, marker_name), suite)
        dirname = os.path.basename(d.rstrip(os.sep))
        if label is None:
            label = dirname

        runs.append(RunInfo(
            suite=suite,
            run_id=f"{run_id_prefix}:{dirname}",
            label=label,
            timestamp=max(f.modified for f in files),
            files=files,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add rundir-based run discovery with suite ownership filtering"
```

---

## Task 3: `output_runs.py` -- Vol_Suite standalone runs + legacy loose bucket

**Files:**
- Modify: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Consumes: `discover_rundir_runs`, `RunFile`, `RunInfo`, `classify_file` from Tasks 1-2.
- Produces: `discover_loose_bucket(directory: str, suite: str, run_id: str, label: str) -> Optional[RunInfo]`. Task 6's public dispatch calls this for `Vol_Suite/vs_output/`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_output_runs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_loose_bucket'`

- [ ] **Step 3: Write the implementation**

Append to `dashboard/output_runs.py`:

```python
def discover_loose_bucket(directory: str, suite: str, run_id: str, label: str) -> Optional[RunInfo]:
    """Wraps every FILE (not subdirectory) directly inside `directory` as one
    single synthetic RunInfo. For legacy output locations with no per-run
    separation at all (Vol_Suite/vs_output/, multiple tickers' files mixed
    together with no run boundary in the data) -- rather than pretending to
    reconstruct run boundaries that don't exist, this shows it honestly as
    one "everything that's here" bucket. Returns None if the directory is
    missing or has no files directly in it."""
    if not os.path.isdir(directory):
        return None

    files: List[RunFile] = []
    for name in os.listdir(directory):
        abs_path = os.path.join(directory, name)
        if not os.path.isfile(abs_path):
            continue
        try:
            stat = os.stat(abs_path)
        except OSError:
            continue
        files.append(RunFile(
            abs_path=abs_path,
            rel_path=os.path.relpath(abs_path, ROOT),
            kind=classify_file(abs_path),
            size_bytes=stat.st_size,
            modified=stat.st_mtime,
        ))
    if not files:
        return None

    return RunInfo(
        suite=suite,
        run_id=run_id,
        label=label,
        timestamp=max(f.modified for f in files),
        files=files,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add loose-bucket discovery for legacy ungrouped output dirs"
```

---

## Task 4: `output_runs.py` -- Options_Suite comparison-file clustering discovery

**Files:**
- Modify: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Consumes: `cluster_by_timestamp`, `extract_timestamp`, `RunFile`, `RunInfo`, `classify_file` from Task 1.
- Produces: `discover_clustered_runs(paths: List[str], suite: str, run_id_prefix: str, window_seconds: int = 10) -> List[RunInfo]`. Task 6's dispatch calls this with `glob.glob(os.path.join(SUITE_ROOTS['options'], 'comparison_*.csv'))` + the `.pdf` equivalent.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_output_runs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_clustered_runs'`

- [ ] **Step 3: Write the implementation**

Append to `dashboard/output_runs.py`:

```python
def discover_clustered_runs(paths: List[str], suite: str, run_id_prefix: str,
                             window_seconds: int = 10) -> List[RunInfo]:
    """Groups flat, ungrouped files (Options_Suite's comparison_*.csv/.pdf,
    written directly into the suite root with no directory or JSON marker to
    group by) into runs via cluster_by_timestamp. Each cluster's run_id and
    label come from the earliest timestamp in that cluster."""
    clusters = cluster_by_timestamp(paths, window_seconds=window_seconds)

    runs: List[RunInfo] = []
    for cluster_paths in clusters:
        files: List[RunFile] = []
        for p in cluster_paths:
            try:
                stat = os.stat(p)
            except OSError:
                continue
            files.append(RunFile(
                abs_path=p,
                rel_path=os.path.relpath(p, ROOT),
                kind=classify_file(p),
                size_bytes=stat.st_size,
                modified=stat.st_mtime,
            ))
        if not files:
            continue

        timestamps = [extract_timestamp(os.path.basename(f.abs_path)) for f in files]
        timestamps = [t for t in timestamps if t is not None]
        if timestamps:
            earliest = min(timestamps)
            run_id_suffix = earliest.strftime('%Y%m%d_%H%M%S')
            label = earliest.strftime('%Y-%m-%d %H:%M:%S')
        else:
            run_id_suffix = os.path.basename(files[0].abs_path)
            label = run_id_suffix

        runs.append(RunInfo(
            suite=suite,
            run_id=f"{run_id_prefix}:{run_id_suffix}",
            label=label,
            timestamp=max(f.modified for f in files),
            files=files,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add timestamp-clustered run discovery for flat comparison files"
```

---

## Task 5: `output_runs.py` -- sentiment date-bucket discovery

**Files:**
- Modify: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Consumes: `RunFile`, `RunInfo`, `classify_file` from Task 1.
- Produces: `discover_date_bucket_runs(root_glob: str, suite: str, run_id_prefix: str) -> List[RunInfo]`. Task 6's dispatch calls this with `sentiment-scanner/data/exports/highlighted_ticker_packs/*`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_output_runs.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_date_bucket_runs'`

- [ ] **Step 3: Write the implementation**

Append to `dashboard/output_runs.py`:

```python
def discover_date_bucket_runs(root_glob: str, suite: str, run_id_prefix: str) -> List[RunInfo]:
    """Each directory matching `root_glob` is treated as one run bucket --
    built for sentiment-scanner's data/exports/highlighted_ticker_packs/
    <YYYYMMDD>/ layout. Since sentiment-scanner loops continuously
    (SCAN_INTERVAL_MINUTES), this deliberately collapses multiple scan
    cycles in one day into one bucket rather than trying to separate them."""
    candidate_dirs = [d for d in glob.glob(root_glob) if os.path.isdir(d)]
    candidate_dirs.sort(key=os.path.getmtime, reverse=True)
    candidate_dirs = candidate_dirs[:_MAX_CANDIDATES]

    runs: List[RunInfo] = []
    for d in candidate_dirs:
        files: List[RunFile] = []
        for name in os.listdir(d):
            abs_path = os.path.join(d, name)
            if not os.path.isfile(abs_path):
                continue
            try:
                stat = os.stat(abs_path)
            except OSError:
                continue
            files.append(RunFile(
                abs_path=abs_path,
                rel_path=os.path.relpath(abs_path, ROOT),
                kind=classify_file(abs_path),
                size_bytes=stat.st_size,
                modified=stat.st_mtime,
            ))
        if not files:
            continue

        dirname = os.path.basename(d.rstrip(os.sep))
        try:
            label = datetime.strptime(dirname, '%Y%m%d').strftime('%Y-%m-%d')
        except ValueError:
            label = dirname

        runs.append(RunInfo(
            suite=suite,
            run_id=f"{run_id_prefix}:{dirname}",
            label=label,
            timestamp=max(f.modified for f in files),
            files=files,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add date-bucket run discovery for sentiment-scanner exports"
```

---

## Task 6: `output_runs.py` -- public dispatch, unified-run detection, file views

**Files:**
- Modify: `dashboard/output_runs.py`
- Test: `dashboard/tests/test_output_runs.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: `discover_runs(suite_key: str) -> List[RunInfo]`, `get_run(suite_key: str, run_id: str) -> Optional[RunInfo]`, `MAX_TABLE_ROWS: int`, `MAX_TABLE_COLS: int`, `read_json_view(path: str) -> Dict[str, Any]`, `read_csv_table(path: str) -> Dict[str, Any]`, `build_file_view(run_file: RunFile) -> Dict[str, Any]`, `SUITE_LABELS: Dict[str, str]`. Task 7 (`dashboard/app.py`) imports and calls all of these; nothing else in `dashboard/app.py`'s old glob code survives past this task.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_output_runs.py`:

```python
from dashboard.output_runs import (
    discover_runs, get_run, read_json_view, read_csv_table, build_file_view,
    SUITE_LABELS,
)


def test_suite_labels_covers_all_five_keys():
    assert set(SUITE_LABELS) == {'options', 'vol', 'var', 'sentiment', 'unified'}


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'discover_runs'`

- [ ] **Step 3: Write the implementation**

Append to `dashboard/output_runs.py`:

```python
import csv as _csv

SUITE_LABELS = {
    'options': 'Options_Suite',
    'vol': 'Vol_Suite',
    'var': 'VaR_Tools_Simulations',
    'sentiment': 'sentiment-scanner',
    'unified': 'Unified',
}

MAX_TABLE_ROWS = 250
MAX_TABLE_COLS = 40

_EXCLUDED_PATH_BITS = (os.sep + '.venv' + os.sep, os.sep + 'site-packages' + os.sep,
                       os.sep + '__pycache__' + os.sep, os.sep + '.git' + os.sep)


def _rundir_sources_for(suite: str):
    """(source_tag, glob_pattern) pairs to scan for `suite`, beyond the
    shared orchestrator_output/ scan every suite gets."""
    if suite == 'vol':
        return [('vsout', os.path.join(SUITE_ROOTS['vol'], 'outputs', '*'))]
    return []


def discover_runs(suite_key: str) -> List[RunInfo]:
    """Combines every discovery source relevant to `suite_key`, marks
    unified-run siblings, and returns all discovered runs sorted
    newest-first. Callers slice [:10] for the dropdown.

    Sibling-suite detection happens HERE, once, for every run this function
    returns -- not as a fallback bolted onto get_run() -- because the normal
    route path (Task 7) reads straight from this function's result list
    first and only calls get_run() for a run_id that fell out of the last-10
    window. Computing siblings only inside get_run() would mean the
    "part of a unified run" note almost never actually shows up."""
    if suite_key not in SUITE_LABELS:
        return []

    if suite_key == 'unified':
        runs = _discover_unified_runs()
        runs.sort(key=lambda r: r.timestamp, reverse=True)
        return runs

    runs = discover_rundir_runs(os.path.join(ORCH_OUTPUT, '*'), suite_key, 'orch')
    for tag, pattern in _rundir_sources_for(suite_key):
        runs.extend(discover_rundir_runs(pattern, suite_key, tag))

    if suite_key == 'options':
        root = SUITE_ROOTS['options']
        paths = (glob.glob(os.path.join(root, 'comparison_*.csv')) +
                glob.glob(os.path.join(root, 'comparison_*.pdf')))
        paths = [p for p in paths if not any(bit in p for bit in _EXCLUDED_PATH_BITS)]
        runs.extend(discover_clustered_runs(paths, 'options', 'cmp'))

    if suite_key == 'vol':
        loose = discover_loose_bucket(
            os.path.join(SUITE_ROOTS['vol'], 'vs_output'), 'vol', 'loose:vs_output',
            'Legacy vs_output (ungrouped)')
        if loose is not None:
            runs.append(loose)

    if suite_key == 'sentiment':
        runs.extend(discover_date_bucket_runs(
            os.path.join(SUITE_ROOTS['sentiment'], 'data', 'exports',
                        'highlighted_ticker_packs', '*'),
            'sentiment', 'pack'))

    unified_siblings = {r.run_id: [s for s in r.sibling_suites if s != suite_key]
                        for r in _discover_unified_runs()}
    runs = [
        r if r.run_id not in unified_siblings else
        RunInfo(suite=r.suite, run_id=r.run_id, label=r.label, timestamp=r.timestamp,
                files=r.files, sibling_suites=unified_siblings[r.run_id])
        for r in runs
    ]

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs


def _discover_unified_runs() -> List[RunInfo]:
    """orchestrator_output/<run_id>/ directories claimed by 2+ of
    {options, var, sentiment, vol} -- i.e. an actual unified run, not a
    suite-kind run that happened to share the directory naming scheme."""
    candidate_dirs = [d for d in glob.glob(os.path.join(ORCH_OUTPUT, '*')) if os.path.isdir(d)]
    candidate_dirs.sort(key=os.path.getmtime, reverse=True)
    candidate_dirs = candidate_dirs[:_MAX_CANDIDATES]

    runs: List[RunInfo] = []
    for d in candidate_dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        per_suite = {s: claim_files_for_suite(names, s)
                     for s in ('options', 'var', 'sentiment', 'vol')}
        suites_present = [s for s, claimed in per_suite.items() if claimed]
        if len(suites_present) < 2:
            continue

        files: List[RunFile] = []
        for s in suites_present:
            for name in per_suite[s]:
                abs_path = os.path.join(d, name)
                try:
                    stat = os.stat(abs_path)
                except OSError:
                    continue
                files.append(RunFile(
                    abs_path=abs_path,
                    rel_path=os.path.relpath(abs_path, ROOT),
                    kind=classify_file(abs_path),
                    size_bytes=stat.st_size,
                    modified=stat.st_mtime,
                ))
        if not files:
            continue

        dirname = os.path.basename(d.rstrip(os.sep))
        runs.append(RunInfo(
            suite='unified',
            run_id=f"orch:{dirname}",
            label=f"unified · {', '.join(sorted(suites_present))}",
            timestamp=max(f.modified for f in files),
            files=files,
            sibling_suites=sorted(suites_present),
        ))
    return runs


def get_run(suite_key: str, run_id: str) -> Optional[RunInfo]:
    """Re-derives suite_key's full run list (already carrying correct
    sibling_suites -- see discover_runs) and returns the one matching
    run_id, or None."""
    return next((r for r in discover_runs(suite_key) if r.run_id == run_id), None)


def _scalar(value) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return text if len(text) <= 400 else text[:400] + ' ...'
    return str(value)


def read_csv_table(path: str) -> dict:
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = _csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row[:MAX_TABLE_COLS])
            if i > MAX_TABLE_ROWS:
                break
    if not rows:
        return {'kind': 'empty', 'headers': [], 'rows': [], 'truncated': False}
    headers, body = rows[0], rows[1:]
    truncated = len(body) > MAX_TABLE_ROWS
    return {
        'kind': 'table',
        'headers': headers,
        'rows': body[:MAX_TABLE_ROWS],
        'truncated': truncated,
    }


def read_json_view(path: str) -> dict:
    with open(path, 'r', encoding='utf-8-sig') as f:
        payload = json.load(f)

    if isinstance(payload, list) and payload and all(isinstance(r, dict) for r in payload):
        headers: List[str] = []
        for record in payload[:MAX_TABLE_ROWS]:
            for key in record:
                if key not in headers:
                    headers.append(key)
        headers = headers[:MAX_TABLE_COLS]
        rows = [[_scalar(record.get(h)) for h in headers]
                for record in payload[:MAX_TABLE_ROWS]]
        return {'kind': 'table', 'headers': headers, 'rows': rows,
                'truncated': len(payload) > MAX_TABLE_ROWS}

    if isinstance(payload, dict):
        pairs = [(k, _scalar(v)) for k, v in payload.items()]
        return {'kind': 'pairs', 'pairs': pairs,
                'raw': json.dumps(payload, indent=2, default=str)[:20000]}

    return {'kind': 'raw', 'raw': json.dumps(payload, indent=2, default=str)[:20000]}


def build_file_view(run_file: RunFile) -> dict:
    """Dispatches a RunFile to its renderable view. json/csv get parsed
    content (pairs/table/raw); png/pdf/other get a bare kind marker -- the
    template renders those directly against the asset route, no parsing
    needed."""
    if run_file.kind == 'json':
        return read_json_view(run_file.abs_path)
    if run_file.kind == 'csv':
        return read_csv_table(run_file.abs_path)
    if run_file.kind == 'png':
        return {'kind': 'image'}
    if run_file.kind == 'pdf':
        return {'kind': 'pdf'}
    return {'kind': 'download'}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_output_runs.py -v`
Expected: all PASS (this is the largest test file in the plan -- confirm the full count looks right, e.g. `... 32 passed`)

- [ ] **Step 5: Commit**

```bash
git add dashboard/output_runs.py dashboard/tests/test_output_runs.py
git commit -m "feat: add output_runs public dispatch, unified detection, file views"
```

---

## Task 7: Wire `/suites/{suite}` and new `/suites/{suite}/asset` routes in `dashboard/app.py`

**Files:**
- Modify: `dashboard/app.py:488-655` (delete `SUITE_OUTPUT_GLOBS`, `_newest_matching`, `_read_csv_table`, `_read_json_view`, `_scalar` -- all moved into `output_runs.py` in Tasks 2-6)
- Modify: `dashboard/app.py:1549-1587` (replace the `/suites/{suite}` route)
- Modify: `dashboard/app.py` imports (add `output_runs`, `FileResponse`, `HTTPException`)
- Test: Create `dashboard/tests/test_suite_output_routes.py`

**Interfaces:**
- Consumes: `output_runs.discover_runs`, `output_runs.get_run`, `output_runs.build_file_view`, `output_runs.SUITE_LABELS` from Task 6; `output_runs.claim_files_for_suite` from Task 2 (reused here to group a unified run's files by owning suite for display).
- Produces: `GET /suites/{suite}` (now accepts `run_id` query param, passes `runs` (last 10), `run`, `file_views: List[Tuple[RunFile, dict]]`, `grouped_file_views: Optional[Dict[str, List[Tuple[RunFile, dict]]]]` to the template), `GET /suites/{suite}/asset` (new), the `epochts` Jinja filter (for `RunInfo.timestamp`'s epoch-float values -- distinct from the pre-existing `ts` filter, which expects ISO strings). Task 9 (`suite.html`) consumes the new template context shape and the new filter.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/tests/test_suite_output_routes.py`:

```python
"""test_suite_output_routes.py

Covers Task 7 of docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md:
the rebuilt GET /suites/{suite} and the new GET /suites/{suite}/asset.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402
import dashboard.output_runs as output_runs  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


@pytest.fixture
def fake_options_run(tmp_path, monkeypatch):
    """One discoverable options run with a JSON marker, isolated from any
    real orchestrator_output/ on disk."""
    orch = tmp_path / "orchestrator_output"
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir(parents=True)
    (run_dir / "options_result.json").write_text(json.dumps({
        "suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR",
        "timestamp": "2026-07-29T05:53:06Z", "sigma": 0.25, "price": 12.3,
        "greeks": {"delta": 0.5},
    }))

    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    return run_dir


def test_unknown_suite_returns_404():
    r = client.get('/suites/not-a-real-suite')
    assert r.status_code == 404


def test_known_suite_with_no_runs_renders_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(tmp_path / "orchestrator_output"))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    r = client.get('/suites/options')
    assert r.status_code == 200
    assert b'Nothing produced yet' in r.content or b'nothing produced' in r.content.lower()


def test_known_suite_with_a_run_renders_it(fake_options_run):
    r = client.get('/suites/options')
    assert r.status_code == 200
    assert b'AAPL' in r.content


def test_run_id_query_param_selects_a_specific_run(fake_options_run):
    r = client.get('/suites/options', params={'run_id': 'orch:20260729T055306Z'})
    assert r.status_code == 200
    assert b'AAPL' in r.content


def test_unknown_run_id_falls_back_gracefully(fake_options_run):
    r = client.get('/suites/options', params={'run_id': 'orch:does-not-exist'})
    assert r.status_code == 200  # not a 404 -- just shows the newest run instead


def test_asset_route_serves_a_file_that_belongs_to_the_named_run(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z',
        'rel_path': 'orchestrator_output/20260729T055306Z/options_result.json',
    })
    # NOTE: rel_path here must match output_runs.ROOT-relative form; since
    # ORCH_OUTPUT is monkeypatched to a tmp_path in this fixture, the real
    # rel_path won't match this literal string -- assert via the discovered
    # run's own rel_path instead of hardcoding it.
    run = output_runs.get_run('options', 'orch:20260729T055306Z')
    real_rel_path = run.files[0].rel_path
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z', 'rel_path': real_rel_path,
    })
    assert r.status_code == 200


def test_asset_route_rejects_a_path_not_part_of_the_named_run(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:20260729T055306Z',
        'rel_path': '../../../../etc/passwd',
    })
    assert r.status_code == 404


def test_asset_route_rejects_unknown_run_id(fake_options_run):
    r = client.get('/suites/options/asset', params={
        'run_id': 'orch:does-not-exist', 'rel_path': 'whatever.json',
    })
    assert r.status_code == 404


@pytest.fixture
def fake_unified_run(tmp_path, monkeypatch):
    """One run directory claimed by 2 suites -- the minimum to be treated
    as a unified run by _discover_unified_runs."""
    orch = tmp_path / "orchestrator_output"
    run_dir = orch / "20260729T055306Z"
    run_dir.mkdir(parents=True)
    (run_dir / "options_result.json").write_text(json.dumps({
        "suite": "options", "status": "ok", "ticker": "AAPL", "method": "CRR",
    }))
    (run_dir / "var_result.json").write_text(json.dumps({"suite": "var", "status": "ok"}))

    monkeypatch.setattr(output_runs, "ORCH_OUTPUT", str(orch))
    monkeypatch.setattr(output_runs, "SUITE_ROOTS", {
        'options': str(tmp_path / "Options_Suite"), 'vol': str(tmp_path / "Vol_Suite"),
        'var': str(tmp_path / "VaR_Tools_Simulations"),
        'sentiment': str(tmp_path / "sentiment-scanner"),
    })
    return run_dir


def test_unified_suite_groups_files_by_owning_suite(fake_unified_run):
    r = client.get('/suites/unified')
    assert r.status_code == 200
    # Both suites' files must be present -- not just one flat undifferentiated list
    assert b'AAPL' in r.content
    assert b'options_result.json' in r.content or b'Options' in r.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_suite_output_routes.py -v`
Expected: FAIL (route doesn't accept `run_id` yet / asset route doesn't exist -- `404`/`AttributeError` depending on which test)

- [ ] **Step 3: Delete the old glob-based code**

In `dashboard/app.py`, delete lines 488-655 (from the `# suite output discovery` comment through the end of the `_scalar` function) -- everything Task 6 already moved into `output_runs.py`. Leave the `ORCH_OUTPUT = os.path.join(ROOT, 'orchestrator_output')` line's *concept* behind (it now lives in `output_runs.py`); nothing in `app.py` needs `ORCH_OUTPUT` directly after this task.

- [ ] **Step 4: Add imports**

In `dashboard/app.py`, change line 46 from:

```python
from fastapi.responses import HTMLResponse, JSONResponse
```

to:

```python
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
```

And change line 45 from:

```python
from fastapi import BackgroundTasks, FastAPI, Request, WebSocket
```

to:

```python
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket
```

Add, alongside the other `from dashboard.*` imports (after line 77's `from dashboard.worker_env import build_worker_env`):

```python
from dashboard.output_runs import (
    discover_runs, get_run, build_file_view, claim_files_for_suite, SUITE_LABELS,
)  # noqa: E402
```

Register a second timestamp filter alongside the existing one -- `_fmt_ts` (already registered at line 157) expects an ISO string (that's what every other page's `modified`/`started_at` values already are), but `RunInfo.timestamp`/`RunFile.modified` from `output_runs.py` are epoch floats, a different representation that filter was never built to handle. Immediately after the existing `TEMPLATES.env.filters['ts'] = _fmt_ts` line, add:

```python
def _fmt_epoch_ts(value: Any) -> str:
    if not value:
        return '--'
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    except (TypeError, ValueError, OSError):
        return '--'


TEMPLATES.env.filters['epochts'] = _fmt_epoch_ts
```

- [ ] **Step 5: Replace the `/suites/{suite}` route and add the asset route**

Replace the existing `suite_output` function (originally at lines 1549-1587) with:

```python
@app.get('/suites/{suite}', response_class=HTMLResponse)
def suite_output(request: Request, suite: str, run_id: Optional[str] = None):
    key = suite.strip().lower()
    if key not in SUITE_LABELS:
        return TEMPLATES.TemplateResponse(request, 'suite.html', {
            'active': 'suites', 'suite': key, 'runs': [], 'run': None,
            'file_views': [], 'grouped_file_views': None, 'active_run_banner': None,
            'error': f'unknown suite {suite!r}; expected one of '
                     f'{", ".join(sorted(SUITE_LABELS))}',
            'suites': SUITE_LABELS,
        }, status_code=404)

    all_runs = discover_runs(key)
    runs = all_runs[:10]

    run = None
    if run_id:
        run = next((r for r in runs if r.run_id == run_id), None) or get_run(key, run_id)
    if run is None and runs:
        run = runs[0]

    file_views = []
    grouped_file_views: Optional[Dict[str, List[Any]]] = None
    error: Optional[str] = None
    if run is not None:
        if key == 'unified':
            # A unified run's files span multiple suites with no per-file
            # suite tag on RunFile itself -- re-derive ownership the same
            # way discover_rundir_runs does, purely for grouping the display,
            # rather than adding an owner_suite field every OTHER discovery
            # path would have to populate too.
            names = [os.path.basename(f.abs_path) for f in run.files]
            grouped_file_views = {}
            for s in ('options', 'var', 'sentiment', 'vol'):
                claimed = set(claim_files_for_suite(names, s))
                s_files = [f for f in run.files if os.path.basename(f.abs_path) in claimed]
                if not s_files:
                    continue
                grouped_file_views[s] = []
                for f in s_files:
                    try:
                        grouped_file_views[s].append((f, build_file_view(f)))
                    except Exception as e:
                        error = f'could not parse {os.path.basename(f.abs_path)}: {type(e).__name__}: {e}'
        else:
            for f in run.files:
                try:
                    file_views.append((f, build_file_view(f)))
                except Exception as e:
                    error = f'could not parse {os.path.basename(f.abs_path)}: {type(e).__name__}: {e}'

    return TEMPLATES.TemplateResponse(request, 'suite.html', {
        'active': 'suites',
        'suite': key,
        'runs': runs,
        'run': run,
        'file_views': file_views,
        'grouped_file_views': grouped_file_views,
        'active_run_banner': _active_run_banner(key),
        'error': error,
        'suites': SUITE_LABELS,
    })


@app.get('/suites/{suite}/asset')
def suite_asset(suite: str, run_id: str, rel_path: str):
    """Serves one file's raw bytes for inline images/PDFs and generic
    downloads. Never trusts `rel_path` directly: only serves it if it is an
    EXACT match against a file that discover_runs/get_run already
    enumerated server-side for this exact run_id -- no path-joining of user
    input, no traversal surface."""
    key = suite.strip().lower()
    if key not in SUITE_LABELS:
        raise HTTPException(status_code=404, detail='unknown suite')

    run = get_run(key, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail='run not found')

    match = next((f for f in run.files if f.rel_path == rel_path), None)
    if match is None:
        raise HTTPException(status_code=404, detail='file not part of this run')

    return FileResponse(match.abs_path)
```

- [ ] **Step 6: Add the `_active_run_banner` helper**

Add this function directly above `suite_output` (it reads the existing in-memory `_RUNS` dict, already defined earlier in `app.py` -- see Task 8 for why this stays in `app.py` rather than `output_runs.py`):

```python
def _active_run_banner(suite_key: str) -> Optional[Dict[str, Any]]:
    """Task 8 fills this in; returns None for now so Task 7's route/template
    wiring can be tested independently of the live-run banner feature."""
    return None
```

(Task 8 replaces this stub with the real implementation -- writing it as an explicit stub now, rather than skipping the parameter entirely, means Task 9's template can be written once against the final context shape instead of needing a follow-up edit.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_suite_output_routes.py -v`
Expected: all PASS

- [ ] **Step 8: Run the full dashboard test suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/ -v`
Expected: all PASS (confirms nothing else in `dashboard/tests/` depended on the deleted `SUITE_OUTPUT_GLOBS`/`_newest_matching`/etc.)

- [ ] **Step 9: Commit**

```bash
git add dashboard/app.py dashboard/tests/test_suite_output_routes.py
git commit -m "feat: rebuild /suites/{suite} route on output_runs, add asset-serving route"
```

---

## Task 8: Live-run banner glue in `dashboard/app.py`

**Files:**
- Modify: `dashboard/app.py` (replace the `_active_run_banner` stub from Task 7)
- Test: `dashboard/tests/test_suite_output_routes.py`

**Interfaces:**
- Consumes: `_RUNS` (existing in-memory dict, already defined in `app.py`), `_iso_utc_now` (existing helper).
- Produces: `_active_run_banner(suite_key: str) -> Optional[Dict[str, Any]]` returning `None` or `{'run_id': ..., 'kind': ..., 'status': ..., 'started_at': ...}`. Task 9's template renders this dict directly.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/tests/test_suite_output_routes.py`:

```python
def test_active_run_banner_none_when_nothing_running():
    assert dashboard_app._active_run_banner('options') is None


def test_active_run_banner_shows_a_matching_suite_kind_run():
    with dashboard_app._RUNS_LOCK:
        dashboard_app._RUNS['test-run-1'] = {
            'run_id': 'test-run-1', 'kind': 'options', 'status': 'running',
            'started_at': '2026-08-09T00:00:00Z',
        }
    try:
        banner = dashboard_app._active_run_banner('options')
        assert banner is not None
        assert banner['run_id'] == 'test-run-1'
        assert banner['status'] == 'running'
    finally:
        with dashboard_app._RUNS_LOCK:
            dashboard_app._RUNS.pop('test-run-1', None)


def test_active_run_banner_shows_a_unified_run_on_every_suite():
    with dashboard_app._RUNS_LOCK:
        dashboard_app._RUNS['test-run-2'] = {
            'run_id': 'test-run-2', 'kind': 'unified', 'status': 'queued',
            'started_at': None,
        }
    try:
        for suite in ('options', 'vol', 'var', 'sentiment'):
            banner = dashboard_app._active_run_banner(suite)
            assert banner is not None and banner['run_id'] == 'test-run-2'
    finally:
        with dashboard_app._RUNS_LOCK:
            dashboard_app._RUNS.pop('test-run-2', None)


def test_active_run_banner_ignores_completed_runs():
    with dashboard_app._RUNS_LOCK:
        dashboard_app._RUNS['test-run-3'] = {
            'run_id': 'test-run-3', 'kind': 'options', 'status': 'ok',
            'started_at': '2026-08-09T00:00:00Z',
        }
    try:
        assert dashboard_app._active_run_banner('options') is None
    finally:
        with dashboard_app._RUNS_LOCK:
            dashboard_app._RUNS.pop('test-run-3', None)


def test_active_run_banner_prefers_most_recently_started_when_multiple():
    with dashboard_app._RUNS_LOCK:
        dashboard_app._RUNS['older'] = {
            'run_id': 'older', 'kind': 'options', 'status': 'running',
            'started_at': '2026-08-09T00:00:00Z',
        }
        dashboard_app._RUNS['newer'] = {
            'run_id': 'newer', 'kind': 'options', 'status': 'running',
            'started_at': '2026-08-09T00:05:00Z',
        }
    try:
        banner = dashboard_app._active_run_banner('options')
        assert banner['run_id'] == 'newer'
    finally:
        with dashboard_app._RUNS_LOCK:
            dashboard_app._RUNS.pop('older', None)
            dashboard_app._RUNS.pop('newer', None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_suite_output_routes.py -v -k active_run_banner`
Expected: FAIL (stub always returns `None`, so the "shows a matching run" tests fail)

- [ ] **Step 3: Write the implementation**

In `dashboard/app.py`, replace the Task 7 stub:

```python
def _active_run_banner(suite_key: str) -> Optional[Dict[str, Any]]:
    """Task 8 fills this in; returns None for now so Task 7's route/template
    wiring can be tested independently of the live-run banner feature."""
    return None
```

with:

```python
def _active_run_banner(suite_key: str) -> Optional[Dict[str, Any]]:
    """The most recently started queued/running run tracked in _RUNS that's
    relevant to `suite_key` -- either a suite-kind run for this exact suite,
    or a unified run (which touches every suite). Reuses the existing
    _RUNS/GET-/runs/{run_id} polling infrastructure Quant Console already
    established (3s client-side poll) -- deliberately NOT the
    /suites/{suite}/live WebSocket, since nothing in this repo currently
    writes to the log file it tails (see that route's own comments)."""
    with _RUNS_LOCK:
        candidates = [
            dict(entry) for entry in _RUNS.values()
            if entry.get('status') in ('queued', 'running')
            and entry.get('kind') in (suite_key, 'unified')
        ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.get('started_at') or '', reverse=True)
    return candidates[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_suite_output_routes.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/tests/test_suite_output_routes.py
git commit -m "feat: implement live-run banner backed by existing _RUNS tracking"
```

---

## Task 9: Rebuild `dashboard/templates/suite.html`

**Files:**
- Modify: `dashboard/templates/suite.html` (full rewrite)

**Interfaces:**
- Consumes: template context from Task 7/8's route: `suite: str`, `suites: Dict[str, str]` (now `SUITE_LABELS`, not glob specs), `runs: List[RunInfo]`, `run: Optional[RunInfo]`, `file_views: List[Tuple[RunFile, dict]]`, `grouped_file_views: Optional[Dict[str, List[Tuple[RunFile, dict]]]]` (populated only when `suite == 'unified'`), `active_run_banner: Optional[dict]`, `error: Optional[str]`. Also relies on the `epochts` Jinja filter Task 7 registered for epoch-float timestamps (`RunInfo.timestamp`) -- the pre-existing `ts` filter stays reserved for ISO-string timestamps like `active_run_banner.started_at`.

- [ ] **Step 1: Replace the template**

Replace the entire contents of `dashboard/templates/suite.html` with:

```html
{% extends "base.html" %}
{% block title %}{{ suites.get(suite, suite) }} output -- FinancialDevelopment Dashboard{% endblock %}

{% macro filecard(f, view, suite, run) %}
  <div class="panel filecard {{ f.kind }}">
    <header>
      <h2 class="mono" style="font-size:13px;">
        {% if f.rel_path.endswith('quant_summary.json') %}
          Quant Summary
        {% else %}
          {{ f.rel_path }}
        {% endif %}
      </h2>
      <span class="note">{{ f.kind }} &middot; {{ f.size_bytes }} bytes</span>
    </header>

    {% if f.rel_path.endswith('quant_summary.json') and view.kind == 'pairs' %}
      <div class="body">
        <div class="kv">
          {% for k, v in view.pairs %}
            <div>{{ k }}</div><div class="val">{{ v if v != '' else '--' }}</div>
          {% endfor %}
        </div>
        {% for k, v in view.pairs %}
          {% if k == 'run_id' %}
            <p class="small" style="margin-top:10px;">
              <a href="/runs/{{ v }}/summary">Raw Quant Summary (JSON) &rarr;</a>
            </p>
          {% endif %}
        {% endfor %}
      </div>

    {% elif view.kind == 'table' %}
      <div class="tablewrap scrollbox">
        <table>
          <thead><tr>{% for h in view.headers %}<th>{{ h }}</th>{% endfor %}</tr></thead>
          <tbody>
          {% for row in view.rows %}
            <tr>{% for cell in row %}<td class="small">{{ cell if cell not in (none, '') else '--' }}</td>{% endfor %}</tr>
          {% endfor %}
          </tbody>
        </table>
      </div>
      {% if view.truncated %}<div class="pager muted small">truncated -- open the file for the rest</div>{% endif %}

    {% elif view.kind == 'pairs' %}
      <div class="body">
        <div class="kv">
          {% for k, v in view.pairs %}
            <div>{{ k }}</div><div class="val">{{ v if v != '' else '--' }}</div>
          {% endfor %}
        </div>
      </div>

    {% elif view.kind == 'raw' %}
      <div class="body"><pre class="json">{{ view.raw }}</pre></div>

    {% elif view.kind == 'image' %}
      <div class="body">
        <img src="/suites/{{ suite }}/asset?run_id={{ run.run_id }}&rel_path={{ f.rel_path | urlencode }}"
             alt="{{ f.rel_path }}" style="max-width:100%; border-radius:8px; border:1px solid var(--border);">
      </div>

    {% elif view.kind == 'pdf' %}
      <div class="body">
        <embed src="/suites/{{ suite }}/asset?run_id={{ run.run_id }}&rel_path={{ f.rel_path | urlencode }}"
               type="application/pdf" width="100%" height="500px"
               style="border-radius:8px; border:1px solid var(--border);">
        <p class="small" style="margin-top:8px;">
          <a href="/suites/{{ suite }}/asset?run_id={{ run.run_id }}&rel_path={{ f.rel_path | urlencode }}" target="_blank">Open in new tab &rarr;</a>
        </p>
      </div>

    {% else %}
      <div class="body">
        <a href="/suites/{{ suite }}/asset?run_id={{ run.run_id }}&rel_path={{ f.rel_path | urlencode }}">Download {{ f.rel_path.split('/') | last }} &rarr;</a>
      </div>
    {% endif %}
  </div>
{% endmacro %}

{% block content %}
<h1>{{ suites.get(suite, suite) }} output</h1>
<p class="sub">Last {{ runs | length }} run{{ '' if runs | length == 1 else 's' }} on disk for this suite.</p>

<div class="panel">
  <header><h2>Suites</h2><span class="note">last {{ runs | length }} runs shown</span></header>
  <div class="body">
    {% for key, label in suites.items() %}
      <a href="/suites/{{ key }}"
         class="pill suitepill {{ 'on' if key == suite else '' }}">
        {{ label }}
      </a>
    {% endfor %}
  </div>
</div>

{% if active_run_banner %}
<div class="panel banner-live" id="live-banner" data-run-id="{{ active_run_banner.run_id }}">
  <div class="body">
    <span class="pill running">{{ active_run_banner.status }}</span>
    a <strong>{{ active_run_banner.kind }}</strong> run is in progress
    {% if active_run_banner.started_at %}(started {{ active_run_banner.started_at | ts }} UTC){% endif %}
    -- this page will refresh automatically when it finishes.
  </div>
</div>
{% endif %}

{% if error %}<div class="errbox">{{ error }}</div>{% endif %}

{% if not runs %}
  <div class="panel"><div class="body">
    <div class="empty">
      <strong>Nothing produced yet.</strong><br>
      No run found on disk for {{ suites.get(suite, suite) }}.<br>
      <span class="small">Trigger a run from the <a href="/">overview page</a>; output shows up here once it writes something.</span>
    </div>
  </div></div>
{% elif run %}
  <div class="panel">
    <header>
      <h2>{{ run.label }}</h2>
      <span class="note">{{ run.files | length }} file{{ '' if run.files | length == 1 else '' }} &middot; modified {{ run.timestamp | epochts }} UTC</span>
    </header>
    <div class="body">
      <form method="get" action="/suites/{{ suite }}" class="runform">
        <div class="field">
          <label for="run_id">Run</label>
          <select name="run_id" id="run_id" onchange="this.form.submit()">
            {% for r in runs %}
              <option value="{{ r.run_id }}" {{ 'selected' if r.run_id == run.run_id else '' }}>
                {{ r.label }}
              </option>
            {% endfor %}
          </select>
        </div>
      </form>

      {% if run.sibling_suites %}
        <p class="small muted" style="margin-top:10px;">
          Part of a unified run -- also produced: {{ run.sibling_suites | join(', ') }}.
          <a href="/suites/unified?run_id={{ run.run_id }}">View all &rarr;</a>
        </p>
      {% endif %}
    </div>
  </div>

  {% if suite == 'unified' and grouped_file_views %}
    {% for owning_suite, views in grouped_file_views.items() %}
      <h2 style="margin: 20px 0 8px;">{{ suites.get(owning_suite, owning_suite) }}</h2>
      {% for f, view in views %}
        {{ filecard(f, view, suite, run) }}
      {% endfor %}
    {% endfor %}
  {% else %}
    {% for f, view in file_views %}
      {{ filecard(f, view, suite, run) }}
    {% endfor %}
  {% endif %}
{% endif %}
{% endblock %}

{% block scripts %}
{% if active_run_banner %}
<script>
// No framework, no CDN -- matches quant.html's own polling script (same
// 3s interval, same GET /runs/{run_id} endpoint this dashboard already
// established for watching an in-flight run to completion).
(function () {
  var banner = document.getElementById('live-banner');
  if (!banner) return;
  var runId = banner.getAttribute('data-run-id');

  function tick() {
    fetch('/runs/' + encodeURIComponent(runId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status && data.status !== 'queued' && data.status !== 'running') {
          window.location.reload();
        }
      })
      .catch(function () { /* transient fetch error -- just try again next tick */ });
  }
  setInterval(tick, 3000);
})();
</script>
{% endif %}
{% endblock %}
```

- [ ] **Step 2: Manual check -- confirm the template renders without a Jinja error**

Run: `.venv\Scripts\python.exe -c "from dashboard.app import TEMPLATES; TEMPLATES.get_template('suite.html')"`
Expected: no output, no exception (confirms the template at least parses)

- [ ] **Step 3: Run the route tests to confirm the real template renders correctly**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_suite_output_routes.py -v`
Expected: all PASS (these were passing against the Task 7 stub template already; this step confirms the rewrite -- including the unified grouping and the `epochts` filter -- didn't regress them)

- [ ] **Step 4: Commit**

```bash
git add dashboard/templates/suite.html
git commit -m "feat: rebuild suite.html with run picker, live banner, multi-asset rendering"
```

---

## Task 10: Visual reskin -- `dashboard/templates/base.html`

**Files:**
- Modify: `dashboard/templates/base.html:9-43` (CSS custom properties)
- Modify: `dashboard/templates/base.html` (append new component styles: `.suitepill`, `.filecard`, `.banner-live`)

**Interfaces:**
- Produces: same CSS custom property *names* as before (`--bg`, `--panel`, `--panel-2`, `--border`, `--text`, `--muted`, `--accent`, `--accent-soft`, `--ok`, `--ok-soft`, `--warn`, `--warn-soft`, `--err`, `--err-soft`, `--mono`), new *values*, plus new tokens `--accent-2`, `--file-json`, `--file-csv`, `--file-png`, `--file-pdf`, `--file-other`. Every other template (`index.html`, `quant.html`, `swaps.html`, `tools_*.html`) reuses the existing class names (`.panel`, `.pill`, `.card`, button, `.kv`, etc.) unchanged -- they inherit the new look automatically without their own markup changing.

- [ ] **Step 1: Replace the `:root` and dark-mode-media-query blocks**

In `dashboard/templates/base.html`, replace lines 9-43:

```css
:root {
  --bg: #f6f7f9;
  --panel: #ffffff;
  --panel-2: #fbfbfd;
  --border: #dfe3e8;
  --text: #1a1d21;
  --muted: #6b7280;
  --accent: #2563eb;
  --accent-soft: #e8effd;
  --ok: #15803d;
  --ok-soft: #e3f5e9;
  --warn: #b45309;
  --warn-soft: #fdf0dc;
  --err: #b91c1c;
  --err-soft: #fbe6e6;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1116;
    --panel: #161b22;
    --panel-2: #1b2129;
    --border: #2a323d;
    --text: #e6edf3;
    --muted: #8b949e;
    --accent: #58a6ff;
    --accent-soft: #17273f;
    --ok: #56d364;
    --ok-soft: #12261a;
    --warn: #e3b341;
    --warn-soft: #2b2213;
    --err: #f85149;
    --err-soft: #2d1618;
  }
}
```

with (dark-first now, per the approved visual direction -- bold/high-contrast base with a magenta-to-violet gradient accent):

```css
:root {
  --bg: #0b0f1a;
  --panel: #111827;
  --panel-2: #151b2b;
  --border: #1e293b;
  --text: #f8fafc;
  --muted: #8291a8;
  --accent: #a855f7;
  --accent-2: #f472b6;
  --accent-soft: #a855f722;
  --ok: #34d399;
  --ok-soft: #34d39922;
  --warn: #fbbf24;
  --warn-soft: #fbbf2422;
  --err: #f87171;
  --err-soft: #f8717122;
  --file-json: #a855f7;
  --file-csv: #a3e635;
  --file-png: #22d3ee;
  --file-pdf: #f472b6;
  --file-other: #94a3b8;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f6f7f9;
    --panel: #ffffff;
    --panel-2: #fbfbfd;
    --border: #dfe3e8;
    --text: #1a1d21;
    --muted: #6b7280;
    --accent: #2563eb;
    --accent-2: #7c3aed;
    --accent-soft: #e8effd;
    --ok: #15803d;
    --ok-soft: #e3f5e9;
    --warn: #b45309;
    --warn-soft: #fdf0dc;
    --err: #b91c1c;
    --err-soft: #fbe6e6;
    --file-json: #7c3aed;
    --file-csv: #15803d;
    --file-png: #0891b2;
    --file-pdf: #be185d;
    --file-other: #6b7280;
  }
}
```

- [ ] **Step 2: Add depth to `.panel` and give buttons a gradient hover**

In `dashboard/templates/base.html`, find:

```css
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 10px;
  margin-bottom: 18px;
  overflow: hidden;
}
```

Replace with:

```css
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 18px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,.16), 0 12px 28px -16px rgba(0,0,0,.4);
  transition: box-shadow .15s ease;
}
```

Find:

```css
button {
  background: var(--accent); border-color: var(--accent); color: #fff;
  font-weight: 600; cursor: pointer;
}
button.ghost { background: var(--panel-2); color: var(--text); border-color: var(--border); }
button:hover { filter: brightness(1.08); }
button:disabled { opacity: .55; cursor: not-allowed; filter: none; }
```

Replace with:

```css
button {
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  border-color: transparent; color: #fff;
  font-weight: 700; cursor: pointer;
  box-shadow: 0 2px 12px -4px var(--accent);
  transition: filter .15s ease, transform .1s ease;
}
button.ghost { background: var(--panel-2); color: var(--text); border-color: var(--border); box-shadow: none; }
button:hover { filter: brightness(1.1); transform: translateY(-1px); }
button:disabled { opacity: .55; cursor: not-allowed; filter: none; transform: none; }
```

- [ ] **Step 3: Add the new component styles**

At the end of the `<style>` block in `dashboard/templates/base.html` (immediately before the closing `</style>` tag), add:

```css
/* Suite tabs on the Output page (dashboard/templates/suite.html) -- gradient
   pill for the active suite, muted flat pill for the rest. */
.pill.suitepill {
  background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
  padding: 5px 14px; font-weight: 700; margin-right: 8px;
}
.pill.suitepill.on {
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  color: #fff; border-color: transparent;
  box-shadow: 0 2px 12px -2px var(--accent);
}

/* One card per output file, accented by file kind (dashboard/templates/suite.html) */
.filecard.json > header { border-left: 3px solid var(--file-json); }
.filecard.csv > header { border-left: 3px solid var(--file-csv); }
.filecard.png > header { border-left: 3px solid var(--file-png); }
.filecard.pdf > header { border-left: 3px solid var(--file-pdf); }
.filecard.other > header { border-left: 3px solid var(--file-other); }

/* Live-run banner (dashboard/templates/suite.html) */
.banner-live { border-color: var(--accent); }
.banner-live .body { display: flex; align-items: center; gap: 10px; }
```

- [ ] **Step 4: Manual verification**

Run: `dashboard.bat` (or `.venv\Scripts\python.exe -m uvicorn dashboard.app:app --port 8787` directly), then in a browser:
1. Open `http://127.0.0.1:8787/suites/options` -- confirm dark background, gradient "Options_Suite" pill, panels have visible shadow/depth.
2. Open `http://127.0.0.1:8787/` (Overview) and `http://127.0.0.1:8787/quant` (Quant Console) -- confirm they picked up the same dark/gradient look automatically (no changes made to their own templates).
3. Toggle your OS to light mode and reload -- confirm the light fallback palette still renders legibly (not a broken half-dark/half-light mix).

Expected: all three checks look right, no unstyled/broken elements.

- [ ] **Step 5: Commit**

```bash
git add dashboard/templates/base.html
git commit -m "feat: reskin dashboard with dark/bold base and gradient accent pills"
```

---

## Task 11: End-to-end manual verification

**Files:** none (verification only, no code changes)

- [ ] **Step 1: Start the dashboard**

Run: `dashboard.bat`

- [ ] **Step 2: Verify each suite tab**

For each of `http://127.0.0.1:8787/suites/options`, `/suites/vol`, `/suites/var`, `/suites/sentiment`:
- Confirm the run-picker dropdown lists real runs found on disk (check against `Options_Suite/comparison_*.csv`, `Vol_Suite/outputs/*/`, `orchestrator_output/*/`, `sentiment-scanner/data/exports/highlighted_ticker_packs/*/` to sanity-check the count/labels match what's actually there).
- Select an older run from the dropdown and confirm the page reloads showing that run's files, not the newest one.
- For a run with a PDF (Options_Suite's `comparison_*.pdf` or Vol_Suite's `volatility_suite_*.pdf`), confirm it renders inline via `<embed>` and the "open in new tab" link works.
- For a run with a PNG (any Vol_Suite gamma/correlation/garch chart), confirm it renders inline as an image, not a download link.
- For a run with a CSV, confirm the table view renders (unchanged from before).

- [ ] **Step 3: Verify the unified view**

Trigger a unified run (`orchestrator.bat --unified --ticker AAPL --expiry <valid future date>`, or via the dashboard's own Overview page trigger form) if one hasn't run recently. Once it completes:
- Visit `http://127.0.0.1:8787/suites/unified` -- confirm the run shows up, grouped into subsections per suite.
- Visit `http://127.0.0.1:8787/suites/options` and select that same run -- confirm the "Part of a unified run" note appears with a working link back to the unified view.

- [ ] **Step 4: Verify the live-run banner**

Trigger any single-suite run from the Overview page, then immediately navigate to that suite's Output tab (`/suites/<suite>`) while it's still running:
- Confirm the banner appears with the correct status pill.
- Leave the tab open until the run completes -- confirm the page auto-reloads (Task 9's polling script) and the finished run now appears in the dropdown.

- [ ] **Step 5: Verify the Quant Console cross-link**

Pick any run that has a `quant_summary.json` (any dashboard-triggered run) -- confirm its file card is labeled "Quant Summary" (not the raw filename) and the "Raw Quant Summary (JSON)" link returns valid JSON.

- [ ] **Step 6: Verify asset-route security**

With any real run's `run_id` in hand, manually hit `http://127.0.0.1:8787/suites/options/asset?run_id=<real_run_id>&rel_path=../../.env` in a browser -- confirm this returns 404, not the contents of the root `.env` file.

- [ ] **Step 7: Run the full test suite one last time**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/ -v`
Expected: all PASS

No commit for this task -- it's verification only. If any check in Steps 2-6 fails, go back to the relevant earlier task, fix it there (with a new test covering what was missed), and re-run this task's checklist from the top.
