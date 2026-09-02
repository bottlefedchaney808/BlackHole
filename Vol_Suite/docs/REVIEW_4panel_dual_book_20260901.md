# REVIEW — Dealer 4-Panel Output Set Under Dual Books (Phase 8b)

Date: 2026-09-01 · Plan: `PLAN_dealer_band_integration_20260901.md` §Phase 8b · Scope: review + minimal label corrections only (no panel redesign, no new charts).

## 1. Inventory — which chart renders what, from which book

The production suite (`volatility_suite.py::_run_production_dealer_positioning`, ~line 466) always renders **two** 4-panel charts from the **exposure snapshot book** (`expiry_book_production.fetch_production_result` → `ProductionDealerExposure.snapshot`). The legacy-engine charts (`plot_greek_exposure_comparison`, `plot_heatmap`) render only from `volatility_suite.py`'s non-production path with `compute_dealer_positioning` (the **assumed-position** legacy model — today's OI, or the 150d accumulated position only when `DEALER_ACCUMULATION=1`).

| Chart function (file:line) | Panels | Data source | Label status before fix |
|---|---|---|---|
| `dealer_positioning.py::plot_expiry_book_greek_exposure` (~:1243) | Gamma/Delta/Vanna/Charm by strike | Exposure book — `result.snapshot.rows` (+ `d_gex/d_vex/d_cex` only when flow layer on) | **Misleading:** title hardcoded "prior close + intraday flow"; ylabels "GEX prior+dGEX", "VEX prior+dVEX", "CEX prior+dCEX" — all false under `EXPOSURE_BOOK_FLOW=0` default. Fixed. |
| `dealer_positioning.py::plot_expiry_book_heatmap` (~:1334) | Γ by strike · OI by strike · spot×IV gamma surface · gamma profile vs spot | Same exposure snapshot (pure OI×BS-gamma repricing; no flow anywhere) | **Ambiguous:** said only "expiry-book engine" — did not say snapshot vs position book. Fixed. |
| `dealer_positioning.py::plot_greek_exposure_comparison` (~:1178) | Gamma/Delta/Vanna/Charm by strike (shares) | Legacy `DealerPositioningResult` — assumed-position model (today's OI, or 150d accumulated under `DEALER_ACCUMULATION=1`) | **Ambiguous:** no book provenance in title; today's-OI and accumulated renders were indistinguishable. Fixed. |
| `dealer_positioning.py::plot_heatmap` (~:872) | Γ by strike · OI · heatmap · gamma profile + interp footer | Same legacy assumed-position model | Not modified — its panels are explicitly the legacy engine's own; the "sign model" tag row already exists there and `result.accumulate` provenance is printed in `print_report`. No claim it is the exposure book. |

## 2. Provenance audit — findings (file:line, pre-fix)

1. **`dealer_positioning.py:1268`** — title `"...— prior close + intraday flow"` hardcoded regardless of `flow_layer`. Under the default (`EXPOSURE_BOOK_FLOW=0`, `flow_layer="snapshot_only"`, `expiry_book_production.py:503-514`) the book contains **no intraday flow**, so the title stated a composition that did not exist. **Severity: high (false claim), fixed.**
2. **`dealer_positioning.py:1273-1274, 1286`** — ylabels `GEX prior+dGEX ($ / 1%)`, `VEX prior+dVEX`, `CEX prior+dCEX / day` claimed prior+flow math unconditionally. **Fixed:** flow-on keeps the old labels (accurate in legacy mode); flow-off renders `GEX ($ / 1%)`, `VEX (shares / 1pp IV)`, `CEX ($ / day)`.
3. **`dealer_positioning.py:1363`** — heatmap subtitle `"sign model: expiry-book engine"` did not distinguish exposure-snapshot from assumed-position book. **Fixed:** now appends `— exposure snapshot (<flow_layer>)`.
4. **`dealer_positioning.py:1192-1194`** — legacy 4-panel title gave no book provenance: a `DEALER_ACCUMULATION=1` accumulated render and a plain same-day OI render were identically titled. **Fixed:** appends `— assumed accumulated position (DEALER_ACCUMULATION)` or `— assumed today's-OI position (snapshot)` via `result.accumulate`.
5. **Plan requirement met:** exposure-snapshot panels and assumed-position panels are now distinguishable by label alone on both engines' 4-panel sets.

## 3. Flow-layer check (`EXPOSURE_BOOK_FLOW` default 0)

- After the fix, **no exposure-book panel claims intraday flow when it did not run**: title says "exposure snapshot (no intraday flow)", and all three "prior+d*" ylabels downgrade to pure-snapshot units. With `EXPOSURE_BOOK_FLOW=1`, the original "prior+d…" wording is preserved (still accurate).
- `flow_layer` is read off the result (`getattr(result, 'flow_layer', 'snapshot_only')`), matching the field set at `expiry_book_production.py:654` — no re-read of the env, so the chart can never disagree with the book it was built from.
- **Where a flow panel COULD render (not built, per scope):** `dealer_position_book.py::PositionBookResult.daily_trace` (line 93; entries appended at :209 and :377 carry `date, kind, d_iv, day_sign, net_change, n_strikes_included, n_new_strikes`) — a per-day net-change/ΔIV trace over the 150d window is the natural source for a position-book flow time-series panel. Jason decides; deliberately not implemented here.
- Guard checked: `_by_strike` in the production 4-panel already tolerates the flow-off default — it adds `getattr(r, 'd_gex', 0.0) or 0.0` per row, which is 0.0 on a pure snapshot, so the bars equal the raw snapshot values. No further guard needed.

## 4. Changes made (all in `Vol_Suite/dealer_positioning.py`)

| Line | Before | After |
|---|---|---|
| ~1268 | `"— prior close + intraday flow"` (hardcoded) | `"— exposure snapshot + intraday flow"` or `"— exposure snapshot (no intraday flow)"`, from `result.flow_layer` |
| ~1273-74, 1286 | `GEX prior+dGEX ($ / 1%)` / `VEX prior+dVEX` / `CEX prior+dCEX / day` | Same when flow on; `GEX ($ / 1%)` / `VEX (shares / 1pp IV)` / `CEX ($ / day)` when off |
| ~1363 | `"sign model: expiry-book engine"` | `"... — exposure snapshot (snapshot_only|legacy_flow)"` |
| ~1192 | title without book provenance | appends `— assumed accumulated position (DEALER_ACCUMULATION)` / `— assumed today's-OI position (snapshot)` |

No layout, palette, panel-content, or filename changes. No chart is removed.

## 5. Recommendations for Jason (one decision each)

1. **Production 4-panel book:** keep it on the exposure snapshot as-is, or add a second title-tagged copy from the dual-book/position book — currently only the snapshot book renders in the production path.
2. **Flow panel:** approve (or reject) a daily flow trace panel sourced from `dealer_position_book.py::daily_trace` — the only honest intraday/flow story now that the exposure book is snapshot-only by default.
3. **Legacy 4-panel default:** with the book now labeled, decide whether `DEALER_ACCUMULATION` stays opt-in or becomes the default for `plot_greek_exposure_comparison` in the non-production path.
4. **Spread visualization:** whether the dual-book spread (`dual_book.py`, exposure − position, mixed-units caveat) deserves a dedicated panel or stays text-only in `format_dual_book_interp`.
5. **Unit readability:** VEX/CEX ylabels mix share and $ conventions between engines (legacy shares vs production $) — standardize or keep the per-engine ylabels as now labeled.

## 6. Tests

`pytest Vol_Suite/tests/test_scanner_vanna_parity.py Vol_Suite/tests/test_expiry_book_production_contract.py Vol_Suite/tests/test_band_wiring.py Vol_Suite/tests/test_dual_book.py -q` → **39 passed** (no test asserted the old label strings; no test edits required). Pre-existing numpy RuntimeWarnings in `test_expiry_book_production_contract` are unchanged.
