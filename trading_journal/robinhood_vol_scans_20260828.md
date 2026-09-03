# Robinhood Volatility Scans — 2026-08-28

**As-of:** 2026-08-28 ~08:10 CDT (pre-market). **Scope:** screening pass only — no orders placed, no
trade execution. Part of the 2026-08-28 trading-mode session; see `market_rumor_watchlist_20260828.md`
for the macro/rumor context and `sentiment_scanner_3packs_20260825-0827.md` for the sentiment side.

---

## 1. "Volatility Comparison" scan (Cortex-managed, `scan_id=2c7667c4-ed7e-49a3-a0d8-a2b32e1fc54f`)

100 results, sorted by options volume. Filtered to liquid names (market cap > $2B, price $5-$800,
options volume > 1,000) — 71 of 100 survive the filter.

**Richest IV vs HV (top by IV-HV delta):**

| Ticker | IV | HV(1M) | Delta | IV rank | Mkt cap | Price |
|---|---:|---:|---:|---:|---:|---:|
| PCG | 53.9% | 25.7% | +28.2 | 0.85 | $54B | $18.40 |
| PATH | 86.6% | 59.2% | +27.4 | 1.00 | $9B | $18.13 |
| BB | 81.5% | 56.7% | +24.8 | 1.00 | $5B | $8.69 |
| GAP | 59.7% | 35.1% | +24.6 | 0.74 | $7B | $24.68 |
| CRCL | 82.4% | 65.9% | +16.5 | 0.27 | $24B | $92.60 |
| ORCL | 69.0% | 53.6% | +15.4 | 1.00 | $438B | $152.59 |
| ASST | 105.9% | 90.9% | +15.0 | 1.00 | $2B | $22.57 |
| BMNR | 85.4% | 72.9% | +12.5 | 1.00 | $15B | $25.61 |
| GME | 55.8% | 46.6% | +9.2 | 1.00 | $8B | $18.35 |
| PDD | 41.2% | 32.7% | +8.5 | 0.59 | $121B | $85.25 |

**Cheapest IV vs HV (bottom by delta):**

| Ticker | IV | HV(1M) | Delta | Mkt cap | Price |
|---|---:|---:|---:|---:|---:|
| MRNA | 68.0% | 351.5%* | -283.4 | $57B | $138.05 |
| NBIS | 95.9% | 163.3% | -67.4 | $59B | $214.06 |
| PLTR | 43.7% | 98.4%* | -54.7 | $447B | $184.97 |
| CRWV | 75.2% | 126.2% | -51.0 | $48B | $86.14 |
| TTD | 49.4% | 96.9% | -47.6 | $6B | $13.45 |
| CRM | 42.1% | 76.5%* | -34.4 | $207B | $247.85 |

\* Flagged noisy — see interpretation below.

**Research on the top hits — every one traces to an earnings artifact, not a standing dislocation:**

- **PATH** — reports 2026-09-03 PM (earnings in 6 trading days). Rich IV is normal pre-earnings
  expansion, not mispriced. Stock is also up ~80% in a month ($10.20 low 7/23 -> $18.33 close 8/27),
  RSI(14) = **78.6** — parabolic on top of a binary event. Cross-checked against the sentiment-scanner
  8/27 pack (`sentiment_scanner_3packs_20260825-0827.md`), which flags PATH as the only "uncontested
  bull, thesis_ratio 0.167" name in three days of packs — reads as the crowd chasing the same move,
  not independent confirmation.
- **GAP, and (from the second scan below) S** — both reported earnings **2026-08-27 PM** (last night).
  Still-high IV rank is a stale pre-print snapshot about to crush intraday today.
- **CRM** — reported **2026-08-26 PM**. Same stale-IV-rank issue; the "cheap vol" HV=76.5% reading is
  itself inflated by the post-earnings gap still sitting in the trailing 1-month window.
- **PLTR** "cheap vol" reading is an **HV-computation artifact**, not a real edge: the trailing 1-month
  realized-vol window still contains the 8/4 one-day +32% earnings gap ($125.65->$162.66) from the 8/3
  print. Next earnings isn't until 11/2 — no near-term catalyst to buy premium against regardless.
  Textbook case of "don't trust IV rank alone" (see `trading-mode` skill and `vol-suite` skill).
- **MRNA**'s HV=351.5% is almost certainly the same kind of artifact (a huge one-day gap still inside
  the trailing window) — not investigated further given the other four hits already explained the
  pattern.
- **ORCL** is the one name in the top-10 whose IV richness is *not* earnings-driven: next report is
  2026-09-10 (~2 weeks out), and its realized vol is genuinely elevated on distributed multi-day moves
  (chart round-tripped $132->$115->$156->$142->$152 over the past month), not one gap. Likely
  AI-capex/datacenter narrative (same OpenAI-compute story as NVDA). IV (69%) still sits above that
  already-hot realized vol (53.6%) — a real "rich vol, no dated catalyst" read, but it's a **short-vol**
  thesis. This account is single-leg-long-only and cannot express it. Not actionable here; would be a
  genuine Options_Suite/Vol_Suite candidate for account A (has spread access) if that were in scope.

**Verdict: no candidate survives.** Every hit is either an earnings artifact (rich or cheap side) or,
for the one clean exception (ORCL), a short-vol setup this account structurally can't place.

---

## 2. "High options volume and IV" scan (editable, `scan_id=ad2715de-8bc5-43bb-b9e8-6ed3a9fba207`)

394 total matches, sorted by implied volatility descending; pulled the top 200. **Confirmed the same
issue already flagged in the 2026-08-24 session**: sorted raw by IV, the list is dominated end-to-end
by illiquid micro-caps with garbage/noisy IV prints. Of the top 200 rows, only 4 have market cap above
$1B (ALMS $3.1B, RGC $2.7B, CRAI $1.1B, TRON $1.0B) and IVs in this slice run 200%-3000%+ — not usable
options-liquidity data, just noise from thin option books. Applying the same liquidity filter used
above (mkt cap > $2B, price $5-$800, options volume > 2,000) returns **zero** names from this scan's
top 200.

**Verdict: not usable in its current sort for this purpose.** Re-sorting by "Relative options volume"
(unusual-activity) rather than raw IV would likely surface better candidates, but this scan is
Cortex/user-editable, not re-sortable via `run_scan` alone — would need `update_scan_config` to change
the default sort, or a client-side re-sort of the full un-truncated result set (394 rows, this pass
only pulled 200). Flagging as a follow-up rather than doing it live during a thin-catalyst morning.

---

## Bottom line

Neither Robinhood scan produced an actionable, non-earnings-driven vol dislocation this morning. See
`vol_suite_screener_sellvol_20260828.md` for the third screen (Vol_Suite variance-swap screener on the
user's curated "Sell Vol Plays" basket) and the main Trading journal entry for the combined plan.
