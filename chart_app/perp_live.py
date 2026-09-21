"""Turn the shadow book's position into Kalshi contracts and orders. Pure; no I/O.

The live sleeve trades exactly what `perp_sleeve.replay` says the tune holds --
it has no signal logic of its own. This module is the translation layer:

    book units (-max_units..+max_units)  x unit_fraction  x full contracts
                                      -> target contracts on KXBTCPERP

`full` is the whole cap at 1x: floor(min(cap, equity) / contract price), so the
book at max size is at most 1.00x notional and never borrows (Jason, 2026-09-20).

Order style is the fee finding: the walk-forward at 1x is +27% OOS at 2bp/side
and -9.7% at 12bp/side, so every order starts post-only at the touch (maker,
2bp). Only a position that must SHRINK is ever allowed to cross (taker) -- after
`cross_after_s` of not filling -- because an exit that never fills is risk, and
an entry that never fills is only a missed trade.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

CONTRACT_SIZE_BTC = 0.0001
TICK = 0.0001


def full_contracts(cap_dollars: float, equity: float, contract_price: float) -> int:
    """Contracts at 1.00x of the smaller of the cap and the account's equity."""
    budget = max(0.0, min(cap_dollars, equity))
    if contract_price <= 0:
        return 0
    return math.floor(budget / contract_price)


def sleeve_pnl(fills: list[dict[str, Any]], since: datetime, unrealized: float) -> float:
    """The sleeve's own P&L: its subaccount's fills since launch, plus the open mark.

    Why not `equity - equity0`: a subaccount's equity also moves when
    collateral moves between subaccounts. Measured 2026-09-21: every isolated
    manual trade in subaccount 64 moved subaccount 0's equity by the margin it
    posted (+$22, -$20, -$5, +$25, -$5) with no sleeve trade at all. The kill
    switch and the sizing both read that number, so a manual buy could flatten
    the sleeve or shrink it. Kalshi's `realized_pnl` excludes fees (2 @ 8.1509
    -> 8.3811 reports exactly 0.4604), so fees are charged here.
    """
    total = unrealized
    for fill in fills:
        ts = datetime.fromisoformat(str(fill["created_time"]))
        if ts < since:
            continue
        total += float(fill.get("realized_pnl") or 0) - float(fill.get("fees") or 0)
    return total


def book_target_units(book: dict[str, Any]) -> float:
    """What the tune holds after the next open: the pending decision, else now."""
    pending = book.get("pending")
    if pending:
        return float(pending["units_after"])
    return float(book.get("units") or 0.0)


def target_contracts(units: float, unit_fraction: float, full: int) -> int:
    raw = round(units * unit_fraction * full)
    return int(max(-full, min(full, raw)))


def reduces_risk(current: int, target: int) -> bool:
    """True when the move only shrinks exposure on the same side (or flattens)."""
    if current == 0:
        return False
    if target == 0:
        return True
    return (current > 0) == (target > 0) and abs(target) < abs(current)


def kalshi_stop(book_stop: float | None) -> float | None:
    """The book's stop is a BTC price; Kalshi quotes per 0.0001-BTC contract."""
    if not book_stop:
        return None
    return round(book_stop * CONTRACT_SIZE_BTC, 4)


@dataclass(frozen=True)
class OrderPlan:
    side: str  # "bid" buys, "ask" sells
    count: int
    price: float
    post_only: bool
    tif: str
    reduce_only: bool
    why: str


def plan_order(
    current: int,
    target: int,
    bid: float,
    ask: float,
    *,
    cross: bool = False,
    cross_band_ticks: int = 20,
) -> OrderPlan | None:
    """One order that moves `current` to `target`, or None when already there.

    Maker (default): post-only GTC resting AT the touch -- bid to buy, ask to
    sell -- so it can never take liquidity. Cross: an IOC limit `cross_band_ticks`
    through the touch, reduce-only, and only ever for a risk-reducing move.
    """
    delta = target - current
    if delta == 0:
        return None
    side = "bid" if delta > 0 else "ask"
    count = abs(delta)
    if cross:
        if not reduces_risk(current, target):
            raise ValueError("refusing to cross the spread to ADD risk")
        band = cross_band_ticks * TICK
        price = ask + band if side == "bid" else max(TICK, bid - band)
        return OrderPlan(
            side, count, price, False, "immediate_or_cancel", True, "cross-exit"
        )
    price = bid if side == "bid" else ask
    return OrderPlan(side, count, price, True, "good_till_canceled", False, "maker")
