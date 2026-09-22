from __future__ import annotations

import os
from typing import Any


PY = r"E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe"
BLACKHOLE = r"E:/BlackHole_Investments/BlackHole"
EVENT_DESK = r"E:/BlackHole_Investments/BlackHole/Event_Desk"
KALSHI = {
    "BTC-PERP": "KXBTCPERP",
    "XRP-PERP": "KXXRPPERP",
    "GOLD": "KXGOLDPERP",
}
DETACH = 0x00000200 | 0x00000008 | 0x08000000


class LaunchRefused(RuntimeError):
    pass


def _busy(cards: list[dict], card: dict) -> None:
    for other in cards:
        if other.get("id") == card.get("id"):
            continue
        if other.get("state") not in ("running", "launching"):
            continue
        if card["seed"] == "event_desk" and other.get("seed") == "event_desk":
            raise LaunchRefused("event_desk watch already running")
        if (
            other.get("seed") == card.get("seed")
            and other.get("instrument") == card.get("instrument")
        ):
            raise LaunchRefused(f"{card['instrument']} already running")


def perp_argv(
    card: dict,
    seed: dict,
    cards: list[dict] | None = None,
) -> list[str]:
    if cards:
        _busy(cards, card)

    cap = seed.get("cap_dollars")
    if not cap or float(cap) <= 0:
        raise LaunchRefused("perp seed cap_dollars is unset")
    if card.get("leverage") is None:
        raise LaunchRefused("leverage is unset")

    instrument = card["instrument"].upper()
    if instrument not in KALSHI:
        raise LaunchRefused(f"no Kalshi map for {card['instrument']}")

    hours = float(seed.get("hours") or 0.0)
    argv = [
        PY,
        "-m",
        "chart_app.run_live_perp",
        "--ticker",
        instrument,
        "--interval",
        card.get("interval") or "15m",
        "--cap-dollars",
        str(float(cap)),
        "--leverage",
        str(float(card["leverage"])),
        "--hours",
        str(hours),
        "--subaccount",
        str(int(card.get("subaccount") or 0)),
    ]

    if card.get("pnl_since"):
        argv += ["--pnl-since", card["pnl_since"]]
        if card.get("base_capital") is None:
            raise LaunchRefused("pnl_since set but base_capital missing")
        argv += ["--base-capital", str(float(card["base_capital"]))]
    if card.get("live"):
        argv.append("--live")
    return argv


def stocks_argv(card: dict) -> list[str]:
    return [PY, "-m", "Direction.whale_scanner", card["instrument"].upper()]


def event_desk_argv() -> list[str]:
    return ["py", "-3.11", "event_desk/churn.py", "watch"]


def apply_session(
    card: dict,
    session: dict,
    *,
    saved_at: str | None = None,
) -> dict:
    card = dict(card)
    card["pnl_since"] = session["pnl_since"]
    card["base_capital"] = float(session["equity"])
    card["running_saved_at"] = saved_at
    card["state"] = "running"
    return card
