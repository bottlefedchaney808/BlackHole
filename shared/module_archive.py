"""module_archive.py -- automatic archiving system for module-registry runs
(Phase 6 of the Modularization Overhaul).

Every `shared.module_registry.ModuleSpec.run()` call, no matter which of the
three launch paths triggered it (orchestrator/dashboard via
`orchestrator.run_selected_modules`, or a suite's standalone `cli_entry`
`__main__` block), gets one archived row here: what ran, for which
ticker/expiry, what it produced (artifact paths), and its summary metrics.
This is metadata-mapping only -- the module itself already wrote its own
charts/JSON/CSV; `record()` just notes where.

**Dedicated DB, not `swaps.db`** (see the source plan's Phase 6, "Post-
Approval Update 2, 2026-09-02" correction, and CLAUDE.md's `swaps.db`
single-writer convention): `setup_db.py`'s `MIGRATIONS_DIR`/`migrate()`
hardcode `swaps.db` as their target, so `migrations/*.sql` is exclusively
that DB's schema-change mechanism -- a file placed there would create this
module's table INSIDE `swaps.db`, silently undoing the dedicated-DB
decision. This file owns its own schema directly (`CREATE TABLE IF NOT
EXISTS` / `CREATE INDEX IF NOT EXISTS`, run once per `ArchiveIndex`
construction), the same fresh-per-call-table pattern
`dashboard/widget_cache.py::WidgetCache` already uses for its own SQLite
table.

**Why a dedicated file matters for real** (not just style): a standalone
`cli_entry` module run is a separate OS process from a running dashboard.
CLAUDE.md documents `swaps.db`'s real, observed cross-process "database is
locked" failure mode when two OS processes race a write against it. Putting
`module_archive` in its own file (`module_archive.db` by default, see
`MODULE_ARCHIVE_DB_PATH` below) sidesteps that convention entirely rather
than risking a collision with it. WAL mode (via
`shared.connection_pool.ConnectionPool`, which already enables it) is what
actually makes concurrent cross-process writes to *this* file safe --
verified by this module's own multi-process test
(`tests/test_module_archive.py::test_concurrent_multiprocess_writes`).

**Never raises.** `record()`'s contract is the single most important
correctness property in this module (per the plan's explicit requirement):
a failure to archive must never fail the module run that produced the
result being archived. The entire body is wrapped in `try/except Exception`
-- a bad DB path, a non-JSON-serializable metrics dict, a pool timeout, all
log a warning via the standard `logging` module and return, never propagate.

**Design choices made here** (the brief left these to this task's judgment):
- `ArchiveIndex` is a class owning its own `ConnectionPool` instance
  (per-instance, NOT the `swaps.db` singleton reached via
  `connection_pool.init_pool()`/`get_pool()` -- constructing a second,
  separate `ConnectionPool` here is exactly what the brief calls for). A
  lazily-constructed module-level singleton backs the `record()`/`query()`
  free functions every call site actually uses, so callers don't need to
  thread an `ArchiveIndex` instance through `orchestrator.py`/`cli_entry`
  blocks -- `get_default_index()` exposes that singleton for tests that
  want to point it at a temp DB.
- `triggered_by` values: `"cli"` for a suite's standalone `cli_entry`
  `__main__` block (a real separate OS process bypassing
  `run_selected_modules` entirely), `"orchestrator"` for every call made
  from inside `orchestrator.py::run_selected_modules` (`_archive_module_result`)
  -- which covers BOTH orchestrator.py's own `--modules` CLI flag AND
  `dashboard/app.py::_execute_run`'s call into the very same function
  (traced directly: `_execute_run` calls `orchestrator.run_selected_modules`
  when `modules is not None`, so `_archive_module_result` already runs once
  per module for the dashboard path -- there is no second, dashboard-only
  hook to add, and adding one would double-archive). `"dashboard"` is kept
  as a valid value in the schema/validation (per the brief's documented
  three) for a future call site that bypasses `run_selected_modules`, but
  no current call site uses it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.artifact_paths import to_rel
from shared.connection_pool import ConnectionPool

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: MODULE_ARCHIVE_DB_PATH env var overrides (mirrors SWAPS_DB_PATH's own
#: convention -- see orchestrator.py/db_loader.py/setup_db.py), e.g. for a
#: mounted volume or an isolated test run; otherwise a sibling file next to
#: swaps.db at the repo root.
MODULE_ARCHIVE_DB_PATH = os.environ.get("MODULE_ARCHIVE_DB_PATH") or os.path.join(
    str(_REPO_ROOT), "module_archive.db"
)

_VALID_TRIGGERED_BY = ("cli", "orchestrator", "dashboard")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS module_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_slug TEXT NOT NULL,
    suite TEXT NOT NULL,
    ticker TEXT,
    expiry TEXT,
    run_id TEXT,
    triggered_by TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    artifact_paths_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL
)
"""

# Two documented query patterns: by (ticker, expiry, module_slug), and by
# (module_slug, date range). Both benefit from newest-first ordering, so
# `timestamp DESC` is included in both indexes rather than relying on a
# separate sort-only index.
_CREATE_INDEX_TICKER_SQL = """
CREATE INDEX IF NOT EXISTS idx_module_archive_ticker_expiry_slug
ON module_archive (ticker, expiry, module_slug, timestamp DESC)
"""

_CREATE_INDEX_SLUG_TIME_SQL = """
CREATE INDEX IF NOT EXISTS idx_module_archive_slug_timestamp
ON module_archive (module_slug, timestamp DESC)
"""


@dataclass(frozen=True)
class ArchiveRow:
    """One archived module run. `query()` returns these (as dicts, via
    `as_dict()`, matching the brief's `-> list[dict]` signature) rather than
    a bare sqlite3.Row so callers get parsed `artifacts`/`metrics` back."""

    id: int
    module_slug: str
    suite: str
    ticker: str | None
    expiry: str | None
    run_id: str | None
    triggered_by: str
    timestamp: str
    artifacts: list[dict[str, str]]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "module_slug": self.module_slug,
            "suite": self.suite,
            "ticker": self.ticker,
            "expiry": self.expiry,
            "run_id": self.run_id,
            "triggered_by": self.triggered_by,
            "timestamp": self.timestamp,
            "artifacts": self.artifacts,
            "metrics": self.metrics,
        }


class ArchiveIndex:
    """SQLite-backed archive of module-registry runs, in a dedicated DB file
    (never `swaps.db`). Owns its own `ConnectionPool` instance -- NOT
    `shared.connection_pool`'s `swaps.db` singleton (`init_pool()`/
    `get_pool()` are never called here, per the brief's explicit
    instruction not to risk clobbering/conflicting with that singleton).
    """

    def __init__(self, db_path: str | None = None, *, pool_size: int = 5) -> None:
        self.db_path = str(db_path or MODULE_ARCHIVE_DB_PATH)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._pool = ConnectionPool(self.db_path, pool_size=pool_size)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._pool.get_connection_context() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_TICKER_SQL)
            conn.execute(_CREATE_INDEX_SLUG_TIME_SQL)
            conn.commit()

    def close(self) -> None:
        self._pool.close()

    # -- write ---------------------------------------------------------

    def record(
        self,
        module_result: Any,
        module_spec: Any,
        context: dict[str, Any],
        *,
        triggered_by: str,
    ) -> None:
        """Write one archive row for `module_result`. Never raises -- a
        failure to archive logs a warning and returns, per the plan's
        explicit requirement that archiving must never fail the module run
        that produced the result being archived.
        """
        try:
            self._record_unsafe(module_result, module_spec, context, triggered_by)
        except Exception:
            logger.warning(
                "module_archive.record failed for module_slug=%r triggered_by=%r "
                "(archiving is best-effort; the module run itself is unaffected)",
                getattr(module_spec, "slug", None),
                triggered_by,
                exc_info=True,
            )

    def _record_unsafe(
        self,
        module_result: Any,
        module_spec: Any,
        context: dict[str, Any],
        triggered_by: str,
    ) -> None:
        if triggered_by not in _VALID_TRIGGERED_BY:
            raise ValueError(
                f"triggered_by must be one of {_VALID_TRIGGERED_BY}, got {triggered_by!r}"
            )

        module_slug = str(module_spec.slug)
        suite = str(module_spec.suite)
        key_shape = getattr(
            getattr(module_spec, "archive", None), "key_shape", "global"
        )

        ticker: str | None = None
        expiry: str | None = None
        if key_shape in ("ticker_expiry", "ticker_only"):
            ticker = _resolve_ticker(context)
            if key_shape == "ticker_expiry":
                expiry = _resolve_expiry(context)

        run_id = context.get("run_id")
        run_id = str(run_id) if run_id not in (None, "") else None

        artifacts = []
        for a in (module_result.artifacts or []):
            try:
                path_rel = to_rel(str(a.path))
            except Exception:
                # If to_rel fails, store original path (graceful degradation)
                path_rel = str(a.path)
            artifacts.append({"path": path_rel, "kind": str(a.kind)})
        metrics = dict(module_result.metrics or {})

        # No `default=str` fallback here on purpose: a metrics dict that
        # cannot be JSON-serialized as-is is exactly the failure this
        # method's caller (record()) is contracted to catch, log, and
        # swallow -- see test_record_never_raises_on_unserializable_metrics.
        artifact_paths_json = json.dumps(artifacts)
        metrics_json = json.dumps(metrics)

        timestamp = datetime.now(UTC).isoformat()

        with self._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_slug,
                    suite,
                    ticker,
                    expiry,
                    run_id,
                    triggered_by,
                    timestamp,
                    artifact_paths_json,
                    metrics_json,
                ),
            )
            conn.commit()

    # -- read ------------------------------------------------------------

    def query(
        self,
        *,
        ticker: str | None = None,
        expiry: str | None = None,
        module_slug: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Supports both documented query patterns: by
        `(ticker, expiry, module_slug)` and by `(module_slug, date range)`.
        Rows ordered newest-first. Returns plain dicts (via
        `ArchiveRow.as_dict()`), not raw sqlite3.Row objects.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker)
        if expiry is not None:
            clauses.append("expiry = ?")
            params.append(expiry)
        if module_slug is not None:
            clauses.append("module_slug = ?")
            params.append(module_slug)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(_iso(since))
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(_iso(until))

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, module_slug, suite, ticker, expiry, run_id, triggered_by, "
            "timestamp, artifact_paths_json, metrics_json FROM module_archive "
            f"{where} ORDER BY timestamp DESC LIMIT ?"
        )
        params.append(int(limit))

        with self._pool.get_connection_context() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [_row_to_archive_row(r).as_dict() for r in rows]


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _row_to_archive_row(row: Any) -> ArchiveRow:
    (
        id_,
        module_slug,
        suite,
        ticker,
        expiry,
        run_id,
        triggered_by,
        timestamp,
        artifact_paths_json,
        metrics_json,
    ) = row
    return ArchiveRow(
        id=id_,
        module_slug=module_slug,
        suite=suite,
        ticker=ticker,
        expiry=expiry,
        run_id=run_id,
        triggered_by=triggered_by,
        timestamp=timestamp,
        artifacts=json.loads(artifact_paths_json),
        metrics=json.loads(metrics_json),
    )


def _resolve_ticker(context: dict[str, Any]) -> str | None:
    """Best-effort ticker extraction -- mirrors
    `Vol_Suite/module_registry.py::_resolve_ticker`'s convention, but never
    raises (this feeds `record()`'s never-raise contract)."""
    focus = context.get("focus") or {}
    ticker = str(context.get("ticker") or focus.get("ticker") or "").strip().upper()
    return ticker or None


def _resolve_expiry(context: dict[str, Any]) -> str | None:
    focus = context.get("focus") or {}
    expiry = str(context.get("expiry") or focus.get("expiry") or "").strip()
    return expiry or None


# ---------------------------------------------------------------------------
# Module-level singleton + free functions -- the interface every call site
# (orchestrator.py, cli_entry __main__ blocks) actually uses, so callers
# never need to construct or thread an ArchiveIndex instance themselves.
# ---------------------------------------------------------------------------

_default_index: ArchiveIndex | None = None
_default_index_lock = threading.Lock()


def get_default_index() -> ArchiveIndex:
    """Lazily construct (once) the process-wide default ArchiveIndex, bound
    to MODULE_ARCHIVE_DB_PATH at first-call time. Tests that need an
    isolated DB should call `set_default_index(ArchiveIndex(tmp_path))`
    (or monkeypatch `MODULE_ARCHIVE_DB_PATH` before the first call in that
    process) rather than relying on this global.
    """
    global _default_index
    with _default_index_lock:
        if _default_index is None:
            _default_index = ArchiveIndex()
        return _default_index


def set_default_index(index: ArchiveIndex | None) -> None:
    """Test/advanced-use seam: replace (or clear) the process-wide default
    ArchiveIndex singleton."""
    global _default_index
    with _default_index_lock:
        _default_index = index


def record(
    module_result: Any,
    module_spec: Any,
    context: dict[str, Any],
    *,
    triggered_by: str,
) -> None:
    """Free-function convenience wrapper over `get_default_index().record(...)`.
    Never raises -- see `ArchiveIndex.record`'s docstring."""
    try:
        index = get_default_index()
    except Exception:
        logger.warning(
            "module_archive.record: could not obtain default ArchiveIndex "
            "(archiving is best-effort; the module run itself is unaffected)",
            exc_info=True,
        )
        return
    index.record(module_result, module_spec, context, triggered_by=triggered_by)


def query(
    *,
    ticker: str | None = None,
    expiry: str | None = None,
    module_slug: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Free-function convenience wrapper over `get_default_index().query(...)`."""
    return get_default_index().query(
        ticker=ticker,
        expiry=expiry,
        module_slug=module_slug,
        since=since,
        until=until,
        limit=limit,
    )
