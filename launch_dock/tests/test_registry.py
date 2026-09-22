from pathlib import Path
import json
import pytest
from launch_dock.registry import EMPTY, STANDING_LEVERAGE, copy_card, load, save


def test_copy_keeps_lane_and_drops_books(tmp_path: Path):
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "perp-btc",
        "seed": "perp",
        "instrument": "BTC-PERP",
        "interval": "15m",
        "leverage": 6.0,
        "live": True,
        "subaccount": 0,
        "pnl_since": "2026-09-20T00:00:00+00:00",
        "base_capital": 150.0,
        "pid": 99,
        "argv": ["run_live_perp"],
        "running_saved_at": "2026-09-20T00:00:00+00:00",
        "state": "running",
    })
    card = copy_card(data, "perp-btc", "perp-xrp", "XRP-PERP")
    assert card["seed"] == "perp"
    assert card["instrument"] == "XRP-PERP"
    assert card["leverage"] == STANDING_LEVERAGE["XRP-PERP"]
    assert card["live"] is False
    assert card["pnl_since"] == ""
    assert card["base_capital"] is None
    assert card["pid"] is None
    assert card["state"] == "stopped"
    assert "config" not in card and "elmo" not in card


def test_gold_copy_has_null_leverage(tmp_path: Path):
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
        "interval": "15m", "leverage": 6.0, "live": True, "subaccount": 0,
        "pnl_since": "", "base_capital": None, "pid": None, "argv": [],
        "running_saved_at": None, "state": "stopped",
    })
    card = copy_card(data, "perp-btc", "perp-gold", "GOLD")
    assert card["leverage"] is None
    assert card["live"] is False


def test_event_desk_copy_refused():
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "desk", "seed": "event_desk", "instrument": "",
        "interval": "15m", "leverage": None, "live": False, "subaccount": 0,
        "pnl_since": "", "base_capital": None, "pid": None, "argv": [],
        "running_saved_at": None, "state": "stopped",
    })
    with pytest.raises(ValueError, match="event_desk"):
        copy_card(data, "desk", "desk-2", "")
