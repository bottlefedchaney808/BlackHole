import pytest

from launch_dock.launch import (
    LaunchRefused,
    apply_session,
    event_desk_argv,
    perp_argv,
    stocks_argv,
)


PY = r"E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe"


def _perp(**over):
    card = {
        "id": "perp-btc",
        "seed": "perp",
        "instrument": "BTC-PERP",
        "interval": "15m",
        "leverage": 6.0,
        "live": False,
        "subaccount": 0,
        "pnl_since": "",
        "base_capital": None,
        "state": "stopped",
    }
    card.update(over)
    return card


def test_perp_argv_uses_seed_cap_and_card_leverage():
    argv = perp_argv(_perp(), {"hours": 0.0, "cap_dollars": 100.0})
    assert argv[0] == PY
    assert argv[1:3] == ["-m", "chart_app.run_live_perp"]
    assert "--cap-dollars" in argv and "100.0" in argv
    assert "--leverage" in argv and "6.0" in argv
    assert "--live" not in argv
    assert "--pnl-since" not in argv


def test_relaunch_passes_stored_books():
    argv = perp_argv(
        _perp(
            pnl_since="2026-09-20T01:02:03+00:00",
            base_capital=150.0,
            live=True,
        ),
        {"hours": 0.0, "cap_dollars": 100.0},
    )
    assert "2026-09-20T01:02:03+00:00" in argv
    assert "150.0" in argv
    assert "--live" in argv


def test_null_cap_and_null_leverage_refuse():
    with pytest.raises(LaunchRefused, match="cap_dollars"):
        perp_argv(_perp(), {"hours": 0.0, "cap_dollars": None})
    with pytest.raises(LaunchRefused, match="leverage"):
        perp_argv(
            _perp(leverage=None),
            {"hours": 0.0, "cap_dollars": 100.0},
        )


def test_second_running_card_refused():
    cards = [
        {"id": "a", "seed": "perp", "instrument": "BTC-PERP", "state": "running"},
        {"id": "b", "seed": "perp", "instrument": "BTC-PERP", "state": "stopped"},
    ]
    with pytest.raises(LaunchRefused, match="already running"):
        perp_argv(
            _perp(id="b"),
            {"hours": 0.0, "cap_dollars": 100.0},
            cards=cards,
        )


def test_stock_launch_refuses_without_a_configured_account(monkeypatch):
    import launch_dock.launch as launch_mod

    monkeypatch.setattr(launch_mod, "AGENTIC_ACCOUNT", "")
    card = {"id": "stocks-fcel", "seed": "stocks", "instrument": "fcel", "cap_pct": 0.25}
    with pytest.raises(LaunchRefused, match="ROBINHOOD_AGENTIC_ACCOUNT"):
        stocks_argv(card)


def test_stocks_and_event_desk_commands(monkeypatch):
    import launch_dock.launch as launch_mod

    # A made-up number: the real one lives in `.env`, never in this repo.
    monkeypatch.setattr(launch_mod, "AGENTIC_ACCOUNT", "100000001")
    card = {"id": "stocks-fcel", "seed": "stocks", "instrument": "fcel", "interval": "5m",
            "cap_pct": 0.25, "live": False}
    argv = stocks_argv(card)
    assert argv[:3] == [PY, "-m", "chart_app.run_live_equity"]
    assert argv[argv.index("--ticker") + 1] == "FCEL"
    assert argv[argv.index("--cap-fraction") + 1] == "0.25"
    # Only ever the agentic account -- never Jason's own.
    assert argv[argv.index("--account") + 1] == "100000001"
    assert "--live" not in argv and "--live" in stocks_argv({**card, "live": True})
    with pytest.raises(LaunchRefused, match="no size"):
        stocks_argv({**card, "cap_pct": None})
    # Stocks and perps are separate books: a full perp book does not block a stock.
    running = [{"id": "p", "seed": "perp", "instrument": "BTC-PERP", "cap_pct": 1.0, "state": "running"},
               {"id": "s", "seed": "stocks", "instrument": "GME", "cap_pct": 0.8, "state": "running"}]
    with pytest.raises(LaunchRefused, match="stock sleeves would be 105%"):
        stocks_argv(card, cards=running)
    assert stocks_argv({**card, "cap_pct": 0.2}, cards=running)
    desk = event_desk_argv()
    assert desk == ["py", "-3.11", "event_desk/churn.py", "watch"]


def test_apply_session_stores_runner_values():
    card = _perp()
    out = apply_session(
        card,
        {
            "pnl_since": "2026-09-21T12:00:00+00:00",
            "equity": 80.0,
        },
        saved_at="2026-09-21T11:00:00+00:00",
    )
    assert out["pnl_since"] == "2026-09-21T12:00:00+00:00"
    assert out["base_capital"] == 80.0
    assert out["running_saved_at"] == "2026-09-21T11:00:00+00:00"
    assert out["state"] == "running"


# --- per-card cap_dollars (2026-09-24) -------------------------------------


def _perp_card(**kw):
    base = {
        "id": "perp-btc",
        "seed": "perp",
        "instrument": "BTC-PERP",
        "interval": "5m",
        "leverage": 1.0,
        "subaccount": 0,
        "live": True,
    }
    base.update(kw)
    return base


def _cap_of(argv):
    return float(argv[argv.index("--cap-dollars") + 1])


def test_card_cap_overrides_the_shared_seed():
    """Two perps at different prices need different books."""
    argv = perp_argv(_perp_card(cap_dollars=100.0), {"cap_dollars": 12.0})
    assert _cap_of(argv) == 100.0


def test_seed_cap_is_the_fallback_when_the_card_has_none():
    argv = perp_argv(_perp_card(), {"cap_dollars": 12.0})
    assert _cap_of(argv) == 12.0


def test_launch_is_refused_when_neither_sets_a_cap():
    with pytest.raises(LaunchRefused, match="no size for"):
        perp_argv(_perp_card(), {})


def test_a_zero_card_cap_is_refused_not_silently_seeded():
    with pytest.raises(LaunchRefused):
        perp_argv(_perp_card(cap_dollars=0.0), {"cap_dollars": 12.0})


# --- cap_pct: size tracks the book up and down -----------------------------


def test_cap_pct_emits_a_fraction_not_a_frozen_dollar_cap():
    argv = perp_argv(_perp_card(cap_pct=0.6), {"cap_dollars": 12.0})
    assert "--cap-fraction" in argv
    assert float(argv[argv.index("--cap-fraction") + 1]) == 0.6
    assert "--cap-dollars" not in argv


def test_cap_pct_outside_zero_to_one_is_refused():
    with pytest.raises(LaunchRefused, match="cap_pct"):
        perp_argv(_perp_card(cap_pct=1.5), {})
    with pytest.raises(LaunchRefused, match="cap_pct"):
        perp_argv(_perp_card(cap_pct=0.0), {"cap_dollars": 12.0})


def test_two_sleeves_may_be_fully_invested():
    cards = [
        _perp_card(id="perp-btc", instrument="BTC-PERP", cap_pct=0.6, state="running"),
        _perp_card(id="perp-xrp", instrument="XRP-PERP", cap_pct=0.4),
    ]
    argv = perp_argv(cards[1], {}, cards)
    assert float(argv[argv.index("--cap-fraction") + 1]) == 0.4


def test_over_allocation_is_refused_as_back_door_leverage():
    cards = [
        _perp_card(id="perp-btc", instrument="BTC-PERP", cap_pct=0.6, state="running"),
        _perp_card(id="perp-xrp", instrument="XRP-PERP", cap_pct=0.6),
    ]
    with pytest.raises(LaunchRefused, match="120%"):
        perp_argv(cards[1], {}, cards)


def test_a_stopped_sleeve_does_not_reserve_allocation():
    cards = [
        _perp_card(id="perp-btc", instrument="BTC-PERP", cap_pct=0.9, state="stopped"),
        _perp_card(id="perp-xrp", instrument="XRP-PERP", cap_pct=0.9),
    ]
    perp_argv(cards[1], {}, cards)  # must not raise
