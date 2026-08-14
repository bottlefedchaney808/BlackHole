# Delivered Research — Cem Karsan Dealer/Vanna Framework (for session 20260813_035322_ed6f70)

**Delivered to:** the dealer-positioning working session (20260813_035322_ed6f70) and this handoff folder, so the vannaflow / accumulation / SVI-RP work can consume Karsan's framework directly.

**Purpose:** This session is building exactly what Cem Karsan's framework describes — multi-day accumulation, vanna-weighted flow, the SVI-RP surface as dealer positioning. Cross-reference your MEASURED findings against his framework; where they agree, that's confirmation; where they disagree, flag it.

---

## 1. Your established findings (measured — the ground truth your work rests on)

Full detail in `established_findings_20260811_session.md` (same folder). Headlines:

- **Vanna convention (Gate-0 pin, MEASURED):** `rec.vanna = −1 × BS_vanna(IV, spot, TTE)`. SPY −0.956, QQQ −0.989, |scale|≈1. Read `rec.vanna` directly, never stack a right_dir (double-signing), never use median-strike as spot.
- **Seed axis is DEAD; flow is the signal.** 12 tickers @150d: all 3 seeds (replication/vanna/svi_rp) give near-identical books (seed_share 0.000–0.009). **vannaflow** (flow × rec.vanna × sabr sign) moves the book 10/12 toward LESS short, flips SPY→LONG, NFLX→SHORT, cuts −39% to −80%.
- **M2 joint all GREY** → lead-lag (k∈{0,1,2}) is the pre-registered decider (NOT yet run).
- **08-13 falsifier finding:** the accumulated book settles into **ONE persistent regime per ticker** (never flips sign within a ticker's window).
- **v5 is NOT accumulation** (level-OI model) — don't compare seed/accumulation arms against it.

---

## 2. The Karsan deep-dive (incoming)

A dedicated research pass is extracting Karsan's dealer/vanna/charm framework and mapping it onto your constructs. It will land at:
- `trading_journal/cem_karsan_dealer_vanna_deepdive.md`
- plus the broad KB at `trading_journal/knowledge_base_cem_karsan.md`

When it lands, the key mapping questions to check:
1. **Does "one persistent regime per ticker" match Karsan's view that dealer positioning is a structural carry?** (Likely YES — his "Gary" is a persistent structural book; regime flips come from vol compression / skew / seasonality / structured-product issuance / 0-DTE, not day-to-day.)
2. **Vanna sign:** Karsan's directional framing vs your measured `−1 × BS_vanna`.
3. **Vannaflow direction:** Karsan says vanna flow is a structural BID (vol compression → dealers buy stock back → index up). Your arm moves the book LESS short (toward long) 10/12 — is that the same mechanism?
4. **What surface read predicts a regime flip** in the accumulated book.
5. **Lead-lag design** (k∈{0,1,2}) for the M2 decider.
6. **SVI-RP surface** as the cheap/rich sign source.

---

## 3. Karsan's framework essentials (mapped to your code, from the broad KB)

- **"Gary" = dealer positioning.** Everyone owns puts → market *less* likely down; short puts → explosion (XIV 2018). [VERIFIED]
- **Vanna flow:** world short puts/long calls → dealers short stock → **vol compression forces dealers to buy stock back = structural bid** pushing index up. Accelerates in high vol + seasonal lulls; reverses if call-skew dominates. (Mar-2020 example.) [VERIFIED]
- **Charm:** delta decay forces dealer hedging on a calendar basis → effects cluster at **OpEx**. [VERIFIED]
- **Skew as a structural carry** — "better risk-adjusted than VRP"; liquidity clusters ~30 days; skew peaks just before expiry. [VERIFIED]
- **Dispersion:** index pinned (vol compressed) → constituents diverge → correlation breaks down. "Lower VIX means more rotation." [VERIFIED]
- **Structured products / covered-call ETFs:** $500B→$2.5T; short index vol → compress index → single-stock rotation explodes. [VERIFIED]
- **0-DTE:** majority of SPX volume, speculative, self-fulfilling; flattening skew. [VERIFIED]
- **VVIX/vol-of-vol:** most penny-for-buck convexity. [VERIFIED]
- **Macro:** 15-20yr higher-rates/inflation/populism regime; "lost decade-plus"; debt monetization "Japan is the template"; **midterms = flashpoint**. [VERIFIED]

*"Everything is vol" / "wall of vanna"* are widely attributed but NOT confirmed verbatim in extracted sources — treat as UNVERIFIED until the deep-dive confirms.

---

## 4. Caution for whoever continues

- Some Karsan attributions are popular FinTwit folklore, not verified quotes — the deep-dive tags [VERIFIED]/[UNVERIFIED]. Don't treat attribution as confirmation.
- Your proxy rules still apply (OI-by-day sequential, EOD staged pairs, oi=0 after errors = garbage).
- `env -u PYTHONPATH` before running venv python.
