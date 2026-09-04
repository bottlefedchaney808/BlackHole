"""shared/context_store.py -- live, queryable context store for widget outputs.

Replaces the implicit context threading that used to happen only inside
``orchestrator.py::_thread_vol_stats_into_context``. Any module's
``ModuleResult.context_patch`` can be persisted to this store keyed by scope
(``ticker`` / ``ticker+expiry`` / ``basket``), and any later module can read it
back via ``get(scope, key)`` without depending on execution order.

Storage: reuses ``artifacts/widget_cache.db`` (new ``context_entries`` table).
Concurrency: uses ``shared/connection_pool.ConnectionPool`` with WAL mode and
busy timeout, matching the pattern already used for ``swaps.db``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from shared.connection_pool import ConnectionPool


class ContextStoreError(Exception):
    """Raised for scope-key normalization or store operation failures."""


@dataclass(frozen=True)
class Scope:
    """Normalized scope for a context entry.

    ``basket`` takes precedence when present; otherwise ``ticker`` (+ optional
    ``expiry``). The string representation is stable and used as the DB key.
    """

    ticker: str | None = None
    expiry: str | None = None
    basket: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.basket is not None and self.ticker is not None:
            raise ContextStoreError("scope cannot specify both basket and ticker")

    @classmethod
    def from_dict(cls, scope: dict[str, Any] | Scope) -> Scope:
        if isinstance(scope, Scope):
            return scope
        basket = scope.get("basket")
        ticker = scope.get("ticker")
        if basket is not None and ticker is not None:
            raise ContextStoreError("scope cannot specify both basket and ticker")
        if basket is not None:
            if not isinstance(basket, (list, tuple)):
                raise ContextStoreError("basket must be a list/tuple of tickers")
            return cls(basket=tuple(_normalize_ticker(t) for t in basket))
        return cls(
            ticker=_normalize_ticker(scope.get("ticker")),
            expiry=_normalize_expiry(scope.get("expiry")),
        )

    def to_db_key(self) -> str:
        if self.basket is not None:
            return f"basket:{','.join(sorted(self.basket))}"
        if self.ticker:
            if self.expiry:
                return f"ticker_expiry:{self.ticker}|{self.expiry}"
            return f"ticker:{self.ticker}"
        raise ContextStoreError("scope must specify ticker or basket")

    def to_dict(self) -> dict[str, Any]:
        if self.basket is not None:
            return {"basket": list(self.basket)}
        return {"ticker": self.ticker, "expiry": self.expiry}


def _normalize_ticker(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip().upper()
    if not v:
        return None
    return v


def _normalize_expiry(value: Any) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    return v


def _json_hash(value: Any) -> str:
    """Stable SHA-256 hash of a JSON-serializable value."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize(value: Any) -> str:
    return json.dumps(value, default=str)


def _deserialize(raw: str) -> Any:
    return json.loads(raw)


class ContextStore:
    """SQLite-backed scoped key/value store for widget context patches.

    Parameters
    ----------
    db_path:
        Path to the SQLite file. Defaults to ``artifacts/widget_cache.db``
        relative to the repo root.
    pool:
        Optional existing ``ConnectionPool`` to use. If omitted, a small
        pool is created lazily on first use.
    """

    _table_sql = """
        CREATE TABLE IF NOT EXISTS context_entries (
            scope_key TEXT NOT NULL,
            entry_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            source_slug TEXT NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (scope_key, entry_key)
        );
        CREATE INDEX IF NOT EXISTS idx_context_entries_scope
            ON context_entries (scope_key);
    """

    _audit_table_sql = """
        CREATE TABLE IF NOT EXISTS context_store_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope_key TEXT NOT NULL,
            entry_key TEXT NOT NULL,
            before_hash TEXT,
            after_hash TEXT NOT NULL,
            changed_keys TEXT,
            source_slug TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_context_store_audit_scope
            ON context_store_audit (scope_key, entry_key);
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        pool: ConnectionPool | None = None,
    ) -> None:
        self._db_path = self._resolve_db_path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._provided_pool = pool
        self._own_pool: ConnectionPool | None = None
        self._init_schema()

    @classmethod
    def _resolve_db_path(cls, db_path: str | Path | None) -> Path:
        if db_path is not None:
            return Path(db_path).resolve()
        env_path = os.environ.get("CONTEXT_STORE_PATH")
        if env_path:
            return Path(env_path).resolve()
        # Repo root is the parent of this file's directory.
        root = Path(__file__).resolve().parent.parent
        return root / "artifacts" / "widget_cache.db"

    def _pool(self) -> ConnectionPool:
        if self._provided_pool is not None:
            return self._provided_pool
        if self._own_pool is None:
            self._own_pool = ConnectionPool(
                str(self._db_path), pool_size=2, max_pool_size=5
            )
        return self._own_pool

    def _init_schema(self) -> None:
        # A bare connection is fine here; schema creation is once per process.
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        try:
            conn.executescript(self._table_sql)
            conn.executescript(self._audit_table_sql)
        finally:
            conn.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _write_audit(
        self,
        conn: sqlite3.Connection,
        scope_key: str,
        entry_key: str,
        before: Any | None,
        after: Any,
        source_slug: str,
    ) -> None:
        before_hash = _json_hash(before) if before is not None else None
        after_hash = _json_hash(after)
        before_keys = set(before.keys()) if isinstance(before, dict) else set()
        after_keys = set(after.keys()) if isinstance(after, dict) else set()
        changed = ",".join(sorted(after_keys.symmetric_difference(before_keys)))
        conn.execute(
            """
            INSERT INTO context_store_audit
                (scope_key, entry_key, before_hash, after_hash, changed_keys, source_slug, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (scope_key, entry_key, before_hash, after_hash, changed, source_slug, self._now()),
        )

    def put(
        self,
        scope: dict[str, Any] | Scope,
        key: str,
        value: Any,
        source_slug: str = "",
        *,
        audit: bool = True,
    ) -> None:
        """Persist ``value`` under ``key`` for the given scope.

        ``source_slug`` records which module/widget produced the value.
        """
        s = Scope.from_dict(scope)
        scope_key = s.to_db_key()
        with self._pool().get_connection_context() as conn:
            existing_row = conn.execute(
                "SELECT value_json FROM context_entries WHERE scope_key = ? AND entry_key = ?",
                (scope_key, key),
            ).fetchone()
            before = _deserialize(existing_row[0]) if existing_row else None
            conn.execute(
                """
                INSERT INTO context_entries (scope_key, entry_key, value_json, source_slug, computed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(scope_key, entry_key) DO UPDATE SET
                    value_json = excluded.value_json,
                    source_slug = excluded.source_slug,
                    computed_at = excluded.computed_at
                """,
                (scope_key, key, _serialize(value), source_slug, self._now()),
            )
            if audit:
                self._write_audit(conn, scope_key, key, before, value, source_slug)
            conn.commit()

    def get(
        self,
        scope: dict[str, Any] | Scope,
        key: str,
        *,
        max_age_s: int | None = None,
    ) -> Any:
        """Return the stored value for ``key`` in ``scope``, or ``None``.

        ``max_age_s`` filters out entries older than the given number of
        seconds (``None`` = no age filtering).
        """
        s = Scope.from_dict(scope)
        scope_key = s.to_db_key()
        with self._pool().get_connection_context() as conn:
            row = conn.execute(
                """
                SELECT value_json, computed_at FROM context_entries
                WHERE scope_key = ? AND entry_key = ?
                """,
                (scope_key, key),
            ).fetchone()
            if row is None:
                return None
            value = _deserialize(row[0])
            computed_at = row[1]
            if max_age_s is not None:
                try:
                    computed_dt = datetime.fromisoformat(computed_at)
                    if computed_dt.tzinfo is None:
                        computed_dt = computed_dt.replace(tzinfo=UTC)
                    if datetime.now(UTC) - computed_dt > timedelta(seconds=max_age_s):
                        return None
                except ValueError:
                    # Malformed timestamp; treat as stale.
                    return None
            return value

    def describe(
        self,
        scope: dict[str, Any] | Scope | None = None,
    ) -> list[dict[str, Any]]:
        """Return provenance for all entries in ``scope`` (or all entries).

        Each result contains ``key``, ``source_slug``, ``computed_at``,
        ``age_s``.
        """
        s = Scope.from_dict(scope) if scope is not None else None
        scope_key = s.to_db_key() if s is not None else None
        query = "SELECT scope_key, entry_key, source_slug, computed_at FROM context_entries"
        params: tuple[Any, ...] = ()
        if scope_key is not None:
            query += " WHERE scope_key = ?"
            params = (scope_key,)
        query += " ORDER BY computed_at DESC"
        rows = []
        with self._pool().get_connection_context() as conn:
            for row in conn.execute(query, params).fetchall():
                try:
                    computed_dt = datetime.fromisoformat(row[3])
                    if computed_dt.tzinfo is None:
                        computed_dt = computed_dt.replace(tzinfo=UTC)
                    age_s = (datetime.now(UTC) - computed_dt).total_seconds()
                except ValueError:
                    age_s = None
                rows.append(
                    {
                        "scope": row[0],
                        "key": row[1],
                        "source_slug": row[2],
                        "computed_at": row[3],
                        "age_s": age_s,
                    }
                )
        return rows

    def close(self) -> None:
        """Close the internally-managed connection pool, if any."""
        if self._own_pool is not None:
            self._own_pool.close()
            self._own_pool = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
