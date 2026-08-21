# MORNING PLAN — 2026-08-20 (pre-open)

**Source:** `desk_note_20260819.md` (8/19 post-close pack read — ST/X/YT sentiment across 12 names,
three clusters). Cross-checked live against `get_equity_quotes`, `get_portfolio`,
`get_equity_positions`/`get_option_positions`, `get_equity_orders`/`get_option_orders`
(751521659, "Agentic"), and `get_earnings_calendar` as of ~06:05 UTC / ~1:05 AM CT 8/20.

**Discipline carried from the note:** SIP closes are VERIFIED. Catalyst *details* (Moderna/Merck
Phase 3 neoantigen numbers, TEM's AI-oncology tag, SLS's REGAL binary, MRVL's 206.58 warrant strike,
BMNR's mNAV framing) are **UNVERIFIED** — X/CNBC color, not filings. Nothing here promotes an
unverified claim to a trade input.

---

## 0. Status check — reconcile the 8/19 plan first

The 8/19 morning plan (NKE spread + SPY 800C) did **not** carry both legs into today:

- **SPY 800C (Sep 18) — filled and closed.** Bought 1 contract 8/18 @ $1.45 (limit, matched the plan).
  A GTC stop-sell at $1.40 triggered 8/19 and filled @ $1.39 — small loss (~$6 + the $0.06/share stop
  slippage). **This position is closed, nothing to manage today.**
- **NKE 40/45C spread (Nov 20) — never opened.** No NKE orders exist in account history at all. The
  thesis is stale: NKE printed $41.05 at 8/19 SIP close, already above both the $38 no-go floor *and*
  the original entry zone the spread was priced against. **Don't resurrect this without re-pricing
  the spread at current spot** — the setup that existed at $39 isn't the same setup at $41.

**Unrelated activity since 8/18 (not part of either plan, flagging for awareness only):** the account
opened and is now managing stop-protected positions in **TGB (8sh, avg $8.86), KOS (2sh, avg $2.57,
down from 3 after a partial sell), PTEN (18sh, avg $12.34), RIVN (6sh, avg $15.30), LCID (12sh, avg
$5.75)**. All five carry **GTC stop-sell orders still in `confirmed` (queued, untriggered) state** —
TGB stop $8.20, PTEN stop $11.40, RIVN stop $14.12, LCID stop $5.31, plus a KOS stop $2.50 already
absorbing one partial fill. **Leave these alone today unless the thesis behind them changed** — they
aren't part of this note's cluster and re-deriving them isn't in scope here.

## 1. Buying power reality — this constrains everything below

`get_portfolio`: **buying power $61.18**, cash $61.18, equity value $455.64, total $516.82. The $350
"pending deposit" shown on the account is **not spendable yet** (`get_accounts` confirms
`unsettled_funds: 0.0000` — it isn't sitting there as unsettled proceeds either, it just hasn't
settled in). **Realistic new-options budget today is ~$61, before any of the five GTC stops above
trigger and free up more.** That rules out sizing a fresh structure on any of the desk-note names
this morning — a single WMT/MRVL/COIN contract runs well past $61. Today's plan is **watch +
journal + conditional**, not **place**, unless a stop triggers or the deposit clears intraday.

## 2. Live re-check of the desk-note universe (06:05 UTC / ~1:05 AM CT 8/20 — premarket, re-confirm at open)

| Ticker | 8/19 SIP | Last (AH/premkt) | Δ vs SIP | Desk-note cluster | Note |
|---|---:|---:|---:|---|---|
| **MRNA** | 174.38 | 166.07 | **−4.8%** | A: cancer-vax | Gap continues fading past the note's 167.45 AH mark — the "does the gap hold" question is trending toward *no* so far. |
| **MRK** | 152.20 | 152.25 | flat | A: cancer-vax | Exactly where the note left it — still the sticky name, no fresh info either way. |
| **TEM** | 61.25 | 62.07 | +1.3% | A: cancer-vax | Small continuation, not a fresh signal. |
| **SLS** | 13.85 | 14.01 | +1.2% | A: cancer-vax (binary) | Unchanged read — still fade-the-sympathy until REGAL is dated. |
| **COIN** | 160.20 | 164.82 | +2.9% | B: crypto | Crypto cluster still bid. |
| **MSTR** | 104.25 | 107.55 | +3.2% | B: crypto | Same. |
| **HOOD** | 95.77 | 97.84 | +2.2% | B: crypto | Same — still the one X-confirmed war name in the cluster per the note. |
| **BMNR** | 20.24 | 21.46 | **+6.0%** | B: crypto (residual) | Matches the note's read almost exactly (+6.13% AH there vs +6.0% now) — still the high-beta tell for the whole crypto cluster. |
| **MRVL** | 237.27 | 240.06 | +1.2% | C: one-sided | Unanimous tape holding. |
| **OMER** | 18.39 | 18.31 | flat | C: one-sided | Same. |
| **WMT** | 114.30 | 114.63 | flat | C: pre-event | **Reports before today's bell** (`get_earnings_calendar`: WMT, 2026-08-20, timing `am`, EPS est. $0.74) — as of this pull the print **has not happened yet**; premarket is flat because there's no number out. This is a live, unresolved event risk into the open, not a resolved one. |

## 3. Read for today

**A. Cancer-vax complex (MRNA/MRK/TEM/SLS) — the MRNA fade is the story to watch, not trade at this
size.** MRNA's AH drift (167.45 → 166.07 overnight) is incremental evidence the gap is *not* holding,
which is the exact question watch item #1 from the note asked. No position affordable here today;
just track whether MRNA opens below 166 or snaps back — that's the tell for whether this was a
one-day repricing (bearish for chasing any residual strength in TEM/SLS) or a genuine re-rate that
still has legs (bullish for MRK holding, since it's the name ST actually kept).

**B. Crypto complex — BMNR AH strength continues, unchanged thesis.** Nothing new here since the note;
still one risk-on tape, BMNR still the residual/tell. No affordable entry today.

**C. WMT — the one live, dated, resolvable event in the set.** Print is `am` timing, meaning it likely
lands before or at the open. **Do not treat premarket-flat as "no reaction expected"** — flat premarket
with no print yet just means the market hasn't seen the number. Once it's out:
- If WMT gaps and holds beyond the ST-vs-X contested range the note flagged, that confirms one side of
  the split — worth a fresh look at whether IV richened into the print and is worth fading post-move
  (classic IV-crush setup), but **only if buying power allows** — a defined-risk spread here, not a
  naked single leg, and not this account today unless a stop frees cash first.
- If WMT is muted, the "structure > story" read from the note stands unchanged — no trade either way.

**MRVL 240.06** — still trading like deal-day, not contested. The 206.58 warrant strike (UNVERIFIED)
would put current spot ~16% above that floor if the number is real; treat that gap as unconfirmed
cushion, not a level to size against.

## 4. Today's actual checklist (in order)

1. **Do not touch the five standing GTC stops** (TGB/KOS/PTEN/RIVN/LCID) — they're unrelated to this
   note's thesis and already doing their job.
2. **Confirm the WMT print** at/after the open (`get_equity_quotes(["WMT"])` + `get_earnings_results`
   once actual EPS posts) before drawing any conclusion from price action — don't react to a gap that
   might just be broad-market drift.
3. **Re-pull `get_portfolio`** after the open — if any of the five stops triggered overnight-adjacent
   volatility or intraday weakness, buying power changes and a same-day WMT or MRNA-fade structure
   could become affordable. Re-evaluate sizing only if that happens; don't force a trade into $61.
4. **MRNA**: note the open print vs. 166.07 last AH level. No action — this is a watch item for
   Thursday's cluster continuation read, not a today trade at this account size.
5. If NKE comes back into consideration later, **re-price the spread at $41 spot**, not the stale
   $39-anchored 40/45C math from the 8/19 note — that setup no longer describes the current chain.

## 5. Standing risk rules (unchanged)

Defined-risk only, no naked short vol, nothing sized past available buying power (currently $61.18).
Any structure proposed intraday still requires `review_option_order` + explicit go-ahead before
`place_option_order` — this note is a watch/prep document, not a standing authorization.
