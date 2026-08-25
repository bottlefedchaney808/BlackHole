"""Assemble the agent-facing chart snapshot. No live PH."""

from __future__ import annotations

from typing import Any

from chart_app.flow_pane import bin_flow
from chart_app.flow_stamp import apply_whale, stamp_whale
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


def build_state(cache, ticker: str, interval: str, rh=None, *, flow_fn=None, flow_cache=None) -> dict:
    records = cache.load(ticker, interval)
    rows = price_scores(records)
    if records and flow_fn is not None:
        key = (
            ticker,
            interval,
            records[0].timestamp.isoformat(),
            records[-1].timestamp.isoformat(),
        )
        if flow_cache is not None and key in flow_cache:
            trades = flow_cache[key]
        else:
            try:
                trades = flow_fn(
                    ticker,
                    records[0].timestamp,
                    records[-1].timestamp,
                    0.0,
                )
            except Exception:  # noqa: BLE001 — one range call; degrade, never fabricate
                trades = []
            if flow_cache is not None:
                flow_cache[key] = trades
        apply_whale(rows, stamp_whale(records, trades or []))
        flow = bin_flow(records, list(trades or [])) if trades else None
    else:
        flow = None
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
            "signals": {
                "whale": bool(last_signals.get("whale")),
                "wave3": bool(last_signals.get("wave3")),
                "squeeze": bool(last_signals.get("squeeze")),
                "trend": bool(last_signals.get("trend")),
                "liquidity": bool(last_signals.get("liquidity")),
            },
        },
        "signals": [row["signals"] for row in rows],
        "rh": _rh_payload(rh),
        **({"flow": flow} if flow is not None else {}),
    }
