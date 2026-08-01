# Monte-Carlo American Pricer - Heston & Model Comparison

This document describes the recent changes and how to run the comparison/reporting features.

Overview
- Each model (Standard, SABR, Vanna-Volga, Heston) prints a standardized debug block when run. The block includes:
  - timestamp, inputs (S, K, T, r, q), sigma/parameters, calibration summary (if any), price, greeks
- Option 5 in the CLI runs all models and produces a comparison. You can save the comparison as CSV and/or PDF.

Running
1. Ensure dependencies are installed (recommended):
   - numpy, scipy, pandas, matplotlib, yfinance, httpx
   - Example: pip install numpy scipy pandas matplotlib yfinance httpx

2. Start the CLI:
   python main.py

3. Choose option 5 (Run all models & compare) and follow prompts.

Notes
- Heston calibration fits European IVs (for parameters) but pricing is American LSM.
- The PDF report is generated using matplotlib and saved into the working directory with a timestamped filename.
- The comparison CSV is machine-readable and suitable for downstream analysis.
- During "Run all models & compare", after the per-model comparison prints you'll be asked
  "Include chain-wide smile comparison chart? (y/n) [n]". This is opt-in and off by default --
  answering 'y' calls `chain_evaluation.build_smile_comparison` to solve market IV plus every
  model's own IV curve across every real strike in the options chain (not just the priced K),
  which is added to the PDF as an extra smile-comparison page (CSV output is unaffected --
  the smile chart is PDF-only). This is more expensive than the base run, so it's only computed
  when requested. See NOTES_chain_evaluation.md for details.

Option 10: Full chain evaluation
- Choose option 10 ("Full chain evaluation (every model, every strike)") to run every model
  against every real strike in the options chain -- not one priced K plus an opt-in smile
  chart, but price + implied vol + Greeks for every model at every strike in one pass.
- Setup (market data, expiry resolution, RR25/BF25 for Vanna-Volga, etc.) is identical to
  option 9's -- you'll see the same prompts. Each model still uses its own pricer/Greek engine
  (CRR its own tree, MC its own LSM, Heston its own calibration, and so on), never a shared
  generic engine standing in for all of them.
- Scope: every model gets full-accuracy price + IV + 1st-order Greeks (Delta/Gamma/Vega/Rho/
  Theta) at every chain strike. CRR/Leisen-Reimer/Newton-Raphson/SABR/Vanna-Volga/BAW also get
  2nd/3rd-order Greeks (Vanna/Vomma/Speed/Charm/Color) at every strike (cheap on a tree/analytic
  pricer). MC and Heston's 2nd/3rd-order Greeks are only computed once, at the single strike you
  priced (the "focus K") -- recomputing those at 100-150+ strikes would push a single report to
  10-30+ minutes for those two models alone, given each Greek there is a CRN bump-and-revalue on
  a full Monte Carlo/LSM re-simulation.
- After it runs, you'll be asked "Save full-chain report? (none/csv/pdf/both) [none]":
  - CSV: one row per (strike, model) pair with price/IV/every Greek (blank where a model didn't
    compute or solve that Greek at that strike -- e.g. MC/Heston's higher-order Greeks away from
    the focus K). This is the complete dataset; nothing is summarized away here.
  - PDF: a paginated summary table windowed around the focus K (5 strikes each side by default,
    1st-order Greeks only, since a full 100+ row x 9-row-per-strike table isn't printable or
    readable as one page) followed by the same branded smile-comparison chart option 9's opt-in
    prompt produces (model IV curves vs. market IV across the whole chain) -- reused directly,
    not rebuilt. Full 2nd/3rd-order Greek data at every strike is in the CSV, not the PDF.
- A model that fails at a given strike (or across the whole chain) is simply omitted there --
  no zero-filled or borrowed numbers, matching the house convention everywhere else in this tool.
- See NOTES_chain_evaluation.md's "SPEC (2026-07-28): 'full chain' evaluation mode" section for
  the full design writeup, including the MC/Heston scope tradeoff and the PDF table-scoping
  decision.

Next steps you asked for (to be implemented later):
- CLI presets for sims/steps (fast/accurate)
- Background queueing for multiple comparisons
- More polished PDF layout (charts) if desired

