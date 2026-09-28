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

# Every copied perp starts at 1x. This table used to hand a BTC copy 6x and
# an XRP copy 2x, and anything else None -- which then refused at Launch with
# "leverage is unset" and no way to set it from the page. Leverage on an algo
# sleeve is not a default; a card that wants it is edited on purpose.
STANDING_LEVERAGE: dict[str, float] = {}
DEFAULT_PERP_LEVERAGE = 1.0

EDITABLE = ("cap_pct", "live", "interval", "leverage")
INTERVALS = ("1m", "5m", "15m", "1h")
BUSY = ("running", "launching")


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
    # Cards copied before the 1x default existed were saved with leverage
    # None, which Launch refuses and the page cannot edit. Heal them to 1x.
    for card in data["cards"]:
        if card.get("seed") == "perp" and card.get("leverage") is None:
            card["leverage"] = DEFAULT_PERP_LEVERAGE
    return data


def save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _new_id(data: dict[str, Any], seed: str, instrument: str) -> str:
    base = f"{seed}-{instrument.lower()}"
    taken = {c.get("id") for c in data["cards"]}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _find(data: dict[str, Any], card_id: str) -> dict[str, Any]:
    card = next((c for c in data["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise ValueError(f"no card {card_id}")
    return card


def copy_card(
    data: dict[str, Any],
    source_id: str,
    new_id: str | None,
    instrument: str,
    known: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A stopped, dry-run copy of `source_id` on another instrument.

    `known` is the venue map (`launch.KALSHI`): a perp copy on a name not in it
    is refused HERE, not at Launch -- the form used to accept "PERP" and make a
    card that could never run. `new_id` is optional; it is derived otherwise.
    """
    source = _find(data, source_id)
    if source.get("seed") == "event_desk":
        raise ValueError("event_desk cards cannot be copied")
    inst = instrument.strip().upper()
    if not inst:
        raise ValueError("pick an instrument")
    if source["seed"] == "perp" and known is not None and inst not in known:
        raise ValueError(f"{inst} is not a Kalshi perp; pick one of {', '.join(sorted(known))}")
    new_id = (new_id or "").strip() or _new_id(data, source["seed"], inst)
    if any(c.get("id") == new_id for c in data["cards"]):
        raise ValueError(f"id taken {new_id}")
    card = {
        "id": new_id,
        "seed": source["seed"],
        "instrument": inst,
        "interval": source.get("interval") or "15m",
        "leverage": (
            STANDING_LEVERAGE.get(inst, DEFAULT_PERP_LEVERAGE)
            if source["seed"] == "perp"
            else None
        ),
        "cap_pct": None,
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


def delete_card(data: dict[str, Any], card_id: str) -> None:
    card = _find(data, card_id)
    if card.get("state") in BUSY:
        raise ValueError(f"{card_id} is {card.get('state')}; stop it first")
    data["cards"] = [c for c in data["cards"] if c.get("id") != card_id]


def update_card(data: dict[str, Any], card_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Edit a stopped card's share, live flag, interval or leverage."""
    card = _find(data, card_id)
    if card.get("state") in BUSY:
        raise ValueError(f"{card_id} is {card.get('state')}; stop it before editing")
    unknown = set(fields) - set(EDITABLE)
    if unknown:
        raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
    if "cap_pct" in fields:
        raw = fields["cap_pct"]
        if raw in (None, ""):
            card["cap_pct"] = None
        else:
            pct = float(raw)
            if not 0.0 < pct <= 1.0:
                raise ValueError(f"share must be above 0% and at most 100%, got {pct:.0%}")
            card["cap_pct"] = pct
    if "live" in fields:
        card["live"] = bool(fields["live"])
    if "interval" in fields:
        itv = str(fields["interval"])
        if itv not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        card["interval"] = itv
    if "leverage" in fields:
        lev = float(fields["leverage"])
        if lev < 1.0:
            raise ValueError(f"leverage must be >= 1, got {lev}")
        card["leverage"] = lev
    return card


def card_from_profile(
    data: dict[str, Any], key: str, known: dict[str, str]
) -> dict[str, Any]:
    """A stopped, dry-run card for a saved `TICKER|interval` profile.

    A Kalshi name becomes a 1x perp card; any other ticker becomes a stock
    card on the profile's own interval. Copying a card onto another ticker
    carried the SOURCE's interval, so an FCEL|5m tune landed on a 15m card
    that ran somebody else's preset -- starting from the profile cannot.
    """
    ticker, _, interval = key.partition("|")
    ticker = ticker.strip().upper()
    if not ticker or ticker == "*":
        raise ValueError("a wildcard profile has no ticker to trade; save it under a ticker first")
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
    seed = "perp" if ticker in known else "stocks"
    card = {
        "id": _new_id(data, seed, ticker),
        "seed": seed,
        "instrument": ticker,
        "interval": interval,
        "leverage": DEFAULT_PERP_LEVERAGE if seed == "perp" else None,
        "cap_pct": None,
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
