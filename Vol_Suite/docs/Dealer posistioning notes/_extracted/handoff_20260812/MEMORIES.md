# Durable Memories / Project Facts (WSL → Windows handoff)

Import these as persistent memory so the Windows Hermes doesn't re-learn them.

## Project identity
- Monorepo: Financial_Development (quant finance: options pricing, vol, dealer
  positioning, variance swaps, sentiment, VaR, DTCC swap data).
- Python 3.14 venv at `Financial_Dev_Env/`. Run via
  `env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 ...`.

## The ONE dealer model (final, do NOT flip-flop)
- sign_model = `direction` (V5 Direction 5-signal) with per-expiry
  sabr_deviation decomposition, 150d accumulation ON, NO_CALL gate, fallback_bias
  gate RESTORED (bias 0 → no trade). Single canonical model, no alternates.
- vannaflow is the LIVE flow weighting (since 2026-08-12): daily OI flow ×
  rec.vanna × sabr_deviation sign. `DEALER_VANNA_FLOW` defaults "1".

## Vanna convention (Gate-0, measured — do NOT assume)
- `rec.vanna = -1 × BS_vanna(IV, spot, TTE)`. SPY −0.956, QQQ −0.989, |scale|≈1.
- Read `rec.vanna` directly from data rows. NEVER stack an extra right_dir
  (double-signing). NEVER estimate spot as median strike (false right-dependence).
- Dense vanna history: recompute from solved EOD IV under this rule (non-circular).

## Seed axis is dead; flow is the signal
- Across 12 tickers at 150d: replication/vanna/svi_rp seeds all give
  near-identical books (seed_share 0.000-0.009). Flow dominates.
- live+vannaflow moves the dealer-short read 10/12 tickers toward less short;
  flips SPY SHORT→LONG, NFLX LONG→SHORT.
- Seed-flip DITCHED (only initial book may flip, never flow/SABR/gates).
- RP-quantized seed DEAD (w_K ~1e-5 ATM, OI/w in millions). vega-scaled σ_ref
  DEAD (tail collapse). v5 is NOT accumulation (level-OI model).

## SVI everywhere (2026-08-12)
- All smile features use SVI (SSVI, Gatheral-Jacquier). Scanner, strategy tool
  (cached loader), dealer sign resolver. `VOL_SURFACE_FITTER=svi|sabr|quadratic`
  toggle keeps SABR accessible.
- Reusable module: `Vol_Suite/svi_rp.py` (`calibrate_ssvi`, `SviRpReference`).

## ThetaData / PotatoHedge proxy rules
- `option_bulk_hist_oi_by_day` = MOST fragile route. Run SEQUENTIALLY, 3× retry.
  Never 2 concurrent tickers (502s → oi=0 garbage).
- EOD route: staged pairs at THETADATA_HIST_CONCURRENCY=4-6 clean; 4×4 opens
  breaker (~60s cooldown).
- oi=0 after genuine errors = GARBAGE, not "flow dominates".

## Verification
- Authoritative check = per-suite scrubbed pytest:
  `cd Vol_Suite && env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 -m pytest tests -q`
  → 408 passed / 5 skipped. Bare root `pytest`/`hermes verify` FAILS collection
  (cross-suite collision: var_engine/expiry_selector) — documented, not a regression.

## Environment
- Canonical path: /home/bottl/Financial_Development (only editable copy).
- Creds in .env → PotatoHedge ThetaData proxy. yfinance purged.
