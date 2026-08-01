# Chain Evaluation — status and next steps

`chain_evaluation.py` was split out of `main.py` on 2026-07-25. It builds the
"Smile Comparison" chart data: market IV plus every model's own implied-vol
curve across a whole options chain, not just a single priced strike `K`.
This file tracks what's fixed, what's still open, and the planned next step
(a "full chain" mode) so this doesn't have to be re-derived from scratch.

## TODO (top of mind)

- [x] **Fix Heston** — root cause found live 2026-07-28 and fixed: Heston was
      re-deriving its own expiry instead of using the one already resolved for
      every other model, so it calibrated against a different contract's smile.
      See "RESOLVED — Heston" below. One follow-up remains open (the
      `xi >= 4.5` guard) — tracked in `PROJECT_ROADMAP.md` Known Issues P2b,
      NOT fixed.
- [x] Wire up a real "chain evaluation" entry point that calls
      `chain_evaluation.build_smile_comparison` deliberately, on request --
      NOT automatically during a normal single-K "Run all models & compare".
      **2026-07-28: done.** Rather than a separate menu option (which would
      have meant duplicating the ~200-line per-model price/Greeks/sigma
      gathering block that already lives inside `choice == '9'`), this was
      wired in as an **opt-in y/n prompt inside the existing `choice == '9'`
      branch**, right where the old unconditional `smile_data = None` sat.
      The prompt ("Include chain-wide smile comparison chart? (y/n) [n]")
      only calls `build_smile_comparison` when the user types `y`; any other
      answer (including just Enter) leaves `smile_data = None` exactly as
      before, so a normal "Run all models & compare" run is unchanged unless
      you explicitly ask for the chart. `build_smile_comparison` itself was
      not touched. See `main.py` around the `smile_data = None` line inside
      `choice == '9'`.
- [x] Full-chain mode as its OWN menu option (choice `10`), with its own
      CSV/PDF report shape. **2026-07-28: done** — see the "SPEC" section
      below for the full write-up (entry point + report sub-bullets).

## Fixed this session

- **cupy/typing crash + CUDA headers** — stray `typing`/`datetime` PyPI
  packages in `requirements.txt` were shadowing stdlib and crashing cupy's
  subprocess probe; removed. Separately, `cupy-cuda12x` needed the `[ctk]`
  extra for CUDA Runtime headers. GPU confirmed working after both fixes.
- **MC.py / MCHestonLSM.py vectorization** — GBM path simulation collapsed
  from a `steps`-long Python loop to one `cumsum` (exact, not approximate).
  Heston's path sim and LSM backward induction now route through the same
  `xp` (numpy/cupy) backend MC.py already had, so Heston actually uses the
  GPU too. `brute_force_mc`'s repeated RNG regeneration was hoisted out
  (bit-identical output, but this turned out NOT to be the dominant MC cost
  — see "Known limitations" below).
- **CRR / Leisen-Reimer / Newton-Raphson chain-wide IV solve** — batched
  across the whole strike chain (`crr_american_price_batch`,
  `leisen_reimer_american_price_batch` in `american_binomial.py`, plus
  `brute_force_batch` / `brute_force_lr_batch`) instead of one Python-level
  bisection per strike. ~3.3x faster at 100-150 strikes, verified bit-close
  to the old scalar solves. Newton-Raphson's curve reuses the Leisen-Reimer
  batched bisection (same root, verified to agree to ~1e-6) rather than a
  separate batched Newton implementation.
- **VannaVolga flat/wrong smile — TWO separate bugs, both fixed:**
  1. `get_vol` hard-clamped every strike beyond the 25-delta pillars to a
     flat constant vol instead of extrapolating the weighted formula (which
     is well-defined everywhere, no clamp needed).
  2. **The real root cause of the "why does VV look nearly flat no matter
     what" behavior across AMD/BA/JPM**: `get_vol` applied RR and BF with
     their symmetry backwards. RR25 (`sigma_25C - sigma_25P`) is
     anti-symmetric and must flip sign between call/put; BF25
     (`(sigma_25C+sigma_25P)/2 - sigma_ATM`) is symmetric and must apply
     with the same sign to both. The code had this swapped — so the actual
     skew driver (RR) shifted both pillars together (no skew produced) while
     only BF (usually much smaller) created any call/put spread at all.
     Fixed in both `get_vol` and `get_vol_batch`.
- **Market IV vendor-vs-solved priority reverted** — `smile_utils.py` had
  been flipped (by an earlier session) to always solve IV from price and
  use the vendor `implied_vol` field only as an absolute last resort. That
  made the "Market" curve tautologically identical to CRR/Leisen-
  Reimer/Newton-Raphson (all inverting the same price through the same
  family of American solver) for nearly every liquid strike, and only
  showed genuine vendor quotes in the illiquid deep wings — backwards.
  Reverted: vendor field is used whenever present; solving from price is
  now only a fallback for strikes with a missing/null vendor IV.

## Known limitations (not bugs — structural, worth knowing)

- **CRR/Leisen-Reimer/Newton-Raphson/MC's strike coverage is narrower than
  Market's.** These models can only solve IV where a strike has a live,
  invertible 2-sided price (bid/ask mid, or close/mark/last). Market now
  shows vendor IV for *any* strike with a vendor field, including illiquid
  far-wing strikes with no tradeable quote at all — so on names with thin
  wings (confirmed live on JPM: tree/MC curves only spanned ~350-400 while
  Market's vendor points ran 130-490), the tree/MC curves will look like a
  tiny sliver next to a much wider Market curve. This is inherent (you
  cannot invert a price that doesn't exist), not a solving failure.
- **SABR fits ATM almost exactly, then extrapolates aggressively in the
  wings.** Alpha is bisection-solved to match the true ATM market vol
  exactly (a standard, conventional SABR simplification — see West 2005 —
  not unconventional). But the rho/nu least-squares fit is vega-weighted,
  and vega craters to ~0 away from ATM, so far strikes have almost no say
  in the fit. On a steep short-dated skew (JPM, T=0.148y) this produced a
  very aggressive wing (SABR priced $6.52 vs Market's $5.38 at the reported
  K). Not a bug in the pinning itself; the vega-weighting leaving the wings
  under-constrained is the tunable part, if this needs to be tamed (options:
  a wing-anchoring regularization term, or clipping nu).
- **VannaVolga's 3-pillar method structurally cannot reproduce curvature
  beyond the 25-delta points** — it only ever knows about ATM + 25P + 25C.
  Even with the RR/BF sign bug fixed, don't expect it to trace JPM's real
  far-wing skew (0.86 at K=190) — that's out of scope for what this method
  computes, by design.
- **MC's chain-wide curve is still the slowest one on the chart.** The RNG
  hoist fix (see above) turned out to be a ~1.02x improvement, not the
  ~40x hoped for — benchmarked directly. The real cost is the Longstaff-
  Schwartz regression inside the backward-induction loop, which runs
  ~40 bisection iterations x 60 steps x every strike. Batching that across
  strikes needs per-strike ITM-masked regressions of different sample
  sizes, which doesn't vectorize as cleanly as the tree pricers did (ragged
  arrays). Left as a per-strike loop; a real fix here is a separate,
  bigger job than what CRR/LR/NR got.

## RESOLVED — Heston (2026-07-28): it was calibrating against the WRONG CONTRACT

Diagnosed live against real ThetaData, not guessed. The ranked candidate list
that used to live here was **wrong on the primary cause** — candidate 2
("something in `HestonCalibrator._prepare`, expiry parsing") was closest, but
the problem wasn't a parse failure, it was that `_prepare` was resolving an
expiry *at all*.

**Root cause.** `main.py` resolves the typed target `T` to a concrete listed
expiry exactly once (`expiry_selector.choose_expiry()` → `resolved_exp`,
YYYYMMDD) and threads it into every other model (CRR / LR / NR / SABR / VV /
MC / BAW) and the Market row. Heston alone never got it. `run_heston_full`
recomputed its own target date from scratch
(`now + timedelta(days=int(T*365))`) and `HestonCalibrator._prepare` then ran
its own independent "nearest listed expiration" search. On a chain with
several weeklies close together — normal on any liquid name — that lands on a
different contract than everything else in the report is using.

**Live proof** (TSLA Put K=310, S=309.22, r=0.0408, q=0.0, nominal T=0.0055,
both calls the same minute):

| path | contract | T | strikes | IV range | calibration |
|---|---|---|---|---|---|
| pre-fix `run_heston_full` (re-derived date) | **20260729** | 0.0027 | 127 | 0.4938–4.5430 | xi=3.722, rho=0.990, rmse=0.1623 |
| what every other model + Market used | **20260731** | 0.0055 | 109 | 0.5000–3.2187 | xi=1.122, rho=0.614, rmse=0.0369 |

Different contract, different smile, different parameters. So this was a
**correctness** bug (Heston's price/Greeks keyed to a smile nobody else in the
report was looking at), and only *secondarily* an availability bug — a
mis-selected smile is also more likely to trip the stability guard, which is
how it surfaced as `N/A`.

**Fix.** `HestonCalibrator.__init__` now takes `known_expiry` (YYYYMMDD) and,
when given, uses it directly for both `T` and the smile fetch, skipping the
nearest-expiration search entirely; it **raises** if that expiry isn't
actually listed (no snapping to a neighbour — same no-fallback rule as the
rest of `MCHestonLSM.py`). `run_heston_full` takes a matching `exp=` and
passes it through. Both `main.py` call sites now always pass it: option 5
passes `resolved_exp`, option 9 passes `market_exp`. Verified post-fix:
`run_heston_full(..., exp="20260731")` calibrates on T=0.0055 / 109 strikes /
IV 0.5000–3.2187, identical to the contract everything else uses. Clean
end-to-end no-regression run: SPY 20261030, T=0.2548, K=739 → rmse=0.0043,
price=29.2897, full Greeks populated. Full write-up with numbers in
`PROJECT_ROADMAP.md` (2026-07-28 session).

**Still open, deliberately NOT changed:** the `xi >= 4.5 or rmse > 0.25`
guard (old candidate 1) does fire on some correctly-aligned contracts, purely
on the xi term, with excellent rmse — TSLA 20260731 xi=4.5165/rmse=0.0301,
AAPL 20260821 xi=5.0000/rmse=0.0437 (xi=5.0 is the optimizer's own upper
bound, i.e. pinned, not diverging). Changing a calibration bound is a separate
empirically-justified decision and was not bundled into the expiry fix.
Tracked as `PROJECT_ROADMAP.md` Known Issues **P2b**.

## SPEC (2026-07-28): "full chain" evaluation mode — approved, in progress

Instead of one priced `K` plus a bolted-on smile chart, this adds a mode that
runs every model against every strike in the chain directly — no separate
"single-K table" vs. "chain-wide chart" split. `chain_evaluation.py` is the
foundation: `build_smile_comparison` already does the "solve every model
across every strike" work for IV curves; this generalizes it to also report
price/Greeks per strike, per model.

**Scope decision (explicit, asked of Jason before implementing — MC/Heston's
2nd/3rd-order Greeks are CRN bump-and-revalue on a Monte Carlo LSM, each
costing seconds; doing that at every one of 100-150+ chain strikes would push
a single report to 10-30+ minutes just for those two models' higher-order
Greeks):**

- **Every model, every strike, full accuracy on price + IV + 1st-order Greeks**
  (Delta/Gamma/Vega/Rho/Theta) — CRR/LR/NR/SABR/VV/BAW/Heston/MC all included,
  no shortcuts, using each model's own engine (never a shared/borrowed one,
  same principle as the existing single-K report).
- **MC and Heston's 2nd/3rd-order Greeks (Vanna/Vomma/Speed/Charm/Color)
  are NOT computed at every chain strike.** They're computed only at the
  single focus `K` — identical to what the existing single-K "Run all models
  & compare" report already shows for these two models. The analytic/tree
  models (CRR/LR/NR/SABR/VV/BAW) DO get 2nd/3rd-order Greeks at every strike
  (cheap — LR-tree FD, a "small fraction of a second per model" per
  `PROJECT_ROADMAP.md`'s round-3 Greek-engine section).

**New function**: `chain_evaluation.run_full_chain(ticker, market_exp, S, K,
T, r, q, models, vol_manager, atm_vol_vv, rr25, bf25, option_type)` in
`chain_evaluation.py`. Returns a dict:
```
{
  'strikes': np.array([...]),      # every real chain strike, sorted
  'market': {strike: {'iv':.., 'price':.., 'right':..}, ...},
  'per_model': {
      'CRR': {strike: {'price':.., 'iv':.., 'greeks': {delta,gamma,vega,
                        rho,theta, vanna,vomma,speed,charm,color}}, ...},
      ...
      'MC':      {strike: {'price':.., 'iv':.., 'greeks': {1st-order only}}, ...},
      'Heston':  {strike: {'price':.., 'iv':.., 'greeks': {1st-order only}}, ...},
  },
  'focus_k_greeks': {  # 2nd/3rd-order for MC/Heston at the single reported K only
      'MC': {vanna,vomma,speed,charm,color}, 'Heston': {...},
  },
  'smile': { ... same shape build_smile_comparison already returns ... },
}
```
Reuses every existing batched primitive rather than re-deriving:
`crr_american_price_batch` / `leisen_reimer_american_price_batch` (price),
`brute_force_batch` / `brute_force_lr_batch` (IV), `heston_call_prices_batch`
+ `heston_iv_smile_batch` (Heston), `vanna_volga_vol_batch`,
`_sabr_vol_hagan_vec` — all already exist from the smile-curve work. Per-
strike Greeks loop over each model's own `*_all_greeks` function (already
fast/deterministic per `PROJECT_ROADMAP.md`'s LR-binomial-FD section) — this
is the one genuinely new per-strike loop, since none of the existing batched
helpers compute Greeks, only price/IV.

**No fake numbers on failure** (house convention throughout this project):
a strike that fails for a given model is omitted from that model's dict, not
zero-filled or borrowed from a neighbor. Heston's per-strike calibration
reuses the SAME single calibration `models['Heston']['calib']` already used
for the smile chart (never re-calibrated per strike — recalibrating 100+
times per report would be both wrong, since the smile chart's whole point is
one calibration evaluated across strikes, and prohibitively slow).

**Report**: new report path (extends `reports.py` or a sibling module) with
(1) a per-strike/per-model price+Greeks table (CSV, and a paginated/summary
view in the PDF), (2) the smile chart (model IV curves vs market IV, already
built by `build_smile_comparison`'s logic), (3) same branded
header/traffic-light styling as the existing single-K
`save_comparison_pdf`.

**2026-07-28: done.** `reports.py` gained `save_full_chain_csv` and
`save_full_chain_pdf`:
- **CSV** (`save_full_chain_csv`): one row per (strike, model) pair, every
  column in the existing `_GREEK_COLS` set (blank/NaN where a model didn't
  compute/solve that Greek at that strike — e.g. MC/Heston's 2nd/3rd-order
  away from the focus K), plus the same Ticker/FocusK/Spot/T/Rate/DivYield/
  OptionType/Expiry/GeneratedAt meta columns `save_comparison_csv` already
  writes (renamed `K`->`FocusK` since `Strike` is now a per-row chain
  strike, not the single focus K).
- **PDF** (`save_full_chain_pdf`): a **moneyness-windowed, paginated summary
  table** (default: 5 strikes each side of the focus K, 1st-order Greeks
  only — Delta/Gamma/Vega/Theta — paginated at ~26 rows/page via
  `PdfPages`) followed by the smile-comparison page. Deliberately does NOT
  try to print the full 100+ strike x 9-row (8 models + Market) chain on
  one page or even one PDF section — that's what the CSV is for; the PDF
  is for "is this thing behaving sanely near the money," which is what
  Jason actually asked for. The smile page reuses `_draw_smile_page`
  **unmodified** — `run_full_chain`'s `'smile'` key is the exact
  `build_smile_comparison`-shaped dict already expected there, no
  reimplementation needed. A new `_draw_chain_header` helper mirrors
  `save_comparison_pdf`'s navy header styling (title/subtitle/params) but
  supports a `page_label` since the summary table spans multiple pages.
  Per-strike traffic-light coloring (`_cell_color`) compares each model's
  row against **that same strike's** Market row (not one global Market
  row, since every strike has its own).
  Verified against a hand-built fake `chain_result` dict matching this
  spec's documented shape before `run_full_chain` existed, then re-checked
  directly against the real `chain_evaluation.run_full_chain` (implemented
  by a parallel session) — the real function's dict is a superset of the
  spec (adds `right`/`source` fields and a top-level `meta` key), which
  `reports.py`'s new code tolerates fine since it only reads the
  documented keys.

**Entry point**: its own menu option in `main.py` (not the opt-in prompt used
for the smile-only chart inside `choice == '9'`) — full-chain is a
genuinely different report shape (a table per strike, not one row per
model), not a bolt-on to the single-K flow.

## LIVE VERIFICATION (2026-07-28): done, two issues found and fixed

Two agents built this in parallel (`chain_evaluation.run_full_chain` +
`main.py`/`reports.py` wiring), each independently live-verified their own
half. I then reconstructed a real `chain_result` from the live AMD run's own
checkpoint data (89 strikes, all 8 models, no synthetic values) and fed it
through the ACTUAL `save_full_chain_csv`/`save_full_chain_pdf` shipped code
end-to-end, catching two things neither agent's own narrower testing surfaced:

1. **Missing `Right` column.** A chain window straddles the forward, so a
   model prices whichever side is OTM per strike (put side below forward,
   call side above — same convention `fetch_market_smile` already uses
   throughout this project). Without a per-row `Right` column, Delta
   legitimately flips sign partway down the Strike column with nothing in
   the report explaining why — looked like a bug at a glance (confirmed
   live: AMD K=660 in a K=490 put report showed Delta=+0.27, correct once
   you know that strike priced as an OTM call, confusing without it). Added
   `Right` (C/P) to both `save_full_chain_csv` and `save_full_chain_pdf`.
2. **Header text collision on the PDF's first table page.** `note` (the
   long "showing N of M strikes..." caveat, page 1 only) and `params_line`
   (r/q/generated-at) used to share one row — same class of bug
   `save_comparison_pdf`'s header had once before (see this doc's "Fixed
   this session" history / `PROJECT_ROADMAP.md`'s "three distinct rows"
   fix). Gave them separate rows in `_draw_chain_header`.

Verified after both fixes: CSV round-trips cleanly with the new `Right`
column (`pd.read_csv` + spot-check), PDF renders 5 pages (4 windowed-table +
1 smile) with no header overlap and correct per-strike traffic-light
coloring — screenshots inspected directly, not just "no exception raised."

**Known pre-existing cosmetic nit, NOT introduced today, not fixed:**
`_draw_smile_page`'s own title can crowd its "Calibration strike range: ..."
subtitle line above it on wide legends (visible on the AMD case's smile
page). Neither agent touched `_draw_smile_page` (both were told to reuse it
unmodified), and this predates today's work. Worth a small follow-up
sometime, not blocking.

## FOLLOW-UP (2026-07-28): MC and Heston excluded from full-chain by default -- too slow

Jason flagged the full-chain report as taking too long. Live timing backed
this up precisely: MC=26.1s + Heston=22.5s of the 63.6s total run (~76%),
vs. CRR/LR/NR/SABR/VannaVolga/BAW combined at ~14s for all six.

`run_full_chain` gained `include_mc`/`include_heston` (both default `True`
for any direct caller, so this is additive, not a breaking change). When
`False`, that model is fully absent from `per_model`, `focus_k_greeks`, AND
`smile['curves']` -- not just its Greeks. This mattered for MC specifically:
its smile-chart IV curve is ITS OWN separate ~40-iteration-per-strike
bisection (see "Known limitations" above), independent of the Greeks loop,
so skipping only the Greeks wouldn't have delivered the real speedup.
`main.py`'s `choice == '10'` now passes both as `False`.

**Verified live** (same AMD chain, same setup): `include_mc=False,
include_heston=False` -> **14.0s total** (CRR=3.3s, LR+NR=4.8s, SABR=2.7s,
VannaVolga=2.9s, BAW=0.04s) vs. 63.6s before -- a 4.5x speedup. CSV/PDF both
confirmed to render correctly with just the 6 remaining models (BAW, CRR,
Leisen-Reimer, Newton-Raphson, SABR, VannaVolga) and no MC/Heston columns
or rows anywhere in the output.

**One test-harness note, not a shipped-code bug:** my first verification
pass mistakenly reconstructed the smile chart from only the LAST of several
chunked live-test slices (a sandbox time-limit artifact, strikes 950-1120
only) instead of the full 89-strike chain — the smile page looked broken
(missing Market dots, most model curves absent) purely because of that
reconstruction shortcut, not because of anything in `run_full_chain` or
`save_full_chain_pdf` itself. Rebuilding the smile over the full chain (via
`chain_evaluation.build_smile_comparison` directly) produced the correct
7-curve chart shown above. Flagging so a future session doesn't waste time
re-diagnosing the same non-bug.

**2026-07-28: done.** Added as menu choice `10` ("Full chain evaluation
(every model, every strike)"). The ~200-line per-model price/Greeks/market
gathering block that used to live inline in `choice == '9'` was factored
out into `_gather_all_models_and_market(...)` (a straight move, same
order/logic/prints, including the Heston expiry-alignment fix) so both
`choice == '9'` and `choice == '10'` call it instead of duplicating it.
`choice == '10'` then calls `chain_evaluation.run_full_chain(...)` with
that same `models`/`vol_manager`/`atm_vol_vv`/`rr25`/`bf25`, and offers to
save the result via `save_full_chain_csv`/`save_full_chain_pdf`. The menu
print string, the `input("Choice (1-10): ")` prompt, and the
`else: raise ValueError(...)` message were all updated from "1-9" to
"1-10"; the `elif choice != '9':` guard in the single-model price/Greeks
block was widened to `elif choice not in ('9', '10'):` so choice 10 doesn't
fall through and try to price with `sigma=None`.

Not yet done: a real end-to-end live run (ThetaData + all 8 models + PDF/CSV
save) was not performed in this session — the sandbox used to build/verify
this had no ThetaData terminal reachable. Rendering code was verified
against a hand-built fake `chain_result` dict (CSV: 63 rows x 25 cols
written and re-read cleanly with `pd.read_csv`; PDF: multi-page file
rendered, ~71KB, no exceptions) and the interface was cross-checked
directly against the real, already-implemented `chain_evaluation.py`
source (not guessed). Whoever runs this live next should confirm the
`choice == '10'` path end-to-end on a real ticker and note the actual
strike/model counts and timing here.
