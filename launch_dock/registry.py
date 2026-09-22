from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EMPTY: dict[str, Any] = {
    "seeds": {
        "perp": {"hours": 0.0, "cap_dollars": None},
        "stocks": {},
        "event_desk": {},
    },
    "cards": [],
}

STANDING_LEVERAGE = {"BTC-PERP": 6.0, "XRP-PERP": 2.0}


def load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    data = json.loads(json.dumps(EMPTY))
    if isinstance(raw, dict):
        seeds = raw.get("seeds") if isinstance(raw.get("seeds"), dict) else {}
        data["seeds"]["perp"].update(seeds.get("perp") or {})
        data["cards"] = list(raw.get("cards") or [])
    return data


def save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def copy_card(data: dict[str, Any], source_id: str, new_id: str, instrument: str) -> dict[str, Any]:
    source = next((c for c in data["cards"] if c.get("id") == source_id), None)
    if source is None:
        raise ValueError(f"no card {source_id}")
    if source.get("seed") == "event_desk":
        raise ValueError("event_desk cards cannot be copied")
    if any(c.get("id") == new_id for c in data["cards"]):
        raise ValueError(f"id taken {new_id}")
    inst = instrument.upper()
    card = {
        "id": new_id,
        "seed": source["seed"],
        "instrument": inst,
        "interval": source.get("interval") or "15m",
        "leverage": STANDING_LEVERAGE.get(inst) if source["seed"] == "perp" else None,
        "live": False,
        "subaccount": 0,
        "pnl_since": "",
        "base_capital": None,
        "pid": None,
        "argv": [],
        "running_saved_at": None,
        "state": "stopped",
    }
    data["cards"].append(card)
    return card
