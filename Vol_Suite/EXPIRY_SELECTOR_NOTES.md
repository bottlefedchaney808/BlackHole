# Expiry selector — open issue: far-off "weekly" candidate on long-dated targets

**Status:** open / not yet fixed. Flagged 2026-07-21.

## Symptom

When the target time-to-expiry is far out, `choose_expiry` presents a sensible
OPEX near the target *and* a weekly that is nowhere near it. Example from a live
QQQ/NVDA run, target `T=0.45` (target date ≈ 2027-01-01):

```
Target date for T=0.4500yr: 2027-01-01
  1) 2026-12-18 (Fri, MONTHLY/OPEX)  DTE=150  T=0.4110yr   <- 14 days from target, good
  2) 2026-08-28 (Fri, WEEKLY)        DTE=38   T=0.1041yr   <- 125 days from target, nonsense
```

Option 2 is not a real "closest weekly to the target" — it's just the closest
weekly that *happens to be listed*, and it doesn't bracket the target at all.
Same thing showed up earlier on a `T=0.33` SPY run (offered an Oct 30 weekly
against a Nov 20 OPEX).

## Root cause

`expiry_selector.find_candidates()` returns the single closest OPEX **and** the
single closest weekly to the target date. For long-dated targets the underlying
usually lists **no weeklies that far out** — weeklies only exist in the near
term — so "closest listed weekly" collapses to a near-dated contract months away
from the target. The monthly is fine; the weekly leg is the silly one.

## Fix directions (pick one)

1. **Proximity guard (simplest):** only surface the weekly if it's actually near
   the target — e.g. drop it when `weekly.diff_days > monthly.diff_days * 1.5`
   (or an absolute cap like `> 21` days from the target). If it fails the guard,
   present just the OPEX, or fall back to the two closest expiries of *any* kind.
2. **Window filter:** keep only candidates within ±N days of the target
   (N scaling with the tenor, e.g. `max(10, 0.15 * target_dte)`), then within
   that window take the closest OPEX and closest weekly. If only one kind
   qualifies, present the two closest overall instead.
3. **Bracket the target:** prefer one candidate just before and one just after
   the target date (regardless of weekly/monthly), and only label them
   OPEX/weekly as secondary info.

Option 1 is the smallest change and directly kills the observed bug. Whichever
we pick, keep the invariant: **every option presented should be close to the
target** — no more 125-day-off "weeklies."

## Where

`expiry_selector.py` → `find_candidates()` (the `_add(weeklies)` pick) and the
list `choose_expiry()` prints. `nearest_expiry()` already returns the closest
overall, so the non-interactive path is unaffected — this is purely about which
two dates the interactive picker offers.
