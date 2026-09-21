"""The live translation layer: book units -> Kalshi contracts -> one order."""

from __future__ import annotations

import pytest

from chart_app.perp_live import (
    book_target_units,
    full_contracts,
    kalshi_stop,
    plan_order,
    reduces_risk,
    target_contracts,
)


def test_full_is_one_x_of_the_smaller_of_cap_and_equity():
    assert full_contracts(100, 500, 8.13) == 12  # cap binds
    assert full_contracts(100, 40, 8.13) == 4  # equity binds: never borrows
    assert full_contracts(100, 0, 8.13) == 0


def test_max_book_never_exceeds_one_x():
    full = full_contracts(100, 100, 8.13)
    for units in (5, -5, 7, -9):  # even a malformed book is clamped
        assert abs(target_contracts(units, 0.2, full)) <= full
    assert target_contracts(5, 0.2, 12) == 12
    assert target_contracts(1, 0.2, 12) == 2
    assert target_contracts(-3, 0.2, 12) == -7


def test_pending_decision_is_the_target():
    assert book_target_units({"units": 2, "pending": {"units_after": 0}}) == 0
    assert book_target_units({"units": 2, "pending": None}) == 2


def test_reduces_risk():
    assert reduces_risk(5, 2) and reduces_risk(-5, 0) and reduces_risk(3, 0)
    assert not reduces_risk(0, 3)
    assert not reduces_risk(2, 5)
    assert not reduces_risk(3, -3)  # reversal opens new risk


def test_maker_rests_at_the_touch_and_never_crosses():
    buy = plan_order(0, 4, 8.1288, 8.1290)
    assert (buy.side, buy.count, buy.price, buy.post_only) == ("bid", 4, 8.1288, True)
    sell = plan_order(4, -2, 8.1288, 8.1290)
    assert (sell.side, sell.count, sell.price, sell.post_only) == (
        "ask",
        6,
        8.1290,
        True,
    )
    assert plan_order(3, 3, 8.1, 8.2) is None


def test_cross_only_to_shrink():
    out = plan_order(6, 0, 8.1288, 8.1290, cross=True)
    assert out.side == "ask" and out.reduce_only and not out.post_only
    assert out.tif == "immediate_or_cancel" and out.price < 8.1288
    with pytest.raises(ValueError):
        plan_order(0, 6, 8.1288, 8.1290, cross=True)


def test_stop_converts_btc_price_to_contract_price():
    assert kalshi_stop(79_983.0) == pytest.approx(7.9983)
    assert kalshi_stop(None) is None


# --- the sleeve's own P&L, not the subaccount's equity (2026-09-21) ---------


def _fill(ts: str, realized: str, fees: str) -> dict:
    return {"created_time": ts, "realized_pnl": realized, "fees": fees}


def test_sleeve_pnl_is_its_fills_since_launch_plus_the_open_mark():
    from datetime import UTC, datetime

    from chart_app.perp_live import sleeve_pnl

    since = datetime(2026, 9, 21, 7, 41, tzinfo=UTC)
    fills = [
        _fill("2026-09-21T08:43:09.248787Z", "0.4604", "0.0202"),  # closed 2 @ +0.46
        _fill("2026-09-21T08:44:06.2737Z", "0.0000", "0.0034"),  # an open: fee only
        _fill("2026-09-15T14:40:49Z", "9.9900", "0.0818"),  # before launch: ignored
    ]
    assert abs(sleeve_pnl(fills, since, 1.2826) - (1.2826 + 0.4604 - 0.0202 - 0.0034)) < 1e-9


def test_collateral_moving_between_subaccounts_is_not_sleeve_pnl():
    # A manual isolated buy in 64 moved subaccount 0's equity by -$5 with no
    # sleeve fill. Only fills and the open mark count, so the P&L is unchanged.
    from datetime import UTC, datetime

    from chart_app.perp_live import sleeve_pnl

    since = datetime(2026, 9, 21, 7, 41, tzinfo=UTC)
    assert sleeve_pnl([], since, 1.28) == 1.28
