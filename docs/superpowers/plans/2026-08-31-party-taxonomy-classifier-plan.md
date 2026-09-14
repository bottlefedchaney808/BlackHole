# Plan: Party-taxonomy dealer classifier — full wiring (falsifier + band + flow)

Date: 2026-08-31
Spec: docs/superpowers/specs/2026-08-31-party-taxonomy-classifier-design.md
Directive: wire up EVERYTHING studied — classifier-signed flow, falsifier rerun,
band-edge direction redo, and the live band monitor input — not just the falsifier.

## What exists (no rebuild)

- `Temp/pull_spy_flow_timestamped.py` — 71 days of 15-min bars (flow_ts/) w/
  per-print ms_of_day, size_bought/sold per contract, cex/gex flow per bar. DONE.
- `Temp/charm_falsifier.py` + `.json` — v1 regression (quote-side proxy).
- `Temp/delta_band_test.py` + `delta_band_series.json` — N(t), 251d.
- `Temp/band_edge_model.py` + `ou_band_fit.json` — OU fit (κ, μ, σ_eq).
- `Temp/signed_edge_test.py` — v1 direction test (null: corr −0.060).
- `Temp/party_pairs_replica.py` — party proxy taxonomy (validated vs AIGamma).
- scanner_trades fields verified: buyer_score, seller_score, score_delta,
  trade_type, trade_condition, size, premium, ms_of_day, days_to_expiry.

## Build steps

### 1. `Temp/pull_classifier_inputs.py`
For each of the 71 band days: pull full scanner_trades (paginated, use_csv,
retry w/ backoff — pattern in party_pairs_replica.fetch_day), keep per-print:
ms_of_day, size, premium, buyer_score, seller_score, score_delta, trade_type,
trade_condition, days_to_expiry, strike_price, trade_right, expiration,
underlying_price. Write `opex_full_book/flow_cls/<day>.json.gz`.
NOTE: 71 days × ~45 pages × ~10-20s — background job, notify when done.

### 2. `Temp/dealer_classifier.py` (module)
- `score_print(row, smile_marks) -> (p_dealer, direction)`:
  * Voters (weights): aggressor imbalance |score_delta|/2 (0.30),
    trade-type group sweep/block→dealer-side (0.20), size tier (0.15),
    SVI mark (0.15), SABR mark (0.15), VV mark (0.05).
  * p_dealer = Σ w_i·v_i; direction = sign from aggressor side × right × bs.
- `classify_day(rows, smile_marks) -> per-print + daily aggregates`:
  dollar-weighted customer-initiated premium (signed), dealer-residual premium,
  per-bar splits. Output schema versioned.
- Smile marks: reuse svi_rp calibrate on day greeks (cache per day);
  SABR fit via Vol_Suite sabr if importable, else logit-smooth proxy mark from
  SVI residuals (documented fallback, flagged in output).

### 3. `Temp/falsifier_v2.py`
Rerun of charm_falsifier with realized proxy = classifier-signed daily
dealer-residual delta pressure (not quote-side Δdelta-burden alone).
Regress: realized_v2 ≈ β_c·F_charm + β_g·F_gamma + β_v·F_vanna.
Compare table: v1 βs/R² vs v2. Gate: classifier "adds information" if
charm-leg R² improves OR gamma leg stabilizes with better fit.

### 4. `Temp/signed_edge_v2.py`
Rerun signed_edge_test with classifier-signed dealer flow (dollar-weighted):
corr(dev_from_band_center, dealer_net_premium) + terciles + |z|>1 opposition %
Gate: direction "cracked" if corr < −0.15 or opposition > 60%.

### 5. Band monitor feed
`band_monitor.py` extended: after computing z, also pull today's classified
dealer-residual premium (classifier on live scanner_trades, last 60 min) and
print DEALER PRESSURE line. Monitor stays one-shot, cron-able.

### 6. Logging
Update Obsidian `Cem charm clock insight.md` — Round 7: classifier build +
v1/v2 comparison + edge-direction verdict. Update spec status.

## Verification

- Unit: classifier on synthetic prints (known dealer/customer patterns).
- Data: day counts vs flow_ts days (71); print counts within 5% of raw pulls.
- Statistical: v1 vs v2 table must show numeric deltas, not narrative.
- Band monitor: live run prints both BAND and PRESSURE lines.

## Risks

- smile_marks add latency per day (SVI calib ~seconds) — cache, vectorize.
- SABR import path may not exist in worktree → documented fallback, flagged.
- scanner_trades pagination caps (450k prints/day verified OK w/ use_csv).
