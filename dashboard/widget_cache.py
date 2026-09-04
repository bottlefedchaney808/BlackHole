"""SQLite-backed cache for Overview widget payloads.

One row per widget_id: the JSON payload a background job (or, for
"positions", an external POST from a scheduled agent) last computed, its
status, and when it was written. Mirrors chart_app/bar_cache.py's
BarCache -- connect per call, no held-open connection, so a background
asyncio task and a request handler can both write without coordinating a
shared connection object.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively replace NaN/Infinity/-Infinity with None.

    Python's json.dumps happily writes these as the (non-standard) literals
    NaN/Infinity/-Infinity by default, so a widget payload containing one
    (real financial calcs produce these -- e.g. an undefined ratio) writes
    to the cache without error. But Starlette's JSONResponse renders with
    allow_nan=False, so GET /api/widgets/{id} 500s the moment it tries to
    serialize that value back out over HTTP. Sanitizing at write time keeps
    the cache itself valid JSON and pushes the failure mode from "500 on
    read" to "null in the payload," which every widget's frontend already
    treats as a normal missing-value case.
    """
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


class WidgetCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS widget_cache (
                    widget_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    computed_at TEXT NOT NULL
                )
                """
            )
            # Scoped cache backing the generic widget-run API
            # (POST /api/widgets/{slug}/run -> GET /api/widgets/{slug}/state).
            # Composite key: one row per (slug, scope_key) so the same widget
            # can cache results for many scopes (SPY, QQQ, baskets, ...)
            # without clobbering each other. Deliberately separate from the
            # legacy single-id `widget_cache` table so the fixed Overview ids
            # (widget_id PK) are untouched.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS widget_cache_scoped (
                    slug TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    PRIMARY KEY (slug, scope_key)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        # WAL + busy_timeout, matching shared/connection_pool.py's pattern.
        # Phase 3 makes every widget run write to this same file (the scoped
        # row AND any context_patch via shared/context_store.py), which is the
        # racing-writers shape CLAUDE.md documents as the "database is locked"
        # failure mode -- enable WAL and a busy timeout so concurrent writers
        # queue instead of failing immediately.
        conn = sqlite3.connect(self._path, timeout=30.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
        except sqlite3.Error:
            # PRAGMA failures are non-fatal -- the connection still works.
            pass
        return conn

    def set(self, widget_id: str, payload: Any, status: str = "ok") -> None:
        computed_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO widget_cache (widget_id, payload, status, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (widget_id) DO UPDATE SET
                    payload = excluded.payload,
                    status = excluded.status,
                    computed_at = excluded.computed_at
                """,
                (
                    widget_id,
                    json.dumps(_sanitize_for_json(payload)),
                    status,
                    computed_at,
                ),
            )

    def get(self, widget_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, status, computed_at FROM widget_cache WHERE widget_id = ?",
                (widget_id,),
            ).fetchone()
        if row is None:
            return None
        payload_raw, status, computed_at = row
        return {
            "payload": json.loads(payload_raw),
            "status": status,
            "computed_at": computed_at,
        }

    def set_scoped(
        self,
        slug: str,
        scope_key: str,
        payload: Any,
        status: str = "ok",
    ) -> None:
        """Write one (slug, scope_key) row to the generic widget cache.

        Backs the generic widget-run API: every POST /api/widgets/{slug}/run
        caches its result here, keyed by the widget's scope, so a later
        GET /api/widgets/{slug}/state?scope=<scope_key> returns it without a
        re-run. Payload is sanitized for NaN/Infinity exactly like set().
        """
        computed_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO widget_cache_scoped (slug, scope_key, payload, status, computed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (slug, scope_key) DO UPDATE SET
                    payload = excluded.payload,
                    status = excluded.status,
                    computed_at = excluded.computed_at
                """,
                (
                    slug,
                    scope_key,
                    json.dumps(_sanitize_for_json(payload)),
                    status,
                    computed_at,
                ),
            )

    def get_scoped(self, slug: str, scope_key: str) -> dict[str, Any] | None:
        """Return the last cached (slug, scope_key) row, or None if absent."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload, status, computed_at
                FROM widget_cache_scoped WHERE slug = ? AND scope_key = ?
                """,
                (slug, scope_key),
            ).fetchone()
        if row is None:
            return None
        payload_raw, status, computed_at = row
        return {
            "payload": json.loads(payload_raw),
            "status": status,
            "computed_at": computed_at,
        }
