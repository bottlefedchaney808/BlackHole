# General knowledge — 0DTE credit book (Anderson × Lakha, Cem filter)

**Registered:** 2026-08-20 (Jason). Trade-journal knowledge, not a live-book patch, not Honcho canon, not auto-promoted.
**Sources:** Options Insight clip (Imran Lakha × Mark Anderson / @multistratmark, ~62 min, 2026-08-20) via research cliff notes; Cem Karsan consult on what is durable vs one-trader color.
**Boundary:** 0DTE magnifies intraday hedge. It does **not** flip the structural short-put / long-call index regime. Do not mix this P&L recipe into `gamma_book`, expiry-book, or GEX walls.

---

## Durable — loose rules (also in quant + trader SOUL.md)

1. **0DTE P&L is credit + path + slippage.** Not GEX-level. Anderson’s ~55% slippage / ~25% wings / 10–15% fees is color on the mix; the structure is: microstructure eats the edge.

2. **Path can nuke a short-gamma book while realized looks fine.** 2-sigma up, stop, 2-sigma down. Close-to-close RV is the wrong clock for 0DTE. That is why daily GEX→next-day-return tests are the wrong question.

3. **GEX-as-traded / magnets / reverse-engineered-from-vol is unbacked folklore until a pre-registered test.** Anderson got a null. Cem: OI plus a story (2021). Magnets that reverse and tag stops are the retail wall. Jason’s live GEX is already labeled imported. Do not drag walls back in as stops.

4. **Sub-5-delta 0DTE wings are a buying-power / lottery product** MMs invent because everyone has to cap BP. That is **not** 30d skew-as-carry. Do not use far 0DTE wings as the structural book-sign.

5. **VRP is unpaid if the expected move is understated and you sold the strangle.** A check. Not a signal.

6. **Read order:** spot, then live chain, then OI on a round strike. Desk hygiene. Not a sign convention.

7. **A giant 0DTE credit book looks long-vol / long-notional / short-gamma** because the body is short premium and the cheap wings are the BP hedge. Description of **that operator’s mix**. Not the index dealer inventory.

---

## Color — Anderson-specific (journal only, not SOUL)

- 0DTE credit is the income; 1D/3D tenors, calendars, 4×3 call ratios as extra streams.
- Reusable longer-dated wings as fee-cheap alpha (inventory — don’t pay the spread twice).
- ≥~5-delta fairly priced (~2–3% EV). Sub-5-delta / $0.10 wings “mispriced” because MMs invent a vol.
- Wing mix: highest-EV / reusable 5c / recycle-on-stop.
- CTA overlay: sell the other side, ignore vol.
- Futures net-delta as *the* breakout proxy (his test beat GEX magnets — interesting falsifier, not a rule).
- 55 / 25 / 10–15 P&L split.
- Lakha: fade crushed risk premium into long vol. Anderson: stay short premium, sized down, wings as hedge. **Timing preference**, not structure. Index structural carry stays short-ish skew/VRP until an **event** flips it. Neither is Jason’s live SVI book.

---

## Research cliff notes (audio, not chapter titles)

**Book at the tail:** net credit seller (e.g. ~$1M credit vs ~$50k long vol). Contract count can flip (2,000 short vs 3,500 long) → long SPX notional via wings, short theta. Limit-down tail: long vega / short gamma. Broker greeks on a normal day don’t show it.

**Why 80-vol wings:** capital, not a vol-edge buy. Naked 0DTE ~$75k margin/contract; ~$5 wing → ~$10k. Far 300pt OTM $0.05 put can print ~80 vol vs ATM ~15; after costs still ~50c on the dollar.

**Three vol factors:** (1) VRP — IV vs realized/expected move. (2) Direction/trend — 0DTE CTA. (3) Path — 2σ up stop then 2σ down stop.

**FOMC:** tail-heavy, EV among the best. Does not switch off. Cuts size into 2pm (entries every 90 min vs 30 → ~1/3 pre-event). Full size after. Turn-of-month: skip last 90 min (MOC). No market orders.

**GEX:** unbacked as a traded signal until a systematic backtest. Hierarchy: spot > live chain > OI on a round strike.

**One-liner (color):** giant 0DTE book is a credit machine whose tail greeks look like a long-vol, long-notional, short-gamma pile of cheap wings. Most of the money is not the view.
