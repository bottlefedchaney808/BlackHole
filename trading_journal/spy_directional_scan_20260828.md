# SPY Directional Daily Play — Full-Suite Scan — 2026-08-28

**As-of:** 2026-08-28 ~09:30 CDT. **Trigger:** user asked to run the full suite on SPY for a directional
daily (0DTE) play, superseding this morning's "stand pat" trading-mode conclusion for SPY specifically.
Research only until explicit go-ahead — no order placed yet.

## Runs performed

1. `orchestrator.py --unified --ticker SPY --expiry 2026-08-28 --index SPY` — run id
   `20260828T142311Z288545` (`orchestrator_output/20260828T142311Z288545/`).
2. `Direction.py SPY` (whale flow + Elliott wave + Bollinger + multi-timeframe trend + liquidity/dealer
   walls, unified signal).

## Orchestrator results

- **Vol_Suite: `status: error`.** Variance-swap replication (index and focus) both hit
  `ZeroDivisionError` and dealer positioning hit `ExpiryBookUnavailable: positive DTE is required` —
  **the live expiry_book dealer-positioning engine and variance-swap replication cannot run on a 0DTE
  expiry** (needs DTE > 0). This is a real tool limitation, not a data problem with SPY. Gamma/vanna/
  charm walls for today therefore come from `Direction.py`'s liquidity/OI-snapshot module instead (see
  below), not the full Vol_Suite dealer model.
- **Options_Suite: `status: ok`.** LeisenReimer ATM-ish call: sigma (solved IV) 9.19%, price $1.54,
  delta 0.508, gamma 0.081, theta -0.81/day. (Default no-strike call — informational, not the trade
  candidate below.)
- **VaR_Tools: `status: ok`** but not ticker-specific — generic `corr_sim` with identity correlation
  and a flat 0.25 fallback vol (no real weights/positions supplied for this ad hoc run). Not useful for
  sizing this specific trade; skip.

## Direction.py signal (whale flow, Elliott wave, Bollinger, multi-timeframe trend, liquidity)

**SPY: HIGH conviction, score 4/5, bullish. Price $771.17-771.69 (updated through the run).**

| Module | Signal | Detail |
|---|---|---|
| Whale flow | ON (bullish) | 61 whale calls vs 37 whale puts; call premium $146.7M vs put premium $91.3M. Note: sample spans many expirations including far-dated LEAPs (e.g. 2028-12-15 strikes) — this is a medium/long-term positioning tilt, not purely a same-day signal. |
| Elliott wave | ON | wave_number 5, wave_type impulse_wave_3 — reads as a bullish continuation leg. |
| Bollinger squeeze | off | Not compressed — already trending, not coiled for a breakout. `regime: bullish`. |
| Multi-timeframe trend | ON | Daily/weekly/monthly ADX aligned, `aligned: true`. |
| Liquidity/dealer (OI snapshot, today's expiry) | ON | max_pain **$767**, call_wall **$770**, put_wall **$765**, PCR (put/call OI) **1.52**. Spot ($771+) is already trading *above* the call wall and above max pain — a location that often either continues on gamma-chase momentum (dealers short calls near 770 must buy more stock as price rises) or gets pulled back toward max pain into the close. Cuts both ways; noted as the key tension in this setup. |

**Score is 4/5, not 5/5** — squeeze is the missing module (market's already moved, not compressed for a
fresh breakout).

## Live chain check (0DTE calls, spot ~$771.4-771.7)

| Strike | Mark | Delta | IV | OI | Vol | Breakeven |
|---|---:|---:|---:|---:|---:|---:|
| $773 | $0.515 | 0.268 | 16.07% | 4,462 | 121,489 | $773.52 |
| $774 | $0.275-0.32 | 0.17-0.19 | 15.5-15.7% | 7,152 | 88,632 | $774.28-774.32 |
| $775 | $0.145 | 0.102 | 15.73% | **13,968 (highest OI of the three)** | 93,610 | $775.15 |

IV is unremarkable (~15.5-16%) for SPY 0DTE — no rich/cheap vol edge here; this is a pure directional
momentum/positioning read, not a vol-dislocation trade (a deliberate departure from this morning's
vol-specialist framing, per the user's explicit ask for a directional play).

## The dominant risk: Jackson Hole today

Fed Chair Warsh's keynote is today, ~10:00 MDT / ~12:00 CT / ~13:00 ET (see
`market_rumor_watchlist_20260828.md`) — roughly 2.5-3 hours from this scan. This is exactly the kind of
index-level macro catalyst that can overwhelm a same-day technical/positioning read in either direction,
and 0DTE theta means any premium paid decays fast if the move doesn't happen before the close. Account A
already holds a SPY $23/26 call vert expiring today, held through Jackson Hole on purpose for event vol
— this new position would be a second, unrelated SPY exposure in the *other* (agentic) account.

## Candidate (not yet placed)

Reviewed (dry-run only, `review_option_order`) — **buy 2x SPY 2026-08-28 $774 call**, limit, ask moved
$0.28 -> $0.32 between quote pulls (SPY ticking up in real time). At $0.32: cost ~$64 + $0.08 fees
(~13.6% of the account's $472.23 cash), max loss capped at that premium, breakeven $774.32 (+0.4% from
spot as of the review). No broker alerts on the review. **Awaiting explicit go-ahead before any
`place_option_order` call**, per standing rule — this is a live account and this specific setup carries
real event risk into Warsh's speech.
