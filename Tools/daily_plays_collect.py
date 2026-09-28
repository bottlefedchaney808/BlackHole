"""Daily Plays collect orchestrator.

Fans out configured discovery producers, unions tickers, runs sentiment over
that universe, and writes collect.json + collect_status.json.

Producer I/O lives behind callables so tests inject fakes. main() wires the
real ones. No live network in tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ENABLED = ("rumors", "x", "sentiment")
DEFAULT_REPO = Path("E:/BlackHole_Investments/BlackHole")
DEFAULT_ARTIFACT = Path("C:/Users/bottl/hermes-artifacts/artifacts/daily-plays")
RUMOR_SCANNER = Path(
    "C:/Users/bottl/.hermes/profiles/gork/skills/stock-rumors-slot3/scripts/rumor_scanner.py"
)
X_BUZZ_REL = Path("trading_journal/x_buzz/LATEST.md")
SENTIMENT_MAIN_REL = Path("sentiment-scanner/main.py")
ALLOWED_NULL_ERRORS = frozenset({"no_narrative_messages"})

_CASHTAG_CELL = re.compile(r"^\$?[A-Za-z][A-Za-z0-9.\-]{0,9}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_asof(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_run_id(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def parse_enabled(raw: str | None) -> tuple[str, ...]:
    if raw is None or not str(raw).strip():
        return DEFAULT_ENABLED
    parts = tuple(p.strip() for p in str(raw).split(",") if p.strip())
    return parts or DEFAULT_ENABLED


def _norm_ticker(value) -> str:
    return str(value or "").strip().lstrip("$").upper()


def load_reddit_tickers() -> list[str]:
    raise RuntimeError("reddit-reading not wired")


def parse_cashtag_table(text: str) -> list[str]:
    """Tickers from the x_buzz LATEST.md cashtag table."""
    idx = text.find("Extracted cashtags")
    section = text[idx:] if idx >= 0 else text
    out: list[str] = []
    seen: set[str] = set()
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        raw = cells[0].lstrip("$").strip()
        if not raw or raw.lower() == "ticker" or set(raw) <= {"-", ":"}:
            continue
        if not _CASHTAG_CELL.match("$" + raw if not raw.startswith("$") else raw) and not _CASHTAG_CELL.match(raw):
            continue
        ticker = _norm_ticker(raw)
        if not ticker or ticker == "UNTAGGED" or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


def load_x_tickers(repo: Path) -> list[str]:
    path = Path(repo) / X_BUZZ_REL
    if not path.is_file():
        raise RuntimeError(f"x_buzz missing: {path}")
    return parse_cashtag_table(path.read_text(encoding="utf-8"))


def _json_blob(text: str):
    text = (text or "").strip()
    if not text:
        raise ValueError("empty JSON")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        fenced = re.search(r"```json\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
        if fenced:
            return json.loads(fenced.group(1))
        inline = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if inline:
            return json.loads(inline.group(1))
        raise


def parse_rumor_tickers(stdout: str) -> list[str]:
    payload = _json_blob(stdout)
    out: list[str] = []
    seen: set[str] = set()

    def add(value) -> None:
        ticker = _norm_ticker(value)
        if ticker and ticker not in seen:
            seen.add(ticker)
            out.append(ticker)

    if isinstance(payload, dict):
        for sig in payload.get("signals") or []:
            if isinstance(sig, dict):
                add(sig.get("ticker") or sig.get("symbol"))
            else:
                add(sig)
        for key in ("tickers", "symbols"):
            for item in payload.get(key) or []:
                if isinstance(item, dict):
                    add(item.get("ticker") or item.get("symbol"))
                else:
                    add(item)
        if payload.get("ticker"):
            add(payload.get("ticker"))
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                add(item.get("ticker") or item.get("symbol"))
            else:
                add(item)
    return out


def load_rumor_tickers(repo: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, str(RUMOR_SCANNER), "--output", "json"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"rumors exit {proc.returncode}"
        raise RuntimeError(err)
    try:
        return parse_rumor_tickers(proc.stdout)
    except Exception as e:
        raise RuntimeError(f"rumors JSON parse failed: {e}") from e


def parse_output_path(stdout: str) -> str | None:
    for line in (stdout or "").splitlines():
        if line.startswith("OUTPUT "):
            return line[len("OUTPUT "):].strip() or None
    return None


def _scanner_block(scanners: dict, slug: str) -> dict:
    raw = scanners.get(slug)
    return raw if isinstance(raw, dict) else {}


def map_sentiment_row(row: dict | None) -> dict:
    src = row or {}
    narr_raw = src.get("narrative")
    narr = dict(narr_raw) if isinstance(narr_raw, dict) else {}
    oi_raw = src.get("oi")
    oi = oi_raw if isinstance(oi_raw, dict) else {}
    scanners_raw = src.get("scanners")
    scanners = scanners_raw if isinstance(scanners_raw, dict) else {}
    iv = _scanner_block(scanners, "iv_rank")
    skew_scan = _scanner_block(scanners, "skew")
    pain = _scanner_block(scanners, "max_pain")
    uoi = _scanner_block(scanners, "unusual_oi")
    if iv.get("status") == "ok" and iv.get("atm_iv") is not None:
        atm_iv = iv.get("atm_iv")
    else:
        atm_iv = oi.get("atm_iv")
    if skew_scan.get("status") == "ok":
        skew = skew_scan.get("skew_signal")
    else:
        skew = oi.get("skew_vol_pts")
    stats = {
        "spot": oi.get("spot"),
        "atm_iv": atm_iv,
        "iv_rank": iv.get("iv_rank") if iv.get("status") == "ok" else None,
        "skew": skew,
        "max_pain": pain.get("max_pain_strike") if pain.get("status") == "ok" else None,
        "gex": None,
        "oi_surge": bool(uoi.get("surge_detected")) if uoi.get("status") == "ok" else None,
    }
    return {
        "narrative": narr,
        "stats": stats,
        "errors": list(src.get("errors") or []),
    }


def run_sentiment(tickers: list[str], repo: Path) -> dict:
    py = Path(repo) / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    main_py = Path(repo) / SENTIMENT_MAIN_REL
    cmd = [
        str(py),
        str(main_py),
        "--universe",
        ",".join(tickers),
        "--skip-gex",
        "--skip-youtube",
        "--skip-sector-prompt",
        "--skip-report-prompt",
        "--no-loop",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip() or f"sentiment exit {proc.returncode}"
        raise RuntimeError(err)
    out_path = parse_output_path(proc.stdout)
    if not out_path:
        raise RuntimeError("sentiment OUTPUT path missing")
    data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        raise RuntimeError("sentiment results missing")
    return {_norm_ticker(t): map_sentiment_row(row) for t, row in results.items()}


def default_producers(repo: Path) -> dict:
    return {
        "rumors": lambda: load_rumor_tickers(repo),
        "x": lambda: load_x_tickers(repo),
        "reddit": load_reddit_tickers,
        "sentiment": lambda tickers: run_sentiment(list(tickers), repo),
    }


def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _write_status(artifact_dir: Path, *, ok: bool, error: str | None, asof_utc: str, run_id: str | None = None) -> None:
    payload = {"ok": ok, "error": error, "asof_utc": asof_utc}
    if ok:
        payload["run_id"] = run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "collect_status.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _throwing_errors(errors) -> list[str]:
    bad = []
    for item in errors or []:
        text = str(item) if item is not None else ""
        if text and text not in ALLOWED_NULL_ERRORS:
            bad.append(text)
    return bad


def _assert_no_sentiment_throws(rows: dict) -> None:
    problems = []
    for ticker, row in (rows or {}).items():
        row = row or {}
        bad = _throwing_errors(row.get("errors"))
        if bad:
            problems.append(f"{ticker}: {', '.join(bad)}")
    if problems:
        raise RuntimeError("sentiment throw: " + "; ".join(problems))


def _discover(enabled: tuple[str, ...], producers: dict) -> list[str]:
    universe: list[str] = []
    seen: set[str] = set()
    for name in enabled:
        if name == "sentiment":
            continue
        fn = producers.get(name)
        if fn is None:
            raise RuntimeError(f"producer {name} not wired")
        for raw in fn() or []:
            ticker = _norm_ticker(raw)
            if ticker and ticker not in seen:
                seen.add(ticker)
                universe.append(ticker)
    return universe


def run_collect(*, artifact_dir, producers=None, enabled=None, repo=None) -> int:
    artifact_dir = Path(artifact_dir)
    enabled_tuple = tuple(enabled) if enabled is not None else DEFAULT_ENABLED
    producers = producers if producers is not None else {}
    now = utc_now()
    asof = format_asof(now)
    run_id = format_run_id(now)
    try:
        if "reddit" in enabled_tuple:
            raise RuntimeError("reddit-reading not wired")
        universe = _discover(enabled_tuple, producers)
        if not universe:
            raise RuntimeError("empty universe")
        sentiment_rows: dict = {}
        if "sentiment" in enabled_tuple:
            fn = producers.get("sentiment")
            if fn is None:
                raise RuntimeError("producer sentiment not wired")
            sentiment_rows = fn(universe) or {}
            _assert_no_sentiment_throws(sentiment_rows)
        tickers_out = {}
        for ticker in universe:
            row = sentiment_rows.get(ticker) or {}
            tickers_out[ticker] = {
                "narrative": row.get("narrative") or {},
                "stats": row.get("stats") or {},
                "errors": list(row.get("errors") or []),
            }
        collect = {
            "asof_utc": asof,
            "run_id": run_id,
            "tickers": tickers_out,
        }
        _atomic_write_json(artifact_dir / "collect.json", collect)
        _write_status(artifact_dir, ok=True, error=None, asof_utc=asof, run_id=run_id)
        return 0
    except Exception as e:
        _write_status(artifact_dir, ok=False, error=str(e), asof_utc=asof)
        return 1


def resolve_repo() -> Path:
    raw = os.environ.get("BLACKHOLE_REPO")
    return Path(raw) if raw else DEFAULT_REPO


def resolve_artifact() -> Path:
    raw = os.environ.get("DAILY_PLAYS_ARTIFACT")
    return Path(raw) if raw else DEFAULT_ARTIFACT


def main(argv=None) -> int:
    repo = resolve_repo()
    artifact = resolve_artifact()
    enabled = parse_enabled(os.environ.get("DAILY_PLAYS_PRODUCERS"))
    return run_collect(
        artifact_dir=artifact,
        producers=default_producers(repo),
        enabled=enabled,
        repo=repo,
    )


if __name__ == "__main__":
    sys.exit(main())
