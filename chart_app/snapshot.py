"""Assemble the agent-facing chart snapshot. No live PH."""

from __future__ import annotations

from typing import Any

from chart_app.score_engine import classic_overlays, gated_markers, price_scores


def _rh_payload(rh: dict[str, Any] | None) -> dict[str, Any]:
    if rh is None:
        return {"position": None, "fills": []}
    return {
        "position": rh.get("position"),
        "fills": list(rh.get("fills") or []),
    }


def _conviction(signals: dict[str, Any], score: int) -> str:
    # Same rule as signal_generator.generate. whale/liq stay False via
    # price_scores, so HIGH/MEDIUM from whale will not fire.
    if (
        signals.get("whale")
        and signals.get("wave3")
        and (signals.get("squeeze") or signals.get("trend"))
        and score >= 3
    ):
        return "HIGH"
    if signals.get("whale") and score >= 3:
        return "MEDIUM"
    return "NONE"


def build_state(cache, ticker: str, interval: str, rh=None) -> dict:
    records = cache.load(ticker, interval)
    rows = price_scores(records)
    scores = [int(row["score"]) for row in rows]
    last = rows[-1] if rows else None
    last_score = int(last["score"]) if last is not None else 0
    last_signals = last["signals"] if last is not None else {}
    return {
        "ticker": ticker,
        "interval": interval,
        "as_of": records[-1].timestamp.isoformat() if records else None,
        "bars": [
            {
                "ts": record.timestamp.isoformat(),
                "open": record.open,
                "high": record.high,
                "low": record.low,
                "close": record.close,
                "volume": record.volume,
            }
            for record in records
        ],
        "scores": scores,
        "markers": gated_markers(rows),
        "overlays": classic_overlays(records),
        "live": {
            "conviction": _conviction(last_signals, last_score),
            "score": last_score,
        },
        "rh": _rh_payload(rh),
    }
