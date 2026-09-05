"""shared/run_store.py -- read API for module archive + run directories.

This module provides read-only operations over the module archive DB and run
output directories. It's the canonical way for dashboards/tools/agents to query
past runs.

API:
  - last_run(ticker=None, suite=None) -> latest archive row(s) enriched with
    manifest when the run dir exists
  - runs_since(iso_ts) -> archive rows with timestamp >= ts
  - artifacts(run_id) -> resolved absolute paths via resolve_stored (None filtered)

All read operations are never-raises (graceful degradation on DB/read failures).
Full docstrings on each function.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.artifact_paths import resolve_stored
from shared.module_archive import get_default_index

logger = logging.getLogger(__name__)


def last_run(
    *,
    ticker: str | None = None,
    suite: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest archive row, optionally filtered by ticker/suite.

    If a corresponding run directory exists (outputs/<run_id>/), the result
    is enriched with the run_manifest.json contents under a 'manifest' key.

    Never raises -- on DB errors or missing manifest file, returns None or
    the row without enrichment.

    Args:
        ticker: Optional filter by ticker (case-insensitive match)
        suite: Optional filter by suite name

    Returns:
        Archive row dict enriched with 'manifest' key if run dir exists,
        or None if no matching row found or on error.
    """
    try:
        index = get_default_index()
        # Query without suite filter (not supported by archive API)
        rows = index.query(
            ticker=ticker,
            limit=50,  # Fetch more, filter locally
        )
        if not rows:
            return None

        # Filter by suite if provided (suite is not a query parameter)
        if suite is not None:
            rows = [r for r in rows if r.get("suite") == suite]

        if not rows:
            return None

        row = rows[0]
        run_id = row.get("run_id")
        if not run_id:
            return row

        # Look for run_manifest.json in outputs/<run_id>/
        repo_root = Path(__file__).resolve().parent.parent
        manifest_path = repo_root / "outputs" / run_id / "run_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
                row = dict(row)  # Make a copy to enrich
                row["manifest"] = manifest
            except Exception:
                logger.warning(
                    "run_store.last_run: could not load manifest for run_id=%s",
                    run_id,
                    exc_info=True,
                )

        return row
    except Exception:
        logger.warning(
            "run_store.last_run failed (ticker=%r, suite=%r)",
            ticker,
            suite,
            exc_info=True,
        )
        return None


def runs_since(iso_ts: str) -> list[dict[str, Any]]:
    """Return archive rows with timestamp >= iso_ts.

    Never raises -- on DB errors, returns empty list.

    Args:
        iso_ts: ISO 8601 timestamp string (e.g., '2026-09-05T12:00:00+00:00')

    Returns:
        List of archive row dicts, newest-first, or empty list on error.
    """
    try:
        index = get_default_index()
        # Parse the ISO timestamp to a datetime for the query
        try:
            since_dt = datetime.fromisoformat(iso_ts)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
        except ValueError:
            logger.warning(
                "run_store.runs_since: invalid iso_ts=%r, returning empty list",
                iso_ts,
            )
            return []

        rows = index.query(
            since=since_dt,
            limit=50,
        )
        return rows
    except Exception:
        logger.warning("run_store.runs_since failed (iso_ts=%r)", iso_ts, exc_info=True)
        return []


def artifacts(run_id: str) -> list[Path | None]:
    """Return resolved absolute paths for all artifacts of a run.

    Looks up artifacts from module_archive.db for the given run_id, then
    resolves each path via resolve_stored() (handles relative, absolute,
    Windows-style paths). None entries (missing files) are filtered out.

    Never raises -- on DB errors, returns empty list.

    Args:
        run_id: The run identifier (e.g., '20260905T120000Z-abcd')

    Returns:
        List of resolved Path objects (None entries filtered), or empty list
        on error.
    """
    try:
        index = get_default_index()
        # Query without run_id filter (not supported by archive API)
        rows = index.query(limit=50)
        if not rows:
            return []

        # Filter by run_id in Python
        rows = [r for r in rows if r.get("run_id") == run_id]

        resolved: list[Path | None] = []
        for row in rows:
            for artifact in row.get("artifacts", []):
                path_str = artifact.get("path")
                if not path_str:
                    continue
                resolved_path = resolve_stored(path_str)
                resolved.append(resolved_path)

        # Filter out None entries (missing files)
        return [p for p in resolved if p is not None]
    except Exception:
        logger.warning("run_store.artifacts failed (run_id=%r)", run_id, exc_info=True)
        return []
