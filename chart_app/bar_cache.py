from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from shared.chart_data import CandleRecord


class BarCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    ticker TEXT,
                    interval TEXT,
                    ts TEXT,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    PRIMARY KEY (ticker, interval, ts)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    ticker TEXT NOT NULL,
                    interval TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def upsert(self, ticker: str, interval: str, records: list[CandleRecord]) -> int:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO bars
                    (ticker, interval, ts, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ticker,
                        interval,
                        record.timestamp.isoformat(),
                        record.open,
                        record.high,
                        record.low,
                        record.close,
                        record.volume,
                    )
                    for record in records
                ],
            )
        return len(records)

    def load(self, ticker: str, interval: str) -> list[CandleRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, open, high, low, close, volume
                FROM bars
                WHERE ticker = ? AND interval = ?
                ORDER BY ts ASC
                """,
                (ticker, interval),
            ).fetchall()
        return [
            CandleRecord(
                timestamp=datetime.fromisoformat(ts),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
            for ts, open_, high, low, close, volume in rows
        ]

    def last_ts(self, ticker: str, interval: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ts
                FROM bars
                WHERE ticker = ? AND interval = ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (ticker, interval),
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row[0])

    def get_session(self) -> tuple[str, str] | None:
        """Last ticker/interval the user was viewing, so a restart reopens there."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ticker, interval FROM session WHERE id = 1"
            ).fetchone()
        return (row[0], row[1]) if row else None

    def set_session(self, ticker: str, interval: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session (id, ticker, interval) VALUES (1, ?, ?)
                ON CONFLICT (id) DO UPDATE SET ticker = excluded.ticker, interval = excluded.interval
                """,
                (ticker, interval),
            )
