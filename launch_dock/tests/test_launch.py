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


def test_stocks_and_event_desk_commands():
    argv = stocks_argv({"instrument": "nvda"})
    assert argv[:3] == [PY, "-m", "Direction.whale_scanner"]
    assert argv[-1] == "NVDA"
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
