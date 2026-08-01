"""
Tests for the CLI-based trade journal.

Covers:
  - TradeEntry dataclass creation
  - TradeJournal add / list / close lifecycle
  - Stats computation (2 wins, 1 loss → 66.7% win rate)
  - Persistence to temp JSON, reload, verify
  - Corrupted JSON handling
"""

import json
import os
import tempfile
import pytest


# ---------------------------------------------------------------------------
# TradeEntry creation
# ---------------------------------------------------------------------------

class TestTradeEntry:
    """Verify TradeEntry dataclass works correctly."""

    @pytest.mark.unit
    def test_create_open_entry(self):
        """A TradeEntry with no exit_price should have status 'open'."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="SPY",
            direction="long",
            instrument="call",
            entry_price=450.00,
            exit_price=None,
            quantity=10,
            thesis="IV too high",
            predicted_outcome=500.00,
            actual_pnl=None,
            predicted_correct=None,
            status="open",
        )
        assert entry.ticker == "SPY"
        assert entry.direction == "long"
        assert entry.status == "open"
        assert entry.exit_price is None
        assert entry.actual_pnl is None

    @pytest.mark.unit
    def test_create_closed_entry(self):
        """A TradeEntry with an exit_price should have status 'closed'."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="AAPL",
            direction="short",
            instrument="put",
            entry_price=200.00,
            exit_price=180.00,
            quantity=5,
            thesis="earnings crush",
            predicted_outcome=1000.00,
            actual_pnl=100.00,
            predicted_correct=True,
            status="closed",
        )
        assert entry.ticker == "AAPL"
        assert entry.status == "closed"
        assert entry.exit_price == 180.00
        assert entry.actual_pnl == 100.00
        assert entry.predicted_correct is True

    @pytest.mark.unit
    def test_default_none_exit(self):
        """An entry created with exit_price=None should stay None."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:30:00",
            ticker="QQQ",
            direction="long",
            instrument="stock",
            entry_price=350.00,
            quantity=1,
            thesis="momentum play",
        )
        assert entry.exit_price is None
        assert entry.actual_pnl is None
        assert entry.predicted_correct is None


# ---------------------------------------------------------------------------
# TradeJournal add / list / close lifecycle
# ---------------------------------------------------------------------------

class TestTradeJournalLifecycle:
    """Verify adding, listing, and closing trades."""

    @pytest.fixture
    def journal(self):
        """A fresh in-memory journal backed by a temp file."""
        from trade_journal import TradeJournal
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("[]")
        f.close()
        obj = TradeJournal(path=f.name)
        yield obj
        os.unlink(f.name)

    @pytest.mark.unit
    def test_add_entry(self, journal):
        """Adding an entry should increase count."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="SPY",
            direction="long",
            instrument="call",
            entry_price=450.00,
            quantity=10,
            thesis="IV too high",
        )
        journal.add_entry(entry)
        assert len(journal._entries) == 1
        assert journal._entries[0].ticker == "SPY"

    @pytest.mark.unit
    def test_list_open_empty(self, journal):
        """list_open() should return empty list when no trades exist."""
        assert journal.list_open() == []

    @pytest.mark.unit
    def test_list_open_only_open(self, journal):
        """list_open() should only return trades with status 'open'."""
        from trade_journal import TradeEntry

        e1 = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="SPY",
            direction="long",
            instrument="call",
            entry_price=450.00,
            quantity=10,
            thesis="IV too high",
            status="open",
        )
        e2 = TradeEntry(
            timestamp="2026-07-29T11:00:00",
            ticker="AAPL",
            direction="short",
            instrument="put",
            entry_price=200.00,
            exit_price=180.00,
            quantity=5,
            thesis="earnings crush",
            actual_pnl=100.00,
            predicted_correct=True,
            status="closed",
        )
        journal.add_entry(e1)
        journal.add_entry(e2)
        open_trades = journal.list_open()
        assert len(open_trades) == 1
        assert open_trades[0].ticker == "SPY"

    @pytest.mark.unit
    def test_close_trade(self, journal):
        """close_trade should set exit_price, compute PnL, and set status to closed."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="SPY",
            direction="long",
            instrument="stock",
            entry_price=400.00,
            quantity=10,
            thesis="momentum",
            status="open",
        )
        journal.add_entry(entry)

        result = journal.close_trade("SPY", 420.00)
        assert result is True

        closed = journal._entries[0]
        assert closed.exit_price == 420.00
        assert closed.status == "closed"
        # long: entry 400, exit 420, qty 10 => PnL = (420 - 400) * 10 = 200
        assert closed.actual_pnl == 200.00

    @pytest.mark.unit
    def test_close_trade_not_found(self, journal):
        """close_trade should return False if no matching open trade exists."""
        from trade_journal import TradeEntry

        entry = TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="SPY",
            direction="long",
            instrument="stock",
            entry_price=400.00,
            quantity=10,
            thesis="momentum",
            status="open",
        )
        journal.add_entry(entry)

        result = journal.close_trade("AAPL", 100.00)
        assert result is False


# ---------------------------------------------------------------------------
# Stats computation
# ---------------------------------------------------------------------------

class TestTradeJournalStats:
    """Verify get_stats() returns correct metrics."""

    @pytest.fixture
    def journal_with_trades(self):
        """A journal with 2 winners and 1 loser, all closed."""
        from trade_journal import TradeJournal, TradeEntry
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("[]")
        f.close()
        obj = TradeJournal(path=f.name)

        # Win 1: long stock, +200
        obj.add_entry(TradeEntry(
            timestamp="2026-07-29T09:00:00",
            ticker="SPY",
            direction="long",
            instrument="stock",
            entry_price=400.00,
            exit_price=420.00,
            quantity=10,
            thesis="momentum",
            actual_pnl=200.00,
            predicted_correct=True,
            status="closed",
        ))

        # Win 2: short put, +150
        obj.add_entry(TradeEntry(
            timestamp="2026-07-29T09:30:00",
            ticker="AAPL",
            direction="short",
            instrument="put",
            entry_price=200.00,
            exit_price=185.00,
            quantity=10,
            thesis="earnings crush",
            actual_pnl=150.00,
            predicted_correct=True,
            status="closed",
        ))

        # Loss: long call, -100
        obj.add_entry(TradeEntry(
            timestamp="2026-07-29T10:00:00",
            ticker="QQQ",
            direction="long",
            instrument="call",
            entry_price=50.00,
            exit_price=40.00,
            quantity=10,
            thesis="breakout fail",
            actual_pnl=-100.00,
            predicted_correct=False,
            status="closed",
        ))

        yield obj
        os.unlink(f.name)

    @pytest.mark.unit
    def test_win_rate(self, journal_with_trades):
        """2 wins out of 3 → 66.7% win rate."""
        stats = journal_with_trades.get_stats()
        assert stats["win_rate"] == pytest.approx(66.6667, abs=0.01)

    @pytest.mark.unit
    def test_total_pnl(self, journal_with_trades):
        """Total PnL = 200 + 150 - 100 = 250."""
        stats = journal_with_trades.get_stats()
        assert stats["total_pnl"] == 250.0

    @pytest.mark.unit
    def test_avg_win_and_loss(self, journal_with_trades):
        """Avg win = (200 + 150) / 2 = 175. Avg loss = -100."""
        stats = journal_with_trades.get_stats()
        assert stats["avg_win"] == pytest.approx(175.0)
        assert stats["avg_loss"] == pytest.approx(-100.0)

    @pytest.mark.unit
    def test_trade_count(self, journal_with_trades):
        """trade_count should be 3."""
        stats = journal_with_trades.get_stats()
        assert stats["trade_count"] == 3

    @pytest.mark.unit
    def test_stats_empty_journal(self):
        """get_stats() on empty journal should return zeros."""
        from trade_journal import TradeJournal
        obj = TradeJournal(path="/dev/null")
        stats = obj.get_stats()
        assert stats["trade_count"] == 0
        assert stats["total_pnl"] == 0.0
        assert stats["win_rate"] == 0.0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    """Verify JSON save/load round-trip."""

    @pytest.mark.unit
    def test_save_and_reload(self):
        """Trades written to JSON should survive a reload."""
        from trade_journal import TradeJournal, TradeEntry

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("[]")
            tmp = f.name

        try:
            j1 = TradeJournal(path=tmp)
            j1.add_entry(TradeEntry(
                timestamp="2026-07-29T10:00:00",
                ticker="SPY",
                direction="long",
                instrument="call",
                entry_price=450.00,
                exit_price=470.00,
                quantity=10,
                thesis="IV too high",
                actual_pnl=200.00,
                predicted_correct=True,
                status="closed",
            ))
            j1._save()

            j2 = TradeJournal(path=tmp)
            assert len(j2._entries) == 1
            assert j2._entries[0].ticker == "SPY"
            assert j2._entries[0].actual_pnl == 200.0

        finally:
            os.unlink(tmp)

    @pytest.mark.unit
    def test_corrupted_json(self):
        """Corrupted JSON file should produce an empty journal (no crash)."""
        from trade_journal import TradeJournal

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("THIS IS NOT VALID JSON {{{")
            tmp = f.name

        try:
            j = TradeJournal(path=tmp)
            assert len(j._entries) == 0
        finally:
            os.unlink(tmp)

    @pytest.mark.unit
    def test_missing_file(self):
        """Non-existent file should produce an empty journal (no crash)."""
        from trade_journal import TradeJournal

        j = TradeJournal(path="/tmp/nonexistent_journal_xyz.json")
        assert len(j._entries) == 0

    @pytest.mark.unit
    def test_close_trade_persists(self):
        """Closing a trade should be reflected after reload."""
        from trade_journal import TradeJournal, TradeEntry

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("[]")
            tmp = f.name

        try:
            j1 = TradeJournal(path=tmp)
            j1.add_entry(TradeEntry(
                timestamp="2026-07-29T10:00:00",
                ticker="SPY",
                direction="long",
                instrument="stock",
                entry_price=400.00,
                quantity=10,
                thesis="momentum",
                status="open",
            ))
            j1.close_trade("SPY", 420.00)

            j2 = TradeJournal(path=tmp)
            assert len(j2._entries) == 1
            assert j2._entries[0].status == "closed"
            assert j2._entries[0].exit_price == 420.00
        finally:
            os.unlink(tmp)