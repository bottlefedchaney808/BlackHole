from __future__ import annotations

from launch_dock.launch import KALSHI


def stop_card(card: dict, *, cmdline: str | None, cancel, kill, cancel_equity=None) -> dict:
    out = dict(card)
    seed = card.get("seed")

    if seed == "stocks" and (cmdline is None or "run_live_equity" in cmdline):
        # Same order as a perp: cancel the ticker's resting orders at the
        # broker FIRST (a killed runner cannot cancel its GTC maker buy), keep
        # the protective stop, then kill. Stop never flattens.
        instrument = str(card.get("instrument", "")).upper()
        if cmdline is not None and f"--ticker {instrument}" not in cmdline:
            out["state"] = "unknown"
            return out
        if card.get("live"):
            if cancel_equity is None:
                out["error"] = "no Robinhood cancel wired; not killing a live stock sleeve"
                out["state"] = "running"
                return out
            try:
                cancel_equity(instrument)
            except Exception as exc:
                out["error"] = str(exc)
                out["state"] = "running"
                return out
        if cmdline is not None:
            kill(card["pid"])
        out["state"] = "stopped"
        out["pid"] = None
        return out

    if seed == "perp":
        if cmdline is not None:
            instrument = str(card.get("instrument", "")).upper()
            if "run_live_perp" not in cmdline or f"--ticker {instrument}" not in cmdline:
                out["state"] = "unknown"
                return out

        if card.get("live"):
            try:
                cancel(KALSHI[card["instrument"].upper()])
            except Exception as exc:
                out["error"] = str(exc)
                out["state"] = "running"
                return out

        if cmdline is None:
            out["state"] = "stopped"
            out["pid"] = None
            return out

        kill(card["pid"])
        out["state"] = "stopped"
        out["pid"] = None
        return out

    if cmdline is None:
        out["state"] = "stopped"
        out["pid"] = None
        return out

    expected = "whale_scanner" if seed == "stocks" else "event_desk/churn.py watch"
    if expected not in cmdline:
        out["state"] = "unknown"
        return out

    kill(card["pid"])
    out["state"] = "stopped"
    out["pid"] = None
    return out
