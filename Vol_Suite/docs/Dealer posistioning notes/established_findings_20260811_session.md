# Established Dealer/Vanna Findings — session 20260811_011529_029dc3

**Purpose:** The deep-dive on Cem Karsan's dealer/vanna framework MUST be cross-referenced against these already-MEASURED facts from Jason's 2026-08-11/12 dealer battery. If Karsan's framework predicts something that contradicts these measurements, flag it explicitly — that's the whole point of the comparison.

## The one canonical dealer model (final — do NOT flip-flop)
- `sign_model = direction` (V5 Direction 5-signal) with per-expiry `sabr_deviation` decomposition, **150-day accumulation ON**, NO_CALL gate, fallback_bias gate.
- **vannaflow is the LIVE flow weighting** (since 2026-08-12): daily OI flow × `rec.vanna` × `sabr_deviation` sign. `DEALER_VANNA_FLOW` defaults "1".

## Vanna convention — Gate-0 pin, MEASURED (do not assume)
- **`rec.vanna = -1 × BS_vanna(IV, spot, TTE)`**.
- SPY: sign-consistency **−0.956**, |scale| ≈ 0.951. QQQ: **−0.989**, |scale| ≈ 1.005. Uniform across moneyness + rights.
- **CRITICAL pitfall:** estimating spot as the *median strike* produces a FALSE right-dependent sign reading. Use real spot (`underlying_price` / `hist_stock_eod`).
- Consumers MUST read `rec.vanna` directly from data rows. NEVER stack an extra `right_dir` (double-signing).
- Historical vanna recompute from solved EOD IV is **non-circular** because the convention is measured first.

## Seed axis is DEAD; flow is the signal
- Across **12 tickers at 150d**, all three seed constructions (replication / vanna / svi_rp) produce near-identical end books — **seed_share 0.000–0.009**, flow utterly dominates. No seed construction matters.
- **live+vannaflow** (flow × rec.vanna × sabr sign) moves the dealer-short read on **10/12 tickers**, always toward LESS short. Regime flips: **SPY SHORT→LONG, NFLX LONG→SHORT**. Cuts −39% to −80%.
- Seed-flip DITCHED (only initial book may flip, never daily flow/SABR/gates).
- RP-quantized seed DEAD (w_K ~1e-5 at ATM, OI/w in millions). vega-scaled flat σ_ref DEAD (tail collapse → all far-OTM marked SHORT).
- **v5 is NOT accumulation** — never compare seed/accumulation arms against `net_gamma_v5` (that's the level-OI direction model).

## 12-ticker seed/flow comparison (150d, 4 arms)
Arms: `live(repl)` / `vanna_seed` / `live+vannaflow` / `svi_rp_seed`, same SABR-signed accumulation flow.

| Ticker | live | vannaflow | vf effect |
|---|---|---|---|
| SPY  | −1,282,622 S | +275,735 L | FLIP→LONG |
| QQQ  | −78,082 S | −32,390 | −59% |
| AAPL | −187,635 S | −114,435 | −39% |
| NVDA | −201,396 S | −109,785 | −45% |
| AMD  | −84,854 S | −50,999 | −40% |
| TSLA | −73,042 S | −77,212 | +6% (odd) |
| MSFT | −152,965 S | −57,209 | −63% |
| META | −108,906 S | −60,791 | −44% |
| GOOGL| +7,873 L | +202 L | −97% (near-0) |
| AMZN | −79,407 S | −45,894 | −42% |
| NFLX | +50,260 L | −35,689 S | FLIP→SHORT |
| JPM  | −17,164 S | −3,402 | −80% |

**Findings:** seed axis dead; vannaflow moves book 10/12 toward less short; GOOGL near-flat (noise), TSLA +6% more short (odd one out).

## M2 joint test results (all GREY — the target relation)
M2 = net delta-OI (pooled p 0.0245, inverted-positive) was the original target.

| Sample | n days | sign-agreement | Spearman ρ (p) | Verdict |
|---|---|---|---|---|
| SPY 30d | 40 | 0.600 | −0.004 (0.98) | GREY |
| SPY+QQQ | 71 | 0.561 | 0.165 (0.17) | GREY |
| AAPL+NVDA | 278 | 0.481 | 0.003 (0.95) | GREY |
| AMD+TSLA | 279 | 0.523 | 0.158 (0.008) | GREY |

**Interpretation:** book_b (Δvanna flow leg) is **orthogonal to M2** and not predictive of realized vol on its own. Bounds: ≥0.80 = collinear/artifact ROBUST; ≤0.40 = separate volga book; GREY → **lead-lag (k∈{0,1,2}) is the pre-registered decider** (NOT yet run).

## Key open questions the deep-dive should inform
1. **Does the accumulated book settling into ONE persistent regime per ticker (never flipping sign) match Karsan's view that dealer positioning is a structural carry?** (Jason's 08-13 falsifier finding.)
2. **Sign convention:** Karsan's vanna sign framing vs Jason's measured `rec.vanna = -1 × BS_vanna`. Does Karsan's directional read (vanna flow pushes index UP as vol decays) match the −1 flip?
3. **Vannaflow direction:** Karsan says vanna flow is a structural BID (vol compression → dealers buy stock back). Jason's vannaflow arm makes the book LESS short (toward long) 10/12 — is that the same mechanism?
4. **What surface read predicts a regime flip** in the accumulated book (Karsan: vol compression, skew, seasonality, structured-product issuance, 0-DTE)?
5. **Lead-lag design** for the M2 decider (k∈{0,1,2}).
6. **SVI-RP reference smile** as the cheap/rich sign source — Karsan reads the surface (skew, term, ATM level) as dealer positioning. Note: SVI is now the default fitter everywhere (`VOL_SURFACE_FITTER=svi|sabr|quadratic`).

## SVI everywhere (2026-08-12)
- All smile features use SVI (SSVI, Gatheral-Jacquier). Scanner, strategy tool (cached loader), dealer sign resolver.
- Reusable module: `Vol_Suite/svi_rp.py` (`calibrate_ssvi`, `SviRpReference`).
- `VOL_SURFACE_FITTER=svi|sabr|quadratic` toggle keeps SABR accessible.

## Proxy/data rules (for any re-run)
- `option_bulk_hist_oi_by_day` = MOST fragile route. Run SEQUENTIALLY, 3× retry. Never 2 concurrent tickers (502s → oi=0 garbage).
- EOD route: staged pairs at THETADATA_HIST_CONCURRENCY=4-6 clean; 4×4 concurrent whole-chain fan-outs open the breaker (~60s cooldown).
- **oi=0 after genuine errors = GARBAGE**, not "flow dominates". Re-fetch.

## The known SVI limitation
- SVI's falling right tail under-marks SPY's call-wing upturn (known; 2-sided/asymmetry refinement possible).

## What Karsan says that maps DIRECTLY here (for the deep-dive to verify & expand)
- "Gary" = dealer positioning; if everyone owns puts → market less likely down; if short puts → explosion (XIV 2018).
- **Vanna flow:** world short puts/long calls → dealers short stock → **vol compression forces dealers to buy stock back → structural bid**. Accelerates in high vol + seasonal lulls. Reverse if call-skew dominates.
- **Charm:** delta decay forces hedging on a calendar basis → effects cluster at **OpEx**.
- **Skew as a structural carry** — "better risk-adjusted than VRP"; liquidity clusters ~30 days; skew peaks just before expiry.
- **Dispersion:** index pinned (vol compressed) → constituents diverge → correlation breaks down.
- **Structured products / covered-call ETFs:** $500B→$2.5T; short index vol → compress index → single-stock rotation explodes. "Lower VIX means more rotation."
- **0-DTE:** majority of SPX volume, speculative, self-fulfilling; flattening skew.
- **"Summer of George"** — summer vol compression → rotation/dispersion call.
- **Vol-of-vol / VVIX** — most penny-for-buck convexity.
- **Macro:** 15-20yr regime of higher rates/inflation/populism; "lost decade-plus"; debt monetization "Japan is the template"; **midterms = flashpoint**.

## One caution to pass to the deep-dive
Some of the above (e.g. "everything is vol", "wall of vanna") are widely attributed to Karsan on FinTwit but NOT confirmed verbatim in extracted sources — the deep-dive should mark [VERIFIED]/[UNVERIFIED] and not treat popular attribution as confirmed.
