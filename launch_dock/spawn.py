from __future__ import annotations

import json
from subprocess import DEVNULL
from typing import Callable, Iterable

from .launch import DETACH, apply_session
from .status import mark_stale, reap


def spawn(card: dict, argv: Iterable[str], *, popen: Callable, cwd: str) -> dict:
    """Start a detached runner through the injected Popen implementation."""
    argv = list(argv)
    process = popen(argv, creationflags=DETACH, cwd=cwd, stdin=DEVNULL)
    out = dict(card)
    out["pid"] = process.pid
    out["argv"] = argv
    out["state"] = "launching"
    return out


def finish_from_journal(
    card: dict,
    journal_text: str,
    saved_at: str | None = None,
) -> dict:
    """Apply the first session record found in a runner journal."""
    for line in journal_text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("kind") == "session":
            return apply_session(card, record, saved_at=saved_at)
    return dict(card)


def poll(
    cards: Iterable[dict],
    *,
    alive: Callable[[int], bool],
    saved_at_for: Callable[[dict], str | None],
) -> list[dict]:
    """Reap exited processes and mark running perp cards stale when needed."""
    out = []
    for card in cards:
        pid = card.get("pid")
        current = reap(card, alive(pid) if pid else False)
        current = mark_stale(current, saved_at_for(current))
        out.append(current)
    return out
