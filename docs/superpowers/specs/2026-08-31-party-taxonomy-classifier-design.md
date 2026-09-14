# Design: Weighted party-taxonomy dealer-flow classifier + falsifier rerun

Date: 2026-08-31
Status: approved concept (Jason), design draft
Depends on: band model (`ou_band_fit.json`, `delta_band_series.json`), falsifier v1
(`charm_falsifier.py`), scanner_trades access (400k prints/day SPY verified)

## Problem

Two sign-assignment layers in the dealer model are inference, not observation:

1. **Book signs** — `apply_svi_book_signs` marks resting OI dealer-long/short by
   SVI cheap/rich. Works, but it's one model's opinion, assumption-heavy.
2. **Flow signs** — our signed-edge test (Round 6) used quote-side only
   (customer sold@bid vs bought@ask) with no party identity → direction null.

## Idea (Jason's)

Weighted multi-signal classifier producing a cumulative dealer score:

- Flow scorer (per print): net buyer/seller score, score_delta, trade-type group,
  SVI + SABR + VV smile markings (6 voters, weighted; cumulative score threshold
  with a min-vote sanity floor)
- Resting scorer (per strike): 3 smile voters + trailing flow-scorer evidence
  (smile prior + classified flow update)
- Direction: aggressor side × right × buy/sell (observable), NOT vendor sentiment
- Weighting "clock": net premium (dollar-weighted dealer pressure)

First consumer: **the falsifier** (option C). Calibrate the classifier by whether
classifier-signed flow improves the charm/gamma regression and cracks the
band-edge direction null.

## Honest design notes

- Ground truth for "dealer" doesn't exist → threshold calibration is against
  model outcomes (regression R², edge-direction emergence), not labels.
- VV marks nearly always agree with market (it interpolates) → down-weight (0.2)
  vs SVI/SABR (0.4 each).
- Party labels (Customer/ProCustomer/Firm/MM) are proxies from size/premium/
  sweep/block. Validated shape vs AIGamma panel: customer-initiated 35.7% vs
  their 39.8%.
- Two layers need different voters; the 6-voter rule is flow-only. Resting gets
  3 smiles + trailing flow evidence.

## Scope of this build (falsifier rerun)

1. Pull classifier inputs for the 71 band-days (scanner_trades w/ scores +
   trade_type) — puller pattern exists.
2. Flow scorer per print → daily customer-initiated signed premium series.
3. Rerun charm falsifier regression with classifier-signed realized proxy vs
   v1's quote-side proxy: compare β_charm, β_gamma, R².
4. Rerun band-edge direction test with classifier-signed flow (the null we want
   to crack): does dealer flow oppose N's deviation from band center?
5. Deliverable: comparison table v1 vs v2, verdict on whether the taxonomy adds
   information, logged to Obsidian.

Out of scope (later builds): live chart panel, resting-scorer production wiring,
replacement of apply_svi_book_signs.
