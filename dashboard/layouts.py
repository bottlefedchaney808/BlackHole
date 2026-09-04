"""Layout persistence backend (Phase 5, standalone APIRouter).

Persists per-page widget-instance layouts in the shared
``artifacts/widget_cache.db`` SQLite file under a dedicated
``dashboard_layouts`` table. Deliberately NOT wired into ``dashboard/app.py``
yet -- Phase 5 integration adds the router to the app at merge time (the one
line is documented in the module docstring below / tests).

Design decisions
----------------
* **Full-replace PUT.** ``PUT /api/layout/{page}`` treats its ``instances``
  body as the authoritative, complete layout for that page: it deletes every
  existing row for the page and inserts the incoming instances inside a
  single transaction (delete + inserts + commit). This gives idempotent,
  last-write-wins semantics that match a frontend that drags/reorders widgets
  and then saves the whole grid. Upsert-with-partial-patch is the alternative
  and was rejected because a layout save is naturally a whole-grid operation
  (one widget moving affects positions around it); incremental patching would
  force callers to diff state they'd rather just resend.
* **Same DB file, own table.** Reuses ``artifacts/widget_cache.db`` per the
  Phase 5 plan ("don't spin up a second SQLite file"), in a table named
  ``dashboard_layouts`` that is independent of ``widget_cache``. Connect per
  call with WAL + ``busy_timeout=30000`` (mirrors ``shared/connection_pool.py``
  so the shared file tolerates the concurrent widget-writer shape Phase 3
  introduces), no held-open connection.
* **DB path override.** ``WIDGET_CACHE_PATH`` env var overrides the default
  ``<repo>/artifacts/widget_cache.db``. The override is read at call time
  (not import time) so tests can monkeypatch it. Mirrors the repo's
  ``CONTEXT_STORE_PATH`` convention.

Integration (for the PM, added at Phase 5 wiring -- NOT done here):
    from dashboard import layouts
    app.include_router(layouts.router)   # prefix /api/layout
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import JSONResponse

router = APIRouter(prefix="/api/layout", tags=["layout"])

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
_DASHBOARD_DIR = Path(__file__).resolve().parent
_ROOT = _DASHBOARD_DIR.parent
_DEFAULT_DB_PATH = _ROOT / "artifacts" / "widget_cache.db"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dashboard_layouts (
    page                TEXT    NOT NULL,
    widget_instance_id  TEXT    NOT NULL,
    slug                TEXT    NOT NULL,
    position            INTEGER NOT NULL,
    scope_override      TEXT,             -- JSON-encoded dict or NULL
    sync_enabled        INTEGER NOT NULL, -- 0 or 1
    config_json         TEXT,             -- JSON-encoded dict or NULL
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    PRIMARY KEY (page, widget_instance_id)
)
"""


def _db_path() -> Path:
    """DB file path, honoring WIDGET_CACHE_PATH when set."""
    override = os.environ.get("WIDGET_CACHE_PATH")
    return Path(override) if override else _DEFAULT_DB_PATH


def _connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a per-call WAL-mode connection, ensuring the table exists."""
    path = path or _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# JSON sanitization (local copy of widget_cache.py's _sanitize_for_json;
# re-implemented here intentionally -- do NOT import from widget_cache.py,
# another worker owns that file)
# ---------------------------------------------------------------------------
def _sanitize_for_json(obj: Any) -> Any:
    """Recursively replace NaN/Infinity/-Infinity with None.

    Starlette's JSONResponse renders with allow_nan=False, so any NaN/Inf in
    a scope override or config that survived into a response would 500 the
    GET. Sanitize at write time so stored JSON is always standard.
    """
    if isinstance(obj, float):
        return None if (obj != obj or obj in (float("inf"), float("-inf"))) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


def _encode_json_or_null(value: Any) -> str | None:
    """Encode a scope/config value for storage: JSON text or NULL."""
    if value is None:
        return None
    return json.dumps(_sanitize_for_json(value))


def _decode_json_or_none(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


def _row_to_instance(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "page": row["page"],
        "widget_instance_id": row["widget_instance_id"],
        "slug": row["slug"],
        "position": row["position"],
        "scope_override": _decode_json_or_none(row["scope_override"]),
        "sync_enabled": bool(row["sync_enabled"]),
        "config_json": _decode_json_or_none(row["config_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------
def _validate_instance(raw: Any, idx: int) -> dict[str, Any] | str:
    """Return a normalized instance dict, or a human-readable error string."""
    if not isinstance(raw, dict):
        return f"instances[{idx}] must be an object"
    widget_instance_id = raw.get("widget_instance_id")
    if not isinstance(widget_instance_id, str) or not widget_instance_id.strip():
        return f"instances[{idx}].widget_instance_id must be a non-empty string"
    slug = raw.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        return f"instances[{idx}].slug must be a non-empty string"
    position = raw.get("position")
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        return f"instances[{idx}].position must be an integer >= 0"
    sync_enabled = raw.get("sync_enabled")
    if not isinstance(sync_enabled, bool):
        return f"instances[{idx}].sync_enabled must be a boolean"
    # scope_override / config_json may be omitted (-> None) or any JSON value.
    return {
        "widget_instance_id": widget_instance_id.strip(),
        "slug": slug.strip(),
        "position": position,
        "scope_override": raw.get("scope_override"),
        "sync_enabled": sync_enabled,
        "config_json": raw.get("config_json"),
    }


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.get("/{page}")
def get_layout(page: str):
    """List widget instances for a page, ordered by position.

    An empty / never-saved page returns 200 with an empty list.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM dashboard_layouts
            WHERE page = ?
            ORDER BY position ASC, widget_instance_id ASC
            """,
            (page,),
        ).fetchall()
    return [_row_to_instance(r) for r in rows]


@router.put("/{page}")
async def put_layout(page: str, request: Request):
    """Full-replace the page layout with the supplied instances."""
    try:
        body = await request.json()
    except Exception as exc:  # invalid JSON body
        return JSONResponse(status_code=400, content={"detail": f"Invalid JSON body: {exc}"})

    instances_raw = body.get("instances") if isinstance(body, dict) else None
    if instances_raw is None:
        return JSONResponse(status_code=400, content={"detail": "Body must include 'instances' list"})
    if not isinstance(instances_raw, list):
        return JSONResponse(status_code=400, content={"detail": "'instances' must be a list"})

    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(instances_raw):
        result = _validate_instance(item, idx)
        if isinstance(result, str):
            return JSONResponse(status_code=400, content={"detail": result})
        normalized.append(result)

    now = _now_iso()
    with _connect() as conn:
        # Full replace inside one transaction.
        conn.execute("DELETE FROM dashboard_layouts WHERE page = ?", (page,))
        conn.executemany(
            """
            INSERT INTO dashboard_layouts
                (page, widget_instance_id, slug, position, scope_override,
                 sync_enabled, config_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    page,
                    inst["widget_instance_id"],
                    inst["slug"],
                    inst["position"],
                    _encode_json_or_null(inst["scope_override"]),
                    1 if inst["sync_enabled"] else 0,
                    _encode_json_or_null(inst["config_json"]),
                    now,
                    now,
                )
                for inst in normalized
            ],
        )
    return JSONResponse(status_code=200, content={"ok": True, "count": len(normalized)})


@router.delete("/{page}/{widget_instance_id}")
def delete_layout_item(page: str, widget_instance_id: str):
    """Delete a single widget instance from a page layout."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM dashboard_layouts WHERE page = ? AND widget_instance_id = ?",
            (page, widget_instance_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Layout instance not found")
    return {"ok": True}


# ---------------------------------------------------------------------------
# LayoutStore -- thin programmatic adapter over the module functions above.
# This is the interface dashboard/quant_console_agent.py's layout write-back
# (Phase 6, plan step 3) codes against; instances that omit
# ``widget_instance_id`` get a generated one (``wi_<uuid8>``), so callers can
# append prompt-produced widgets without inventing ids.
# ---------------------------------------------------------------------------
class LayoutStore:
    """list_page(page) -> list[dict]; replace_page(page, instances) -> int."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = Path(db_path) if db_path else None

    def list_page(self, page: str) -> list[dict[str, Any]]:
        with _connect(self._path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM dashboard_layouts
                WHERE page = ?
                ORDER BY position ASC, widget_instance_id ASC
                """,
                (page,),
            ).fetchall()
        return [_row_to_instance(r) for r in rows]

    def replace_page(self, page: str, instances: list[dict[str, Any]]) -> int:
        """Validate + full-replace a page layout. Returns the row count."""
        normalized: list[dict[str, Any]] = []
        for idx, raw in enumerate(instances):
            item = dict(raw)
            # Generate an id when omitted (agent write-back path).
            if not item.get("widget_instance_id"):
                item["widget_instance_id"] = (
                    "wi_" + uuid.uuid4().hex[:8]
                )
            result = _validate_instance(item, idx)
            if isinstance(result, str):
                raise ValueError(f"instances[{idx}]: {result}")
            normalized.append(result)

        now = _now_iso()
        with _connect(self._path) as conn:
            conn.execute("DELETE FROM dashboard_layouts WHERE page = ?", (page,))
            conn.executemany(
                """
                INSERT INTO dashboard_layouts
                    (page, widget_instance_id, slug, position, scope_override,
                     sync_enabled, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        page,
                        inst["widget_instance_id"],
                        inst["slug"],
                        inst["position"],
                        _encode_json_or_null(inst["scope_override"]),
                        1 if inst["sync_enabled"] else 0,
                        _encode_json_or_null(inst["config_json"]),
                        now,
                        now,
                    )
                    for inst in normalized
                ],
            )
        return len(normalized)
