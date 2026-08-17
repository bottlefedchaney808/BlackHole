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
    'swaps': ('swaps_result.json',),
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
        return f"{data.get('ticker', '?')} · market signals · {status}"
    if suite == 'swaps':
        return f"{data.get('ticker', '?')} · {data.get('row_count', 0)} row(s) · {status}"
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
            run_timestamp = earliest.timestamp()
        else:
            run_id_suffix = os.path.basename(files[0].abs_path)
            label = run_id_suffix
            run_timestamp = max(f.modified for f in files)

        runs.append(RunInfo(
            suite=suite,
            run_id=f"{run_id_prefix}:{run_id_suffix}",
            label=label,
            timestamp=run_timestamp,
            files=files,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs


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
            parsed_date = datetime.strptime(dirname, '%Y%m%d')
            label = parsed_date.strftime('%Y-%m-%d')
            # Prefer the folder's own date over raw mtime for sort ordering:
            # two date folders created back-to-back (as this function's own
            # "sorts newest first" test does, and as a real backfill/restore
            # could too) can land on identical mtimes at this filesystem's
            # granularity -- confirmed directly (Task 4 hit the same failure
            # mode with clustered files). The folder name is the actual
            # identity of the run; mtime is only a fallback for the
            # unparseable case below.
            run_timestamp = parsed_date.timestamp()
        except ValueError:
            label = dirname
            run_timestamp = max(f.modified for f in files)

        runs.append(RunInfo(
            suite=suite,
            run_id=f"{run_id_prefix}:{dirname}",
            label=label,
            timestamp=run_timestamp,
            files=files,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs


import csv as _csv

SUITE_LABELS = {
    'options': 'Options_Suite',
    'vol': 'Vol_Suite',
    'var': 'VaR_Tools_Simulations',
    'sentiment': 'Market Signals',
    'swaps': 'Swaps',
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
        # Cap before clustering (not after) -- this pile has no retention policy
        # and grows unboundedly (see design doc's Known Limitations), so without
        # this the mtime-sort/stat/cluster work below would run over every file
        # on disk on every page load instead of just the newest candidates.
        paths.sort(key=os.path.getmtime, reverse=True)
        paths = paths[:_MAX_CANDIDATES]
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
                     for s in ('options', 'var', 'sentiment', 'vol', 'swaps')}
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
