"""context_loader.py

Discovers and loads suite_context.json handoff objects produced by any of
the four sibling suites (Vol_Suite, Options_Suite, VaR_Tools_Simulations,
sentiment-scanner) or by the root-level orchestrator.py, so a Tool can pick
"whatever run I care about" instead of being wired to exactly one suite's
output directory.

Deliberately does NOT reimplement the context schema or its validation --
suite_context.py (in Vol_Suite/) already owns that (build_suite_context /
validate_suite_context / read_suite_context), and duplicating it here would
be exactly the kind of drift the original module's docstring warns about
(a malformed context should fail loudly and in one place). This module adds
sys.path so it can import that module directly and calls straight through
to it.

Known output locations (confirmed against real run artifacts on disk, not
guessed):
  - FinancialDevelopment/orchestrator_output/<run_id>/suite_context.json
    (root orchestrator.py, when it builds output_dir itself)
  - FinancialDevelopment/Vol_Suite/outputs/<run_id>/suite_context.json
    (Vol_Suite's own unified flow)
Options_Suite, VaR_Tools_Simulations, and sentiment-scanner are currently
CONSUMERS of a context built elsewhere (they read --context, write their
own <suite>_result.json into the SAME output_dir) rather than producers of
a fresh suite_context.json in their own tree. Their suite roots are still
scanned below (via suite_context.py's DEFAULT_*_ROOT paths) under a few
plausible output-directory names, so the day one of them starts a run of
its own and writes a suite_context.json there, this loader picks it up
without needing a code change.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# --- wire up import of Vol_Suite/suite_context.py -------------------------
_TOOLS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

import suite_context as sc  # noqa: E402  (validated, reused, not reimplemented)

# Re-exported so callers of context_loader don't also need to know where
# suite_context.py lives.
validate_suite_context = sc.validate_suite_context
read_suite_context = sc.read_suite_context


# --- suite roots (same resolution suite_context.py itself uses) ----------
OPTIONS_SUITE_ROOT = Path(sc.DEFAULT_OPTIONS_SUITE_ROOT)
VAR_SUITE_ROOT = Path(sc.DEFAULT_VAR_SUITE_ROOT)
SENTIMENT_SUITE_ROOT = Path(sc.DEFAULT_SENTIMENT_SUITE_ROOT)

# Candidate output-directory names to check under each suite root, and
# under the repo root for the shared orchestrator. "outputs" is the
# confirmed real name (Vol_Suite/outputs, orchestrator_output); "output"
# and "out" are included defensively in case a suite adopts a different
# convention later.
_OUTPUT_DIR_NAMES = ("outputs", "output", "out")

SUITE_OUTPUT_ROOTS: Dict[str, List[Path]] = {
    "Vol_Suite": [_VOL_SUITE_ROOT / name for name in _OUTPUT_DIR_NAMES],
    "Options_Suite": [OPTIONS_SUITE_ROOT / name for name in _OUTPUT_DIR_NAMES],
    "VaR_Tools_Simulations": [VAR_SUITE_ROOT / name for name in _OUTPUT_DIR_NAMES],
    "sentiment-scanner": [SENTIMENT_SUITE_ROOT / name for name in _OUTPUT_DIR_NAMES],
    "orchestrator": [_REPO_ROOT / "orchestrator_output"],
}


def _iter_context_files():
    """Yield (suite_name, path) for every suite_context.json found under
    any known output root -- either directly in that root, or one level
    down in a per-run subdirectory (the real layout: outputs/<run_id>/
    suite_context.json).
    """
    seen = set()
    for suite_name, roots in SUITE_OUTPUT_ROOTS.items():
        for root in roots:
            if not root.is_dir():
                continue

            direct = root / "suite_context.json"
            if direct.is_file() and direct.resolve() not in seen:
                seen.add(direct.resolve())
                yield suite_name, direct

            try:
                children = sorted(root.iterdir())
            except OSError:
                continue
            for child in children:
                if not child.is_dir():
                    continue
                candidate = child / "suite_context.json"
                if candidate.is_file() and candidate.resolve() not in seen:
                    seen.add(candidate.resolve())
                    yield suite_name, candidate


def _sort_key(summary: Dict[str, Any]) -> str:
    """Sort key for newest-first ordering. Prefers the context's own
    created_at_utc (ISO 8601, so it sorts correctly as a string); falls
    back to the file's mtime (also rendered as an ISO string) for a
    context that -- despite passing validation -- somehow carries a
    non-string timestamp, so one bad run can't crash the whole listing.
    """
    ts = summary.get("created_at_utc")
    if isinstance(ts, str) and ts.strip():
        return ts
    return "0000-00-00T00:00:00Z"


def list_available_contexts() -> List[Dict[str, Any]]:
    """Scan all known suite output directories for suite_context.json
    files and return lightweight summaries, newest-first.

    Each summary dict has:
      run_id, ticker, created_at_utc, output_dir, path, suite, valid
    (path is where the file actually was found on disk; output_dir is
    the value recorded INSIDE the context, which may point at a
    different machine/OS than the one this is running on -- see
    load_context's docstring.)

    A context file that exists but fails validation is still listed
    (valid=False, with an "error" key) rather than silently dropped --
    an operator staring at a shorter-than-expected list with no
    explanation is worse than one extra row that says why it's unusable.
    """
    summaries: List[Dict[str, Any]] = []
    for suite_name, path in _iter_context_files():
        try:
            context = read_suite_context(str(path))
        except Exception as e:  # noqa: BLE001 -- surface any failure, don't drop the row
            try:
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
            except OSError:
                mtime = None
            summaries.append({
                "run_id": None,
                "ticker": None,
                "created_at_utc": mtime,
                "output_dir": None,
                "path": str(path),
                "suite": suite_name,
                "valid": False,
                "error": str(e),
            })
            continue

        summaries.append({
            "run_id": context.get("run_id"),
            "ticker": (context.get("focus") or {}).get("ticker"),
            "created_at_utc": context.get("created_at_utc"),
            "output_dir": context.get("output_dir"),
            "path": str(path),
            "suite": suite_name,
            "valid": True,
        })

    summaries.sort(key=_sort_key, reverse=True)
    return summaries


def load_context(path: str) -> Dict[str, Any]:
    """Load and validate the full suite_context.json at `path`.

    Thin wrapper over suite_context.read_suite_context -- kept here (rather
    than having every Tool import suite_context directly) so a Tool only
    needs to know about context_loader, and so the sys.path wiring above
    only has to happen in one place.

    Raises FileNotFoundError / ValueError exactly as read_suite_context
    does; callers (e.g. a Tool's run()) should let that propagate or catch
    it explicitly -- a silently-swallowed invalid context is precisely the
    failure mode suite_context.py's module docstring was written to avoid.
    """
    return read_suite_context(path)
