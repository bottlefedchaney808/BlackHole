"""Initialize and migrate the SQLite schema for DTCC swap trade data.

Schema changes live as numbered SQL files in migrations/ (e.g.
001_initial.sql, 002_add_data_source.sql), applied in order and tracked in
the schema_version table. Running this module -- as a script or via
migrate() -- is idempotent: migrations already recorded in schema_version
are skipped, so re-running against an up-to-date database is a no-op beyond
opening a connection.

CLI:
    python setup_db.py              # apply any pending migrations (default)
    python setup_db.py --migrate    # same, explicit
    python setup_db.py --status     # show current/latest version, don't apply
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional

from shared.connection_pool import init_pool, close_pool, get_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.abspath(__file__))
# SWAPS_DB_PATH env var overrides, e.g. for a mounted Docker volume; see
# .env.example / docker-compose.yml
DB_PATH = os.environ.get("SWAPS_DB_PATH") or os.path.join(ROOT, "swaps.db")
MIGRATIONS_DIR = os.path.join(ROOT, "migrations")

# Matches e.g. "001_initial.sql" -> version=1, name="initial". Anything that
# doesn't match (README.md, .gitkeep, stray notes) is silently skipped by
# _discover_migrations rather than treated as an error.
_MIGRATION_FILENAME_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_]+)\.sql$")


class Migration(NamedTuple):
    version: int
    name: str
    path: str

    @property
    def label(self) -> str:
        return f"{self.version:03d}_{self.name}"


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _discover_migrations(migrations_dir: str = None) -> List[Migration]:
    """Scan migrations_dir for NNN_name.sql files, sorted by version number."""
    migrations_dir = migrations_dir or MIGRATIONS_DIR
    if not os.path.isdir(migrations_dir):
        return []

    found: Dict[int, str] = {}
    migrations: List[Migration] = []
    for fname in sorted(os.listdir(migrations_dir)):
        m = _MIGRATION_FILENAME_RE.match(fname)
        if not m:
            continue
        version = int(m.group(1))
        name = m.group(2)
        if version in found:
            raise ValueError(
                f"Duplicate migration version {version}: {found[version]} and {fname} "
                f"in {migrations_dir}"
            )
        found[version] = fname
        migrations.append(Migration(version=version, name=name, path=os.path.join(migrations_dir, fname)))

    migrations.sort(key=lambda mig: mig.version)
    return migrations


def _ensure_schema_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL,
            migration_name TEXT NOT NULL
        );
        """
    )
    conn.commit()


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 for an unversioned/new database."""
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version;").fetchone()
    value = row[0] if row else None
    return int(value) if value is not None else 0


def _apply_migration(conn: sqlite3.Connection, migration: Migration) -> None:
    """Execute one migration file and record it in schema_version.

    The migration's own statements run inside an explicit transaction so a
    mid-script failure (e.g. a bad ALTER TABLE) leaves the database exactly
    as it was before this migration started. The schema_version INSERT is a
    second, separate commit -- if that specific step failed after a
    successful script, the next run would attempt to redo already-applied
    DDL. All migrations here use IF-NOT-EXISTS / additive statements, so
    that residual risk is a rerun-safe no-op in practice, not a broken DB.
    """
    with open(migration.path, "r", encoding="utf-8") as f:
        raw_sql = f.read()

    conn.executescript(f"BEGIN;\n{raw_sql}\nCOMMIT;")
    conn.execute(
        "INSERT INTO schema_version (version, applied_at, migration_name) VALUES (?, ?, ?);",
        (migration.version, _iso_utc_now(), migration.name),
    )
    conn.commit()
    logger.info(f"Applied migration {migration.label}.sql")


def migrate(db_path: str = None, migrations_dir: str = None) -> Dict[str, Any]:
    """Apply all pending migrations to db_path, in version order.

    Idempotent -- migrations already recorded in schema_version are skipped,
    so calling this against an up-to-date database logs "already at vN" and
    returns with applied=[].
    """
    db_path = db_path or DB_PATH
    migrations_dir = migrations_dir or MIGRATIONS_DIR

    all_migrations = _discover_migrations(migrations_dir)
    latest_available = all_migrations[-1].version if all_migrations else 0

    conn = sqlite3.connect(db_path)
    try:
        # WAL mode so readers (dashboard, orchestrator) and writers
        # (scheduler, backfill) don't block each other; matches db_loader.py.
        conn.execute("PRAGMA journal_mode=WAL;")

        _ensure_schema_version_table(conn)
        starting_version = _get_current_version(conn)

        pending = [m for m in all_migrations if m.version > starting_version]
        pending_before = [m.label for m in pending]

        if not pending:
            logger.info(f"Schema already at v{starting_version} ({db_path}); nothing to apply.")
        else:
            logger.info(
                f"Schema at v{starting_version} ({db_path}); "
                f"{len(pending)} pending migration(s): {', '.join(pending_before)}"
            )

        applied: List[str] = []
        for migration in pending:
            _apply_migration(conn, migration)
            applied.append(migration.label)

        current_version = _get_current_version(conn)
        if applied:
            logger.info(f"Schema v{current_version} initialized ({db_path}).")

        return {
            "db_path": db_path,
            "starting_version": starting_version,
            "current_version": current_version,
            "latest_available": latest_available,
            "applied": applied,
            "pending_before": pending_before,
        }
    finally:
        conn.close()


def get_schema_status(db_path: str = None, migrations_dir: str = None) -> Dict[str, Any]:
    """Read-only view of schema version vs. the latest migration on disk.

    Never applies migrations or creates schema_version -- safe to call from
    a startup check (orchestrator.py) that must not mutate the database.
    """
    db_path = db_path or DB_PATH
    migrations_dir = migrations_dir or MIGRATIONS_DIR

    all_migrations = _discover_migrations(migrations_dir)
    latest_available = all_migrations[-1].version if all_migrations else 0

    if not os.path.exists(db_path):
        return {
            "db_path": db_path,
            "exists": False,
            "current_version": 0,
            "latest_available": latest_available,
            "pending": [m.label for m in all_migrations],
        }

    conn = sqlite3.connect(db_path)
    try:
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version';"
        ).fetchone() is not None
        current_version = _get_current_version(conn) if has_table else 0
    finally:
        conn.close()

    pending = [m.label for m in all_migrations if m.version > current_version]
    return {
        "db_path": db_path,
        "exists": True,
        "current_version": current_version,
        "latest_available": latest_available,
        "pending": pending,
    }


def check_schema_version(db_path: str = None, migrations_dir: str = None) -> Dict[str, Any]:
    """Startup check for callers like orchestrator.py: warn on stderr if the
    on-disk schema is behind the latest migration, but never raise and never
    block startup -- a stale schema is a "run setup_db.py --migrate" nudge,
    not a fatal condition, and a cold/missing database is normal on a first
    run (setup_db.py hasn't been run yet).
    """
    try:
        status = get_schema_status(db_path, migrations_dir)
    except Exception as e:
        logger.warning(f"Schema version check failed (continuing anyway): {e}")
        return {"ok": False, "error": str(e)}

    if not status["exists"]:
        # Nothing to warn about yet -- there's no database to be behind.
        return {"ok": True, **status}

    if status["pending"]:
        logger.warning(
            f"swaps.db schema is v{status['current_version']}, "
            f"latest available is v{status['latest_available']}. "
            f"Pending migrations: {', '.join(status['pending'])}. "
            f"Run: python setup_db.py --migrate"
        )
        return {"ok": False, **status}

    return {"ok": True, **status}


def init_database(db_path: str = None) -> Dict[str, Any]:
    """Backward-compatible alias for migrate().

    Older callers (tests, scripts) import setup_db.init_database(path) to
    stand up a fresh schema; that now means "apply every migration" rather
    than the hand-written CREATE TABLE block this function used to contain.
    """
    return migrate(db_path)


def initialize_connection_pool(db_path: str = None, pool_size: int = 5,
                               max_pool_size: int = 20) -> Dict[str, Any]:
    """Initialize the global connection pool for high-throughput ingestion.

    Should be called once during application startup after schema initialization.

    Args:
        db_path: Path to SQLite database (default from DB_PATH)
        pool_size: Initial number of pooled connections (default 5)
        max_pool_size: Maximum connections allowed (default 20)

    Returns:
        Dict with pool statistics
    """
    db_path = db_path or DB_PATH
    try:
        pool = init_pool(db_path, pool_size=pool_size, max_pool_size=max_pool_size)
        stats = pool.get_stats()
        logger.info(f"Connection pool initialized: {stats}")
        return {"ok": True, **stats}
    except Exception as e:
        logger.error(f"Failed to initialize connection pool: {e}")
        return {"ok": False, "error": str(e)}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup_db.py",
        description="Initialize/migrate the swaps.db schema via migrations/*.sql.",
    )
    parser.add_argument(
        "--migrate", action="store_true",
        help="Apply pending migrations. This is also the default action when no flags are given.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show current schema version and pending migrations without applying them.",
    )
    parser.add_argument(
        "--db-path", default=None,
        help=f"Database file path (default: {DB_PATH}).",
    )
    args = parser.parse_args(argv)

    db_path = args.db_path or DB_PATH

    if args.status:
        status = get_schema_status(db_path)
        print(f"Database: {status['db_path']}")
        print(f"Exists: {status['exists']}")
        print(f"Current schema version: v{status['current_version']}")
        print(f"Latest available version: v{status['latest_available']}")
        if status["pending"]:
            print("Pending migrations:")
            for label in status["pending"]:
                print(f"  - {label}")
        else:
            print("No pending migrations.")
        return 0

    # Default action (bare `python setup_db.py`, or explicit --migrate) is
    # to apply pending migrations.
    result = migrate(db_path)
    print(f"Database ready: {result['db_path']}")
    print(f"Schema version: v{result['starting_version']} -> v{result['current_version']}")
    if result["applied"]:
        print("Applied migrations:")
        for label in result["applied"]:
            print(f"  - {label}")
    else:
        print("No pending migrations (already up to date).")
    if result["starting_version"] == 0 and result["current_version"] > 0:
        print("  Now run: python backfill.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        logger.error(f"Database setup failed: {e}")
        raise
