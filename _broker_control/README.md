# Broker-Book Control Corpus (forward-return accuracy test)

**Purpose:** accumulate per-expiry chain-scan snapshots into a convention-free
"broker book" control, so the forward-return accuracy test gains power over time.
Every chain scan the suite produces should keep adding a row here.

## Why this exists
The dealer-positioning model's *net gamma* sign is a convention (call + / put -),
not a measured fact: open interest is a count without a long/short direction, so the
data cannot produce a signed net gamma. What we CAN compute convention-free is a
broker-book net for every greek whose sign is built into its raw value.

## Convention rules (Jason, 2026-08-16) -- DO NOT VIOLATE
- **delta / vanna / charm: NO direction assignment.** Take each greek's raw sign
  as-is: `Net = sum(raw_greek * OI)`. Do NOT multiply by a -1 dealer convention --
  the sign is already in the value (call delta +, put delta -, vanna moneyness-signed).
- **gamma: the one greek with NO sign in the raw value** (positive on both rights).
  Report `net_gamma` as-is (always positive) AND a clearly-labeled GEX reference
  `net_gamma_gex = call_gamma*OI - put_gamma*OI` (calls +, puts -). The GEX column
  is an imported convention, never a measured fact.
- theta / vega are all-long proxies (raw long-option theta is -, vega is +).

## Files
- `build_broker_book.py`       -> `broker_book_control.csv`  (46 rows so far; one per chain-scan file)
- `test_forward_returns.py`    -> `forward_return_test.csv`  (joins nets to ThetaData forward returns)
- `compare_control_vs_model.py`-> `control_vs_model.csv`     (PRE-CORRECTION reference only: it applies
  the _dealer_sign convention to delta, which Jason corrected 2026-08-16 -- do NOT use as the control;
  use build_broker_book.py's raw as-is nets instead)

Re-run:
```
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe _broker_control/build_broker_book.py
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe _broker_control/test_forward_returns.py
```

## Result so far (2026-08-16): exploratory null, UNDER-POWERED
No control net predicts forward moves at 1d (r all near 0). The largest r
(net_vanna fwd-5d +0.319, n=21) is NOT meaningful: effective independence is
~18 unique dates, not 46 nominal rows -- same-date snapshots share one underlying
return. Do NOT read any correlation here as signal until the unique-date count is
large.

## AGGREGATION -- keep adding snapshots
- The corpus is the accumulation of every `*chain_scan*.csv` under
  `orchestrator_output/` and `Vol_Suite/outputs/`.
- Keep producing chain scans (see the producer note in
  `Vol_Suite/options_chain_scanner.py`); `build_broker_book.py` picks up new files
  automatically on re-run.
- Power target: this needs many more INDEPENDENT (ticker, day) units (hundreds),
  not just more rows. Dedupe same-day/expiry duplicates before trusting any number.

## Data caveats
- vanna/charm *magnitudes* were never verified against a live response (signs are safe).
- `hist_stock_eod` returns rows keyed by `created` (ISO datetime), not `date`.
