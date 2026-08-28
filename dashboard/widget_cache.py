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

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

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
                (widget_id, json.dumps(payload), status, computed_at),
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
