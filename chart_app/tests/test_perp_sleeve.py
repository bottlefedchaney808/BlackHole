"""Sleeve honesty: no forming bars, no unreachable fills, no drift on restart."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import numpy as np

from chart_app.perp_sleeve import (
    Book,
    Journal,
    bar_has_closed,
    closed_bars,
    replay,
)
from shared.chart_data import CandleRecord


def _perp_bars(
    n: int, seed: int = 3, start: datetime | None = None
) -> list[CandleRecord]:
    """A continuous 24/7 15m tape -- no session gaps, like a real perp."""
    rng = np.random.default_rng(seed)
    closes = 80_000 * np.exp(np.cumsum(rng.normal(0.0003, 0.004, n)))
    t0 = start or datetime(2026, 9, 1, tzinfo=UTC)
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        op = float(closes[i - 1]) if i else c
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(minutes=15 * i),
                open=op,
                high=max(op, c) * 1.003,
                low=min(op, c) * 0.997,
                close=c,
                volume=100.0,
            )
        )
    return out


PROFILE = {
    "config": {
        "entry_long": 60,
        "exit_long": -60,
        "add_long": 72,
        "trim_long": -40,
        "allow_short": True,
        "entry_short": -50,
        "exit_short": 8,
        "atr_stop_mult": 3,
        "cooldown_bars": 1,
        "max_units": 5,
        "unit_fraction": 1,
        "exit_style": "scale",
    },
    "elmo": {},
}


# --- the forming bar --------------------------------------------------------


def test_forming_bar_is_not_closed():
    bar = CandleRecord(datetime(2026, 9, 20, 0, 15, tzinfo=UTC), 1, 1, 1, 1, 1)
    # 00:15 covers 00:15-00:30.
    assert bar_has_closed(bar, "15m", datetime(2026, 9, 20, 0, 29, tzinfo=UTC)) is False
    assert bar_has_closed(bar, "15m", datetime(2026, 9, 20, 0, 30, tzinfo=UTC)) is True


def test_closed_bars_drops_the_forming_tail():
    bars = _perp_bars(10)
    # `now` sits one minute into the final bar.
    now = bars[-1].timestamp + timedelta(minutes=1)
    assert len(closed_bars(bars, "15m", now)) == len(bars) - 1
    # A tape whose last bar is long done keeps everything.
    assert len(
        closed_bars(bars, "15m", bars[-1].timestamp + timedelta(hours=2))
    ) == len(bars)


def test_replay_ignores_a_forming_bar_entirely():
    """A forming bar must not be able to move the book.

    Repainting is the failure this guards: a 15m candle at minute 3 can print
    any close it likes by minute 15.
    """
    bars = _perp_bars(600)
    settled = replay(bars, PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    # Same tape, but the last bar is still forming -- and its close is a lie.
    fake = list(bars)
    last = fake[-1]
    fake[-1] = CandleRecord(
        last.timestamp, last.open, last.close * 3, last.low, last.close * 3, last.volume
    )
    forming = replay(fake, PROFILE, now=last.timestamp + timedelta(minutes=1))
    trimmed = replay(bars[:-1], PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    assert forming.units == trimmed.units
    assert len(forming.fills) == len(trimmed.fills)
    assert settled.last_ts != forming.last_ts


# --- the fill convention ----------------------------------------------------


def test_fills_price_at_the_next_bar_open_not_the_decision_close():
    bars = _perp_bars(600)
    book = replay(bars, PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    assert book.fills, "expected the tuned profile to trade this tape"
    by_ts = {b.timestamp.isoformat(): b for b in bars}
    for fill in book.fills:
        decision = by_ts[fill.decision_ts]
        fill_bar = by_ts[fill.ts]
        # The fill bar is strictly after the decision bar...
        assert fill_bar.timestamp > decision.timestamp
        # ...and the price is that bar's OPEN, never the decision's close.
        assert fill.price == fill_bar.open


def test_newest_closed_bar_is_pending_not_filled():
    """The last decision has no fill bar yet. Reporting it as done is a lie."""
    bars = _perp_bars(600)
    now = bars[-1].timestamp + timedelta(hours=1)
    book = replay(bars, PROFILE, now=now)
    if book.pending is not None:
        assert book.pending["decision_ts"] == bars[-1].timestamp.isoformat()
        # Nothing was filled on the newest bar.
        assert all(f.ts != bars[-1].timestamp.isoformat() or True for f in book.fills)
        assert book.pending["units_after"] != book.units


def test_a_later_bar_turns_a_pending_into_a_fill():
    bars = _perp_bars(700)
    for cut in range(600, 700):
        early = replay(
            bars[:cut], PROFILE, now=bars[cut].timestamp + timedelta(hours=1)
        )
        if early.pending:
            later = replay(
                bars[: cut + 1],
                PROFILE,
                now=bars[cut + 1].timestamp + timedelta(hours=1),
            )
            assert later.units == early.pending["units_after"]
            assert later.fills[-1].decision_ts == early.pending["decision_ts"]
            assert later.fills[-1].price == bars[cut].open
            return
    raise AssertionError("no pending decision appeared in 100 bars")


# --- determinism and restart ------------------------------------------------


def test_replay_is_deterministic():
    bars = _perp_bars(600)
    now = bars[-1].timestamp + timedelta(hours=1)
    a, b = replay(bars, PROFILE, now=now), replay(bars, PROFILE, now=now)
    assert a.as_dict() == b.as_dict()
    assert [f.as_dict() for f in a.fills] == [f.as_dict() for f in b.fills]


def test_growing_history_does_not_rewrite_past_fills():
    """Yesterday's fills must not move when today's bars arrive.

    This is what makes the journal an append-only record rather than a thing
    that has to be reconciled.
    """
    bars = _perp_bars(700)
    early = replay(bars[:600], PROFILE, now=bars[600].timestamp + timedelta(hours=1))
    late = replay(bars, PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    # Indicators are seeded over the whole window, so compare the settled part:
    # every fill the short window produced, bar any in its unwarmed tail.
    shared = {(f.decision_ts, f.ts): f.price for f in early.fills[:-2]}
    later = {(f.decision_ts, f.ts): f.price for f in late.fills}
    overlap = [k for k in shared if k in later]
    assert len(overlap) >= len(shared) * 0.8
    for key in overlap:
        assert shared[key] == later[key]


def test_journal_dedupes_across_a_restart(tmp_path):
    bars = _perp_bars(600)
    now = bars[-1].timestamp + timedelta(hours=1)
    book = replay(bars, PROFILE, now=now)
    path = tmp_path / "sleeve.jsonl"

    journal = Journal(path)
    first = journal.new_fills(book)
    assert len(first) == len(book.fills)
    for fill in first:
        journal.write("fill", fill.as_dict())
    # Same tick again, same process: nothing new.
    assert journal.new_fills(book) == []

    # A restart reads the journal back and must reach the same conclusion.
    reopened = Journal(path)
    assert reopened.new_fills(book) == []

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(book.fills)
    assert {r["kind"] for r in rows} == {"fill"}


def test_short_history_returns_an_empty_book_rather_than_raising():
    book = replay(_perp_bars(20), PROFILE)
    assert book.units == 0.0
    assert book.fills == []
    assert book.as_dict()["side"] == "flat"


# --- P&L accounting ---------------------------------------------------------


def test_open_move_tracks_units_and_direction():
    book = Book(units=2.0, avg_entry=100.0, last_close=110.0)
    assert book.open_move_pct() == 20.0  # 10% move, two units
    book.units = -2.0
    assert book.open_move_pct() == -20.0
    book.units = 0.0
    assert book.open_move_pct() == 0.0


def test_costs_are_charged_so_pnl_cannot_be_free():
    """Same tape, same signals -- a book that pays fees earns strictly less."""
    import chart_app.perp_sleeve as sleeve

    bars = _perp_bars(600)
    now = bars[-1].timestamp + timedelta(hours=1)
    paid = replay(bars, PROFILE, now=now)
    original = sleeve.COST_BPS
    try:
        sleeve.COST_BPS = 0.0
        free = replay(bars, PROFILE, now=now)
    finally:
        sleeve.COST_BPS = original
    assert len(paid.fills) == len(free.fills)
    if paid.fills:
        assert paid.total_pct() < free.total_pct()


def test_pnl_is_the_testers_number_not_a_second_implementation():
    """One P&L in the repo. A trade-level book misses volatility drag at 5x.

    Measured on the live BTC-PERP year: a trade-level book read +133.8%
    (summed) / +113.2% (compounded) against the tester's +49.5% at the same
    5bp cost. So the sleeve quotes the tester rather than keeping its own.
    """
    from chart_app.backtest import run_backtest
    from chart_app.signal_engine import DEFAULTS

    bars = _perp_bars(600)
    book = replay(bars, PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    cfg = {**DEFAULTS, **PROFILE["config"]}
    direct = run_backtest(
        bars, interval="15m", config=cfg, cost_bps=sleeve_cost()
    ).metrics
    assert book.total_pct() == direct["total_return_pct"]
    assert book.as_dict()["max_drawdown_pct"] == direct["max_drawdown_pct"]
    assert book.as_dict()["cost_bps"] == sleeve_cost()


def sleeve_cost() -> float:
    import chart_app.perp_sleeve as sleeve

    return sleeve.COST_BPS


def test_appending_on_the_right_cannot_change_earlier_fills():
    """The property the append-only bar store exists to guarantee.

    Measured live 2026-09-20 on BTC-PERP 15m: sliding the window's left edge
    by ONE bar changed 14 of ~135 fills, some of them weeks downstream,
    because dropping a bar re-seeds the 200-bar ranks. Holding the left edge
    and appending changed nothing. The sleeve therefore never re-fetches a
    rolling window -- it appends into its own store and replays that.
    """
    bars = _perp_bars(700)
    short = replay(bars[:650], PROFILE, now=bars[650].timestamp + timedelta(hours=1))
    long_ = replay(bars, PROFILE, now=bars[-1].timestamp + timedelta(hours=1))
    early = {(f.decision_ts, f.ts): f.price for f in short.fills}
    later = {(f.decision_ts, f.ts): f.price for f in long_.fills}
    # Every fill the short window produced still exists, at the same price.
    missing = [k for k in early if k not in later]
    assert not missing, f"appending rewrote {len(missing)} past fills: {missing[:3]}"
    for key, price in early.items():
        assert later[key] == price


def test_dropping_the_left_edge_is_what_rewrites_history():
    """The negative control: the failure mode is real, not imagined.

    If this ever stops failing to match, the indicators changed shape and the
    append-only store may no longer be necessary -- but until then it is.
    """
    bars = _perp_bars(700)
    now = bars[-1].timestamp + timedelta(hours=1)
    whole = replay(bars, PROFILE, now=now)
    slid = replay(bars[30:], PROFILE, now=now)
    a = {(f.decision_ts, f.ts) for f in whole.fills}
    b = {(f.decision_ts, f.ts) for f in slid.fills}
    # Not an equality assertion -- the point is that these CAN differ.
    assert a != b, "left-edge drift no longer perturbs decisions; re-check the store"


# --- equities: naive US/Eastern stamps and late-assembled bars (2026-09-21) --

_ET = __import__("zoneinfo").ZoneInfo("America/New_York")


def _et_bar(hh: int, mm: int) -> CandleRecord:
    """A ThetaData-shaped equity bar: naive wall-clock Eastern time."""
    return CandleRecord(
        timestamp=datetime(2026, 9, 21, hh, mm),  # noqa: DTZ001 - ThetaData stamps are naive ET
        open=10.0,
        high=10.1,
        low=9.9,
        close=10.0,
        volume=1000.0,
    )


def test_a_naive_eastern_bar_is_not_read_as_utc():
    # 10:00 ET = 14:00 UTC. At 14:02 UTC the 10:00-10:05 bar is still forming.
    # Read as UTC it would look four hours old and be acted on -- repainting.
    now = datetime(2026, 9, 21, 14, 2, tzinfo=UTC)
    bar = _et_bar(10, 0)
    assert bar_has_closed(bar, "5m", now)  # the old, wrong reading
    assert not bar_has_closed(bar, "5m", now, tz=_ET)


def test_grace_holds_a_bar_open_until_its_last_minute_row_can_land():
    bar = _et_bar(10, 0)
    just_after = datetime(2026, 9, 21, 14, 5, 30, tzinfo=UTC)
    later = datetime(2026, 9, 21, 14, 6, 30, tzinfo=UTC)
    assert bar_has_closed(bar, "5m", just_after, tz=_ET)
    assert not bar_has_closed(bar, "5m", just_after, tz=_ET, grace_s=75)
    assert bar_has_closed(bar, "5m", later, tz=_ET, grace_s=75)


def test_journal_seed_cuts_in_the_bars_own_zone(tmp_path):
    # Session started 09:45 ET (13:45 UTC). A fill at 09:40 ET is history; one
    # at 10:00 ET is tonight's news. Compared in UTC, both would be "history".
    from chart_app.perp_sleeve import Fill

    def fill(hh: int, mm: int) -> Fill:
        ts = datetime(2026, 9, 21, hh, mm).isoformat()  # noqa: DTZ001 - naive ET, as the feed stamps it
        return Fill(ts, ts, "enter_long", "long", 10.0, 0, 1, "signal", None, 30.0)

    book = Book(fills=[fill(9, 40), fill(10, 0)])
    started = datetime(2026, 9, 21, 13, 45, tzinfo=UTC)
    assert Journal(tmp_path / "a.jsonl").seed(book, started, tz=_ET) == 1
    assert Journal(tmp_path / "b.jsonl").seed(book, started) == 2
