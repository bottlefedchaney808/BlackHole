#!/usr/bin/env python3
"""swaps_archive.py -- archive DTCC equity swap disseminations to weekly Parquet
files stored as GitHub release assets.

Why this exists
---------------
The local swaps.db grew to 346 GB, mostly because every row kept its DTCC record
a second time as JSON text (field names repeated 71 million times). The data
itself is small once stored columnar: one DTCC day of 1.7M rows is 98.8 MB as
DTCC's zip and 52.8 MB as zstd Parquet with all 110 fields kept (verified
2026-09-12, row-for-row).

What DTCC still serves (verified 2026-09-12)
--------------------------------------------
Daily files live at ``{BUCKET}/{sec|cftc}/eod/{SEC|CFTC}_CUMULATIVE_EQUITIES_YYYY_MM_DD.zip``.
The API listing only shows the latest 366 days, but S3 keeps older objects.
Each object moves to S3 DEEP_ARCHIVE 730 days after upload; after that a HEAD
still answers 200 but a GET fails with 403 InvalidObjectState. So the
downloadable history is a rolling two-year window that loses its oldest day
every day. ``run`` therefore processes oldest weeks first and records cold
days rather than silently skipping them.

Layout
------
- One Parquet file per series per ISO week: ``sec-eq_2025-W31.parquet``
  (``_part2`` etc. if a week would exceed PART_MAX_BYTES). Every DTCC column is
  kept as a string exactly as published (empty strings preserved), plus
  ``report_date`` and ``source_file``.
- Release per series per ISO year: tag ``sec-eq-2025``.
- ``manifest/<series>.json`` in the data repo clone: per-day zip sha256, bytes,
  rows, DTCC-listed rows; per-week asset name, sha256, bytes, rows.

Commands
--------
  python swaps_archive.py run [--series sec-eq,cftc-eq] [--start YYYY-MM-DD]
                              [--end YYYY-MM-DD] [--max-weeks N] [--no-upload]
  python swaps_archive.py status
  python swaps_archive.py verify-db [--db PATH]

``verify-db`` compares the archive's per-day row counts with the local
swaps.db's scrape_log (read-only). Delete the big DB only after it passes.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pcsv
import pyarrow.parquet as pq
import requests

BUCKET = "https://kgc0418-tdw-data-0.s3.amazonaws.com"
SERIES = {"sec-eq": ("sec", "SEC"), "cftc-eq": ("cftc", "CFTC")}
DATA_REPO = os.environ.get("DTCC_ARCHIVE_REPO", "bottlefedchaney808/dtcc-swaps-data")
REPO_DIR = Path(os.environ.get("DTCC_ARCHIVE_DIR", r"C:\Users\bottl\dtcc-swaps-data"))
STAGE_DIR = REPO_DIR / ".staging"
DEFAULT_DB = r"C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db"
USER_AGENT = "FinancialDevelopment-swaps-archive/1.0"

ARCHIVE_AFTER_DAYS = 730  # S3 lifecycle: objects go to DEEP_ARCHIVE after this
PART_MAX_BYTES = int(1.8 * 1024**3)  # GitHub release assets cap at 2 GiB
DOWNLOAD_PAUSE_SEC = 0.5  # sequential and rate-limited; never fan out
ZSTD_LEVEL = 6  # level 9 saves ~1% for no time benefit (measured)
ROW_GROUP_SIZE = 500_000

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


class ColdObject(Exception):
    """The file exists but S3 moved it to DEEP_ARCHIVE; it cannot be downloaded."""


class MissingObject(Exception):
    """No file for that day (not published, or never existed)."""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def manifest_path(series: str) -> Path:
    return REPO_DIR / "manifest" / f"{series}.json"


def load_manifest(series: str) -> dict[str, Any]:
    path = manifest_path(series)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    folder, reg = SERIES[series]
    return {
        "series": series,
        "source": f"{BUCKET}/{folder}/eod/{reg}_CUMULATIVE_EQUITIES_YYYY_MM_DD.zip",
        "days": {},
        "weeks": {},
    }


def save_manifest(series: str, manifest: dict[str, Any]) -> None:
    path = manifest_path(series)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Download + parse one day
# ---------------------------------------------------------------------------


def day_url(series: str, day: dt.date) -> tuple[str, str]:
    folder, reg = SERIES[series]
    name = f"{reg}_CUMULATIVE_EQUITIES_{day:%Y_%m_%d}.zip"
    return f"{BUCKET}/{folder}/eod/{name}", name


def fetch(url: str) -> bytes:
    last: Exception | None = None
    for attempt in range(5):
        try:
            r = _session.get(url, timeout=300)
        except requests.RequestException as exc:
            last = exc
        else:
            if r.status_code == 200:
                return r.content
            if r.status_code == 403 and "InvalidObjectState" in r.text:
                raise ColdObject(url)
            # A public bucket without list permission answers a key that does
            # not exist with 403 AccessDenied, not 404 -- retrying that five
            # times and aborting the run would stop on any unpublished day.
            if r.status_code == 404 or (
                r.status_code == 403
                and ("AccessDenied" in r.text or "NoSuchKey" in r.text)
            ):
                raise MissingObject(url)
            last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        time.sleep(min(60, 2 ** (attempt + 1)))
    raise RuntimeError(f"download failed after retries: {url}: {last}")


def listed_rows(series: str) -> dict[str, int]:
    """fileName -> rowCount from DTCC's listing (latest 366 days only)."""
    _folder, reg = SERIES[series]
    try:
        r = _session.get(
            f"https://pddata.dtcc.com/ppd/api/cumulative/{reg}/EQ", timeout=60
        )
        r.raise_for_status()
        return {d["fileName"]: int(d.get("rowCount") or 0) for d in r.json()}
    except Exception as exc:  # noqa: BLE001 -- the listing is a cross-check, not a dependency
        log(
            f"WARN: could not read the {reg} listing ({exc}); row counts checked by parse only"
        )
        return {}


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}__{seen[n]}")
        else:
            seen[n] = 1
            out.append(n)
    return out


def zip_to_table(blob: bytes, day: dt.date, source_file: str) -> pa.Table:
    """Every DTCC column as a string, exactly as published."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    members = [m for m in z.namelist() if m.lower().endswith(".csv")]
    if len(members) != 1:
        raise ValueError(f"{source_file}: expected one CSV member, got {z.namelist()}")
    with z.open(members[0]) as fh:
        header = next(
            csv.reader(
                [io.TextIOWrapper(fh, encoding="utf-8-sig", newline="").readline()]
            )
        )
    cols = _dedupe([h.strip() for h in header])
    with z.open(members[0]) as fh:
        table = pcsv.read_csv(
            fh,
            read_options=pcsv.ReadOptions(
                block_size=64 << 20, column_names=cols, skip_rows=1
            ),
            parse_options=pcsv.ParseOptions(newlines_in_values=True),
            convert_options=pcsv.ConvertOptions(
                column_types={c: pa.string() for c in cols}, strings_can_be_null=False
            ),
        )
    n = table.num_rows
    table = table.add_column(0, "source_file", pa.array([source_file] * n, pa.string()))
    table = table.add_column(
        0, "report_date", pa.array([day.isoformat()] * n, pa.string())
    )
    return table


# ---------------------------------------------------------------------------
# Weeks
# ---------------------------------------------------------------------------


def iso_week_key(day: dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def weeks_between(start: dt.date, end: dt.date) -> list[tuple[str, list[dt.date]]]:
    monday = start - dt.timedelta(days=start.weekday())
    out = []
    while monday <= end:
        sunday = monday + dt.timedelta(days=6)
        days = [
            d for d in (monday + dt.timedelta(days=i) for i in range(7)) if d >= start
        ]
        # Only complete weeks: a week still in progress is built on a later run,
        # so each weekly asset is written once and never grows.
        if days and sunday <= end:
            out.append((iso_week_key(monday), days))
        monday += dt.timedelta(days=7)
    return out


def stage_day(
    series: str, day: dt.date, manifest: dict, listing: dict[str, int]
) -> None:
    key = day.isoformat()
    entry = manifest["days"].get(key) or {}
    staged = STAGE_DIR / series / f"{key}.parquet"
    if entry.get("status") in ("cold", "missing"):
        return
    if entry.get("status") == "ok" and staged.exists():
        return
    url, name = day_url(series, day)
    time.sleep(DOWNLOAD_PAUSE_SEC)
    try:
        blob = fetch(url)
    except ColdObject:
        manifest["days"][key] = {"status": "cold", "file": name}
        log(f"{series} {key}: COLD (S3 deep archive) -- not downloadable")
        return
    except MissingObject:
        manifest["days"][key] = {"status": "missing", "file": name}
        log(f"{series} {key}: no file")
        return
    table = zip_to_table(blob, day, name)
    listed = listing.get(name)
    if listed is not None and listed != table.num_rows:
        raise RuntimeError(
            f"{series} {key}: parsed {table.num_rows:,} rows but DTCC lists {listed:,}; refusing to archive"
        )
    staged.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        table,
        staged,
        compression="zstd",
        compression_level=ZSTD_LEVEL,
        row_group_size=ROW_GROUP_SIZE,
    )
    manifest["days"][key] = {
        "status": "ok",
        "file": name,
        "zip_bytes": len(blob),
        "zip_sha256": hashlib.sha256(blob).hexdigest(),
        "rows": table.num_rows,
        "listed_rows": listed,
        "columns": table.num_columns - 2,
        "staged_bytes": staged.stat().st_size,
    }
    log(
        f"{series} {key}: {table.num_rows:,} rows, {len(blob) / 1e6:.0f} MB zip -> {staged.stat().st_size / 1e6:.0f} MB"
    )


def build_week(
    series: str, week: str, days: list[dt.date], manifest: dict
) -> list[dict]:
    """Merge a week's staged days into one or more Parquet parts (union schema)."""
    ok_days = [
        d for d in days if manifest["days"].get(d.isoformat(), {}).get("status") == "ok"
    ]
    if not ok_days:
        return []
    files = [STAGE_DIR / series / f"{d.isoformat()}.parquet" for d in ok_days]
    columns: list[str] = []
    for f in files:
        for name in pq.read_schema(f).names:
            if name not in columns:
                columns.append(name)
    schema = pa.schema([(c, pa.string()) for c in columns])

    groups: list[list[Path]] = [[]]
    size = 0
    for f in files:
        b = f.stat().st_size
        if groups[-1] and size + b > PART_MAX_BYTES:
            groups.append([])
            size = 0
        groups[-1].append(f)
        size += b

    out_dir = STAGE_DIR / series / "weeks"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for i, group in enumerate(groups, start=1):
        suffix = "" if len(groups) == 1 else f"_part{i}"
        asset = f"{series}_{week}{suffix}.parquet"
        path = out_dir / asset
        rows = 0
        with pq.ParquetWriter(
            path, schema, compression="zstd", compression_level=ZSTD_LEVEL
        ) as writer:
            for f in group:
                t = pq.read_table(f)
                for c in columns:
                    if c not in t.column_names:
                        t = t.append_column(c, pa.nulls(t.num_rows, pa.string()))
                t = t.select(columns)
                writer.write_table(t, row_group_size=ROW_GROUP_SIZE)
                rows += t.num_rows
        check = pq.ParquetFile(path).metadata.num_rows
        want = sum(manifest["days"][f.stem]["rows"] for f in group)
        if check != rows or rows != want:
            raise RuntimeError(f"{asset}: wrote {check:,} rows, expected {want:,}")
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 24), b""):
                h.update(chunk)
        parts.append(
            {
                "asset": asset,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": h.hexdigest(),
                "rows": rows,
                "days": [f.stem for f in group],
            }
        )
    return parts


# ---------------------------------------------------------------------------
# GitHub release upload
# ---------------------------------------------------------------------------


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    exe = shutil.which("gh")
    if not exe:
        raise RuntimeError("GitHub CLI `gh` is not on PATH")
    return subprocess.run(
        [exe, *args], capture_output=True, text=True, encoding="utf-8", check=check
    )


def upload_part(series: str, week: str, part: dict) -> str:
    tag = f"{series}-{week[:4]}"
    if gh("release", "view", tag, "-R", DATA_REPO, check=False).returncode != 0:
        gh(
            "release",
            "create",
            tag,
            "-R",
            DATA_REPO,
            "--title",
            tag,
            "--notes",
            f"{series} weekly Parquet files for ISO year {week[:4]}. See manifest/{series}.json.",
        )
    gh("release", "upload", tag, part["path"], "-R", DATA_REPO, "--clobber")
    assets = json.loads(
        gh("release", "view", tag, "-R", DATA_REPO, "--json", "assets").stdout
    )["assets"]
    got = next((a for a in assets if a["name"] == part["asset"]), None)
    if not got or int(got["size"]) != part["bytes"]:
        raise RuntimeError(f"upload check failed for {part['asset']}: {got}")
    return tag


def push_manifests(message: str) -> None:
    if not (REPO_DIR / ".git").exists():
        return
    subprocess.run(["git", "-C", str(REPO_DIR), "add", "manifest"], check=True)
    if (
        subprocess.run(
            ["git", "-C", str(REPO_DIR), "diff", "--cached", "--quiet"], check=False
        ).returncode
        == 0
    ):
        return
    subprocess.run(
        ["git", "-C", str(REPO_DIR), "commit", "-q", "-m", message], check=True
    )
    subprocess.run(["git", "-C", str(REPO_DIR), "push", "-q"], check=False)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    today = dt.datetime.now(dt.UTC).date()
    end = dt.date.fromisoformat(args.end) if args.end else today - dt.timedelta(days=1)
    start = (
        dt.date.fromisoformat(args.start)
        if args.start
        else today
        - dt.timedelta(days=ARCHIVE_AFTER_DAYS - 2)  # oldest day still downloadable
    )
    series_list = [s.strip() for s in args.series.split(",") if s.strip()]
    for s in series_list:
        if s not in SERIES:
            raise SystemExit(f"unknown series {s!r}; choose from {', '.join(SERIES)}")

    manifests = {s: load_manifest(s) for s in series_list}
    listings = {s: listed_rows(s) for s in series_list}
    weeks = weeks_between(start, end)
    log(
        f"archiving {', '.join(series_list)} {start}..{end}: {len(weeks)} complete weeks, oldest first"
    )

    done = 0
    for week, days in weeks:
        for s in series_list:
            m = manifests[s]
            if m["weeks"].get(week, {}).get("uploaded"):
                continue
            for d in days:
                stage_day(s, d, m, listings[s])
                save_manifest(s, m)
            parts = build_week(s, week, days, m)
            if not parts:
                m["weeks"][week] = {
                    "uploaded": True,
                    "parts": [],
                    "note": "no downloadable days",
                }
                save_manifest(s, m)
                continue
            if args.no_upload:
                log(
                    f"{s} {week}: built {len(parts)} part(s), {sum(p['bytes'] for p in parts) / 1e6:.0f} MB (not uploaded)"
                )
                continue
            for part in parts:
                part["release"] = upload_part(s, week, part)
                log(
                    f"{s} {week}: uploaded {part['asset']} ({part['bytes'] / 1e6:.0f} MB, {part['rows']:,} rows)"
                )
            for part in parts:
                Path(part.pop("path")).unlink(missing_ok=True)
                for day in part["days"]:
                    (STAGE_DIR / s / f"{day}.parquet").unlink(missing_ok=True)
            m["weeks"][week] = {"uploaded": True, "parts": parts}
            save_manifest(s, m)
        done += 1
        if done % 4 == 0:
            push_manifests(f"manifest: through {week}")
        if args.max_weeks and done >= args.max_weeks:
            break
    last_week = weeks[done - 1][0] if done else None
    if last_week:
        push_manifests(f"manifest: through {last_week}")
    log("done")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    for s in SERIES:
        m = load_manifest(s)
        days = m["days"].values()
        ok = [d for d in days if d.get("status") == "ok"]
        weeks = [w for w in m["weeks"].values() if w.get("uploaded")]
        size = sum(p["bytes"] for w in weeks for p in w.get("parts", []))
        dates = sorted(k for k, v in m["days"].items() if v.get("status") == "ok")
        print(
            f"{s}: {len(ok)} days archived ({dates[0] if dates else '-'}..{dates[-1] if dates else '-'}), "
            f"{sum(d['rows'] for d in ok):,} rows, "
            f"{sum(1 for d in days if d.get('status') == 'cold')} cold, "
            f"{len(weeks)} weeks uploaded ({size / 1e9:.2f} GB)"
        )
    return 0


def cmd_verify_db(args: argparse.Namespace) -> int:
    """Pass only if every scrape_log day in the DB is archived with the same row count."""
    db = sqlite3.connect(
        f"file:{Path(args.db).as_posix()}?mode=ro", uri=True, timeout=60
    )
    logged = dict(
        db.execute(
            "SELECT scrape_date, rows_fetched FROM scrape_log WHERE status='success'"
        ).fetchall()
    )
    db.close()
    m = load_manifest("sec-eq")
    uploaded_days = {
        d
        for w in m["weeks"].values()
        if w.get("uploaded")
        for p in w.get("parts", [])
        for d in p["days"]
    }
    bad = []
    for day, rows in sorted(logged.items()):
        entry = m["days"].get(day, {})
        if entry.get("status") != "ok" or day not in uploaded_days:
            bad.append((day, rows, "not archived yet"))
        elif int(entry["rows"]) != int(rows):
            bad.append((day, rows, f"archive has {entry['rows']:,}"))
    total = sum(logged.values())
    if bad:
        print(f"FAIL: {len(bad)} of {len(logged)} DB days are not safely archived:")
        for day, rows, why in bad[:60]:
            print(f"  {day}: DB {rows:,} rows -- {why}")
        return 1
    print(
        f"PASS: all {len(logged)} DB days ({min(logged)}..{max(logged)}, {total:,} rows) are archived and uploaded with matching row counts."
    )
    print("The local swaps.db can be deleted.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--series", default="sec-eq,cftc-eq")
    r.add_argument("--start")
    r.add_argument("--end")
    r.add_argument("--max-weeks", type=int, default=0)
    r.add_argument("--no-upload", action="store_true")
    sub.add_parser("status")
    v = sub.add_parser("verify-db")
    v.add_argument("--db", default=os.environ.get("SWAPS_DB_PATH") or DEFAULT_DB)
    args = ap.parse_args()
    return {"run": cmd_run, "status": cmd_status, "verify-db": cmd_verify_db}[args.cmd](
        args
    )


if __name__ == "__main__":
    sys.exit(main())
