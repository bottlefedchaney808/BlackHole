# Direction Tool — Position Review & Cash Deployment Plan (2026-08-19)

## Method

Ran `Direction.signal_generator.generate()` (whale flow + Elliott wave3 + Bollinger squeeze +
multi-timeframe trend + liquidity/max-pain) as the primary decision gate today, per instruction to
trust the tool over an ad-hoc read. Whale flow is a hard AND gate — no whale print, conviction is
always NONE regardless of the other four signals.

## Existing positions (Agentic account, ••••1659)

| Ticker | Price | Signals firing | Score | Conviction | Read |
|---|---|---|---|---|---|
| TGB | $8.53 | wave3 | 1/5 | NONE | Hold. No whale gate. Above $8.20 GTC stop (cost $8.86). |
| KOS | $2.85 | whale, squeeze, trend | 3/5 | **MEDIUM** | Live add-candidate — whale gate is firing. Already trimmed 1 sh @ $2.84 today; stop $2.50 (cost $2.57). |
| PTEN | $12.08 | wave3, squeeze | 2/5 | NONE | Hold. No whale gate. Above $11.40 GTC stop (cost $12.34). |

## Cash-deployment scan

Scanned the "Sell Vol Plays" watchlist (8 names) + the higher-beta names on "First list" (7 names) —
15 tickers total. Three cleared the whale gate:

| Ticker | Price | Signals firing | Score | Conviction |
|---|---|---|---|---|
| RIVN | $15.27 | whale, wave3, squeeze, liquidity | 4/5 | **HIGH** |
| LCID | $5.74 | whale, wave3, squeeze | 3/5 | **HIGH** |
| F | $14.14 | whale, wave3, liquidity | 3/5 | MEDIUM |

Everything else scanned (HMC, DK, SHAK, PRGO, CPRI, IRM, CVS, SEDG, TSLA, NVDA, AMD, AMC) scored 1-3
but never got whale confirmation — NONE, no action.

## Proposed cash deployment (plan only — not executed, needs go-ahead per trade)

Real spendable buying power: **$221.98** (the $350 pending deposit is not yet settled/spendable).

- **RIVN**: 6 sh @ ~$15.27 ≈ $91.62. Stop ~$14.12 (-7.5%, matches this account's TGB/PTEN stop convention).
- **LCID**: 12 sh @ ~$5.74 ≈ $68.88. Stop ~$5.31 (-7.5%).
- Leaves ~$61 buffer, plus the $350 pending deposit once it clears.
- **F** (MEDIUM) and **adding to KOS** (MEDIUM) — skipped for now. Buying power reserved for the two
  HIGH-conviction names; MEDIUM setups get revisited once the pending deposit settles or one of the
  HIGH names resolves.

Nothing has been sent to `review_equity_order`/`place_equity_order` yet — this is a plan for Jason to
approve per-symbol in chat before any live order goes out.

## Insights / differences to work on later

1. **This account doesn't run the trading-mode vol-mispricing playbook at all.** The trading-mode
   skill frames edge as IV-vs-realized dislocation (options structures, dealer gamma/vanna). This
   account holds zero options and trades pure equity direction off the Direction tool's 5-signal gate
   instead — a genuinely different edge thesis than what trading-mode's default workflow assumes.
   Worth deciding explicitly whether this account should ever route through trading-mode's
   vol-mispricing side, or whether it's Direction-tool-only by design going forward.
2. **The liquidity signal is options-chain-derived** (`Direction/liquidity_map.py` — max pain, OI
   strike walls, PCR, GEX) even though this account trades equity only and never executes options.
   Options positioning is informing the equity read without any options actually being traded against
   it — noted, not necessarily a problem, but worth being deliberate about.
3. **Prior fills on this account (TGB/KOS/PTEN entries, and the closed SOUN/UUUU/INR/NEXA
   round-trips) all predate this being used as a formal decision gate.** Unknown whether they'd have
   cleared the whale gate at entry. Worth a retroactive check against `Direction.whale_scanner`
   history as a sanity check on whether the gate is actually calibrated to how this account has been
   trading, versus being a new, unvalidated overlay.
4. **Stop-loss convention is inconsistent across current positions**: TGB/PTEN sit at roughly -7.5%
   from cost, KOS sits at only -2.7% (tightened after the position moved favorably). Worth
   standardizing, especially now that KOS is showing a live MEDIUM add signal — adding to a name with
   an unusually tight existing stop creates an awkward blended risk profile.
5. **The conviction gate is a hard binary on whale flow.** Several names died on score 2-3 without a
   whale print (DK, PRGO, CPRI, IRM, CVS, TSLA, NVDA, AMD, AMC) — decent secondary confluence but no
   trade per the tool's own rule. No verdict yet on whether a "score >= 3, no whale" bucket deserves a
   softer MEDIUM-lite treatment, or whether the hard gate is correctly strict. `Direction/README.md`
   already flags that these thresholds haven't survived multiple-comparisons correction — treat any
   loosening of the gate as a deliberate, separate decision, not a drift.

## Execution (2026-08-19, 1:42-2:15 PM ET)

Jason approved both names in chat ("do it pull the trigger" / "spend it ... go on auto"). Placed:

- **LCID**: buy 12 sh, limit $5.75 → **filled** @ avg $5.7499 ($68.99). GTC stop placed @ $5.31
  (order `6a85eb33`), confirmed.
- **RIVN**: buy 6 sh, limit $15.30 → **filled** @ avg $15.30 ($91.80, order `6a85eb09`, executed
  17:43:30Z). GTC stop placed @ $14.12 (order `6a85f2b3`, submitted 18:15:15Z). By the time the stop
  went in, RIVN had already run to $15.47 ask / $15.46 bid — up ~1.1% off the fill within ~30 min,
  first tick of confirmation on the HIGH-conviction read.
- Both positions now stop-protected. Cash remaining: ~$61 buffer, as planned. F and the KOS add still
  held back (MEDIUM, lower priority — unchanged from the plan).

## TGB check-in (2026-08-19, ~2:10 PM ET, via chart_app)

Pushed TGB (8 sh @ $8.86 avg) through the chart at `:8791`, refreshed 5m bars, re-ran the signal:

- Price **$8.43** (down from $8.53 earlier the same session) — unrealized roughly -4.9% vs. cost,
  still comfortably above the $8.20 GTC stop.
- Signals: only `wave3` firing. Score 1/5, conviction **NONE** — no whale, no trend, no squeeze, no
  liquidity confirmation. Nothing actionable per the tool; hold, let the stop do its job.
- Tape context: TGB put in a whale-marked spike top near $8.80 on the morning of 8/19, then rolled
  over and has been grinding down since — currently below both EMA20 and EMA50, fading toward the
  lower Bollinger band. This is the same rollover pattern noted in the position review earlier today,
  just further along now.
- No order actions taken — pure monitoring check, consistent with "trust the tool" for entries but the
  tool gives no exit signal here either; the stop remains the only defined risk control on this name.

## Next

- No further deployment today — F and the KOS add stay on watch per the plan above.
- Keep periodic TGB/PTEN/KOS/RIVN/LCID check-ins through the chart_app `/api/rh` push pattern; nothing
  currently requires action beyond the stops already in place.
