"""Delta-band module: OU band fit loader, net delta from books, band position/regime.

Phase 1 of PLAN_dealer_band_integration_20260901 (section 3.2). All functions are
pure / local-file only — no ThetaData import, no network. The band is a timing
layer over the dealer exposure book: N only *becomes* flow near the band edge.

Standing caveat (cite with every band number): N from daily snapshots mixes dealer
action with mechanical delta repricing on static OI; z is a regime flag, never a
signed-flow predictor.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Union


@dataclass(frozen=True)
class BandFit:
    """Parameters of the fitted OU band for one N definition."""

    mu: float
    sigma_eq: float
    kappa: float
    half_life_days: float
    definition: str
    fit_window: Dict[str, Any] = field(default_factory=dict)
    ar1: float = float("nan")
    vr: Dict[str, float] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True)
class BandPosition:
    """Current position of net delta N within its fitted band."""

    n: float
    dev: float
    z: float
    regime: str


# Regime labels (§3.2): |z|<1 quiet/absorbed, 1<=|z|<2 edge approach, |z|>=2 at edge.
REGIME_QUIET = "QUIET/ABSORBED"
REGIME_EDGE_APPROACH = "EDGE APPROACH (volume regime)"
REGIME_AT_EDGE = "AT EDGE (release regime)"

# Loader validation bounds.
_MAX_HALF_LIFE_DAYS = 60.0


def _as_float(value: Any) -> Union[float, None]:
    """Best-effort numeric coercion; None for missing/non-numeric values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def load_band_fit(path: str, definition: str = "bucketed_0_10") -> BandFit:
    """Load a band fit from the OU fit JSON produced by the Phase-1 refit.

    The JSON has shape::

        {"definitions": {name: {mu, sigma_eq, kappa, half_life_days, ar1, vr, ...}},
         "active_for_live": "bucketed_0_10",
         "fit_window": {"first": ..., "last": ...}}

    Raises ValueError on missing definition/keys or out-of-range values
    (sigma_eq must be > 0, 0 < half_life_days < 60).
    """
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)

    definitions = payload.get("definitions")
    if not isinstance(definitions, dict) or definition not in definitions:
        available = sorted(definitions) if isinstance(definitions, dict) else []
        raise ValueError(
            f"band fit definition {definition!r} not found in {path}; available: {available}"
        )

    raw = definitions[definition]
    if not isinstance(raw, dict):
        raise ValueError(f"band fit definition {definition!r} in {path} is not an object")

    missing = [k for k in ("mu", "sigma_eq", "kappa", "half_life_days") if k not in raw]
    if missing:
        raise ValueError(f"band fit definition {definition!r} in {path} missing keys: {missing}")

    try:
        mu = float(raw["mu"])
        sigma_eq = float(raw["sigma_eq"])
        kappa = float(raw["kappa"])
        half_life_days = float(raw["half_life_days"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"band fit definition {definition!r} in {path} has non-numeric values: {exc}")

    if not (sigma_eq > 0.0):
        raise ValueError(
            f"band fit definition {definition!r}: sigma_eq must be > 0, got {sigma_eq}"
        )
    if not (0.0 < half_life_days < _MAX_HALF_LIFE_DAYS):
        raise ValueError(
            f"band fit definition {definition!r}: half_life_days must be in (0, {_MAX_HALF_LIFE_DAYS:g}),"
            f" got {half_life_days}"
        )

    fit_window = payload.get("fit_window") if isinstance(payload.get("fit_window"), dict) else {}

    ar1_raw = raw.get("ar1")
    vr_raw = raw.get("vr") if isinstance(raw.get("vr"), dict) else {}
    vr = {str(k): float(v) for k, v in vr_raw.items()}

    return BandFit(
        mu=mu,
        sigma_eq=sigma_eq,
        kappa=kappa,
        half_life_days=half_life_days,
        definition=definition,
        fit_window=dict(fit_window),
        ar1=float(ar1_raw) if _as_float(ar1_raw) is not None else float("nan"),
        vr=vr,
        source=os.fspath(path),
    )


def net_delta_from_books(books: Iterable[Mapping[str, Any]]) -> float:
    """Sum delta * open_interest * 100 over all rows of all books.

    Each book is a dict with 'rows' (list of row dicts carrying 'delta' and
    'open_interest' or 'oi') and optional 'spot' (ignored here). Rows with
    missing or non-numeric delta/oi are skipped. Pure — no I/O, no imports
    beyond stdlib.
    """
    total = 0.0
    for book in books:
        rows = book.get("rows") or []
        for row in rows:
            delta = _as_float(row.get("delta"))
            oi_raw = row.get("open_interest", row.get("oi"))
            oi = _as_float(oi_raw)
            if delta is None or oi is None:
                continue
            total += delta * oi * 100.0
    return total


def band_position(n: float, fit: BandFit) -> BandPosition:
    """Locate N within the fitted band and assign the regime label.

    dev = n - mu; z = dev / sigma_eq. Regimes are symmetric in |z| (display
    convention; signed-z skew is a Phase-5 concern per plan §6.2b).
    """
    dev = n - fit.mu
    z = dev / fit.sigma_eq
    az = abs(z)
    if az < 1.0:
        regime = REGIME_QUIET
    elif az < 2.0:
        regime = REGIME_EDGE_APPROACH
    else:
        regime = REGIME_AT_EDGE
    return BandPosition(n=n, dev=dev, z=z, regime=regime)


def append_history(path: str, record: Mapping[str, Any]) -> None:
    """Append a single JSON line to the band history file.

    Creates parent dirs as needed. Append-only: never reads existing content,
    so a malformed pre-existing file cannot crash the caller (§3.5 guard).
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(record), default=str) + "\n")
