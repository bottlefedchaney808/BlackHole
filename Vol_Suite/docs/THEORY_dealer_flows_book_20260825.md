# Working Theory — Dealer Flows Book (OpEx-to-OpEx) — 2026-08-25

Consolidates the design conversation into a single reference. Supersedes the ad-hoc
notes in `PLAN_dealer_flows_book_20260825.md` (which remains the implementation plan).
This is the **theory**; the plan is the **build list**.

## 1. The idea

Dealers don't hold a static inventory — they **rebalance across the OpEx cycle**.
A put written cheap at the start of the cycle gets bought back (or rolled) as the
cycle matures; a rich call gets chased. The current "expiration exposure" view is a
**snapshot**: it shows what's there now, not how it got there or where it's going.

The **dealer flows book** is the OpEx-cycle view:

- **Seed** at cycle start (what dealers were holding when the cycle began).
- **Accumulate flow** each day, signed by whether each strike is cheap or rich.
- **Rollover** at OpEx: the expired book is gone; the new cycle seeds fresh.

The book answers: *"Where has dealer positioning been building up, and is it
consistent with the current price action?"* — not just *"What's the current
inventory?"*

## 2. The core hypothesis (what we're testing)

> **Dealers systematically flow OI toward strikes where they're cheap relative to
> the reference smile, and away from where they're rich.**

If true: the flows book should **anticipate** price moves (cheap strikes get bought
→ support; rich strikes get sold → resistance) before the snapshot GEX view shows it.
If false: the book is just a fancier snapshot.

This is falsifiable. The simulation work (next doc) is designed to test it.

## 3. The Greek structure the book must respect (Cem consult, 2026-08-25)

Dealers **target delta-neutral, not vega-neutral.** They sleep delta-flat. The
surviving book after OpEx is a **vanna/vega carry**, not a gamma object:

| Greek | Residual after monthly OpEx | Why |
|-------|----------------------------|-----|
| Delta | ~0 | Bought/sold stock/futures. Policy. |
| Gamma | Crushed vs 2 DTE | Dead month + 0DTE gone; next ~25–30 DTE is modest. |
| Charm | Collapsed | Clock reset; next 10× is the next OpEx window. |
| Vega/vanna | **Still there, mostly >30d** | That paper didn't expire. **This is the carry.** |

**Consequences for the flows book:**
- The book is a **vanna/vega object**. Charm is the pin machine (front month only).
  The persistent component is vanna in the back. **Flow sign is driven by ΔIV on
  the residual vanna — not by gamma, not by "VIX is 16 so they buy."**
- **The index book sign does NOT flip at OpEx** just because the monthly printed
  0DTE. Structural sign (short put / long call / long vanna) persists in surviving
  tenors. Regime flips come from crash, skew/backwardation, or structured-product
  demand shift — not from the monthly dying.
- **Not every Friday is OpEx.** Weekly Friday = 0DTE/weekly die + weekend calendar
  (louder, not a reset). Monthly/quarterly Friday = the pin machine. Holiday week
  = more gap variance. VIX expiry is **Wednesday** — stop putting every expiry on
  Friday. The flows book's cycle is the **monthly/quarterly** reset, not weekly 0DTE.
- **Weekend carry is revaluation, not a scheduled hedge.** There is no Saturday
  stock hedge; the risk model doesn't print a Monday-10:00 overlay. Monday 9:30 is
  the *first* rebalance after an unhedgeable weekend.
- **The Monday-open book is:** surviving inventory revalued at Sunday-night S,σ
  + unexpected exercise stock + any ES traded overnight — **not** a magic
  insurance layer. Greeks jump because the underlier and IV jumped, not because
  someone added a weekend hedge.

**Safe assumptions (full stop):** spot-delta target ≈ 0 after the hedge;
gamma/charm of the expired month ≈ 0 (the pin left); structural index sign usually
still there in surviving tenors; daily vanna flow still ΔIV-signed; 0DTE Friday
magnified the day but did not flip the book.

**Not safe:** GEX of the dead month → "dealers disarmed"; vega went down → "they
wanted flat"; Monday VIX up → regime flip (calendar until 9D/1M moves on a
trading-day basis); one book for SPX AM and SPY PM (different clocks).

## 4. Mechanics (agreed)

### 4.1 Seed
- **Content**: TBD (Cem consult). Leading candidate: **SVI-marked OI of the new
  cycle's expiry** at the OpEx day's prior close. This is exactly what
  `replication_reference` seed mode `svi` does in backtest, so live and backtest
  share one definition.
- **Timing**: OpEx day's prior close (first flow day = next session).
- **Data**: `option_bulk_hist_oi_by_day` joined to prior-EOD greeks (never
  `eod_greeks` alone — it has no OI, zeros the book).

### 4.2 Flow sign (cheap/rich)
- **Reference smile**: `svi_rp.calibrate_svi` (5-param SVI, OTM-weighted, ATM
  anchored). Pure function, already in production.
- **Marking**: `mark_chain(chain_iv, oi_by, deadband=0.01)`.
  - market IV **<** ref (cheap) → dealer LONG → flow **positive**
  - market IV **>** ref (rich) → dealer SHORT → flow **negative**
  - |diff| ≤ **deadband** (0.01 = 1 vol point) → **unmarked**, no sign
- **Deadband rationale** (from `vol_surface_reference.py`): IV quote cent-rounding
  noise can flip a strike's sign. Any |dev| ≤ 0.01 is NO confident directional read.
  The deadband is the noise floor — it's what kept the SPY vannaflow accumulation
  signal (-128K SHORT) alive when a tree drop silently destroyed it.

### 4.3 SABR deviation (between strikes)
- The flow sign uses **SABR deviation** per strike, not a flat sign.
  `vol_surface_reference.resolve_vol_surface_sign` computes the deviation between
  the market IV and the fitted SABR curve at each strike.
- This is the "between strikes" part: a strike can be cheap even if the whole
  chain is expensive, if it's cheap *relative to its neighbors*.
- Same deadband logic: 0.0 (in-band) → fallback −1.0 (Layer 1b default).

### 4.4 Daily flow
- Per day, per strike present in BOTH days' EOD OI:
  - `delta_oi = oi_today − oi_prev`
  - `signed_flow = sign × delta_oi`
  - Optional vanna weighting (backtest default ON; live TBD)
- **Sparse-gap rule**: strike missing prev day → skip (don't misattribute).

### 4.5 OpEx rollover
- Expired book → 0.
- New cycle seeds per §4.1.
- **Open**: reset vs carry (see §6).

## 5. Existing assets (reuse, don't rebuild)

| Asset | Role |
|-------|------|
| `svi_rp.calibrate_svi` + `mark_chain` | Cheap/rich marking (pure, production) |
| `replication_reference.py` seed/flow loop | The OpEx-cycle book, backtest form |
| `expiry_book_production.py` | Production book assembly, prior-EOD join |
| `vol_surface_reference.py` | SABR deviation + deadband sign resolution |
| `LIVE_expiry_book_20260821.md` | Flow sourcing rules (scanner_trades) |
| `backtest_accumulation_falsifier.py` | Pre-registered falsifier pattern |

**Key insight**: the flows book is the **production/live variant** of the
`replication_reference` backtest loop. Live and backtest share one definition.

## 6. Open questions

| # | Question | Status |
|---|----------|--------|
| 1 | Seed content: SVI-marked OI vs prior book vs neutral? | Cem consult |
| 2 | Seed timing: OpEx day close vs prior cycle last close? | Cem consult |
| 3 | OpEx rollover: reset vs carry (net only? by moneyness?)? | Open |
| 4 | Unmarked-sign fallback: 0 (no read) vs −1.0 (Layer 1b default)? | Open |
| 5 | Vanna weighting on/off for live flows book? | Open |
| 6 | Panel layout for live view (separate from 4-panel GEX/VEX/CEX)? | Open |
| 7 | Cycle granularity: monthly/quarterly OpEx only, or also weekly resets? | Open (Cem: monthly = the reset; weekly = louder, not a reset) |
| 8 | Book revaluation across weekends/gaps (S,σ jump = greeks jump)? | Open |

## 7. What "working" means (success criteria)

1. **Mechanics correct**: seed + flow + rollover produce the expected book on
   synthetic data (unit tests).
2. **Hypothesis tested**: the book's cheap/rich marks correlate with subsequent
   price moves better than the snapshot GEX view (falsifier test).
3. **Live usable**: the book is visible in the live dealer charts, updates daily,
   and doesn't break the existing 4-panel view.

## 8. Simulation (next)

See `PLAN_dealer_flows_book_simulation_20260825.md` (to be written) for the
simulation approaches and pre-registered falsifier design.
