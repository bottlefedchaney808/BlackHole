# PLAN — Dealer Flows Book (OpEx-to-OpEx) — 2026-08-25

Status: DRAFT, awaiting seed definition (Cem consult in progress).
Supersedes nothing; complements `LIVE_expiry_book_20260821.md` (4-panel charts stay as-is).

## 1. Goal

Replace the static "expiration exposure" live view with a **dealer book that moves
OpEx-to-OpEx**: a seed at cycle start, then daily flow accumulation where each flow
is signed by cheap/rich marking.

Mechanics (already agreed):
- **Seed**: state of the book at OpEx-cycle start. CONTENT TBD — see §3.
- **Flow sign**: SVI reference smile marks each strike cheap/rich.
  - market IV < ref (cheap) → dealer LONG → flow counts **positive**
  - market IV > ref (rich) → dealer SHORT → flow counts **negative**
  - |diff| ≤ deadband → unmarked, no sign (noise floor).
- **Deadband**: 1 vol point (`0.01`) — same floor as `IV_DEADBAND_VOL` /
  `VANNA_FLOW_DIV_DEADBAND` / `BOOK_SIGN_DEADBAND`.

## 2. Existing assets (reuse, don't rebuild)

- `svi_rp.calibrate_svi` + `SviRpReference.mark_chain(chain_iv, oi_by, deadband=0.01)`
  — cheap/rich marks + `.seed()` (long_oi, short_oi, net_seed). Pure functions.
- `replication_reference.py` seed/flow loop (line ~780):
  - seed modes: `svi` (SVI-marked OI) vs `oi_heuristic` (call+/put−);
  - daily `delta_oi` × per-strike sign from `vol_surface_reference.resolve_vol_surface_sign`
    (SABR deviation + deadband; 0.0 → fallback −1.0);
  - vanna weighting on flow (normalized by day mean |vanna|).
  **This is the OpEx-cycle book already, in backtest form.** The flows book is its
  production/live variant.
- `expiry_book_production.py` — production book assembly, prior-EOD join,
  `oi_sum > 0` guard, scanner_trades flow path.
- `LIVE_expiry_book_20260821.md` — flow sourcing rules (scanner_trades per print,
  today-only, no backfill from yesterday's session).

## 3. Seed — OPEN (Cem)

Candidates on the table:

| # | Seed | Pros | Cons |
|---|------|------|------|
| a | SVI-marked OI of the new cycle's expiry at OpEx (prior-EOD greeks joined to `option_bulk_hist_oi_by_day`) | Uses existing machinery; book = "what dealers were holding when the cycle began" | Needs the join to work on the OpEx day itself |
| b | Terminal book of the prior cycle carried over | True continuity across OpEx | Prior book is per-expiry; carry needs a rule (net only? by moneyness bucket?) |
| c | Neutral (0), flows are the whole signal | Simplest; pure flow read | Loses inventory context; early-cycle book is thin |

**Recommendation to test with Cem: (a)** — it is exactly what
`replication_reference` seed mode `svi` does in backtest, so live and backtest
share one definition.

Questions for Cem:
1. Content: (a), (b), or (c)?
2. Timing: seed at the OpEx **day's close** (first flow day = next session) or
   at prior cycle's last close?
3. If (b): what carries — net only, or per-strike mapped by moneyness?

## 4. Flow accumulation (per OpEx cycle, per expiry)

- Window: from seed date to expiry.
- Per day, per strike present in BOTH days' EOD OI:
  - `delta_oi = oi_today − oi_prev` (sparse-gap rule: skip strikes missing prev day)
  - sign from SVI cheap/rich on that day's chain (deadband 0.01; unmarked → 0,
    or fallback −1.0 — decide: the backtest falls back to −1.0, live book-sign
    treats 0 as "no confident read"; pick one, write it down)
  - optional vanna weighting (backtest default ON; confirm for live)
- **OpEx rollover**: expired book → 0; new cycle seeds per §3. Decision needed:
  same as (b) above — reset or carry.
- Live intraday: today's scanner_trades flow (per print, size vs mid) applied on
  top of the accumulated book, same as the current 4-panel flow layer.

## 5. Units and conventions

- OI in contracts; book values in the same dollar-gamma-per-1% style as the
  existing book if we add a gamma view, otherwise raw signed OI lots.
- No extra dealer −1 on charm; charm stays its own panel.
- `eod_greeks` has no OI — never seed/flow from it alone (TSLA blank-panel lesson).

## 6. Deliverables (sketch)

- `Vol_Suite/dealer_flows_book.py` — seed + accumulate + rollover, pure functions,
  mirroring `replication_reference` conventions so backtest/live agree.
- Live wiring: one more book in `expiry_book_production` output + a panel in the
  live dealer charts (separate from the 4-panel GEX/VEX/CEX).
- Tests: seed modes, deadband no-flip, sparse-gap skip, OpEx rollover reset.

## 7. Decisions waiting

- [ ] Seed content/timing (Cem)
- [ ] OpEx rollover: reset vs carry
- [ ] Unmarked-sign fallback: 0 vs −1.0 in live
- [ ] Vanna weighting on/off for live flows book
- [ ] Panel layout for the live view
