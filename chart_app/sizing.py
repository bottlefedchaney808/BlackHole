"""How big a position is, how it is bought and sold, and what margin costs.

This replaces the `max_units` x `unit_fraction` ladder as the thing the
backtest sizes with. That ladder described a position as N identical "units"
and each unit as a share of capital, which made every other concept a unit
count: pyramiding was "more units", scale-out was "fewer units", and leverage
was "units that cost more than 1/N". Three different decisions -- how big,
how to get in, how to get out -- were one integer, so none could be set on its
own, and a 5-unit cap meant every order was a fifth of whatever the position
was. On a small book that was a fifth of one share.

The model now is the one a desk uses:

    position_size   the FULL position, as a share of equity (1.0 = the book).
                    The tester shows it in dollars against the starting pool.
    entry_style     "all"   -- the first buy is the whole position.
                    "scale" -- each entry/add order buys `entry_slice` of it,
                               until the full position is on.
    exit_style      "all"   -- an exit signal sells everything.
                    "scale" -- each exit order sells `exit_slice` of the full
                               position, until nothing is left.
    fractional      off by default: every order is whole shares/contracts.
                    Tick it for an instrument where one share is more than an
                    order (BRK-A).
    exit_slice      also sizes a TRIM, whichever exit style is chosen: a trim
                    is one exit-sized order that leaves the rest on.
    margin_pct      the share of every order that is BORROWED. 0% is a cash
                    account. It never changes how many shares an order buys --
                    the order size does that. At 50% the same shares cost half
                    the cash, the other half is a loan that accrues interest,
                    and a maintenance requirement sells the book if equity
                    falls through it. Hard ceiling 80% (MAX_BORROW); the venue
                    lowers it where the broker is stricter (Reg T: 50% on
                    stock; spot crypto: 0%).

Everything is a FRACTION, so a saved profile means the same thing on a $1M
backtest pool and a $500 live sleeve; the dollars are computed from equity at
the moment of each order. The ladder the signal engine walks is `level`, a
signed share of the full position in [-1, 1].

Old profiles (`max_units` / `unit_fraction`, no `position_size`) resolve to the
exact same ladder they always walked: position = max_units * unit_fraction,
one entry order and one exit order = 1/max_units of it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Float noise on a ladder of thirds: three adds of 1/3 are 0.9999999999999999.
EPS = 1e-9

# ---------------------------------------------------------------------------
# Venue margin terms. Published figures, looked up 2026-09-28 -- not tuned.
# ---------------------------------------------------------------------------

# Robinhood margin interest, tiered by the size of the margin balance. The
# rate is the Fed funds target upper bound plus a spread, so these drift with
# the Fed; the figures are Robinhood's own table as of March 2026:
#   https://robinhood.com/us/en/support/articles/margin-rates
# Above $1M the table keeps stepping down (to 3.95% at $50M+); the $100k-$1M
# rate is held flat above that, which overstates cost slightly on a large loan
# rather than inventing tier boundaries nobody checked.
RH_MARGIN_TIERS: tuple[tuple[float, float], ...] = (
    (50_000.0, 0.050),
    (100_000.0, 0.048),
    (math.inf, 0.045),
)
# Gold includes the first $1,000 of margin with the subscription.
RH_MARGIN_FREE_USD = 1_000.0
# Reg T: 50% initial, so a position held overnight tops out at 2x. FINRA's
# floor for maintenance is 25%; Robinhood's house requirement is often higher
# per stock (they publish it per symbol), so 25% is the LEAST that can happen.
RH_INITIAL_MARGIN = 0.50
RH_MAINTENANCE = 0.25

# Kalshi perpetuals: margin is a share of notional, set per product; the help
# centre quotes up to ~6x on crypto, 15.2x on gold, 7.7x on silver, with
# maintenance ~90% of the initial requirement. No borrow interest -- the cost
# of carry on a perp is FUNDING, which this repo has no history for, so it is
# not modelled (and says so in the metrics).
#   https://help.kalshi.com/en/articles/15357594-how-margin-works
#   https://kalshi.com/perpetuals/learn/leverage-trading-crypto
PERP_MAX_LEVERAGE: dict[str, float] = {"GOLD": 15.2, "SILVER": 7.7}
PERP_DEFAULT_MAX_LEVERAGE = 6.0
PERP_MAINTENANCE_OF_INITIAL = 0.90

# Nobody lends 5x against a retail book. Whatever a venue advertises, the
# backtest never borrows more than this share of a position.
MAX_BORROW = 0.80


@dataclass(frozen=True)
class Venue:
    name: str  # "equity" | "perp" | "crypto_spot"
    max_leverage: float
    maintenance: float  # equity / gross notional below which the book is sold
    charges_interest: bool
    note: str

    def margin_rate(self, loan: float) -> float:
        """Annual rate charged on a loan of `loan` dollars."""
        if not self.charges_interest or loan <= 0:
            return 0.0
        for ceiling, rate in RH_MARGIN_TIERS:
            if loan <= ceiling:
                return rate
        return RH_MARGIN_TIERS[-1][1]

    def interest(self, loan: float, years: float) -> float:
        """Dollars of interest on `loan` over `years`, net of Gold's free $1k."""
        billable = max(0.0, loan - RH_MARGIN_FREE_USD)
        return billable * self.margin_rate(loan) * max(0.0, years)


def venue_for(ticker: str | None) -> Venue:
    """The venue a symbol trades on, and therefore its margin terms."""
    t = (ticker or "").upper()
    if t.endswith(("-PERP", "-SWAP")) or (t.startswith("KX") and t.endswith("PERP")):
        base = t.split("-")[0].removeprefix("KX").removesuffix("PERP")
        max_lev = PERP_MAX_LEVERAGE.get(base, PERP_DEFAULT_MAX_LEVERAGE)
        return Venue(
            name="perp",
            max_leverage=max_lev,
            maintenance=PERP_MAINTENANCE_OF_INITIAL / max_lev,
            charges_interest=False,
            note=f"Kalshi perp: up to {max_lev:g}x, maintenance "
            f"{100 * PERP_MAINTENANCE_OF_INITIAL / max_lev:.1f}% of notional; "
            "funding not modelled",
        )
    if t.endswith(("-USD", "-USDT", "-USDC")):
        return Venue(
            name="crypto_spot",
            max_leverage=1.0,
            maintenance=0.0,
            charges_interest=False,
            note="spot crypto: no margin",
        )
    return Venue(
        name="equity",
        max_leverage=1.0 / RH_INITIAL_MARGIN,
        maintenance=RH_MAINTENANCE,
        charges_interest=True,
        note="Robinhood margin: Reg T 50% initial (2x), 25% maintenance, "
        "5.0/4.8/4.5% tiered interest, first $1k free (Gold)",
    )


# ---------------------------------------------------------------------------
# The sizing plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sizing:
    position_size: float  # full position, share of equity
    entry_slice: float  # share of the full position per entry/add order
    exit_slice: float  # share of the full position per trim/scale-out order
    scale_out: bool
    fractional: bool  # orders may fill in fractions of a share
    margin_pct: float  # borrowed share of each order, AFTER the venue ceiling
    margin_pct_asked: float
    legacy_units: int | None  # max_units when resolved from an old profile
    # "Take profit at 5% and 10%": gains off the average entry, ascending. Each
    # sells one exit chunk, once per trade. Empty = no profit target, and the
    # only ways out of a winner are the score and the trailing stop.
    take_profit: tuple[float, ...] = ()

    @property
    def position_scale(self) -> float:
        """Multiplier from `level` to the `position` series the chart and live
        runners read. Old profiles published whole units (level x max_units),
        and their live runners multiply that by `unit_fraction`; keeping the
        scale keeps `position x unit_fraction` equal to the exposure for both."""
        return float(self.legacy_units) if self.legacy_units else 1.0

    @property
    def buying_power(self) -> float:
        """Most gross notional one dollar of equity can carry at this margin."""
        return 1.0 / (1.0 - self.margin_pct)

    # --- ladder arithmetic, shared by the chart's state machine and the
    # backtest so the two cannot disagree about how big a position gets.

    def first(self) -> float:
        return self.entry_slice

    def can_add(self, level: float) -> bool:
        return abs(level) < 1.0 - EPS

    def add(self, level: float) -> float:
        sign = 1.0 if level >= 0 else -1.0
        return sign * _snap(min(1.0, abs(level) + self.entry_slice))

    def more_than_one_exit(self, level: float) -> bool:
        """True when one exit order would leave something on."""
        return abs(level) > self.exit_slice + EPS

    def shed(self, level: float) -> float:
        sign = 1.0 if level >= 0 else -1.0
        return sign * _snap(max(0.0, abs(level) - self.exit_slice))


def _snap(x: float) -> float:
    r = round(x, 9)
    return 0.0 if abs(r) < EPS else r


def _frac(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 and math.isfinite(v) else default


def parse_take_profit(value: Any) -> tuple[float, ...]:
    """`[5, 10]`, `"5, 10"` or `5` -> (5.0, 10.0). Non-positive or junk
    entries are dropped rather than raised: this comes from a text box."""
    if value is None or value == "":
        return ()
    items = (
        value
        if isinstance(value, (list, tuple))
        else str(value).replace(";", ",").split(",")
    )
    out: set[float] = set()
    for item in items:
        try:
            v = float(str(item).strip().rstrip("%"))
        except ValueError:
            continue
        if v > 0 and math.isfinite(v):
            out.add(v)
    return tuple(sorted(out))


def resolve_sizing(cfg: dict[str, Any], venue: Venue | None = None) -> Sizing:
    """Read the sizing keys out of a merged signal config.

    New keys win whenever `position_size` is set. Without it the old ladder is
    resolved exactly, so a profile saved before this model walks the same
    steps and reports the same exposure it always did.
    """
    venue = venue or venue_for(None)
    exit_style = str(cfg.get("exit_style") or "all").lower()
    scale_out = exit_style == "scale"

    ceiling = min(MAX_BORROW, max(0.0, 1.0 - 1.0 / max(1.0, venue.max_leverage)))

    if cfg.get("position_size") is None:
        m = max(1, int(cfg.get("max_units") or 3))
        uf = _frac(cfg.get("unit_fraction"), 1.0 / m)
        size = m * uf
        # An old pyramiding profile (unit_fraction 1.0) carried max_units x
        # the book with no financing at all. Its honest reading is a margin
        # account borrowing everything above the book -- and the ceiling below
        # then caps the position at what a broker would actually lend.
        asked = max(0.0, 1.0 - 1.0 / size)
        return Sizing(
            position_size=size,
            entry_slice=1.0 / m,
            exit_slice=1.0 / m,
            scale_out=scale_out,
            fractional=bool(cfg.get("fractional", False)),
            margin_pct=min(asked, ceiling),
            margin_pct_asked=asked,
            legacy_units=m,
            take_profit=parse_take_profit(cfg.get("take_profit")),
        )

    size = _frac(cfg.get("position_size"), 1.0)
    entry_all = str(cfg.get("entry_style") or "scale").lower() == "all"
    entry_slice = 1.0 if entry_all else min(1.0, _frac(cfg.get("entry_slice"), 1.0))
    exit_slice = min(1.0, _frac(cfg.get("exit_slice"), 1.0 / 3.0))
    try:
        asked = max(0.0, float(cfg.get("margin_pct") or 0.0))
    except (TypeError, ValueError):
        asked = 0.0
    return Sizing(
        position_size=size,
        entry_slice=entry_slice,
        exit_slice=exit_slice,
        scale_out=scale_out,
        fractional=bool(cfg.get("fractional", False)),
        margin_pct=min(asked, ceiling),
        margin_pct_asked=asked,
        legacy_units=None,
        take_profit=parse_take_profit(cfg.get("take_profit")),
    )


def order_quantity(dollars: float, price: float, fractional: bool = False) -> float:
    """Shares/contracts `dollars` buys at `price`.

    Whole shares by default, on every venue: an order is 30 or 50 shares, the
    way a broker shows it. `fractional` is for the instrument where one share
    is more than an order (BRK-A). Whole shares round DOWN -- an order never
    spends money it was not given."""
    if price <= 0 or dollars <= 0:
        return 0.0
    q = dollars / price
    return q if fractional else float(math.floor(q + 1e-9))
