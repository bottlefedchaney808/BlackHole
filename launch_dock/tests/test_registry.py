from pathlib import Path
import json
import pytest
from launch_dock.launch import KALSHI
from launch_dock.registry import (
    EMPTY, card_from_profile, copy_card, delete_card, load, save, update_card,
)


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
    assert card["leverage"] == 1.0  # a copy never inherits leverage
    assert card["live"] is False
    assert card["pnl_since"] == ""
    assert card["base_capital"] is None
    assert card["pid"] is None
    assert card["state"] == "stopped"
    assert "config" not in card and "elmo" not in card


def test_gold_copy_starts_at_1x(tmp_path: Path):
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
        "interval": "15m", "leverage": 6.0, "live": True, "subaccount": 0,
        "pnl_since": "", "base_capital": None, "pid": None, "argv": [],
        "running_saved_at": None, "state": "stopped",
    })
    card = copy_card(data, "perp-btc", "perp-gold", "GOLD")
    assert card["leverage"] == 1.0
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


def _btc(data):
    data["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "5m",
        "leverage": 1.0, "live": True, "state": "stopped",
    })
    return data


def test_copy_refuses_a_name_the_venue_does_not_list():
    data = _btc(json.loads(json.dumps(EMPTY)))
    with pytest.raises(ValueError, match="not a Kalshi perp"):
        copy_card(data, "perp-btc", "", "PERP", known=KALSHI)
    assert len(data["cards"]) == 1


def test_copy_names_the_card_itself():
    data = _btc(json.loads(json.dumps(EMPTY)))
    first = copy_card(data, "perp-btc", "", "sol-perp", known=KALSHI)
    second = copy_card(data, "perp-btc", "", "SOL-PERP", known=KALSHI)
    assert (first["id"], second["id"]) == ("perp-sol-perp", "perp-sol-perp-2")
    assert first["instrument"] == "SOL-PERP" and first["interval"] == "5m"


def test_card_from_profile():
    data = json.loads(json.dumps(EMPTY))
    card = card_from_profile(data, "SOL-PERP|5m", KALSHI)
    assert card["instrument"] == "SOL-PERP" and card["interval"] == "5m"
    assert card["leverage"] == 1.0 and card["live"] is False and card["cap_pct"] is None
    stock = card_from_profile(data, "fcel|5m", KALSHI)
    assert stock["seed"] == "stocks" and stock["id"] == "stocks-fcel"
    assert stock["instrument"] == "FCEL" and stock["interval"] == "5m"
    assert stock["leverage"] is None and stock["live"] is False
    with pytest.raises(ValueError, match="wildcard"):
        card_from_profile(data, "*|5m", KALSHI)


def test_update_and_delete_refuse_a_running_card():
    data = _btc(json.loads(json.dumps(EMPTY)))
    update_card(data, "perp-btc", {"cap_pct": 0.3, "live": False, "interval": "15m"})
    assert data["cards"][0]["cap_pct"] == 0.3 and data["cards"][0]["interval"] == "15m"
    with pytest.raises(ValueError, match="at most 100%"):
        update_card(data, "perp-btc", {"cap_pct": 1.5})
    update_card(data, "perp-btc", {"leverage": 6.0})
    assert data["cards"][0]["leverage"] == 6.0
    with pytest.raises(ValueError, match="leverage must be"):
        update_card(data, "perp-btc", {"leverage": 0.5})
    data["cards"][0]["state"] = "running"
    with pytest.raises(ValueError, match="stop it"):
        update_card(data, "perp-btc", {"cap_pct": 0.5})
    with pytest.raises(ValueError, match="stop it"):
        delete_card(data, "perp-btc")
    data["cards"][0]["state"] = "stopped"
    delete_card(data, "perp-btc")
    assert data["cards"] == []


def test_load_heals_a_perp_card_saved_without_leverage(tmp_path: Path):
    from launch_dock.registry import load

    path = tmp_path / "launch_dock.json"
    path.write_text(json.dumps({"cards": [
        {"id": "perp-solperp", "seed": "perp", "leverage": None},
        {"id": "stocks-spy", "seed": "stocks", "leverage": None},
    ]}), encoding="utf-8")
    cards = {c["id"]: c for c in load(path)["cards"]}
    assert cards["perp-solperp"]["leverage"] == 1.0
    assert cards["stocks-spy"]["leverage"] is None
