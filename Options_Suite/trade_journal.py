"""
CLI-based trade journal to log executed trading views, track them against
outcomes, and measure strategy performance.

Usage:
    python trade_journal.py --add          # interactive
    python trade_journal.py --list         # open positions
    python trade_journal.py --all          # all trades
    python trade_journal.py --close TICKER --price 4.50
    python trade_journal.py --stats        # performance
"""

import argparse
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "trade_journal.json"
)

_DIRECTION_CHOICES = ("long", "short")
_INSTRUMENT_CHOICES = ("call", "put", "vertical", "vs", "spread", "stock")
_STATUS_CHOICES = ("open", "closed")


# ---------------------------------------------------------------------------
# TradeEntry
# ---------------------------------------------------------------------------


@dataclass
class TradeEntry:
    """A single trade record.

    Attributes:
        timestamp: ISO-format datetime string.
        ticker: Ticker symbol.
        direction: 'long' or 'short'.
        instrument: 'call', 'put', 'vertical', 'vs', 'spread', or 'stock'.
        entry_price: Price at entry.
        exit_price: Price at exit (None if still open).
        quantity: Number of contracts / shares.
        thesis: Short description of the trade rationale.
        predicted_outcome: Expected P&L at entry (None if not recorded).
        actual_pnl: Realised P&L, computed on close (None if open).
        predicted_correct: Whether direction matched outcome (None if open).
        status: 'open' or 'closed'.
    """

    timestamp: str
    ticker: str
    direction: str
    instrument: str
    entry_price: float
    quantity: int
    thesis: str = ""
    exit_price: float | None = None
    predicted_outcome: float | None = None
    actual_pnl: float | None = None
    predicted_correct: bool | None = None
    status: str = "open"


# ---------------------------------------------------------------------------
# TradeJournal
# ---------------------------------------------------------------------------


class TradeJournal:
    """In-memory journal backed by a JSON file for persistence."""

    def __init__(self, path: str | None = None):
        self._path = path or _DEFAULT_PATH
        self._entries: list[TradeEntry] = []
        self._load()

    # ---- public API -------------------------------------------------------

    def add_entry(self, entry: TradeEntry) -> None:
        """Append a trade and persist."""
        self._entries.append(entry)
        self._save()

    def close_trade(self, ticker: str, exit_price: float) -> bool:
        """Close the first open trade matching *ticker*.

        Computes actual PnL, sets predicted_correct, and persists.
        Returns True if a trade was closed, False if none found.
        """
        for entry in self._entries:
            if entry.ticker.upper() == ticker.upper() and entry.status == "open":
                entry.exit_price = exit_price
                entry.status = "closed"

                # Compute PnL
                multiplier = 1.0 if entry.direction == "long" else -1.0
                entry.actual_pnl = round(
                    (exit_price - entry.entry_price) * entry.quantity * multiplier,
                    2,
                )

                # Determine predicted correctness
                if entry.predicted_outcome is not None:
                    entry.predicted_correct = (entry.actual_pnl > 0) == (
                        entry.predicted_outcome > 0
                    )

                self._save()
                return True
        return False

    def list_open(self) -> list[TradeEntry]:
        """Return all trades with status 'open'."""
        return [e for e in self._entries if e.status == "open"]

    def get_stats(self) -> dict[str, float]:
        """Compute summary performance statistics.

        Returns a dict with:
            trade_count, win_rate, total_pnl, avg_win, avg_loss,
            max_drawdown, sharpe_approx
        """
        closed = [
            e
            for e in self._entries
            if e.status == "closed" and e.actual_pnl is not None
        ]
        total = len(closed)

        if total == 0:
            return {
                "trade_count": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_drawdown": 0.0,
                "sharpe_approx": 0.0,
            }

        pnls: list[float] = [e.actual_pnl for e in closed]  # type: ignore — all non-None by filter above
        total_pnl = sum(pnls)
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]
        win_count = len(winners)
        win_rate = (win_count / total) * 100.0

        avg_win = sum(winners) / win_count if winners else 0.0
        avg_loss = sum(losers) / len(losers) if losers else 0.0

        # Max drawdown: largest peak-to-trough decline in cumulative PnL
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)

        # Approximate Sharpe (annualised, risk-free ≈ 0)
        if total >= 2 and max(pnls) != min(pnls):
            mean_pnl = sum(pnls) / total
            variance = sum((p - mean_pnl) ** 2 for p in pnls) / (total - 1)
            std = math.sqrt(variance) if variance > 0 else 1.0
            # Annualise assuming ~252 trading days per year, but each "trade"
            # is a single observation — use a conservative scaling factor
            sharpe_approx = round((mean_pnl / std) * math.sqrt(252 / total), 4)
        else:
            sharpe_approx = 0.0

        return {
            "trade_count": total,
            "win_rate": round(win_rate, 2),
            "total_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown": round(max_dd, 2),
            "sharpe_approx": sharpe_approx,
        }

    # ---- persistence ------------------------------------------------------

    def _save(self) -> None:
        """Serialise *self._entries* to the JSON file."""
        data = [asdict(e) for e in self._entries]
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        """Deserialise entries from the JSON file (silent on failure)."""
        try:
            with open(self._path, "r") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            self._entries = []
            return

        if not isinstance(raw, list):
            self._entries = []
            return

        self._entries = []
        for item in raw:
            try:
                self._entries.append(TradeEntry(**item))
            except (TypeError, KeyError):
                continue


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


def _prompt_str(prompt: str, default: str = "") -> str:
    val = input(f"{prompt} [{default}]: ").strip()
    return val if val else default


def _prompt_float(prompt: str) -> float:
    while True:
        try:
            return float(input(f"{prompt}: ").strip())
        except ValueError:
            print("  Please enter a valid number.")


def _prompt_int(prompt: str) -> int:
    while True:
        try:
            return int(input(f"{prompt}: ").strip())
        except ValueError:
            print("  Please enter a valid integer.")


def _prompt_choice(prompt: str, choices: tuple) -> str:
    while True:
        val = input(f"{prompt} ({'/'.join(choices)}): ").strip().lower()
        if val in choices:
            return val
        print(f"  Please choose one of: {'/'.join(choices)}")


def _prompt_optional_float(prompt: str) -> float | None:
    val = input(f"{prompt} (press Enter to skip): ").strip()
    return float(val) if val else None


def _format_entry(e: TradeEntry, idx: int = 0) -> str:
    pnl_str = f"${e.actual_pnl:+.2f}" if e.actual_pnl is not None else "—"
    exit_str = f"${e.exit_price:.2f}" if e.exit_price is not None else "—"
    correct_str = (
        "✓" if e.predicted_correct else "✗" if e.predicted_correct is False else "—"
    )
    return (
        f"  [{idx}] {e.ticker:6s} | {e.direction:5s} | {e.instrument:8s} | "
        f"Entry ${e.entry_price:<8.2f} | Exit {exit_str:>8s} | "
        f"Qty {e.quantity:<4d} | PnL {pnl_str:>8s} | {correct_str} | "
        f"{e.status} | {e.thesis}"
    )


def _format_stats(stats: dict[str, float]) -> str:
    return (
        f"\n{'=' * 52}\n"
        f"  Performance Summary\n"
        f"{'=' * 52}\n"
        f"  Trades closed : {stats['trade_count']}\n"
        f"  Win rate      : {stats['win_rate']:.2f}%\n"
        f"  Total PnL     : ${stats['total_pnl']:+.2f}\n"
        f"  Avg win       : ${stats['avg_win']:+.2f}\n"
        f"  Avg loss      : ${stats['avg_loss']:+.2f}\n"
        f"  Max drawdown  : ${stats['max_drawdown']:.2f}\n"
        f"  Sharpe (approx): {stats['sharpe_approx']:.4f}\n"
        f"{'=' * 52}\n"
    )


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


def cmd_add(journal: TradeJournal) -> None:
    """Interactively add a new trade entry."""
    print("\n--- Add New Trade ---")
    ticker = _prompt_str("Ticker").upper()
    direction = _prompt_choice("Direction", _DIRECTION_CHOICES)
    instrument = _prompt_choice("Instrument", _INSTRUMENT_CHOICES)
    entry_price = _prompt_float("Entry price")
    quantity = _prompt_int("Quantity")
    thesis = _prompt_str("Thesis")
    predicted = _prompt_optional_float("Predicted P&L")

    entry = TradeEntry(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        ticker=ticker,
        direction=direction,
        instrument=instrument,
        entry_price=entry_price,
        quantity=quantity,
        thesis=thesis,
        predicted_outcome=predicted,
        status="open",
    )
    journal.add_entry(entry)
    print(f"  ✓ Trade in {ticker} logged.\n")


def cmd_list(journal: TradeJournal) -> None:
    """Display all open positions."""
    open_entries = journal.list_open()
    if not open_entries:
        print("  No open positions.")
        return
    print(f"\n--- Open Positions ({len(open_entries)}) ---")
    for i, e in enumerate(open_entries, 1):
        print(_format_entry(e, i))
    print()


def cmd_all(journal: TradeJournal) -> None:
    """Display all trades (open + closed)."""
    entries = journal._entries
    if not entries:
        print("  No trades recorded.")
        return
    print(f"\n--- All Trades ({len(entries)}) ---")
    for i, e in enumerate(entries, 1):
        print(_format_entry(e, i))
    print()


def cmd_close(journal: TradeJournal, ticker: str, price: float) -> None:
    """Close the first open trade matching *ticker*."""
    if journal.close_trade(ticker, price):
        print(f"  ✓ Closed {ticker.upper()} at ${price:.2f}.\n")
    else:
        print(f"  ✗ No open trade found for {ticker.upper()}.\n")


def cmd_stats(journal: TradeJournal) -> None:
    """Display performance statistics."""
    stats = journal.get_stats()
    print(_format_stats(stats))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI-based trade journal with persistence and stats.",
    )
    parser.add_argument(
        "--add", action="store_true", help="Add a new trade (interactive)"
    )
    parser.add_argument("--list", action="store_true", help="List open positions")
    parser.add_argument("--all", action="store_true", help="List all trades")
    parser.add_argument(
        "--close",
        type=str,
        metavar="TICKER",
        help="Close the first open trade matching TICKER",
    )
    parser.add_argument(
        "--price", type=float, default=None, help="Exit price (required with --close)"
    )
    parser.add_argument("--stats", action="store_true", help="Show performance stats")
    parser.add_argument(
        "--path", type=str, default=None, help="Path to the JSON journal file"
    )

    args = parser.parse_args()

    # Determine the action count
    actions = [args.add, args.list, args.all, args.close is not None, args.stats]
    if sum(1 for a in actions if a) != 1:
        parser.print_help()
        sys.exit(1)

    journal = TradeJournal(path=args.path)

    if args.add:
        cmd_add(journal)
    elif args.list:
        cmd_list(journal)
    elif args.all:
        cmd_all(journal)
    elif args.close:
        if args.price is None:
            print("Error: --close requires --price <exit price>")
            sys.exit(1)
        cmd_close(journal, args.close, args.price)
    elif args.stats:
        cmd_stats(journal)


if __name__ == "__main__":
    main()
