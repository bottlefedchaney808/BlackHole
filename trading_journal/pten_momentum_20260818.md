# PTEN Momentum Pyramid — opened 2026-08-18

**Account:** 751521659 ("Agentic"). **Style:** discretionary equity momentum, separate from the vol-edge
options plan (NKE spread parked, SPY 800C kept — see `desk_note_20260818.md` /
`morning_plan_20260819.md`). Purpose of this file: document every read and move so the pyramid strategy
is trainable — score calls against outcomes, don't just remember them.

## Thesis

Direction 5-signal battery (`trading_journal/powerhour_prep_20260817.md`) flagged PTEN at **4/5 HIGH
conviction** on 2026-08-17 — whale, wave3, squeeze, and trend all firing (only liquidity flagged False).
That was the single strongest read on the board that day (CRGY 3/5 was next-best; SPY/QQQ/UUUU mixed).
Premarket 2026-08-18 confirmed continuation: last $12.215 (8/17 close) → $12.36–12.67 premarket,
tightening spread (12.30/12.38 by 9:12 AM ET) — real follow-through, not a gap that's about to fade.

**One-sentence edge:** a 4-of-5 momentum-signal name showing live premarket continuation is more likely
to keep trending than mean-revert over the next several sessions — this is a trend-following equity
play, not a vol-mispricing trade (that's the NKE/SPY book, kept separate).

## Entry — tranche 1

- **12 shares, limit $12.70, GFD**, order id `6a845acb-153b-440c-bfa2-ed556d766904`, placed 2026-08-18
  ~9:14 AM ET (premarket, queued for the 9:30 open).
- **FILLED at the open: 12 sh @ $12.47 avg, cost $149.64** (better than the $12.70 limit — confirmed via
  `get_equity_orders` at 9:30:01 AM ET).
- SPY 800C also filled at the open: 1 contract @ **$1.45** (premium $145.00, vs. $1.98 limit) — order
  `6a845ad5-ffc2-4fb2-ac0c-3d7713b8195b`.
- Combined actual spend: **$294.64** (vs. ~$350 planned) — cheaper opening fills than the premarket
  quotes implied, leaving a larger reserve than planned.
- Reserve held back for pyramid adds: **~$110.66** of the original $405.30 buying power (before the
  UUUU stop-triggered scale-in tranche below adds more on top if/when it fires).

## Pyramid plan — signal-gated, not price-gated alone

**Add tranche 2** (size: remaining reserve, target ~$50-55) if, on any subsequent session's Direction
read:
- Score stays ≥ 4/5 (whale + wave3 + squeeze + trend still all on), AND
- PTEN closes above the prior day's high (genuine breakout continuation, not just "still positive"), AND
- Spread stays tradable (< $0.15 wide at the time of the read) — don't chase into an illiquid print.

**Trim / exit signal** (sell the position, or the tranche most recently added) if:
- Any two of {whale, wave3, squeeze, trend} flip off in the same read — momentum thesis broken, not just
  noisy pullback, OR
- PTEN closes below the tranche-1 entry-day premarket low (~$12.29) on above-average volume — failed
  breakout, OR
- 3 consecutive sessions with no new high and a fading score (5/5 signals never confirmed again).

**No stop-loss order placed on PTEN itself** — this is a discretionary signal-gated exit, re-evaluated
each session against a fresh Direction read, not a fixed price stop. Re-state this explicitly each time
the plan is revisited so it doesn't silently become "no exit plan at all."

## Funding tranche — UUUU stop-triggered scale-in (added 2026-08-18)

Separate from the signal-gated pyramid above: **pre-authorized, one-shot** rule set up 2026-08-18 ~9:21
AM ET. UUUU (3 sh, avg cost $14.17, unrelated legacy position) got a fresh GTC stop-market sell at
**$14.00**, order id `6a845c49-f8d4-4dbf-9934-8f0cc1414e7e` (no stop had been active — prior GTC stops
on UUUU/TGB/KOS from 08-14/08-17 all show cancelled, likely a stale daily-reset pattern from before this
file existed).

**Rule:** if/when that stop fills, the proceeds go straight into a PTEN buy (marketable limit at the
then-current ask) — no separate confirmation, this was explicitly pre-authorized as a bounded, specific
contingent trade, unlike the signal-gated pyramid adds above which still require go-ahead. Fires **once**;
the recurring watch (cron job `96146eb1`, every 15 min during regular hours) checks order state and
executes this, then logs the fill price, proceeds, and resulting PTEN share count as a distinct "stop-
triggered scale-in" row below — track this separately from the signal-gated tranches so the two capital
sources (discretionary pyramid vs. stopped-out reallocation) don't get conflated when scoring the
strategy later.

## Read log

| Date | Spot | Direction score | whale/wave3/squeeze/trend/liq | Action | Notes |
|---|---|---|---|---|---|
| 2026-08-18 09:20 ET | PTEN $12.10 limit | n/a (stop scale-in) | — | **Stop-triggered scale-in**: UUUU 3sh@$14.00 -> 3 more PTEN @ $12.0687 avg | automated via Hermes `rh_monitor.py` cron, order `6a846a16-d8de-4f26-b518-18c2fd2e7331`, filled 14:20:06 UTC |
| 2026-08-18 10:20 ET | PTEN $12.10 limit | n/a (**DUPLICATE — race condition**) | — | **Unintended second scale-in**: same rule fired again from the session cron (job `96146eb1`) 9 seconds later, before its pre-check saw Hermes's order — bought another 3 PTEN @ $12.0687 avg | order `6a846a1f-2cef-44d3-a7f1-9692c24bbd59`, filled 14:20:16 UTC. **Net effect: 6 sh bought instead of 3, $72.41 spent against $42.00 UUUU proceeds ($30.41 pulled from reserve, not a risk-limit breach but not the intended sizing).** Root cause: two independent automations (Hermes cron + Claude session cron) both implementing the same pre-authorized one-shot rule with no shared lock — only Hermes's script checks a marker file (`.uuuu_scale_in_done`); the session-cron prompt only checked live order history, which raced. Flagged to Jason 2026-08-18 10:20 ET; decision pending on whether to trim the extra 3 shares. **Going forward: the session cron must check `.uuuu_scale_in_done` before considering this rule, not just order history.** |
| 2026-08-18 10:52 ET | $12.028 (bid 12.02/ask 12.04), new intraday low | not re-run live (price-only check) | — | **Held at 18 sh — discretionary call, not a rule trim** | Jason: "follow the play, cut only if you think it's coming down." Read: lower-low pattern (bounced to $12.155 at 10:04, now below the earlier $12.075 low), red on the day. Caution flag, but NOT cutting — no volume-vs-avg data, no live signal re-run, and market close is hours away; the documented trim rule needs a session close below $12.29 or 2+ signals flipping, not intraday softness. Will flag decisively if it keeps making lower lows into the afternoon. |
| 2026-08-18 10:53 ET | $12.025 (bid 12.02/ask 12.03) | not re-run live (price-only check) | — | none | stabilizing, essentially flat vs. the 10:52 read — no new lower low yet. SPY 800C mark down further to $1.30 (theta/IV drift continuing). Held at 18 sh, no trim trigger. |
| 2026-08-18 11:04 ET | $12.045 (bid 12.05/ask 12.06) | not re-run live (price-only check) | — | none | flat vs. prior tick, holding the $12.02-12.05 range. SPY 800C mark unchanged at $1.30. |
| 2026-08-18 11:19 ET | $12.050 (bid 12.04/ask 12.06) | not re-run live (price-only check) | — | none | still flat, same range. SPY 800C mark down to $1.23 (continued decay). |
| 2026-08-18 11:34 ET | $12.100 (bid 12.10/ask 12.11) | not re-run live (price-only check) | — | none | small tick up, still in-range. SPY 800C mark $1.28, minor bounce. |
| 2026-08-18 11:49 ET | $12.090 (bid 12.08/ask 12.10) | not re-run live (price-only check) | — | none | still range-bound $12.02-12.11 since ~10:52. SPY 800C mark $1.31. |
| 2026-08-18 12:04 ET | $12.135 (bid 12.13/ask 12.14) | not re-run live (price-only check) | — | none | drifted to the upper end of the $12.02-12.14 range. SPY 800C mark $1.30. |
| 2026-08-18 12:19 ET | $12.105 (bid 12.10/ask 12.11) | not re-run live (price-only check) | — | none | still range-bound. SPY 800C mark down to $1.245. |
| 2026-08-18 12:34 ET | $12.085 (bid 12.08/ask 12.09) | not re-run live (price-only check) | — | none | unchanged, mid-range. SPY 800C mark $1.25. Midday lull. |
| 2026-08-18 12:49 ET | $12.080 (bid 12.08/ask 12.09) | not re-run live (price-only check) | — | none | unchanged, still mid-range. SPY 800C mark $1.305. |
| 2026-08-18 13:04 ET | $12.095 (bid 12.09/ask 12.10) | not re-run live (price-only check) | — | none | unchanged, mid-range. SPY 800C mark $1.285. |
| 2026-08-18 13:19 ET | $12.070 (bid 12.07/ask 12.08) | not re-run live (price-only check) | — | none | unchanged, mid-range. SPY 800C mark $1.245. |
| 2026-08-18 13:34 ET | $12.070 (bid 12.07/ask 12.08) | not re-run live (price-only check) | — | none | flat vs prior tick. SPY 800C mark $1.24. |
| 2026-08-18 13:49 ET | $12.060 (bid 12.05/ask 12.06) | not re-run live (price-only check) | — | none | slight drift lower, below the $12.29 trim reference intraday (as it has been most of the session) — no trim trigger since no session close yet. SPY 800C mark $1.205. |
| 2026-08-18 14:04 ET | $12.044 (bid 12.04/ask 12.05) | not re-run live (price-only check) | — | none | continued slow drift lower, still range-bound, no trim trigger. SPY 800C mark $1.165. |
| 2026-08-18 14:19 ET | $12.060 (bid 12.05/ask 12.06) | not re-run live (price-only check) | — | none | steady, no new low. SPY 800C mark ticked up to $1.24. |
| 2026-08-18 14:34 ET | $12.035 (bid 12.03/ask 12.04) | not re-run live (price-only check) | — | none | still range-bound, no new low. SPY 800C mark $1.225. |
| 2026-08-18 14:49 ET | $12.045 (bid 12.04/ask 12.05) | not re-run live (price-only check) | — | none | unchanged. SPY 800C mark $1.195. |
| 2026-08-18 15:04 ET | $12.025 (bid 12.02/ask 12.03) | not re-run live (price-only check) | — | none | marginal new session low, ~1hr to close. Price has sat below the $12.29 trim reference essentially all day but no volume-vs-average data has been tracked to confirm the trim rule's "above-average volume" condition if it closes here. Flagging as approaching-close watch item, not yet a trigger. SPY 800C mark $1.155. |
| 2026-08-18 15:19 ET | $12.030 (bid 12.02/ask 12.03) | not re-run live (price-only check) | — | none | essentially unchanged, ~40min to close. Still watching the close-below-$12.29 question. SPY 800C mark $1.145. |
| 2026-08-18 15:34 ET | $12.055 (bid 12.05/ask 12.06) | not re-run live (price-only check) | — | none | small tick up, ~25min to close. SPY 800C mark $1.155. |
| 2026-08-18 15:49 ET | $12.185 (bid 12.18/ask 12.19) | not re-run live (price-only check) | — | none | real move up, closing in on yesterday's $12.215 close — pulling away from the $12.29 trim concern, ~11min to close. SPY 800C mark $1.16. |
| 2026-08-17 (powerhour prep) | $11.885 | 4/5 HIGH | T/T/T/T/F | none yet | source read that flagged the name |
| 2026-08-18 (premarket) | $12.36–12.67 | not re-run live | — | **Tranche 1 opened**, 12 sh @ $12.70 limit | premarket continuation confirmed the 08-17 read; full live Direction re-run not done pre-open, flagged as a gap — do at next session |
| 2026-08-18 09:30 ET (open) | fill | — | — | Confirmed fills: PTEN 12 sh @ $12.47 avg (better than $12.70 limit); SPY 800C @ $1.45 (better than $1.98 limit) | actual spend $294.64 vs ~$350 planned |
| 2026-08-18 09:34 ET | $12.325 (bid 12.32/ask 12.34) | not re-run live (price-only check) | — | none — no pyramid-add or trim trigger | flat/+0.9% vs prior close, essentially unchanged from open; SPY 800C mark down to $1.36 (theta/IV drift, no action rule attached); UUUU stop still unfilled at $14.00 (spot ~$14.10+) |
| 2026-08-18 09:49 ET | $12.075 (bid 12.07/ask 12.08) | not re-run live (price-only check) | — | none — no hard trigger fired | intraday down -1.1% vs prior close, now below the $12.29 tranche-1 premarket-low trim reference — **watch, not act**: trim rule requires a session *close* below that level on above-avg volume, not an intraday dip. SPY 800C mark down further to $1.385. UUUU stop still unfilled. |
| 2026-08-18 10:04 ET | $12.155 (bid 12.15/ask 12.16) | not re-run live (price-only check) | — | none | recovering off the intraday low ($12.075→$12.155), still slightly below prior close. SPY 800C mark $1.415-1.42, ticked up. UUUU stop still unfilled. |

**Open action item:** re-run the actual Direction 5-signal battery (not just premarket price action) at
the next session open to get a real 08-18/08-19 score before deciding on tranche 2 — this log currently
has one live signal read (08-17) and one price-only confirmation (08-18 premarket), which is thinner
evidence than the pyramid-add rule above technically requires. Don't let "price went up" alone stand in
for "signals confirmed."
