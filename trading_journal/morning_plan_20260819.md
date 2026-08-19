# MORNING PLAN — 2026-08-19 (pre-open)

**Source:** `desk_note_20260818.md` (premarket 2026-08-18 pack read + battery + Vol_Suite).
**Status check (live, pulled ahead of tomorrow's open):** neither leg from the 08-18 note was filled —
`get_option_orders`/`get_equity_orders` on account 751521659 ("Agentic") show zero orders since
2026-08-18. **The plan is still fully open, nothing to unwind or adjust for an existing fill.**

---

## 1. Thesis carried forward (unchanged, re-verify don't re-derive)

**NKE is still the trade.** Direction battery HIGH 4/5 (whale+wave3+squeeze+trend), negative dealer
gamma, extreme put skew, front-week IV (15.4%) at roughly half realized (30%), Vol_Suite fair vol 42.5%
vs ATM IV 37.5% (Nov 17) = VRP +5.0pt and **flat-shaped** (not inverted — sellers haven't capitulated,
this isn't a crash-pricing regime). Max fear priced into puts on a beaten-down $39 name with a clean
bullish tape — the contrarian, defined-risk setup.

**SPY 800C is the cheap-convexity add.** ATM IV 9.6% sat at the note's "vol floor" (SPY normally
14–16%) with negative gamma — asymmetric long-convexity, not a lottery ticket, per the note's own math
(needs +3.7% by Sep 18 expiry).

**Known-broken instrumentation, status update:**
- **L2/max-pain: FIXED tonight (08-18), not yet re-run live.** Root cause was a call/put payoff swap in
  `max_pain_scanner.py::_compute_pain_for_strike` (masked by every existing test using symmetric OI —
  see the desk note addendum for detail). Fixed, regression-tested, 208-test sentiment-scanner suite
  passes clean. Still re-verify with a fresh live pull at tomorrow's open before trusting the number —
  "fixed in a unit test" isn't the same as "confirmed against tomorrow's real chain."
- **Dealer positioning (`expiry_book` "v2 payload is None"): still unresolved as a live data outage.**
  There's separate in-flight (uncommitted, pre-existing) handling in `orchestrator.py`/
  `Vol_Suite/volatility_suite.py` that stops this failure from silently blanking out GARCH/fair-vol
  context threading, but that's error-handling around the outage, not a fix for the outage itself. Retry
  it tomorrow; if it's still returning no payload, proceed on direction + vol data only, same as the
  original note.

## 2. Live snapshot pulled tonight (for tomorrow's pre-open sanity check, not a substitute for a fresh pull at the open)

| | Note's trigger | Latest pulled level | Status |
|---|---|---|---|
| NKE spot | must hold ≥ $38.00 | $39.08 (8/17 close) / $39.25 (last premarket print) | **clear**, ~$1+ above the no-go floor |
| NKE 40/45C spread (Nov 20) | limit $1.70 or better | mid $1.60 (40C mark 2.99 / 45C mark 1.39); wide market — buy-ask/sell-bid worst case **$1.71** | **at the edge** — mid supports $1.70, but the quoted market is wide enough that a $1.70 limit may not fill instantly; be ready to work it up to ~$1.72 rather than chase market |
| SPY spot | enter on dip toward 765–770 | $772.68 (8/17 close), last premarket print **$769.51** | **already inside the buy zone** on the last available snapshot — reconfirm at the open, this moves fast |
| SPY 800C (Sep 18) | limit $1.98 | mark $1.97 / ask $1.98 | **matches**, limit should fill near mid |
| Buying power (751521659) | $400 to deploy | **$405.30** cash, unsettled_funds $0.00 (confirmed via get_accounts) | unchanged from the note; the $350 pending deposit is separate incoming cash, not needed for this plan |

No NKE earnings in the next 14 days (checked `get_earnings_calendar`) — event risk inside the Nov 20
expiry window is unchanged from the note.

## 3. Tomorrow's pre-open checklist (do in order, before placing anything)

1. **Refresh, don't reuse, the data above.** Pull `get_equity_quotes(["NKE","SPY","XLF"])` and the
   three option quotes fresh at/near the open — the table above is from tonight's snapshot and will be
   stale by market open.
2. **Re-run the NKE Vol_Suite read** (fair vol / VRP shape) if time allows — confirm VRP is still
   positive and flat, not inverted. An inverted VRP or a fair-vol collapse kills the NKE thesis; don't
   place the spread on stale math.
3. **Re-run max-pain live** (fixed tonight, needs first live confirmation) **and retry dealer
   positioning** (still unresolved as of tonight). If dealer positioning is still returning no payload,
   proceed on direction + vol data only, same as the original note — don't let it block the primary
   thesis, but don't start trusting a still-broken read either.
4. **Confirm NKE ≥ $38.00** at/near the open. Below that, cancel the NKE leg per the note's own rule.
5. **Confirm SPY is at or below ~770** before entering the SPY leg; if it's gapped back above 772+,
   wait for a dip rather than chasing at a worse convexity price.

## 4. Execution order (both legs still require explicit go-ahead before `place_option_order` — a
morning-plan review does not itself authorize a fill)

1. **NKE 40/45 call spread, Nov 20 expiry, 1 contract** — highest conviction leg. Limit $1.70;
   given tonight's wide quoted market (worst-case $1.71), be willing to work the limit up to ~$1.72
   rather than let it sit unfilled all morning, but don't chase past that without re-checking the
   thesis is still intact.
2. **SPY 800C, Sep 18 expiry, 1 contract** — only on confirmed dip into ≤770. Limit $1.98.
3. **Keep $42 reserve** — don't force it into either leg; if NKE fills better than $1.70, the saved
   cash goes to reserve, not to upsizing.
4. `review_option_order` on each leg before `place_option_order`, and get explicit sign-off from Jason
   per leg — the two legs are independent go/no-go decisions, not a package deal.

## 5. Standing risk rules (unchanged from the note)

Both legs defined-risk, no naked short vol. No adding to a leg already at max loss. Exit the NKE spread
at 50% max profit (~$3.30 on the spread) or roll at 7 DTE. Alternative if pivoting away from index
convexity: XLF 56/59 call spread (Oct 16) instead of the SPY leg — but XLF vol is rich (1.37 IV/RV per
the note), so the spread (never a naked call) is mandatory there, and quotes are wide enough to require
limit orders.
