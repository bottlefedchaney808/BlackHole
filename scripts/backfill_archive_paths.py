#!/usr/bin/env python3
"""backfill_archive_paths.py -- backfill legacy absolute artifact paths.

Task B5: one-shot backfill of legacy absolute artifact paths in module_archive.db.

This script reads all rows from module_archive.db, rewrites artifact_paths_json
mapping each path through shared.artifact_paths.to_rel(), and UPDATEs only changed
rows. It is idempotent: re-running is a no-op for already-relative paths.

Paths outside-root/unrecognizable are left unchanged and counted in the report.

Never touches swaps.db.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Must use .venv python (3.12), never system python
from shared.artifact_paths import to_rel

# Respect MODULE_ARCHIVE_DB_PATH env var for testability
DB_PATH = os.environ.get("MODULE_ARCHIVE_DB_PATH") or os.path.join(
    str(REPO_ROOT), "module_archive.db"
)


def main():
    import sqlite3

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, artifact_paths_json FROM module_archive"
    ).fetchall()

    total_rows = len(rows)
    changed_count = 0
    unchanged_count = 0
    skipped_count = 0

    print(f"Processing {total_rows} rows from module_archive.db...")
    print("-" * 60)

    for row in rows:
        id_ = row["id"]
        artifact_paths_json = row["artifact_paths_json"]

        try:
            artifacts = json.loads(artifact_paths_json)
        except json.JSONDecodeError as e:
            print(f"ID {id_}: SKIP - invalid JSON ({e})")
            skipped_count += 1
            continue

        new_artifacts = []
        row_changed = False

        for artifact in artifacts:
            path = artifact.get("path", "")
            kind = artifact.get("kind", "")

            try:
                path_rel = to_rel(path)
            except Exception as e:
                # If to_rel fails, keep original path
                path_rel = path

            if path_rel != path:
                row_changed = True
                new_artifacts.append({"path": path_rel, "kind": kind})
                print(f"ID {id_}: {path} -> {path_rel}")
            else:
                new_artifacts.append({"path": path, "kind": kind})

        if row_changed:
            new_paths_json = json.dumps(new_artifacts)
            conn.execute(
                "UPDATE module_archive SET artifact_paths_json = ? WHERE id = ?",
                (new_paths_json, id_),
            )
            changed_count += 1

        if not row_changed:
            unchanged_count += 1

    conn.commit()
    conn.close()

    print("-" * 60)
    print(f"Summary:")
    print(f"  Rows processed: {total_rows}")
    print(f"  Rows changed: {changed_count}")
    print(f"  Rows unchanged: {unchanged_count}")
    print(f"  Rows skipped (errors): {skipped_count}")


if __name__ == "__main__":
    main()
