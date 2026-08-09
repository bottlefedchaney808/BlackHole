# Dashboard Output Tab Redesign -- Design Spec

## Problem

The dashboard's "Suite output" tab (`GET /suites/{suite}`, `dashboard/templates/suite.html`) is close to useless today:

- For each of the 4 suites (options/vol/var/sentiment) it globs a fixed, hardcoded list of file patterns and shows **only the single newest matching file**, across the entire suite directory tree.
- It can only render CSV and JSON. Every suite's richest output -- Options_Suite's PDF/CSV comparison reports, Vol_Suite's PNG charts and PDF report -- is invisible here. The only way to see it today is to go find the file on disk yourself or open a terminal.
- There is no concept of a "run" at all. One glob just returns whatever file on disk happens to be newest, which can be from a totally unrelated invocation, and there is no way to look at an older run's output once a newer one exists.
- It doesn't distinguish between runs triggered through the dashboard/orchestrator and runs a human kicked off directly (e.g. Options_Suite's interactive menu) -- the newest-file glob happens to catch both, but shows only one, arbitrarily.

## Goals

1. Show what a run actually produced -- every file, not just the newest one, rendered appropriately for its type (JSON, CSV, PNG, PDF, or a plain download).
2. Separate output by suite, and within a suite, by run -- a dropdown of the last 10 runs, defaulting to the most recent.
3. Work for **any run**, regardless of whether it was triggered through the dashboard or a human ran a suite's own entrypoint directly.
4. Visually modernize the tab (and, since it shares CSS with every other page, the whole dashboard) -- currently flat/utilitarian, moving to a dark, bold, high-contrast look with gradient accent pills (see "Visual Design" below; validated against mockups, see the brainstorming session's screens under `.superpowers/brainstorm/` if still present).
5. Three specific additions identified during the codebase audit for this spec (see "Additional Features"):
   - A live-run banner when a run is currently in progress for that suite.
   - A cross-suite view for unified orchestrator runs (one run, four suites' worth of output).
   - A link from a run's `quant_summary.json` into the existing Quant Console summary endpoint.

## Non-goals

- **Tools/registry output** (Direction's 6 tools, the backtest tool, the options-strategy tool) is explicitly out of scope. Those are synchronous, in-memory JSON results with no run/file history today; giving them one is a different, larger problem with a different data model. They keep their existing `/tools` page.
- No new database schema. `orchestrator_runs` is not touched. See "Architecture" for why.
- No pagination / "unlimited history" -- the dropdown shows the last 10 runs per suite, full stop.
- No changes to how any suite actually produces its output files -- this is a read-only viewer over what already gets written to disk.

## Architecture: filesystem-based discovery, no DB changes

**Decision:** runs are discovered by scanning the filesystem fresh on every page load, not by reading (or adding to) `orchestrator_runs`.

**Why not the DB:** `orchestrator_runs` does not durably store `output_dir` for single-suite ("suite-kind") runs today -- confirmed by reading `dashboard/app.py::_execute_run`/`_finish_run_row` and migration `004_add_quant_alerts.sql`'s own comment about this gap. It's recorded in-memory in `_RUNS` only, which is lost on restart. A migration could patch that, but it wouldn't solve the actual problem: a meaningful fraction of what needs to show up here -- Options_Suite's interactively-generated `comparison_*.csv`/`.pdf`, standalone `Vol_Suite/outputs/<timestamp>/` runs -- **was never triggered through the dashboard and has no DB row at all.**

**Why the filesystem works fine instead:** every suite's own result JSON is already self-describing and schema-validated (`shared/schemas.py`): `options_result.json` carries `suite`/`status`/`ticker`/`method`/`timestamp`; `var_result.json` carries `suite`/`status`/`module`/`timestamp`. Reading these directly gives the same rich labels a DB join would, for every run regardless of how it was launched.

**Cost:** a scan happens on every page load instead of one indexed query. Each per-suite discovery function caps itself to the newest ~40 candidate directories/files before doing any expensive work (reading marker JSON, clustering), so this stays in the tens-of-milliseconds range for a single-user localhost tool.

## Data model

Two dataclasses, defined in a new module `dashboard/output_runs.py`:

```python
@dataclass(frozen=True)
class RunFile:
    abs_path: str
    rel_path: str      # relative to repo ROOT; used for display and as the asset-route key
    kind: str           # 'json' | 'csv' | 'png' | 'pdf' | 'other'
    size_bytes: int
    modified: float     # epoch seconds

@dataclass(frozen=True)
class RunInfo:
    suite: str           # 'options' | 'vol' | 'var' | 'sentiment' | 'unified'
    run_id: str          # stable, URL-safe, e.g. "orch:20260729T055306Z" or "cmp:20260728_093751"
    label: str           # human-readable, e.g. "AAPL - CRR - ok" or "SPY 2026-07-16 14:24"
    timestamp: float     # epoch seconds, for sorting newest-first
    files: List[RunFile]
    sibling_suites: List[str]  # other suites with files in the same directory (unified runs); [] otherwise
```

`run_id` is namespaced by source (`orch:`, `vsout:`, `cmp:`, `loose:`, `pack:`) so the same discovery logic never produces colliding IDs across different sources for the same suite.

## Per-suite discovery strategies

All four funnel into the same `RunInfo`/`RunFile` shapes; only *how a directory of files becomes a list of runs* differs.

### Shared `orchestrator_output/<run_id>/` directories (all 4 suites)

Used whenever a suite is triggered through the dashboard/orchestrator. One directory can hold multiple suites' files at once (a unified run writes all four). Ownership of a file within such a directory is inferred by a naming filter, since there is no per-file suite tag:

| Suite | Files claimed |
|---|---|
| options | `options_result.json`, `suite_context_options.json` |
| var | `var_result.json`, `suite_context_var.json` |
| sentiment | `sentiment_result.json`, `suite_context_sentiment.json` |
| vol | everything else *except* the bare `suite_context.json` (Vol_Suite writes its rich CSV/PNG/PDF assets straight into the shared `output_dir` via the `VS_OUTPUT_DIR` env var; it has no marker file of its own to key off) |

The bare, unsuffixed `suite_context.json` (the merged cross-suite handoff artifact) is excluded from every suite's per-file list -- it's plumbing, not a result to review.

A directory whose files are claimed by 2+ suites is a **unified run** (see "Additional Features" below).

### Options_Suite: timestamp-proximity clustering

`comparison_<YYYYMMDD_HHMMSS>.csv`/`.pdf` land flat, ungrouped, directly in `Options_Suite/` -- written only by a human going through Options_Suite's interactive menu (`main.py`'s `save_comparison_csv`/`save_comparison_pdf`, gated behind an `input()` prompt; confirmed this code path is unreachable from `--context`/headless mode). There is no directory or JSON marker to group by, so files are clustered by the timestamp embedded in their own filename:

```python
_TIMESTAMP_RE = re.compile(r'(\d{8}_\d{6})')

def _extract_timestamp(filename: str) -> Optional[datetime]:
    m = _TIMESTAMP_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), '%Y%m%d_%H%M%S')
    except ValueError:
        return None

def cluster_by_timestamp(paths: List[str], window_seconds: int = 10) -> List[List[str]]:
    """Groups files whose embedded timestamps are within `window_seconds` of
    a neighbor, sorted first -- chains transitively (A-B-C each 5s apart
    cluster together even though A and C are 10s apart). Files with no
    parseable timestamp each become their own singleton cluster. Needed
    because save_comparison_csv/save_comparison_pdf each independently call
    datetime.now() -- a CSV+PDF pair saved together via the 'both' choice can
    differ by a second or two, so exact-string matching is not reliable."""
```

A 10-second window comfortably covers the CSV/PDF pair case without accidentally merging two genuinely separate manual runs (a human re-running the interactive menu takes much longer than that between saves).

### Vol_Suite: directory-per-run (mostly) + one legacy loose bucket

`Vol_Suite/outputs/<timestamp>/` -- each standalone run already lands in its own freshly-named subdirectory; the directory itself is the run boundary, no heuristic needed.

`Vol_Suite/vs_output/` -- a legacy flat dump with multiple tickers' files mixed together, no per-run separation at all. Treated as **one single "loose files, ungrouped" bucket** (`run_id="loose:vs_output"`) rather than attempting to reconstruct fake run boundaries that don't actually exist in the data.

### sentiment-scanner: date-bucketed

`sentiment-scanner/data/exports/highlighted_ticker_packs/<YYYYMMDD>/` -- one bucket per calendar day. Since sentiment-scanner loops continuously (`SCAN_INTERVAL_MINUTES`), this is a deliberate simplification: multiple scan cycles in one day collapse into one "run" bucket. Documented as such, not presented as literal 1:1 run correspondence.

### VaR_Tools_Simulations

No standalone output directory exists on disk today -- confirmed by search, `var_engine/*.py` write CSVs/plots in code but no such files currently exist in this environment. VaR's runs are, in practice, only ever the shared `orchestrator_output/<run_id>/` case above (`var_result.json` + whatever `suite_context_var.json` carries). If/when VaR starts writing a native output directory, it slots into the same rundir-per-directory strategy Vol_Suite already uses -- no new mechanism needed.

## Rendering

Each file in the selected run renders according to its `kind`:

| kind | rendering |
|---|---|
| `json` | existing pairs/table view (kept verbatim from the current implementation's `_read_json_view`, moved into `output_runs.py`) |
| `csv` | existing table view (kept verbatim, `_read_csv_table`) |
| `png` | inline `<img>` |
| `pdf` | `<embed>`/`<iframe>` (native browser PDF rendering) + "open in new tab" link |
| `other` | plain download link with file size |

A file named exactly `quant_summary.json` gets a special label ("Quant Summary") and a link to the existing `GET /runs/{run_id}/summary` endpoint instead of being labeled by its filename -- see "Quant Console cross-link" below.

### New route: asset serving

```
GET /suites/{suite}/asset?run_id=<run_id>&rel_path=<rel_path>
```

Serves file bytes (`FileResponse`) for images/PDFs/downloads. This reads arbitrary paths off disk, so it must never trust the query string directly: it re-runs discovery for `run_id`, then only serves `rel_path` if it is an **exact match** against a `RunFile.rel_path` that discovery itself already enumerated server-side for that run. There is no filesystem access outside what the scan already surfaced -- no path-joining of user input, no traversal surface.

### Run picker

`GET /suites/{suite}` gains an optional `run_id` query param (defaults to the newest run). A `<select>` inside a `<form method="get">` that submits on change (`onchange="this.form.submit()"`) lists the last 10 runs, each labeled from its own source (Section "Per-suite discovery strategies"). No JS framework, no new client-side state -- matches this dashboard's existing "everything inline, minimal vanilla JS, offline-first" convention (`base.html`'s own comment: "Everything inline -- this runs offline on localhost, no CDN anything").

## Additional features (identified during the codebase audit, approved by operator)

### 1. Live-run banner

Reuses **existing** infrastructure -- no new backend endpoint:
- `_RUNS` (in-memory dict, already tracks `kind`, `status`, `started_at` per run)
- `GET /runs/{run_id}` (already polled client-side by Quant Console, 3-4s interval -- matching that exact convention)

When `_RUNS` has an entry with `status in ('queued', 'running')` and `kind` matching the current suite (or `kind == 'unified'`), the page renders a banner at the top and polls `GET /runs/{run_id}` every 3s; on completion it reloads the page so the finished run appears in the dropdown automatically.

**Explicitly not used:** the existing `/suites/{suite}/live` WebSocket log-tail. Confirmed by reading its own code comments (`dashboard/app.py` lines ~500-522) that nothing in this repo currently writes to the file it tails -- it's scaffolding for a future continuous-process launcher (sentiment-scanner loop mode) that doesn't exist yet. Building a banner on top of it would decorate a feature that doesn't work end-to-end.

### 2. Unified-run cross-suite view

A 5th pill next to the 4 suite tabs: **Unified**. Its "suite key" is `'unified'`; its discovery pulls `orchestrator_output/<run_id>/` directories claimed by 2+ suites (per the ownership filter above) and renders all of them in one page, grouped into a subsection per suite -- reusing the exact same per-file-kind renderer, just with an extra grouping level.

Any per-suite run that has `sibling_suites` populated (i.e. it's part of a unified run) shows a small note/link: "Part of a unified run -- also produced: vol, var, sentiment [view all >]" pointing at this view.

### 3. Quant Console cross-link

No new Quant Console UI. `quant_summary.json` (written by `_write_quant_summary` at the end of every dashboard-triggered run, into the same `output_dir` as everything else) already shows up as a file in that run's list. It gets a distinguishing label ("Quant Summary") instead of its raw filename, and a link built from the `run_id` field embedded *inside the file itself* (a different ID space than the filesystem-derived `run_id` this feature otherwise uses) to the existing `GET /runs/{run_id}/summary` endpoint -- a raw-JSON link, not a new rendered page. Building a proper deep-linkable history view inside Quant Console itself is out of scope here.

## Visual design

Validated interactively via the brainstorming visual companion (three initial directions shown, then iterated). Converged on:

- **Base:** dark-first, bold, high-contrast -- squared-off blocks, heavier type weight, reads like a trading terminal rather than a generic admin panel.
- **Suite tabs:** rounded gradient pills (not the bold direction's original squared blocks) -- magenta-to-violet gradient (`linear-gradient(135deg, #f472b6, #a855f7)`) for the active suite, muted flat pill for inactive ones.
- **Per-file-type accent:** a colored left border on each file card, consistent across the whole tab -- pink/magenta family for PDF, cyan/blue family for images, green/lime family for CSV, carrying the same accent language as the suite pills.
- Applied via `base.html`'s shared `<style>` block (CSS custom properties already exist there for light/dark; this becomes a dark-first redesign of those tokens), so every other page (Overview, Quant Console, Swap trades, Tools) inherits the lift automatically through the same `.panel`/`.pill`/`.card`/button classes, without rewriting their content.

Exact mockup markup from the brainstorming session is preserved under `.superpowers/brainstorm/` (gitignored) for reference during implementation, but is not authoritative pixel-for-pixel -- it establishes direction and palette, not final CSS.

## Testing approach

`dashboard/output_runs.py` is designed to be tested without touching a real dashboard instance or any suite's real output:
- `cluster_by_timestamp` / `_extract_timestamp`: pure functions, tested directly with synthetic filename lists.
- Per-suite discovery functions: tested against `tmp_path`-constructed fake directory trees (matching the existing `dashboard/tests/` convention of `TestClient`-free, filesystem-fixture-based unit tests).
- The new/changed routes (`/suites/{suite}`, `/suites/{suite}/asset`): tested via `fastapi.testclient.TestClient` against `dashboard.app`, matching `dashboard/tests/test_dispatch.py`'s established pattern -- including a test that a tampered `rel_path` (one not actually part of the named run) 404s rather than serving anything.

`dashboard/tests/` is not currently in the root `pyproject.toml` `testpaths` list (confirmed separately) -- new tests here are run via `pytest dashboard/tests/` explicitly, matching how the existing dashboard tests already have to be run today. Fixing that gap is out of scope for this feature.

## Known limitations (documented, not fixed here)

- Runs beyond the newest ~40 candidate directories/files per suite are invisible (performance cap). In practice this only matters for suites producing very high run volume with a long tail of "other suites only" unified-run directories in between.
- No cleanup/retention story for Options_Suite's ever-growing pile of `comparison_*.csv`/`.pdf` files, or for `Vol_Suite/outputs/`. This tab surfaces what exists; it doesn't manage disk usage. Worth a future look (mirrors the `swaps.db` retention theme already tracked in `docs/PROJECT_AUDIT_AND_SPEC.md`), not part of this spec.
- sentiment-scanner's one-bucket-per-day simplification means multiple scan cycles in a day are indistinguishable as separate "runs" here.
