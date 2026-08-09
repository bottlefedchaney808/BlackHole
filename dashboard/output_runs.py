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
