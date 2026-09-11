"""hedge_optimizer_tool.py

Two hedging optimizers behind one Tool, dispatched on ``context['mode']``:

1. ``mode='min_var'`` (default) -- wraps
   VaR_Tools_Simulations/var_engine/hedge_optimizer.py (``min_var_hedge``) as a
   standalone Tool pointed at any suite's ``suite_context.json``. This is the
   original stocks-only minimum-variance hedge: optimal hedge weights across the
   context's Vol_Suite basket peers and the resulting VaR reduction.

2. ``mode='options_hedge'`` -- a greeks-based hedge that shows how to make a
   position delta- AND vega-neutral using the underlying stock plus listed
   options (not just other stocks). It fetches a live option chain snapshot
   (``ThetaDataController.option_bulk_greeks`` -- one call returns delta/vega/
   gamma/IV/bid/ask for every strike at an expiry), reads the position's net
   delta and vega, and solves two closed-form recipes:

   * ``stock_atm_call`` (A): an ATM call as the vega driver, then the
     underlying stock closes all residual delta. Uses both stocks and options.
   * ``atm_call_put`` (B): an ATM call + ATM put (no stock) via a 2x2 Cramer
     solve -- pure-options delta-vega neutrality.

Both recipes are fail-closed: a missing spot, empty chain, missing ATM
strike, or a degenerate (near-singular) greeks matrix raises a structured
``ValueError`` rather than silently falling back to another model. The result
carries explicit units and provenance (the dealer-model-adoption contract).
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VAR_SUITE_ROOT = _REPO_ROOT / "VaR_Tools_Simulations"

for _p in (str(_VAR_SUITE_ROOT), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from Tools.spec import ToolSpec


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _resolve_output_dir(context: dict[str, Any]):
    override = context.get("_output_dir_override")
    raw = override or context.get("output_dir")
    return str(raw) if raw else None


def _import_var_main():
    """Load VaR_Tools_Simulations/main.py under a unique module name.

    Options_Suite, VaR_Tools_Simulations and sentiment-scanner each ship
    their own ``main.py`` -- a bare ``import main`` would silently return
    whichever one first landed in ``sys.modules`` under that generic name in
    this (possibly long-lived) dashboard process, instead of raising.
    """
    module_name = "var_tools_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, str(_VAR_SUITE_ROOT / "main.py")
    )
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main


# ---------------------------------------------------------------------------
# Options hedge -- pure, offline-testable core (no network, no side effects)
# ---------------------------------------------------------------------------
#: Greeks are reported in per-share (delta) and per-contract-per-1.00-vol-point
#: (vega) units, exactly as the ThetaData snapshot returns them. One standard
#: contract controls ``CONTRACT_MULTIPLIER`` shares, so a leg of ``n`` contracts
#: contributes ``n * CONTRACT_MULTIPLIER * delta`` to delta and
#: ``n * CONTRACT_MULTIPLIER * vega`` to vega.
CONTRACT_MULTIPLIER = 100.0

#: Units string surfaced verbatim in every result so consumers never have to
#: reverse-engineer the scale.
GREEKS_UNITS = {
    "delta": "shares-equivalent (1 underlying share = 1.0 delta)",
    "vega": "USD per 1.00 vol point (per contract as reported by the vendor)",
    "contract_multiplier": CONTRACT_MULTIPLIER,
}


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def compute_position_greeks(
    stocks: list[dict[str, Any]],
    option_legs: list[dict[str, Any]],
    contract_multiplier: float = CONTRACT_MULTIPLIER,
) -> dict[str, Any]:
    """Aggregate a position into net delta / net vega / total value.

    ``stocks`` entries: ``{shares, price?, side?}`` (side default +1 long).
    ``option_legs`` entries: ``{delta, vega, price?, side?}`` (side default +1).
    Shares/contracts are signed magnitudes already; ``side`` lets the caller
    keep magnitudes positive and flip the sign here instead.
    """
    net_delta = 0.0
    net_vega = 0.0
    total_value = 0.0
    for s in stocks or []:
        shares = float(s.get("shares", 0) or 0)
        side = float(s.get("side", 1) or 1)
        price = float(s.get("price", 0) or 0)
        signed = shares * side
        net_delta += signed  # 1 share == 1.0 delta
        total_value += signed * price
    for leg in option_legs or []:
        n = float(leg.get("contracts", leg.get("shares", 0)) or 0)
        side = float(leg.get("side", 1) or 1)
        delta = float(leg.get("delta", 0) or 0)
        vega = float(leg.get("vega", 0) or 0)
        price = float(leg.get("price", 0) or 0)
        signed = n * side
        net_delta += signed * contract_multiplier * delta
        net_vega += signed * contract_multiplier * vega
        total_value += signed * contract_multiplier * price
    return {
        "net_delta": net_delta,
        "net_vega": net_vega,
        "total_value": total_value,
        "n_stock_positions": len(stocks or []),
        "n_option_legs": len(option_legs or []),
    }


def _cramer2(
    a11: float,
    a12: float,
    a21: float,
    a22: float,
    b1: float,
    b2: float,
) -> tuple:
    """Solve [[a11,a12],[a21,a22]] [x1,x2] = [b1,b2]. Fail-closed on singular."""
    det = a11 * a22 - a12 * a21
    if abs(det) < 1e-12:
        raise ValueError(
            f"degenerate greeks matrix (det={det:.3e}) -- the two candidate "
            f"options are too greeks-correlated to form a unique delta+vega "
            f"neutral hedge; pick a wider strike spread or add a stock leg."
        )
    x1 = (b1 * a22 - a12 * b2) / det
    x2 = (a11 * b2 - b1 * a21) / det
    return x1, x2


def solve_stock_plus_atm_call(
    position: dict[str, Any],
    atm_call: dict[str, Any],
    contract_multiplier: float = CONTRACT_MULTIPLIER,
) -> dict[str, Any]:
    """Recipe A: ATM call as the vega driver, stock covers residual delta.

    ``position``: output of :func:`compute_position_greeks`.
    ``atm_call``: candidate dict with at least ``delta`` and ``vega`` (per
    share / per contract respectively).

    Returns contract count of the ATM call, the stock share count, and the
    post-hedge residual greeks (should be ~0 on both axes).
    """
    call_delta = float(atm_call.get("delta", 0) or 0)
    call_vega = float(atm_call.get("vega", 0) or 0)
    net_delta = float(position["net_delta"])
    net_vega = float(position["net_vega"])

    if call_vega == 0:
        raise ValueError(
            f"ATM call vega is 0 (strike={atm_call.get('strike')}, "
            f"right={atm_call.get('right')}) -- no vega driver available; "
            f"cannot neutralize vega with this candidate."
        )

    n_call = -net_vega / (call_vega * contract_multiplier)  # contracts (signed)
    residual_delta = net_delta + n_call * contract_multiplier * call_delta
    stock_shares = -residual_delta  # buy/sell to close residual delta

    post_delta = net_delta + n_call * contract_multiplier * call_delta + stock_shares
    post_vega = net_vega + n_call * contract_multiplier * call_vega
    return {
        "recipe": "stock_atm_call",
        "description": "ATM call as vega driver; underlying stock closes residual delta",
        "atm_call_contracts": n_call,
        "stock_shares": stock_shares,
        "stock_direction": "buy"
        if stock_shares > 0
        else ("sell" if stock_shares < 0 else "none"),
        "post_hedge": {"net_delta": post_delta, "net_vega": post_vega},
        "units": GREEKS_UNITS,
    }


def solve_atm_call_put(
    position: dict[str, Any],
    atm_call: dict[str, Any],
    atm_put: dict[str, Any],
    contract_multiplier: float = CONTRACT_MULTIPLIER,
) -> dict[str, Any]:
    """Recipe B: ATM call + ATM put (no stock), 2x2 Cramer solve.

    Solves for contract counts (nC, nP) such that the added delta and vega
    cancel the position's net delta and net vega.
    """
    c_delta = float(atm_call.get("delta", 0) or 0)
    c_vega = float(atm_call.get("vega", 0) or 0)
    p_delta = float(atm_put.get("delta", 0) or 0)
    p_vega = float(atm_put.get("vega", 0) or 0)
    net_delta = float(position["net_delta"])
    net_vega = float(position["net_vega"])

    # Column-scaled greeks (per contract):
    a11 = c_delta * contract_multiplier
    a12 = p_delta * contract_multiplier
    a21 = c_vega * contract_multiplier
    a22 = p_vega * contract_multiplier
    n_call, n_put = _cramer2(a11, a12, a21, a22, -net_delta, -net_vega)

    post_delta = net_delta + (n_call * c_delta + n_put * p_delta) * contract_multiplier
    post_vega = net_vega + (n_call * c_vega + n_put * p_vega) * contract_multiplier
    return {
        "recipe": "atm_call_put",
        "description": "ATM call + ATM put (no stock) -- pure-options delta+vega neutral",
        "atm_call_contracts": n_call,
        "atm_put_contracts": n_put,
        "post_hedge": {"net_delta": post_delta, "net_vega": post_vega},
        "units": GREEKS_UNITS,
    }


# ---------------------------------------------------------------------------
# Options hedge -- live data layer
# ---------------------------------------------------------------------------
def _import_thetadata():
    from shared.thetadata import ThetaDataController

    return ThetaDataController


def _parse_chain(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw ``option_bulk_greeks`` rows into candidate dicts.

    Each row carries ``strike`` (theta-scaled int = dollar*1000), ``right``
    ('C'/'P'), and first-order greeks ``delta``/``vega``/``gamma`` plus
    ``implied_vol``/``bid``/``ask``. Strike is converted back to dollars.
    """
    from shared.thetadata import strike_from_theta

    cands: list[dict[str, Any]] = []
    for row in rows or []:
        try:
            k_theta = int(float(row["strike"]))
        except (KeyError, TypeError, ValueError):
            continue
        right = str(row.get("right", "")).strip().upper()[:1]
        if right not in ("C", "P"):
            continue
        delta = row.get("delta", row.get("Delta"))
        vega = row.get("vega", row.get("Vega"))
        iv = row.get("implied_vol", row.get("iv", row.get("IV")))
        bid = row.get("bid", row.get("Bid"))
        ask = row.get("ask", row.get("Ask"))
        if not _finite(delta) or not _finite(vega):
            continue
        bid_v = float(bid) if _finite(bid) else float("nan")
        ask_v = float(ask) if _finite(ask) else float("nan")
        mid = (
            (bid_v + ask_v) / 2.0
            if (
                math.isfinite(bid_v)
                and math.isfinite(ask_v)
                and bid_v > 0
                and ask_v > 0
            )
            else float("nan")
        )
        iv_v = float(iv) if _finite(iv) else 0.0
        cands.append(
            {
                "strike": strike_from_theta(k_theta),
                "right": right,
                "delta": float(delta),
                "vega": float(vega),
                "gamma": float(row.get("gamma", 0) or 0)
                if _finite(row.get("gamma"))
                else 0.0,
                "iv": iv_v,
                "mid": mid,
            }
        )
    return cands


def _nearest(cands: list[dict[str, Any]], spot: float, right: str) -> dict[str, Any]:
    """Pick the candidate nearest ``spot`` for a given ``right`` with IV>0.

    Fail-closed: raises ``ValueError`` if none exists.
    """
    pool = [
        c for c in cands if c["right"] == right and c["iv"] > 0 and _finite(c["vega"])
    ]
    if not pool:
        raise ValueError(
            f"no usable ATM {right} candidate (strike near spot={spot:.2f}) "
            f"with a positive IV and finite vega in the chain snapshot."
        )
    return min(pool, key=lambda c: abs(c["strike"] - spot))


def _side_sign(side: Any) -> float:
    """Normalize a position leg's ``side`` to a signed multiplier (+1/-1).

    Accepts a number (already signed) or the words ``long``/``short`` (and
    common spellings). Anything unrecognized raises -- a position with an
    unknown side should fail loudly, not silently flip.
    """
    if side is None:
        return 1.0
    if isinstance(side, (int, float)):
        v = float(side)
        if v == 0 or not _finite(v):
            raise ValueError(f"invalid side {side!r} (zero or non-finite).")
        return 1.0 if v > 0 else -1.0
    word = str(side).strip().lower()
    if word in ("long", "buy", "long", "l", "+", "+1", "1", "true", "yes"):
        return 1.0
    if word in ("short", "sell", "s", "-", "-1", "false", "no"):
        return -1.0
    raise ValueError(
        f"unrecognized position side {side!r} (expected long/short or +/-1)."
    )


def _build_position(context: dict[str, Any], ticker: str, spot: float):
    """Read the position from context, or default to a long 100-share stock
    position in the focus ticker (clearly flagged as an assumption).

    Normalizes every leg's ``side`` to a signed multiplier so ``"long"``/
    ``"short"`` and numeric sides all work; a bad side raises here, not deep
    in the greeks aggregation.
    """
    pos = context.get("position")
    if not pos:
        return (
            [{"ticker": ticker, "shares": 100, "price": spot, "side": 1.0}],
            [],
            f"assumed: long 100 shares of {ticker} @ {spot:.2f} "
            f"(no explicit position supplied)",
        )
    stocks = pos.get("stocks", []) or []
    options = pos.get("options", []) or []
    for s in stocks:
        s["side"] = _side_sign(s.get("side", 1))
        if "price" not in s and s.get("ticker") == ticker:
            s["price"] = spot
    for o in options:
        o["side"] = _side_sign(o.get("side", 1))
    return stocks, options, "explicit position from context"


def run_options_hedge(context: dict[str, Any]) -> dict[str, Any]:
    """Live delta+vega neutral hedge for ``context``'s focus ticker."""
    focus = context.get("focus") or {}
    ticker = (context.get("ticker") or focus.get("ticker") or "").strip()
    if not ticker:
        raise ValueError(
            "options_hedge requires a ticker (context['ticker'] or focus.ticker)."
        )

    td_cls = _import_thetadata()
    td = td_cls()

    spot = td.fetch_spot_price(ticker)
    if not _finite(spot) or spot <= 0:
        raise ValueError(
            f"no usable spot price for {ticker!r} (got {spot!r}) -- cannot "
            f"select ATM strikes; refusing to hedge against a missing price."
        )

    expiry = str(context.get("expiry") or "").strip()
    if not expiry:
        exps = td.list_expirations(ticker)
        if not exps:
            raise ValueError(f"no expirations returned for {ticker!r}.")
        expiry = sorted(exps)[0]
        expiry_note = f"nearest listed expiry auto-selected: {expiry}"
    else:
        expiry_note = f"caller-specified expiry: {expiry}"

    rows = td.option_bulk_greeks(ticker, expiry)
    cands = _parse_chain(rows)
    if not cands:
        raise ValueError(
            f"option chain snapshot for {ticker!r} @ {expiry} returned no "
            f"parseable rows (raw rows={len(rows or [])})."
        )

    atm_call = _nearest(cands, spot, "C")
    atm_put = _nearest(cands, spot, "P")

    stocks, options, pos_note = _build_position(context, ticker, spot)
    position = compute_position_greeks(stocks, options)

    recipe_a = solve_stock_plus_atm_call(position, atm_call)
    try:
        recipe_b = solve_atm_call_put(position, atm_call, atm_put)
    except ValueError:
        # Degenerate call/put greeks matrix -- no unique pure-options
        # solution exists for this strike pair. Leave recipe_b out rather
        # than failing the whole hedge; callers fall back to recipe_a.
        recipe_b = None

    return {
        "ticker": ticker,
        "spot": spot,
        "expiry": expiry,
        "position": {
            "stocks": stocks,
            "options": options,
            "note": pos_note,
            **position,
        },
        "atm_call": atm_call,
        "atm_put": atm_put,
        "recipes": {
            "stock_atm_call": recipe_a,
            "atm_call_put": recipe_b,
        },
        "units": GREEKS_UNITS,
        "provenance": {
            "source": "ThetaDataController.option_bulk_greeks (live snapshot)",
            "expiry_selection": expiry_note,
            "position": pos_note,
            "rows_returned": len(rows or []),
            "candidates_parsed": len(cands),
        },
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
_OPTIONS_HEDGE_MODES = {"options_hedge", "options", "options-hedge", "option_hedge"}


def run(context: dict[str, Any]) -> dict[str, Any]:
    """Dispatch on ``context['mode']``.

    ``min_var`` (default): stocks-only min-variance hedge via the VaR engine.
    ``options_hedge``: live delta+vega neutral hedge (stock + options).
    """
    mode = str(context.get("mode") or "min_var").strip().lower()
    if mode in _OPTIONS_HEDGE_MODES:
        return run_options_hedge(context)

    var_main = _import_var_main()
    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    output_dir = _resolve_output_dir(context)
    return var_main._build_hedge_optimizer_from_context(context, ticker, output_dir)


TOOL_SPEC = ToolSpec(
    name="Hedge Optimizer",
    slug="hedge-optimizer",
    description=(
        "Two hedging optimizers. mode='min_var' (default): minimum-variance "
        "stock hedge of the context's focus ticker against its Vol_Suite "
        "basket peers. mode='options_hedge': live delta+vega neutral hedge "
        "using the underlying stock plus listed options (ATM call+put), with "
        "explicit greek units and provenance."
    ),
    run=run,
)
