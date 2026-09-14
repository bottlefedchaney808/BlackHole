"""console_book.py -- the desk's second book: tickers and positions you add.

The real book is whatever the broker holds (pushed into widget_cache's
``positions`` row). The console book is everything else you want the desk to
treat as "yours": a name a screener or highlight pack surfaced, a hypothetical
equity position, an option leg you are considering. The two are combined into
one book everywhere a tool asks "what is in my book" -- the basket, signals,
surfaces, position analysis -- with every row tagged ``book: real|console`` so
nothing hypothetical is ever mistaken for something you hold.

Stored as a table in the same SQLite file as the widget cache (WAL, busy
timeout), so the dashboard, the chart tab and scripted callers all see one
console book.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

KINDS = ("ticker", "equity", "option")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ticker TEXT NOT NULL,
    qty REAL,
    avg_price REAL,
    expiry TEXT,
    strike REAL,
    right TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    note TEXT,
    created_at TEXT NOT NULL
)
"""


class ConsoleBookError(ValueError):
    """An entry that cannot be stored as given (reported as a 400)."""


def _clean_ticker(raw: Any) -> str:
    ticker = str(raw or "").strip().upper()
    if (
        not ticker
        or len(ticker) > 12
        or not all(c.isalnum() or c in ".-/" for c in ticker)
    ):
        raise ConsoleBookError(f"not a ticker: {raw!r}")
    return ticker


def _opt_float(raw: Any, name: str) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ConsoleBookError(f"{name} must be a number, got {raw!r}") from None


def _clean_expiry(raw: Any) -> str:
    text = str(raw or "").strip()
    digits = text.replace("-", "")
    if len(digits) != 8 or not digits.isdigit():
        raise ConsoleBookError(f"expiry must be YYYY-MM-DD, got {raw!r}")
    try:
        return (
            datetime.strptime(digits, "%Y%m%d").replace(tzinfo=UTC).strftime("%Y-%m-%d")
        )
    except ValueError:
        raise ConsoleBookError(f"expiry is not a date: {raw!r}") from None


def normalize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Validate one entry. A bare ticker is kind 'ticker'; qty makes it a
    position; strike/expiry/right make it an option leg."""
    if not isinstance(entry, dict):
        raise ConsoleBookError("each entry must be an object")
    ticker = _clean_ticker(entry.get("ticker"))
    kind = str(entry.get("kind") or "").strip().lower()
    if not kind:
        if entry.get("strike") not in (None, "") or entry.get("right"):
            kind = "option"
        elif entry.get("qty") not in (None, ""):
            kind = "equity"
        else:
            kind = "ticker"
    if kind not in KINDS:
        raise ConsoleBookError(f"kind must be one of {KINDS}, got {kind!r}")

    out: dict[str, Any] = {
        "kind": kind,
        "ticker": ticker,
        "qty": None,
        "avg_price": None,
        "expiry": None,
        "strike": None,
        "right": None,
        "source": str(entry.get("source") or "manual").strip()[:80] or "manual",
        "note": (str(entry.get("note")).strip()[:300] or None)
        if entry.get("note")
        else None,
    }
    if kind in ("equity", "option"):
        qty = _opt_float(entry.get("qty"), "qty")
        if qty is None or qty == 0:
            raise ConsoleBookError(
                f"a {kind} position needs a non-zero qty (negative = short)"
            )
        out["qty"] = qty
        out["avg_price"] = _opt_float(entry.get("avg_price"), "avg_price")
    if kind == "option":
        strike = _opt_float(entry.get("strike"), "strike")
        if strike is None or strike <= 0:
            raise ConsoleBookError("an option leg needs a positive strike")
        right = str(entry.get("right") or "").strip().upper()[:1]
        if right not in ("C", "P"):
            raise ConsoleBookError("an option leg needs right C or P")
        out["strike"] = strike
        out["right"] = right
        out["expiry"] = _clean_expiry(entry.get("expiry"))
    return out


class ConsoleBook:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        return conn

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM console_book ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def add(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add entries (all validated before any is written). A bare ticker
        already in the console book is skipped rather than duplicated, so
        clicking a pack twice does not double it."""
        clean = [normalize_entry(e) for e in entries]
        now = datetime.now(UTC).isoformat()
        added: list[dict[str, Any]] = []
        with self._connect() as conn:
            watched = {
                r["ticker"]
                for r in conn.execute(
                    "SELECT ticker FROM console_book WHERE kind='ticker'"
                )
            }
            for e in clean:
                if e["kind"] == "ticker" and e["ticker"] in watched:
                    continue
                cur = conn.execute(
                    "INSERT INTO console_book (kind, ticker, qty, avg_price, expiry, strike,"
                    " right, source, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        e["kind"],
                        e["ticker"],
                        e["qty"],
                        e["avg_price"],
                        e["expiry"],
                        e["strike"],
                        e["right"],
                        e["source"],
                        e["note"],
                        now,
                    ),
                )
                if e["kind"] == "ticker":
                    watched.add(e["ticker"])
                added.append({**e, "id": cur.lastrowid, "created_at": now})
        return added

    def remove(self, entry_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM console_book WHERE id = ?", (int(entry_id),)
            )
        return cur.rowcount > 0

    def clear(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM console_book")
        return cur.rowcount


def console_entry_as_position(entry: dict[str, Any]) -> dict[str, Any]:
    """A console entry in the real book's position-row shape, tagged console."""
    kind = entry.get("kind")
    return {
        "book": "console",
        "console_id": entry.get("id"),
        "account": "console",
        "ticker": entry.get("ticker"),
        "instrument_type": {"ticker": "watch"}.get(kind, kind),
        "qty": entry.get("qty"),
        "avg_price": entry.get("avg_price"),
        "current_price": None,
        "market_value": None,
        "unrealized_pl": None,
        "expiry": entry.get("expiry"),
        "strike": entry.get("strike"),
        "right": entry.get("right"),
        "source": entry.get("source"),
        "note": entry.get("note"),
    }


def combine_books(
    real_positions: list[dict[str, Any]], console_entries: list[dict[str, Any]]
) -> dict[str, Any]:
    """The one book every tool reads: real rows then console rows, tagged."""
    real = [dict(p, book="real") for p in (real_positions or []) if isinstance(p, dict)]
    console = [console_entry_as_position(e) for e in (console_entries or [])]

    def _tickers(rows: list[dict[str, Any]]) -> list[str]:
        return sorted({str(r.get("ticker")).upper() for r in rows if r.get("ticker")})

    real_tickers = _tickers(real)
    console_tickers = _tickers(console)
    return {
        "positions": real + console,
        "real_positions": real,
        "console_positions": console,
        "real_tickers": real_tickers,
        "console_tickers": console_tickers,
        "held_tickers": sorted(set(real_tickers) | set(console_tickers)),
    }
