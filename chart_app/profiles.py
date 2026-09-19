"""Named parameter sets, per symbol and per timeframe.

Why this exists
---------------
Four call sites build an `ElmoResult`, and the LIVE one -- `snapshot.py`'s
`compute_elmo(records)` -- took no overrides at all. So a knob moved in the
tester changed the tester's P&L and nothing else: the chart kept drawing
shipped defaults. Tuning that cannot reach the thing you are looking at is not
tuning, and re-typing four sliders on every symbol switch is not a workflow.

Per timeframe vs per symbol
---------------------------
These are NOT the same claim, and the evidence differs sharply:

* **Per timeframe is structural.** Every window is specified in BARS, so
  `entropy_rank_window = 200` is 7.7 sessions at 15m and 200 sessions at 1d.
  The same integer is a different horizon on every timeframe; storing one per
  timeframe is unit conversion, not fitting.

* **Per symbol is the half the measurements warn about.** Tuning windows per
  ticker returned in-sample median +11.5pp and out-of-sample +0.55pp on 4 of 8
  series (2026-09-18 walk-forward). It is allowed here because Jason asked for
  it and it is his desk -- but a profile carries HOW IT WAS VALIDATED, so
  loading one tells you which kind of number you are looking at. A profile
  saved from a slider drag is `in_sample`, and says so.

Resolution order is specific -> general, so a symbol can override a timeframe
without anyone maintaining a profile for every pair:

    (SPY, 15m)  ->  (*, 15m)  ->  (*, *)  ->  shipped defaults
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# A profile that has not been walk-forward tested is not a finding. The vocab
# is deliberately small and ordered worst -> best.
VALIDATIONS = ("none", "in_sample", "walk_forward", "permutation")

ANY = "*"

_DEFAULT_STORE = Path("artifacts/chart_app_profiles.json")


def store_path() -> Path:
    """`CHART_APP_PROFILES` overrides, so a sweep can use a scratch file."""
    raw = os.environ.get("CHART_APP_PROFILES")
    return Path(raw) if raw else _DEFAULT_STORE


def _key(ticker: str | None, interval: str | None) -> str:
    return f"{(ticker or ANY).upper()}|{interval or ANY}"


def load_all(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored profile. Never raises: a corrupt store reads as empty.

    A tuning file that cannot be parsed must not take the chart down with it --
    the chart's job is to draw, and shipped defaults are a valid answer.
    """
    p = path or store_path()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save(
    ticker: str | None,
    interval: str | None,
    *,
    config: dict[str, Any] | None = None,
    elmo: dict[str, Any] | None = None,
    capital: float | None = None,
    validation: str = "in_sample",
    metrics: dict[str, Any] | None = None,
    note: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Write one profile. Returns the record as stored.

    `validation` defaults to `in_sample` rather than `none` on purpose: the
    normal way a profile gets made is a slider drag against visible bars, and
    that IS an in-sample fit. Claiming `walk_forward` requires passing it
    explicitly, which `backtest_runner` does after actually running folds.
    """
    if validation not in VALIDATIONS:
        raise ValueError(f"validation must be one of {VALIDATIONS}, got {validation!r}")
    p = path or store_path()
    data = load_all(p)
    record = {
        "ticker": (ticker or ANY).upper(),
        "interval": interval or ANY,
        "config": dict(config or {}),
        "elmo": dict(elmo or {}),
        "capital": float(capital) if capital is not None else None,
        # Provenance, house pattern. `metrics` is the run the profile was saved
        # from, so a later reader can see what it was fitted against instead of
        # guessing.
        "validation": validation,
        "metrics": dict(metrics or {}),
        "note": note,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    data[_key(ticker, interval)] = record
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return record


def delete(ticker: str | None, interval: str | None, path: Path | None = None) -> bool:
    p = path or store_path()
    data = load_all(p)
    if _key(ticker, interval) not in data:
        return False
    del data[_key(ticker, interval)]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return True


def resolve(
    ticker: str | None, interval: str | None, path: Path | None = None
) -> dict[str, Any]:
    """The profile that applies to this (ticker, interval), specific first.

    Returns a record with an added `source` naming which key answered, so the
    UI can say "SPY|15m" rather than implying a symbol profile exists when a
    timeframe-wide one is doing the work. A miss returns an empty record with
    `source: "defaults"` -- never None, so callers need no branch.
    """
    data = load_all(path)
    for tkr, itv in (
        (ticker, interval),   # SPY|15m  -- the symbol's own
        (ANY, interval),      # *|15m    -- the timeframe's, structural
        (ticker, ANY),        # SPY|*    -- the symbol across timeframes
        (ANY, ANY),           # *|*      -- the desk default
    ):
        hit = data.get(_key(tkr, itv))
        if hit:
            return {**hit, "source": _key(tkr, itv)}
    return {
        "ticker": (ticker or ANY).upper(),
        "interval": interval or ANY,
        "config": {},
        "elmo": {},
        "capital": None,
        "validation": "none",
        "metrics": {},
        "note": "",
        "saved_at": None,
        "source": "defaults",
    }
