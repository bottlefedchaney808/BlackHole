"""Wires the dock to the real world: Kalshi cancel, process liveness, journal readback.

Imported only by the run path (`serve`). Unit tests never import this module,
so a test run never touches Kalshi or starts a runner.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from chart_app.kalshi_perps import PerpsClient
from launch_dock import spawn as spawn_mod
from launch_dock.launch import EVENT_DESK, KALSHI
from launch_dock.server import ARTIFACTS, create_server

_PERP_ART = ARTIFACTS / "perp_live"
_REPO = str(Path(__file__).resolve().parent.parent)


def make_cancel(subaccount: int = 0):
    """One client per call: a stop is rare and the credentials come from the lab .env."""
    def cancel(kalshi_ticker: str) -> int:
        client = PerpsClient(subaccount=subaccount, ticker=kalshi_ticker)
        return client.cancel_resting()
    return cancel


def cancel_equity(ticker: str) -> int:
    """Cancel a stock sleeve's resting non-stop orders on the agentic account."""
    from chart_app.run_live_equity import cancel_resting

    return cancel_resting(ticker)


def kill(pid: int) -> None:
    import subprocess

    subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, capture_output=True)


def alive(pid: int) -> bool:
    import subprocess

    result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
    return str(pid) in (result.stdout or "")


def cmdline(pid: int) -> str | None:
    import subprocess

    result = subprocess.run(
        ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine"],
        capture_output=True, text=True,
    )
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    return lines[1]


def read_session(journal: Path) -> dict | None:
    """The first `session` record in a perp journal. Missing or empty is None."""
    try:
        text = journal.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("type") == "session":
            return record
    return None


def _newest_journal(ticker: str, mode: str) -> Path | None:
    candidates = sorted(
        _PERP_ART.glob(f"{ticker}_{mode}_*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _journal_paths(ticker: str, kalshi: str, mode: str) -> set[Path]:
    """Journals the runner may have written. Chart ticker is what run_live_perp uses."""
    if not _PERP_ART.exists():
        return set()
    found: set[Path] = set()
    for name in {ticker, kalshi}:
        found.update(_PERP_ART.glob(f"{name}_{mode}_*.jsonl"))
    return found


def finish_launch(card: dict, argv: list[str], timeout_s: float = 15.0) -> dict:
    """Spawn detached, then wait up to timeout_s for the session record to exist.

    The session row the runner writes uses `type: session` (`Log.write` stamps
    `type`, not `kind`); `finish_from_journal` matches on `kind`, so the row is
    re-wrapped here before it is applied.
    """
    ticker = str(card.get("instrument") or "").upper()
    kalshi = KALSHI.get(ticker, ticker)
    mode = "live" if card.get("live") else "dry"
    # Runner journals are {chart ticker}_live_*.jsonl (BTC-PERP), not the Kalshi symbol.
    before = _journal_paths(ticker, kalshi, mode)
    updated = spawn_mod.spawn(
        card, argv, popen=_detached_popen, cwd=str(EVENT_DESK if card.get("seed") == "event_desk" else _REPO)
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(1.0)
        journals = [p for p in _journal_paths(ticker, kalshi, mode) if p not in before]
        for journal in journals:
            session = read_session(journal)
            if session:
                saved = None
                try:
                    from chart_app import profiles
                    saved = profiles.resolve(
                        updated.get("instrument"), updated.get("interval") or "15m"
                    ).get("saved_at")
                except Exception:
                    saved = None
                return spawn_mod.finish_from_journal(
                    updated, json.dumps({"kind": "session", **session}) + "\n", saved_at=saved
                )
    return updated


def finish_launch_readback(card: dict, timeout_s: float = 15.0) -> dict:
    """Poll the artifacts dir for the journal this launch just created.

    The runner writes `type: session`; `finish_from_journal` matches `kind`,
    so the row is re-wrapped before it is applied to the card.
    """
    ticker = str(card.get("instrument") or "").upper()
    kalshi = KALSHI.get(ticker, ticker)
    mode = "live" if card.get("live") else "dry"
    if not _PERP_ART.exists():
        return card
    before = _journal_paths(ticker, kalshi, mode)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(1.0)
        journals = [p for p in _journal_paths(ticker, kalshi, mode) if p not in before]
        for journal in journals:
            session = read_session(journal)
            if session:
                try:
                    from chart_app import profiles

                    saved = profiles.resolve(
                        card.get("instrument"), card.get("interval") or "15m"
                    ).get("saved_at")
                except Exception:
                    saved = None
                return spawn_mod.finish_from_journal(
                    card, json.dumps({"kind": "session", **session}) + "\n", saved_at=saved
                )
    return card


def _detached_popen(argv, log_name=None, **kw):
    import subprocess

    log_dir = ARTIFACTS / "launch_dock_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handle = open(log_dir / f"{log_name or 'launch'}.log", "ab", buffering=0)
    return subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT, **kw)


def build_dock():
    return create_server(
        ARTIFACTS,
        cancel=make_cancel(),
        cancel_equity=cancel_equity,
        kill=kill,
        alive=alive,
        popen=_detached_popen,
    )


if __name__ == "__main__":  # pragma: no cover
    build_dock().serve_forever()
