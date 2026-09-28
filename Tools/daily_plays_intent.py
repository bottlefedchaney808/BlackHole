"""Daily Plays Layer-2 desk-intent builder.

Turns Layer-1 play cards into a draft desk-intent payload. Size and capital
stay null until a real capital snapshot exists. Never status ready.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ALLOWED_LEANS = frozenset({"long", "short", "event", "watch"})
PLAYS_REF = "artifacts/daily-plays/data.json"
SIZE_REASON = "capital snapshot not wired yet"
INSTRUCTION_SUFFIX = "capital snapshot not wired"
CONSTRAINTS = ["no live order in this spec"]
FORBIDDEN_INTENT_KEYS = frozenset({"side", "qty", "limit", "venue"})


class SchemaError(ValueError):
    """Layer-1 card failed desk-intent schema checks."""


def validate_cards(cards: list[dict[str, Any]]) -> None:
    if not isinstance(cards, list):
        raise SchemaError("cards must be a list")
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            raise SchemaError(f"card[{i}] must be an object")
        ticker = card.get("ticker")
        if ticker is None or not str(ticker).strip():
            raise SchemaError(f"card[{i}] missing ticker")
        lean = card.get("lean")
        if lean not in ALLOWED_LEANS:
            raise SchemaError(f"card[{i}] bad lean: {lean!r}")
        # score may be None. stats.atm_iv may be 0 (measured) — do not treat
        # zero as missing.


def _intent_from_card(card: dict[str, Any]) -> dict[str, Any]:
    thesis = card.get("thesis") or ""
    intent = {
        "ticker": card["ticker"],
        "lean": card["lean"],
        "play_score": card.get("score"),
        "status": "draft",
        "instruction": f"{thesis} | {INSTRUCTION_SUFFIX}",
        "size": {
            "notional_usd": None,
            "pct_equity": None,
            "reason": SIZE_REASON,
        },
        "constraints": list(CONSTRAINTS),
        "blocked_reason": None,
        "journal_ref": card.get("journal_ref"),
    }
    for key in FORBIDDEN_INTENT_KEYS:
        intent.pop(key, None)
    return intent


def build_intent(run_id: str, asof_utc: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
    validate_cards(cards)
    intents = [
        _intent_from_card(card)
        for card in cards
        if card.get("lean") != "watch"
    ]
    return {
        "schema_version": 1,
        "run_id": run_id,
        "asof_utc": asof_utc,
        "plays_ref": PLAYS_REF,
        "capital": {
            "asof_utc": None,
            "equity": None,
            "cash": None,
            "source": None,
        },
        "intents": intents,
    }


def write_intent(artifact_dir: str | Path, payload: dict[str, Any]) -> Path:
    """Write artifact_dir/desk-intent/{run_id}.json. Never status ready."""
    for intent in payload.get("intents") or []:
        if intent.get("status") == "ready":
            raise SchemaError("desk-intent must not be status ready")
        for key in FORBIDDEN_INTENT_KEYS:
            if key in intent:
                raise SchemaError(f"desk-intent must not include {key}")
    run_id = payload.get("run_id")
    if not run_id:
        raise SchemaError("payload missing run_id")
    dest_dir = Path(artifact_dir) / "desk-intent"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
