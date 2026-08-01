# Heston vs Standard — Quick Reminder

**Updated 2026-07-28**: Heston now calibrates against the SAME resolved listed
contract as every other model. It used to re-derive its own expiry from `T`
(`now + T*365 days`, then its own nearest-listed-expiration search inside
`HestonCalibrator._prepare`) instead of receiving `main.py`'s already-resolved
`resolved_exp` / `market_exp` the way CRR / LR / NR / SABR / VV / MC / BAW and
the Market row all do. On a chain with several weeklies close together that
picked a *different contract*: verified live on TSLA (nominal T=0.0055) —
Heston calibrated on 20260729 (T=0.0027, 127 strikes, IV 0.49–4.54,
xi=3.72/rmse=0.162) while the rest of the report used 20260731 (T=0.0055,
109 strikes, IV 0.50–3.22, xi=1.12/rmse=0.037). That made Heston's row a fit to
a smile nobody else was looking at, and it's also part of why Heston
intermittently came back `N/A`. `HestonCalibrator(known_expiry=...)` /
`run_heston_full(exp=...)` now take the real contract and use it directly (and
raise, no neighbour-snapping, if it isn't listed). **Separately still open, not
changed:** the `xi >= 4.5` instability guard discards good fits (rmse ~0.03–0.04)
when xi merely pins at its own 5.0 upper bound — see `PROJECT_ROADMAP.md` P2b.

**Updated 2026-07-27**. The pre-2026-07-25 version of this doc described a
"fallback to CRR sigma when Heston is unstable" path. That fallback was
removed in the 2026-07-25 session (see §3 of `Options_Suite_Session_Notes.md`)
and the run-all report now shows Heston as an honest `N/A` on failure. The
2026-07-27 revision below adds the Greek-engine and multi-start changes.

- **Parity is NOT expected in general.** CRR solves IV via bisection against
  its own CRR binomial pricer; Leisen-Reimer, Newton-Raphson, SABR,
  Vanna-Volga, and MC each solve their own IV against their own pricers;
  Heston calibrates its own (κ, θ, ξ, ρ, v0) to the live smile and prices
  under stochastic vol; BAW (new 8th model as of 2026-07-27) uses analytical
  American BS. Their prices AND Greeks are expected to differ — that's
  the point of comparing models, not a bug. See
  `Options_Suite_Session_Notes.md` §2 for the full model-vs-Greek-engine
  matrix.

- **Heston Greeks now come from Heston-under-Heston dynamics (2026-07-27).**
  Previously `compute_greeks_heston` mapped Heston down to
  `effective_sigma = sqrt(V0)` and called `american_all_greeks` (LR tree FD)
  — so kappa/theta/xi/rho contributed NOTHING to any reported Greek despite
  being what makes Heston Heston. `heston_all_greeks` (new) does CRN
  bump-and-revalue on the Heston LSM using ALL five calibrated params.
  Vega is chain-ruled from dP/dV0 to dP/dsigma via sigma = sqrt(V0) so
  units match the other models. Higher-order Greeks (Vanna/Vomma) still
  fall back to closed-form BS at effective_sigma because 2nd-order sigma
  FD on LSM is fundamentally noise-limited — CRN cancels shock noise but
  not regression-fit noise (the LSM continuation-value polynomial is
  refit at each sigma bump, and that fit shifts discontinuously).

- **Heston calibration now uses SABR-style multi-start (2026-07-27).**
  Previously a single Nelder-Mead call from one initial guess — with v0
  as a free 5th parameter added in the 2026-07-25 session, the 5D
  landscape was too non-convex for reliable single-start convergence on
  high-vol names (verified: AMD Put 480 came back N/A on the pre-fix
  live report because the single-start optimizer failed outright).
  `HestonCalibrator.calibrate()` now sweeps a 37-point (kappa, xi, rho)
  seed grid, keeps the caller's suggested initial as one of those seeds,
  and returns the lowest-loss convergence. Verified on synthetic AMD-like
  smiles: 31-35 of 37 restarts converge, rmse ~1e-5, recovers rho/xi
  even from an adversarial caller seed
  (kappa=0.5, xi=5.0, rho=+0.9, v0=0.1 — everything wrong).

- **No fallbacks anywhere in the Heston path.** A calibration failure
  raises (with a "N of M restarts failed" message so the failure mode is
  visible); a pricing failure raises; a real `[Heston] Failed:...`
  surfaces as an honest N/A row in the run-all report, never a
  substituted number from another model.

- **Where to look**: run the "Run all models & compare" menu option
  (currently **(9)**, moved from (8) when BAW was added as the 8th model
  in the 2026-07-27 session). Inspect the generated
  `comparison_<timestamp>.csv`/`.pdf` for the full per-model Greek set.
  On AMD-style near-ATM high-vol puts, the notable cross-check is:
  **BAW's Delta/Vega/Rho/Theta match the vendor's Market row to within
  ~0.4% while the tree-FD Rho is off by ~12%** — strong evidence the
  vendor's Market Greek feed is a BS-based analytical American (BAW or
  equivalent) rather than a tree/simulation.

- **Historical note**: this doc originally (pre-2026-07-25) described an
  expected byte-for-byte parity between Heston and Standard even outside
  the fallback case, from when both shared the same dividend-yield and
  expiry-selection bugs (see `PROJECT_ROADMAP.md`, "QQQ dividend-yield
  bug" and strike-selection fixes). Both biases were fixed in that
  session and Standard's solver was moved from a European BS bisection to
  the CRR binomial, so that old parity expectation no longer applies. The
  2026-07-27 Greek-engine split makes the divergence larger AND more
  legitimate: models now differ in Greeks BECAUSE they differ in
  dynamics, not because one is silently borrowing another's engine.
