"""Assemble the agent-facing chart snapshot. No live PH beyond one flow call.

Payload shape (additions of 2026-09-18 marked NEW):

    ticker / interval / as_of / bars / overlays / oscillators   (unchanged)
    scores        legacy 0-5 leg count, now with a REAL liquidity leg
    markers       NEW meaning: the conviction engine's actions
    markers_legacy            the old `apply_position_gate` output
    elmo          NEW entropy / liquidity / ALMA series
    algo          NEW conviction line, components, actions, trades, stops
    whale         NEW per-bar premium detail + session coverage
    live          headline: conviction, score, per-leg booleans
    direction / rh / flow / signals                             (unchanged)

`markers` changing meaning is the point of the exercise: the old gate could
not emit a buy without four legs agreeing on one bar, and could not emit an
add at all. The old output is still published under `markers_legacy` so
anything that was reading the previous contract can keep doing so.
"""

from __future__ import annotations

import os
from typing import Any

from chart_app.crypto_source import is_crypto
from chart_app.elmo import compute_elmo
from chart_app.flow_pane import bin_flow
from chart_app.flow_stamp import (
    apply_whale,
    flow_observed_bars,
    resolve_min_premium,
    stamp_whale,
    whale_bars,
    whale_coverage,
)
from chart_app.profiles import resolve as resolve_profile
from chart_app.score_engine import (
    classic_overlays,
    direction_coverage,
    gated_markers,
    oscillators,
    price_scores,
)
from chart_app.signal_engine import DEFAULTS as SIGNAL_DEFAULTS
from chart_app.signal_engine import evaluate


def _rh_payload(rh: dict[str, Any] | None) -> dict[str, Any]:
    if rh is None:
        return {"position": None, "fills": []}
    return {
        "position": rh.get("position"),
        "fills": list(rh.get("fills") or []),
    }


def _conviction(signals: dict[str, Any], score: int) -> str:
    # Same rule as signal_generator.generate.
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


def _whale_mode() -> str:
    """`adaptive` (default) or `fixed`, via CHART_APP_WHALE_MODE."""
    mode = (os.environ.get("CHART_APP_WHALE_MODE") or "adaptive").strip().lower()
    return mode if mode in ("adaptive", "fixed") else "adaptive"


def _signal_config() -> dict[str, Any]:
    """Signal-engine overrides from the environment.

    Only the levels that a user actually retunes are exposed; everything else
    stays in `signal_engine.DEFAULTS` where the backtest can sweep it.
    """
    out: dict[str, Any] = {}
    for env_key, cfg_key, cast in (
        ("CHART_APP_ENTRY_LONG", "entry_long", float),
        ("CHART_APP_EXIT_LONG", "exit_long", float),
        ("CHART_APP_ATR_STOP", "atr_stop_mult", float),
        ("CHART_APP_ALLOW_SHORT", "allow_short", lambda v: v.strip().lower() in ("1", "true", "yes")),
    ):
        raw = os.environ.get(env_key)
        if not raw:
            continue
        try:
            out[cfg_key] = cast(raw)
        except (TypeError, ValueError):
            continue
    return out


def build_state(
    cache,
    ticker: str,
    interval: str,
    rh=None,
    *,
    flow_fn=None,
    flow_cache=None,
) -> dict:
    records = cache.load(ticker, interval)
    rows = price_scores(records)

    # ---- one flow range query, cached per (symbol, interval, window) ------
    trades: list[dict[str, Any]] = []
    flow = None
    flow_status = "off"
    if is_crypto(ticker):
        # There is no US options tape for a perpetual swap. Reporting "WH
        # 0/31 sessions" here would read as "we looked and found no whales",
        # which is a different and wrong statement from "this instrument has
        # no options tape to look at".
        flow_status = "unavailable"
    elif records and flow_fn is not None:
        key = (
            ticker,
            interval,
            records[0].timestamp.isoformat(),
            records[-1].timestamp.isoformat(),
        )
        if flow_cache is not None and key in flow_cache:
            trades = flow_cache[key]
            flow_status = "ready"
        else:
            try:
                fetched = flow_fn(ticker, records[0].timestamp, records[-1].timestamp, 0.0)
            except Exception:  # noqa: BLE001 — degrade, never fabricate
                fetched = []
            # A flow_fn may return None to mean "I have started fetching, ask
            # again later". That is how `server._deferred_flow` keeps a full
            # options tape -- five session dates, paginated 10k rows at a time
            # -- from blocking the /api/state request behind it. A synchronous
            # pull timed the endpoint out at 60s on SPY 15m. `None` is NOT
            # cached, so the next poll re-asks and picks up the result.
            if fetched is None:
                flow_status = "loading"
                trades = []
            else:
                trades = list(fetched)
                flow_status = "ready"
                if flow_cache is not None:
                    flow_cache[key] = trades
        trades = list(trades or [])
        if trades:
            flow = bin_flow(records, trades)

    mode = _whale_mode()
    min_premium = resolve_min_premium(trades, mode=mode, bars=len(records))
    whale_detail = whale_bars(records, trades, min_premium=min_premium)
    apply_whale(rows, stamp_whale(records, trades, min_premium=min_premium))

    # ---- ELMo: entropy / liquidity / momentum ----------------------------
    # The active profile for this (ticker, interval) feeds the LIVE chart.
    # This call used to be `compute_elmo(records)` with no overrides, which
    # meant a knob moved in the tester changed the tester and nothing else --
    # the chart kept drawing shipped defaults no matter what was tuned.
    profile = resolve_profile(ticker, interval)
    elmo = compute_elmo(records, **(profile.get("elmo") or {}))
    # The LQ leg is finally real. It was hardwired False since the app was
    # written, which capped the legacy 0-5 score at 4 and made the old gate's
    # ADD (score == 5) unreachable -- see signal_engine's module docstring.
    for index, row in enumerate(rows):
        signals = row.setdefault("signals", {})
        signals["liquidity"] = bool(
            index < len(elmo.liquid) and elmo.liquid[index]
        )
        row["score"] = int(sum(1 for value in signals.values() if value))

    # ---- the conviction engine -------------------------------------------
    # `flow_observed` is per-bar and session-date keyed, so the whale weight
    # leaves the denominator on exactly the bars the flow pull never reached --
    # 71% of a 15m/20d chart, since the pull is capped at 5 session dates.
    conv, run = evaluate(
        records,
        elmo=elmo,
        flow_net=(flow or {}).get("net"),
        flow_observed=flow_observed_bars(records, trades),
        config={**_signal_config(), **(profile.get("config") or {})},
    )

    scores = [int(row["score"]) for row in rows]
    last = rows[-1] if rows else None
    last_score = int(last["score"]) if last is not None else 0
    last_signals = last["signals"] if last is not None else {}
    cfg = {**SIGNAL_DEFAULTS, **_signal_config(), **(profile.get("config") or {})}

    return {
        "ticker": ticker,
        "interval": interval,
        # Which profile the chart is actually running, and how it was
        # validated. Published so the header can never imply a tuned chart is
        # a measured one -- a profile saved from a slider drag reads
        # `in_sample`, which is a warning, not a credential.
        "profile": {
            "source": profile.get("source"),
            "validation": profile.get("validation"),
            "saved_at": profile.get("saved_at"),
            "note": profile.get("note"),
            "config": profile.get("config") or {},
            "elmo": profile.get("elmo") or {},
        },
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
        # NEW meaning: the conviction engine's transitions, not the old gate.
        "markers": run.markers(),
        "markers_legacy": gated_markers(rows),
        "overlays": classic_overlays(records),
        "oscillators": oscillators(records),
        "elmo": elmo.as_dict(),
        "algo": {
            **conv.as_dict(),
            "actions": run.actions,
            "position": run.position,
            "stop": run.stop,
            "trades": [t.as_dict() for t in run.trades],
            "config": {
                k: cfg[k]
                for k in (
                    "entry_long",
                    "add_long",
                    "trim_long",
                    "exit_long",
                    "entry_short",
                    "exit_short",
                    "allow_short",
                    "atr_stop_mult",
                    "cooldown_bars",
                )
            },
        },
        "whale": {
            "bars": whale_detail,
            "coverage": whale_coverage(records, trades),
            "min_premium": min_premium,
            "mode": mode,
            "prints": len(trades),
            "status": flow_status,
        },
        "live": {
            "conviction": _conviction(last_signals, last_score),
            "score": last_score,
            "algo_score": float(conv.score[-1]) if conv.score else 0.0,
            "action": run.actions[-1] if run.actions else "none",
            "position": float(run.position[-1]) if run.position else 0.0,
            "signals": {
                "whale": bool(last_signals.get("whale")),
                "wave3": bool(last_signals.get("wave3")),
                "squeeze": bool(last_signals.get("squeeze")),
                "trend": bool(last_signals.get("trend")),
                "liquidity": bool(last_signals.get("liquidity")),
            },
        },
        "signals": [row["signals"] for row in rows],
        "direction": direction_coverage(records),
        "rh": _rh_payload(rh),
        **({"flow": flow} if flow is not None else {}),
    }
