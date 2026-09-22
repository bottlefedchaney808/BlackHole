from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from chart_app import profiles

CHART_PROFILES = "http://127.0.0.1:8791/api/profiles"


class ChartSaveError(RuntimeError):
    pass


def _changed(
    existing: dict | None,
    config: dict,
    elmo: dict,
    capital: float | None,
) -> bool:
    if not existing or existing.get("source") == "defaults":
        return True
    return (
        dict(existing.get("config") or {}) != dict(config)
        or dict(existing.get("elmo") or {}) != dict(elmo)
        or existing.get("capital") != capital
    )


def save_preset(
    ticker: str,
    interval: str,
    *,
    config: dict[str, Any],
    elmo: dict[str, Any],
    capital: float | None,
    note: str = "",
    path: Path,
    chart_post: Callable[[str, dict], dict] | None,
    existing: dict | None,
) -> dict[str, Any]:
    validation = "in_sample"
    if existing and not _changed(existing, config, elmo, capital):
        stored = existing.get("validation") or "in_sample"
        if stored in profiles.VALIDATIONS:
            validation = stored
    body = {
        "ticker": ticker,
        "interval": interval,
        "config": dict(config),
        "elmo": dict(elmo),
        "capital": capital,
        "validation": validation,
        "metrics": {},
        "note": note,
    }
    if chart_post is not None:
        result = chart_post(CHART_PROFILES, body)
        if not result.get("ok"):
            raise ChartSaveError(str(result.get("error") or "chart save failed"))
        return result["saved"]
    return profiles.save(
        ticker,
        interval,
        config=config,
        elmo=elmo,
        capital=capital,
        validation=validation,
        note=note,
        path=path,
    )
