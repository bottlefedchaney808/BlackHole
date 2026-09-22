def mark_stale(card: dict, resolved_saved_at: str | None) -> dict:
    out = dict(card)
    if out.get("seed") != "perp" or out.get("state") != "running":
        return out
    if resolved_saved_at and resolved_saved_at != out.get("running_saved_at"):
        out["state"] = "stale"
    return out


def reap(card: dict, alive: bool) -> dict:
    out = dict(card)
    if alive or not out.get("pid"):
        return out
    out["state"] = "stopped"
    out["pid"] = None
    return out
