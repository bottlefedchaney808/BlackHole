# LIVE dealer charts — 2026-08-21

Supersedes SVI-as-load-bearing on the 4-panel / flip / hedge. SVI overlay is still computed; it is **not** the plotted GEX/VEX/CEX.

Production: `volatility_suite._run_production_dealer_positioning`
→ `expiry_book_production.fetch_production_result`
→ `ebe.vannacharm_row` + `apply_vannacharm_flow`

Earlier contract: `LIVE_expiry_book_20260820.md` (inventory vs 7d ΔIV; SVI gamma_book). Still true for those fields. Charts moved.

## Stock (prior close)

- Book = last EOD greeks **joined** to `option_bulk_hist_oi_by_day`. `eod_greeks` has **no OI** — using it alone zeros GEX (TSLA blank panels 2026-08-21).
- Guard: `oi_sum > 0` else **keep live snapshot**. Test: `test_prior_eod_without_oi_keeps_live_book`.
- GEX = `callOI·γ·100·S²·0.01 − putOI·γ·100·S²·0.01`
- VEX = `call+|ν|·S·σ − put+|ν|·S·σ` (not opposite charm; not SVI `book_sign`)
- CEX = `CallCharm·callOI·S/365 + PutCharm·putOI·S/365` (ITM call+/OTM put+, ITM put−/OTM call−). No extra dealer −1 on charm.
- Flip / hedge from that GEX smile. Ignore sign-changes outside ±50% of spot (TSLA $25 wing).

## Flow (today only)

- Source: `flow.scanner_trades` via `ThetaDataController.option_session_trades`.
- **Do not** call `flow.analysis(..., exp=)` — HTTP 500. `flow.analysis` without exp is all-expiry strike mix, no bought/sold.
- Quote snapshot has bid/ask **size**, no volume.
- Per print: size at/above mid = bought, below = sold. Same GEX/VEX/CEX kernels with `oi=bought−sold`.
- Chart = prior-close stock **+** dGEX/dVEX/dCEX.
- Overnight / pre-open: today's prints empty → `flow_volume_rows=0`. Do **not** add yesterday's session (already in EOD OI).

`vol_result.dealer_positioning` now carries `flow_provenance`, `flow_volume_rows`, `provenance.book`, `provenance.intraday_flow`.

## Options context-mode (same run)

ATM must be a **listed** strike. `K=spot` (TSLA 341.43) → no quote → LeisenReimer IV fail. `validate_strike` snaps dollars (theta `/1000` if `abs>=1000`) and dict expirations.

## After open

Re-run TSLA. Expect `flow_volume_rows > 0` and Options `status: ok` on nearest listed K (340/345).
