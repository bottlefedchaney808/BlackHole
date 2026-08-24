# 📈 Project Roadmap

## 📋 Next Up

Nothing scoped right now. Candidates for a future session if something isn't
matching Market on the run-all report:

- **Heston higher-order Greeks (Vanna / Vomma / Speed / Color / Charm) are now
  Heston-native**, computed via nested common-random-number (CRN) bump-and-revalue
  on the Heston LSM (`MCHestonLSM.heston_all_greeks`, `_heston_lsm_price_crn`,
  `run_heston_full`) rather than falling back to closed-form Black-Scholes. Same
  limitation as before still applies to MC and CRR for their own higher-order
  Greeks: MC because its LSM has the same regression issue, CRR because its
  u/d/p triple is a step function of sigma and 2nd sigma FD picks up
  tree-node-alignment noise as false signal. LR/NR are the only tree models
  where FD 2nd-order sigma works cleanly, and SABR/VV use closed-form BS at
  their smile-implied sigma at K.
- **Extend BAW's IV solve to a smile fit** if it ever becomes useful to have a
  full BAW smile row on the chart (currently BAW only solves per-strike). Would
  parallel what the tree models already do via `smile_utils.fetch_market_smile`.
- **Vectorize the run-all compare block**: each of the 8 models sequentially fetches
  its sigma and prices; some of that could run in parallel (Heston especially takes
  seconds because of its multi-start calibration + LSM path pricing).

## ✅ Completed (This session: 2026-07-28 -- Heston expiry alignment)

**Root cause of Heston's intermittent `N/A` / wrong-smile behavior: Heston was
the ONE model still re-deriving its own expiry instead of using the resolved
listed contract.** `main.py` resolves the typed target `T` to a real listed
expiry exactly once (`expiry_selector.choose_expiry()` -> `resolved_exp`,
YYYYMMDD) and threads it into CRR, LR, NR, SABR, VV, MC, BAW and the Market
row. `run_heston_full()` never received it: it recomputed
`expiry_date = now + timedelta(days=int(T*365))` and `HestonCalibrator._prepare`
then ran its OWN "nearest listed expiration" search against
`ThetaDataController.list_expirations()`. On a chain with several weeklies
close together, that independent re-derivation lands on a different contract.

This is the same root pattern as the already-fixed "T was never reconciled to
the REAL listed contract" issue (see "PARTIALLY RESOLVED: Rho still ~17-20% off
Market" below) -- a model re-deriving an approximate date instead of using the
one real contract everything else already agreed on. Different model, same bug
class.

**Live proof (2026-07-28, TSLA Put K=310, S=309.22, r=0.0408, q=0.0, nominal
T=0.0055 -- both calls same minute, same market):**

| path | contract used | T | strikes | IV range | calibration |
|---|---|---|---|---|---|
| `run_heston_full(...)` pre-fix (re-derived date) | **20260729** | 0.0027 | 127 | 0.4938 - 4.5430 | kappa=0.072, theta=1.122, xi=3.722, rho=0.990, v0=0.3297, rmse=0.1623 |
| `HestonCalibrator(ticker, "2026-07-31")` (what every other model + Market used) | **20260731** | 0.0055 | 109 | 0.5000 - 3.2187 | kappa=1.501, theta=0.894, xi=1.122, rho=0.614, v0=0.3320, rmse=0.0369 |

Two different contracts, two different smiles, two completely different
parameter sets -- Heston's reported price/Greeks were keyed to a smile nobody
else in the report was looking at. Correctness bug, not just noise.

**The fix (one logical change):**
- `HestonCalibrator.__init__` takes `known_expiry: Optional[str] = None`
  (YYYYMMDD). When given, `_prepare` uses it DIRECTLY for both `T` and the
  smile fetch and **skips the nearest-expiration search entirely** -- there is
  nothing to search for when the real contract is already known.
- If `known_expiry` isn't in `list_expirations()`, it **raises** (no snapping to
  a neighbour) -- consistent with this module's no-fallback convention.
  Verified live: `known_expiry="20261031"` on SPY raises
  `[HestonCalib] known_expiry=20261031 is not a listed expiration for SPY ...`.
- `run_heston_full()` takes a matching `exp: Optional[str] = None` and passes it
  through.
- Both `main.py` call sites now always pass it: option `5` passes `resolved_exp`,
  option `9` passes `market_exp` (which itself came from `resolved_exp`, then
  reconfirmed by `td.fetch_option_iv(..., exp=resolved_exp)`).
- Parameter is optional purely for backward compatibility; omitting it keeps the
  legacy re-derive behavior.

**Post-fix verification (live, 2026-07-28):**
- `run_heston_full("TSLA", ..., exp="20260731")` now prints
  `[HestonCalib] Using caller-resolved listed expiry 20260731 (no re-derivation)`
  and calibrates on T=0.0055, 109 strikes, IV 0.5000-3.2187 -- byte-for-byte the
  same inputs as the direct `HestonCalibrator(ticker, "2026-07-31")` call, i.e.
  the same contract every other model uses. No more 20260729.
- Clean end-to-end, no regression: SPY exp=20261030, T=0.2548, S=739.12, K=739,
  r=0.0408, q=0.0102, call ->
  `kappa=2.9783, theta=0.1161, xi=1.5469, rho=-0.7392, v0=0.003902, rmse=0.0043`,
  **price=29.2897**, Greeks delta=0.6023, gamma=0.016347, vega=143.2026,
  rho=110.6137, theta=-0.084165, vanna=-1.40409, vomma=148.8963,
  speed=-0.000212, charm=-0.188218, color=0.034727.

**NOT changed (deliberately) -- see Known Issues P2b below**: the `xi >= 4.5 or
rmse > 0.25` instability guard and the optimizer's `xi` upper bound of 5.0 were
left exactly as they are. Two post-fix runs tripped that guard purely on the xi
term with excellent fits (TSLA 20260731: xi=4.516, **rmse=0.0301**; AAPL
20260821: xi=5.000, **rmse=0.0437**), which is worth investigating -- but
changing a calibration bound is a separate, empirically-justified decision, not
something to bundle into an expiry fix.

## ✅ Completed (Prior session: 2026-07-27 -- per-model Greek engines, BAW added, Heston multi-start)

The core demand this session was Jason's re-assertion of "each model needs to
solve for each Greek, each IV, EVERYTHING on its own", after a previous session
had been claiming that was done but a comparison report showed Delta / Gamma /
Vega reading byte-for-byte identical across CRR / LR / NR / SABR / VV / MC --
because every model routed through the same `american_all_greeks()` (Leisen-
Reimer tree FD) regardless of what "its own pricer" was supposed to be. Also
Heston was N/A on that report (single-start calibration failed on AMD Put 480),
and Vanna / Vomma were showing spurious signs from tree-FD-at-narrow-bump.

**Refactor: each model has its OWN Greek engine now.** Every one of these prices
via its own pricer AND differentiates via that same pricer, no cross-model
borrowing. Called from `main.py`'s compare block (option 9) and per-model
single-run branches (options 1-8):

- [x] **`crr_all_greeks`** (`american_binomial.py`): FD on `crr_american_price`,
      not the LR tree. Uses a wider `dS_frac=0.03` bump than LR's 1% because CRR's
      u/d/p triple oscillates in S at small bumps, giving spurious low Gamma
      (verified: dS=1% gave Gamma=+0.00098 on AMD Put 480; dS=3% gives +0.00295
      matching the analytical reference). Uses closed-form BS Vanna / Vomma at
      CRR's own sigma because CRR's tree can't be differentiated twice in sigma
      reliably (bump-swept from 5% to 30%; vomma stayed at -1.02 across every
      bump width -- fundamental to CRR's non-smooth-in-sigma discretization,
      not fixable by tuning).
- [x] **`lr_all_greeks`** (`american_binomial.py`): FD on
      `leisen_reimer_american_price`. Used by both Leisen-Reimer (menu 6) and
      Newton-Raphson (menu 4) since both price through the LR tree. Standard
      dS_frac=1% (LR's Peizer-Pratt inversion is smooth-in-S). 2nd-order sigma
      Greeks (Vanna, Vomma) via FD on the LR tree with `dSig_2nd_frac=0.15`
      (much wider than 1st-order 2% -- 2nd derivatives are inherently noise-
      limited on any discrete pricer). Verified converged at that bump on
      AMD 480: Vomma=+0.0364 matching BS reference +0.0365.
- [x] **`mc_all_greeks`** (`MC.py`): CRN bump-and-revalue on
      `AmericanLSMPricer.price_with_rand` with a fixed random-seed array reused
      across every bump direction -- so LSM regression noise CANCELS between
      `p(+bump)` and `p(-bump)`. Wider bumps than trees (`dS=3%`, `dSig=5%`,
      `dR=200bp`) because even with CRN, LSM's continuation-value regression
      isn't smooth in bumped variables at trees' narrow bumps. Verified at
      50k sims: Gamma=+0.00214 (LR's is +0.00298 -- MC has its own LSM
      regression-response to bumps that LR doesn't). Vanna / Vomma fall back
      to closed-form BS at MC's sigma -- attempted CRN FD gave Vomma=-1.03 at
      any bump width because CRN cancels shock noise but NOT the regression
      polynomial's own discontinuous shift across sigma.
- [x] **`sabr_all_greeks`** (`SABRModel.py`): Smile-AWARE FD. Each bump of S / T / r
      re-evaluates the Hagan formula at the bumped state and re-prices through
      the LR tree at THAT bumped smile-implied sigma. Vega bumps SABR's `alpha`
      parameter (the "ATM level" parameter of SABR) and chain-rules to a
      sigma-equivalent for the reported Vega magnitude. Uses the calibration
      cached in `vol_manager.last_sabr_calibration` -- doesn't recalibrate.
      Verified on the AMD case: Delta shifts by 0.012 from the smile-slope
      response (was 0 before this refactor because SABR was using
      `lr_all_greeks` at a flat sigma). Vanna / Vomma via closed-form BS at
      SABR's own smile-implied sigma at K.
- [x] **`vv_all_greeks`** (`VannaVolga.py`): Smile-AWARE FD via the 3-pillar
      VV smile. Every bump re-interpolates `get_vol(...)` at the bumped state
      and reprices through the LR tree. Vega bumps `atm_vol` (holding RR/BF
      fixed -- how a VV trader actually hedges). Verified: Delta shifts by
      0.023 from the smile-slope response on AMD. Vanna / Vomma via closed-form
      BS at VV's smile-implied sigma at K.
- [x] **`heston_all_greeks`** (`MCHestonLSM.py`): CRN bump-and-revalue on
      `_heston_lsm_price_crn` (new function that accepts pre-generated Z1 /
      Z_prime arrays) using ALL calibrated params (kappa, theta, xi, rho, v0).
      Vega = dP/dV0 chain-ruled to dP/dsigma via sigma = sqrt(V0), so units
      match the other models' Vega. Replaces the previous shortcut where
      `compute_greeks_heston` mapped Heston down to `effective_sigma = sqrt(V0)`
      and passed that to `american_all_greeks` -- which meant kappa / theta / xi /
      rho contributed NOTHING to any reported Greek despite being the whole
      point of Heston. Vanna / Vomma / higher-order fall back to closed-form BS
      at effective_sigma because Heston LSM has the same 2nd-order sigma
      instability as MC LSM.
- [x] **`baw_all_greeks`** (`barone_adesi_whaley.py`, new file): FD on the
      analytical Barone-Adesi-Whaley 1987 pricer -- noise-free because BAW is
      itself closed-form, no tree / no sim. This is the 8th model: analytical
      "American Black-Scholes". Was added specifically to test the hypothesis
      "does the vendor's Market Greek feed use a BS-based analytical American?".
      Result on AMD Put 480: BAW's Delta / Vega / Rho / Theta match Market to
      within 0.4%, while the tree-FD Rho was off by 12% at that same strike.
      Strong signal that yes, the vendor is using BAW or equivalent for its
      real-time Greek calculation.

**Heston: SABR-style multi-start restart added** (previously flagged in the
prior session's notes as the known-not-yet-fixed source of Heston fragility on
high-vol names).

- [x] `HestonCalibrator.calibrate()` now sweeps a 37-point (kappa, xi, rho) seed
      grid, keeps the caller's suggested initial as one of those seeds, runs
      `minimize()` from each, and returns the lowest-loss convergence. Verified
      on synthetic AMD-like smile: 31-35 of 37 restarts converge, rmse ~1e-5,
      recovers correct rho/xi even from an adversarial caller seed
      (kappa=0.5, xi=5.0, rho=+0.9, v0=0.1 -- everything wrong).

**Menu / wiring changes**:

- [x] `main.py` menu is now `(1) CRR ... (7) Monte Carlo (LSM), (8) Barone-
      Adesi-Whaley (American BS), (9) Run all models & compare`. Run-all moved
      from 8 to 9.
- [x] Every `american_all_greeks(...)` call in the compare block and the
      per-model branches replaced with the model-specific engine. `american_all_greeks`
      itself is kept as a backwards-compat alias for `lr_all_greeks`.
- [x] `vol_manager.get_sigma(method='BAW')` uses `brute_force_baw` (new
      bisection solver in `barone_adesi_whaley.py`).

**Vanna / Vomma diagnostic detour** (worth remembering for future sessions):

- The previous session's `american_all_greeks` used tree-FD at 2% sigma bump for
  all higher-order Greeks. On AMD Put 480 (near-ATM, high-vol, short-T -- exactly
  where d2 is close to zero) that produced Vomma=+0.005 vs. the analytical BS
  reference of +0.036 (7x underestimate), and MC's Vomma flipped sign to -0.067
  because its wider sigma bump straddled a sign-flip in d2. Root cause is
  fundamental: 2nd finite differences take an extra `/dSig` factor while tree
  quantization noise stays constant, so the noise/signal ratio blows up at any
  bump width in this regime. Landed on: LR/NR use FD-on-LR-tree (works, converges
  at 15% bump); everything else uses closed-form BS at THAT model's own
  calibrated sigma (still model-specific because each has its own sigma, just
  not FD-differentiated on that model's own pricer). Documented in every engine's
  docstring so the tradeoff is visible where it's being applied.

## ✅ Completed (Prior session: vanna/charm stabilization + Leisen-Reimer implementation)

**Part 1 -- vanna and charm were the last unstabilized 2nd-order Greeks.** The prior
session's vomma/speed/color fix (multi-seed CRN averaging + widened bumps, see below)
deliberately left vanna untouched, and charm didn't exist yet when that fix landed.
User reported "all the models' 2nd level greeks are still pretty not as good as they
need to be" -- investigation confirmed vanna was the main culprit:

- [x] **Reproduced the problem empirically** before touching code: ran `MC.py`'s
      `calculate_greeks()` for Standard/NR/VannaVolga at near-identical sigmas (<0.5%
      apart, an ATM call and a separate OTM-put/dividend/longer-T case). Vanna
      sign-flipped and swung 3-14x across the three models in both cases (e.g. ATM
      call: -0.0593 / +0.0135 / +0.0263) -- it was still on a single CRN draw with the
      old `max(sig*0.15, 0.03)` bump, the same failure mode vomma/color/speed had
      before their fix. Charm (already multi-seed averaged, but with a narrow
      `dS_delta`-sized inner bump) showed the same sign-flipping pattern, just less
      severely.
- [x] **Fixed vanna**: widened the bump to `max(sig*0.5, 0.08)` (from `max(sig*0.15,
      0.03)`) and moved it into the same 3-seed CRN-averaging loop vomma/speed/color/
      charm already use, in both `MC.py` and `MCHestonLSM.py` (2 seeds there, matching
      the existing Heston seed count). Verified: both test cases now give vanna
      consistently signed and within 2-21% of each other across models, instead of
      sign-flipping. Heston's vanna reuses the already-wide `dSig_vomma` bump for its
      sigma leg rather than introducing a third bump constant.
- [x] **Fixed charm's inner delta bump**: switched from `dS_delta` (0.5% of spot) to
      `dS_gamma` (2% of spot) for the two nested delta calculations charm differences
      against T. Empirically the narrow bump gave the same sign-flipping problem vanna
      had; the wider one gave a tight, consistently-signed result in testing.
- [x] **Added charm to `MCHestonLSM.py`**, which never computed it at all (only `MC.py`
      did) -- now computed in the same multi-seed loop as vomma/speed/color there, and
      included in the returned greeks dict.
- [x] **Added Charm to `reports.py`'s comparison table** (`_GREEK_COLS`, `_PRECISION` at
      6dp) -- it was already being computed by `MC.py` and shown in `main.py`'s
      single-model FINAL RESULTS printout, but silently dropped from the run-all
      comparison table/CSV/PDF.
- [x] Re-verified after the fix: full offline test (real `MC.py`, no stubs) across both
      scenarios confirms vanna/vomma/color/speed/charm are all consistently signed and
      within a tight band across Standard/NR/VannaVolga-equivalent sigmas. A synthetic-
      pricer wiring test on `MCHestonLSM.compute_greeks_heston` (scipy/yfinance stubbed
      out, since this sandbox has neither installed) confirmed the loop bookkeeping,
      `idx` counters, and new vanna/charm sample lists are all correct with no
      exceptions. Kept seed counts as-is (3 for `MC.py`, 2 for Heston) -- already tight
      enough after the bump-width fix that more seeds weren't worth the extra compute.

**Part 2 -- Leisen-Reimer binomial tree**, implemented per the plan this roadmap had
already scoped out (see the prior version of this section, preserved below in spirit):
a genuinely separate comparison model alongside CRR, not a replacement.

- [x] **Implemented `leisen_reimer_american_price()` in `american_binomial.py`**:
      Peizer-Pratt inversion (method 2, the more accurate variant with the `0.1/(n+1)`
      term) computes `p` from `d2` and a separate `p'` from `d1`; `u`/`d` derived from
      `p'` and the risk-neutral growth factor, `p` used as the actual tree probability
      in backward induction. Step count silently coerced to the next odd number (LR
      requires odd `n`; even `n` doesn't error but biases the price). Formula
      cross-checked against a secondary source (macroption.com/leisen-reimer-formulas)
      rather than implemented from memory alone, per the caution this roadmap entry
      flagged when it was first scoped.
- [x] **Verified correctness and convergence behavior** (offline, no network needed --
      pure numpy): at 201 steps, LR matched the closed-form Black-Scholes European
      price (non-dividend call, deep steps so early exercise ≈ never optimal) to
      -0.000012 absolute error, vs. CRR's +0.011809 at the same step count -- roughly
      1000x more accurate at equal cost. Convergence-vs-steps table (21 to 401 steps,
      ATM call) showed LR essentially flat/converged from ~91 steps on, while CRR was
      still monotonically drifting toward the true value at 401 steps. A second case
      (OTM call, steps 51-151) showed CRR's classic up-then-down oscillation pattern
      directly, while LR stayed flat to within 0.0001 across the same range. Put +
      dividend case also checked. Odd-step coercion verified exact (steps=200 and
      steps=201 produce bit-identical output).
- [x] **Wired into the full pipeline**: `bruteforceimpliedvol.py` gained
      `brute_force_lr()` (refactored the bisection core out of `brute_force` into a
      shared `_brute_force_bisect(pricer, ...)` so both solvers share one
      implementation); `vol_manager.py` gained `method='LeisenReimer'`; `main.py`
      gained menu option (7) and a `models['Leisen-Reimer']` entry in the "Run all &
      compare" block (6), sitting right after Standard since they're the two
      binomial-tree methods; `reports.py` needed no changes (already generic over
      model names). IV round-trip verified offline: solving for sigma against a known
      LR price recovers the original sigma to ~1e-6.

## ✅ Completed (Follow-up session: vomma/color/speed stabilization)

Scoped explicitly to vomma, speed, and color only, per instruction to leave
delta/gamma/vega/rho/theta/vanna alone ("they are supposed to differ some, that's
the point").

- [x] **Checked the user's own reference spreadsheet** (`SABR Vol smile .xlsm`, sheet
      `'2nd level '`) for the expected sign/scale convention on second-order Greeks.
      Vanna/Vomma/Speed formulas there match our unit convention already (raw
      per-unit-vol / per-$1-of-spot, no /100 scaling), so the vomma/speed instability
      wasn't a units bug.
- [x] **Initially removed the `/365` from Color**, reasoning that the spreadsheet's
      Color formula (cell B23) lacks a `/365` the way its Theta formula (B19) has one.
      **This was wrong and has been reverted.** Color and Theta differentiate w.r.t. the
      same T (denominated in years), so both raw derivatives are naturally per-year for
      the same reason, and both need the identical per-year-to-per-day conversion --
      Color is "how much does gamma change by tomorrow," the direct second-order analog
      of Theta's "how much does price change by tomorrow." Separately, and independent
      of that reasoning: the spreadsheet's B23 cell was verified to have an actual
      denominator bug (`S^2` where the textbook closed-form calls for `2*S*T`) --
      reproducing it in Python matched the sheet's cached value exactly, but comparing
      against a from-scratch numerical derivative of Black-Scholes Gamma showed the
      sheet's number is off by ~28x, and the ratio matches `S/(2T)` from that
      exact typo. So its missing `/365` was never legitimate evidence for anything --
      that cell simply isn't reliable. `/365` is restored in both `MC.py` and
      `MCHestonLSM.py`; color now lands around 0.000012-0.000015/day in the live QQQ
      test case (see Known Issues for the remaining magnitude/sign question vs. market).
- [x] **Root-caused the vomma/color instability**: it isn't just CRN noise -- the LSM
      regression's continuation-value fit is only piecewise-smooth in sigma/T (points
      cross the ITM boundary discretely), so a single-seed second-difference can be
      wildly wrong. Tested directly: same inputs, 7 different CRN seeds gave vomma
      values from -707 to +93, including sign flips. More simulation paths alone barely
      helped (tested up to 300k paths, still swinging -12 to +48).
- [x] **Fixed with two changes together** (neither alone was sufficient):
      1. Average vomma/speed/color over 3 independent CRN seeds (2 for Heston, given
         its heavier per-run cost) instead of 1.
      2. Widen the vomma bump from `max(sig*0.15, 0.03)` to `max(sig*0.8, 0.15)`, and
         the color bump from `max(T*0.08, 0.02)` to `max(T*0.25, 0.05)`. Empirically
         tuned: at the old bump size, Standard/NR/VannaVolga (sigmas within 0.5% of
         each other) gave vomma 8-70x apart with inconsistent signs; at the new bump +
         3-seed average, a live-settings test (sims=50000, steps=100) gave vomma
         25.18 / 23.63 / 25.05 across the three models -- consistent sign, within ~6%
         of each other. Color (with `/365` restored, see above) came out
         0.00001438 / 0.00001491 / 0.00001241 -- same sign, within ~20% of each other.
         Speed was already stable at the old bump (-0.001263 / -0.001261 / -0.001261)
         and didn't need widening.
      This trades some finite-difference bias (the wider bump averages over more
      curvature than a strictly local derivative) for something actually usable --
      the old "local" estimate was unusable noise, not a more-accurate signal.
- [x] Cleaned up a latent bug in Heston's greeks: the original `speed` calculation
      reused `dS_gamma`-bumped prices left over from the gamma calculation rather than
      the (already-defined-but-unused) `dS_speed` bump -- meaning Heston's speed wasn't
      actually using its intended bump size. Now computed properly alongside the
      multi-seed vomma/color loop, matching `MC.py`.
- [x] **Increased report precision for Vomma/Color/Speed to 6 decimal places**
      (`reports.py`) -- these three run at magnitudes (as low as ~1e-3 to 1e-5) where
      the old 4-decimal formatting could round a real, meaningful value down to a
      misleading "0.0000".
- [x] Verified in sandbox: full `AmericanLSMPricer.calculate_greeks()` run at
      production settings (sims=50000, steps=100) confirms delta/gamma/vega/rho/theta/
      vanna are bit-identical to before this change (same single-seed code path,
      untouched), and the report/CSV writers render correctly with the new values and
      precision. `MCHestonLSM.py` was verified for syntax only -- scipy isn't available
      in this sandbox, so the Heston pipeline itself needed a live re-check (see below).
- [x] **Live validation (QQQ, strike $710, T=0.33y, call)**: user ran the actual "run
      all & compare" report on their machine. Vomma landed at 8.53 (Standard) / 7.94
      (Newton-Raphson) / 4.25 (SABR) / 9.57 (Vanna-Volga) -- Standard and NR (the pair
      that was previously 8x apart with opposite signs) are now within ~7% of each
      other, and all four are the same sign and same order of magnitude. Speed was
      tight across every model (-0.001123 to -0.001141). This confirmed the vomma/speed
      fix works in production; neither is affected by the color `/365` question below.
      Heston's row matched Standard's exactly, confirming the unstable-calibration
      fallback path (see `HESTON_BRUTEFORCE_REMINDER.md`, updated this session) fired
      correctly. Report rendered cleanly with no header/table overlap.
      Note: this run's Color values (0.0044-0.0066) were captured *before* the `/365`
      revert described above -- they're the un-normalized, per-year numbers. With
      `/365` restored, the same trade produces Color around 0.000012-0.000015/day (see
      the sandbox reproduction above), still consistently signed across models and no
      longer landing on "0.0000" at the report's 6-decimal precision.

## ✅ Completed (Follow-up session: theta, 2nd-order Greeks, SABR/Heston strike selection)

- [x] **Theta and color were computed with the wrong sign and wrong time-scale
      everywhere** (`MC.py` and `MCHestonLSM.py`). Both were `+dV/dT_remaining`
      (annualized) instead of the standard convention: `-dV/dT_remaining` per
      *calendar day*. This is why theta looked like "+75/year" instead of a small
      negative daily-decay number. Fixed sign and added the `/365` conversion in both
      files. Verified against the actual QQQ market snapshot: model theta went from a
      nonsensical `+75.1` to `-0.2006`, within 4.4% of the market's `-0.1920`.
      Heston's theta already had the correct sign (just needed the `/365`); its color
      had the same sign bug as MC.py's, now fixed identically.
- [x] **Heston's gamma was computed with the wrong bump size** -- it reused the
      delta-sized 0.5%-of-spot bump instead of the 2%-of-spot bump used for
      gamma/speed/color, which is why it came out as `0.0001` (vs. ~0.003-0.008 from
      every other model on the same trade). Restructured `compute_greeks_heston` so
      delta uses its own small bump and gamma/speed/vanna/color share the wider
      gamma-sized bump, matching `MC.py`'s design.
- [x] **2nd/3rd-order Greek bump sizes widened with an absolute floor** (vomma/vanna:
      `max(sigma*0.15, 0.03)` up from `sigma*0.10`; color: `max(T*0.08, 0.02)` up from
      `T*0.05`; speed: `S*0.03` up from `S*0.02`) in both `MC.py` and
      `MCHestonLSM.py`. Dividing by a small bump squared/cubed massively amplifies any
      residual non-smoothness in the LSM regression-based price surface (a known
      characteristic of regression-based American Monte Carlo, not something CRN alone
      fixes). Verified empirically: running Standard/NR/SABR/VannaVolga's very similar
      sigmas (0.231-0.270) through the same trade, vomma went from wildly inconsistent
      (18.7, 50.1, -71.8, 208.3 -- no two models agreeing on even the sign) to a much
      tighter, mostly-consistent band (1.7, 3.5, -1.2, 76.0). Vanna, already reasonable,
      stayed reasonable. Speed became nearly identical across all four models
      (-0.00115 to -0.00116). This is a real, measured improvement, not a full fix --
      it's the well-known "P2: Second-order Greeks noisy" limitation, now mitigated
      rather than eliminated. A complete fix would mean pathwise/likelihood-ratio
      Greeks instead of bump-and-revalue for the LSM engine (Level 1 future work).
  - Caveat: color's magnitude still doesn't closely match the market's reported color
    in the one live example checked (model ~1e-5/day vs market -0.0057). Sign is now
    at least consistent with theta's convention. This may be a genuine vendor
    definition difference for color specifically (far less standardized across data
    providers than delta/gamma/theta/vega) rather than a bug -- flagged as unresolved.
- [x] **SABR and Heston's ThetaData strike-fetch mixed calls and puts indiscriminately**
      for every strike (the bulk-greeks endpoint returns both sides; the parsing code
      wasn't filtering by `right` at all), double-counting each strike with two IVs
      that can disagree. Fixed in `SABRModel.py` (both the ThetaData and yfinance
      fallback paths) and `MCHestonLSM.py`'s `HestonCalibrator` to keep only the OTM
      side per strike (put IV for K<=forward, call IV for K>forward) -- standard
      smile-construction convention, and consistent with how liquidity/reliability
      actually concentrates in real option quotes.
- [x] **Heston's ATM-centering picked the wrong strikes entirely.** It selected `mid =
      len(sorted_strikes)//2` -- the middle *array index* of whatever strikes the API
      happened to return -- rather than the strike nearest the forward. Observed in
      the wild: for a $695 QQQ with a ~$705 forward, this selected calibration strikes
      750-790, nowhere near ATM, which alone would explain a poor calibration fit
      (rmse=0.24 in that run). Fixed to select the N strikes nearest the forward by
      actual distance, in both the ThetaData and yfinance-fallback branches.
- [x] **SABR's free-beta pass would override the safe fixed-beta=0.5 baseline for
      *any* RMSE improvement, no matter how tiny** -- observed in the wild: beta moved
      from 0.5 to 0.12 (a huge, financially implausible move for an index ETF) for a
      0.02% relative RMSE improvement (0.039001 -> 0.038927), which is optimizer noise,
      not a genuinely better fit. Added a threshold: free-beta is only trusted if it
      beats fixed-beta by at least 8% relative RMSE, otherwise the safe fixed-beta
      result is kept. This was likely a real contributor to "SABR isn't right."
- [x] **Report layout: three separate text collisions fixed** in `reports.py`'s PDF
      header/table/chart -- (1) the title and the ticker/strike/spot/expiry subtitle
      shared one row and collided when the subtitle was long enough (restructured into
      three distinct rows); (2) the "Model" column was too narrow for longer names like
      "Newton-Raphson" (given explicit, wider column width); (3) the "Market: $X"
      chart annotation could land on top of a bar's own value label whenever that
      model's price was close to market (moved to a fixed top-left position with
      extra headroom instead of an auto-placed legend).

## ✅ Completed (Prior session)

### ThetaData Integration
- [x] Connected to `api.potatohedge.com` ThetaData proxy
- [x] Cloudflare Access authentication working
- [x] Stock snapshots (quote, trade)
- [x] Option snapshots with full Greeks (first, second, third order)
- [x] Implied volatility extraction from chain
- [x] Expiration and strike listing
- [x] `thetadata_controller.py` module created
- [x] Integrated into `vol_manager.py` as primary data source
- [x] `yfinance` fallback when ThetaData unavailable

### Core Engine
- [x] Longstaff-Schwartz LSM with dynamic polynomial degree
- [x] Common Random Numbers (seed 42) for Greek stability
- [x] First-order Greeks: Delta, Gamma, Vega, Rho, Theta
- [x] Second-order Greeks: Vanna, Vomma, Color, Speed
- [x] Optimized bump sizes per Greek (0.5%–10%)

### Volatility Models
- [x] SABR calibration with multi-start L-BFGS-B
- [x] Vanna-Volga with correct 25-delta strike inversion
- [x] VV auto-calc (fetches RR25/BF25 from market chain)
- [x] Standard IV solver rebuilt on a CRR American binomial tree (`american_binomial.py`),
      replacing the old European-Black-Scholes-based brute force. This is the deliberate
      differentiator from Newton-Raphson: Standard now correctly captures early-exercise
      value (verified: American put/call prices exceed their European counterparts by the
      expected premium, deep-ITM put converges to intrinsic, IV round-trips exactly against
      a CRR-generated market price). Newton-Raphson stays fast/European by design — useful
      as the quick/approximate solver, with Standard as the accurate-but-slower one.
- [x] Newton-Raphson IV solver — now wired into `main.py` as menu option 4 and into the
      "Run all models & compare" flow (previously implemented but never exposed)

### Bug Fixes (this session)
- [x] `black_scholes_func`/`vega` (NewtonRaphsonIV.py) were missing the dividend yield `q`
      entirely — every European reference price, the Standard/brute-force IV solve, and the
      IV used inside Heston calibration were pricing as if q=0 regardless of the fetched
      dividend yield. Fixed: `q` is now a real parameter threaded through
      `black_scholes_func`, `vega`, `implied_volatility_nr`, `brute_force`, and the Heston
      calibration's IV conversion.
- [x] `vol_manager.fetch_market_iv_from_chain` hardcoded `T=0.25` when querying ThetaData,
      so any request for a maturity other than ~3 months silently pulled the wrong expiry's
      market price/IV to calibrate Standard/SABR/NewtonRaphson against. Fixed to use the
      actual requested T.
- [x] `implied_volatility_nr` now falls back to bounded bisection (and reports a
      `converged` flag) instead of silently returning a non-converged sigma when vega is
      near zero (deep ITM/OTM).
- [x] `models[...]` entries in the "run all & compare" flow weren't storing `sigma`, so
      the CSV/PDF reports always showed a blank Sigma column — fixed for all methods.
- [x] `except` branch in main.py's "run all" ThetaData fetch didn't set `market_exp`,
      which would have thrown `NameError` if referenced later — now defaults to `None`.
- [x] Heston Greeks (`compute_greeks_heston`) reused a fixed seed across every bump
      scenario instead of the old `time.time()`-derived seed per call, restoring Common
      Random Numbers. Verified: the same bumped delta now reproduces bit-for-bit across
      repeated runs (0.61266842 == 0.61266842), vs. materially different values (0.466 vs
      0.613) under the old independent-seed approach — that gap was pure MC noise
      contaminating every Heston Greek, worst on the second-order ones.
- [x] `_simulate_heston_paths` hardcoded dividend yield to 0 in the drift term (`q` wasn't
      even a parameter) even though `heston_lsm_price` received a real `q`. Fixed and
      verified: pricing the same call at q=0.00 vs q=0.05 now gives materially different,
      correctly-ordered prices (9.36 vs 8.01) instead of being dividend-blind.
- [x] **SABR ATM error (~2.3%) fixed at the root.** Two changes: (1) the ATM vol used to
      seed/target calibration was `median(market_vols)` across the whole ~9-strike window,
      not the actual ATM point — swapped for the market vol at the strike nearest the
      forward. (2) `alpha` is no longer a free least-squares parameter that trades off ATM
      fit against the rest of the skew; it's now solved analytically via bisection against
      the closed-form Hagan ATM formula at every candidate (beta, rho, nu), so the ATM point
      matches by construction. Verified: 23 synthetic test cases across beta ∈ [0.1, 1.0],
      rho ∈ [-0.4, 0.4], nu ∈ [0.3, 0.9], and both a standard $100 forward and a high-vol
      meme-stock-scale forward ($20, vol=0.9) — all hit target ATM vol to within 0.001 bps.
      A full synthetic-smile recovery test (known true alpha=0.42, beta=0.6, rho=-0.35,
      nu=0.55) recovered beta=0.591, rho=-0.349, nu=0.550, alpha=0.440 with RMSE ~7e-7.
- [x] SABR beta is now optionally calibrated (`SABRCalibrator.calibrate(calibrate_beta=True)`,
      the default via `vol_manager`). A fixed-beta=0.5 pass always runs first as a safe
      baseline; a second free-beta pass (bounded [0.1, 1.0], its own multi-start grid) only
      overrides it if the free-beta RMSE is actually lower — freeing beta from a single
      smile snapshot is known to be poorly identified against rho, so this guards against
      it making the fit worse. Both passes print their RMSE so you can see which won.
- [x] `thetadata_controller.py` no longer hardcodes the Cloudflare Access credentials in
      source. They're read from `THETADATA_CF_ACCESS_CLIENT_ID`/`THETADATA_CF_ACCESS_CLIENT_SECRET`
      env vars, or a local `.env` file (gitignored — see `.env.example` for the format; the
      working `.env` was created in the project root so nothing broke). Confirmed via git
      history that `thetadata_controller.py` was never actually committed, so no credential
      rotation is needed — this was closing the door before anything leaked, not after.
      Added a `.gitignore` (`.env`, `.venv/`, `.vs/`, `__pycache__/`, generated reports)
      since none of that was being excluded before either.
- [x] `config.py`'s `PricingConfig` was imported in `main.py` but never instantiated —
      dead code holding stale defaults (10000 sims/50 steps) that conflicted with what was
      actually used (50000/100). Now instantiated once in `main()` and threaded through
      every `AmericanLSMPricer(...)` and `MCHestonLSM.run_heston_full(...)` call as the
      single source of truth (`pricing_config.simulations/.steps` for the plain LSM engine,
      `.heston_sims/.heston_steps` for a single Heston run, `.heston_compare_sims/.steps`
      for the lighter run used inside "Run all & compare"). Defaults were set to match the
      values already in production use, so this is a pure refactor — no behavior change,
      just one place to tune sim/step counts going forward.
- [x] Removed the dead `from historicalvol import get_30d_vol` import in `vol_manager.py`
      (imported, never called).

### Reporting
- [x] `reports.py` PDF rewritten: branded header with ticker/strike/spot/expiry/rate
      context (previously showed none of this), traffic-light coloring of each model's
      Price/Greeks vs the Market row (✅/⚠️/red thresholds matching the CLI reference
      blocks), and an added price-comparison bar chart vs market. CSV export now includes
      the same input metadata columns.

### Market Data
- [x] ThetaData live data (primary)
- [x] yfinance fallback (secondary)
- [x] Dividend yield extraction bug fixed
- [x] SABR forward uses theoretical formula

### Interface
- [x] Reference blocks for all methods (market vs model comparison)
- [x] Heston placeholder (method 4)
- [x] README.md

## ✅ Completed (Follow-up session: QQQ dividend-yield bug)

- [x] **Root-caused why every model looked broken on QQQ (SABR/VannaVolga price way off,
      CRR/Newton-Raphson IV way too high, prices matching only because those two solve
      for whatever sigma hits the target price).** `market_data.fetch_dividend_yield`
      returned `q=0.41` (41%) for QQQ instead of the real ~0.41%. Confirmed by hand:
      `696 * exp((0.0454 - 0.41) * 0.326) = 617.0`, which matched the debug output's
      forward price exactly. Root cause: yfinance's `dividendYield` field apparently now
      returns low-yield tickers as a plain percentage number (0.41 meaning "0.41%"), but
      the old heuristic (`if val < 0.5: treat as already-decimal`) read that as 41%. A
      100x error, and specifically only for tickers yielding under 0.5% -- which is why
      it never showed up testing on GME (no dividend) and slipped through undetected.
  - This bug is *older* than last session's Heston/CRR/NR fixes, but had zero visible
    effect before them: `brute_force`/`implied_volatility_nr` didn't accept `q` at all
    pre-fix, and Heston's path simulation hardcoded `q=0`. Once `q` was correctly wired
    into every pricer (the point of that session), this pre-existing bug suddenly had a
    real, large effect everywhere at once -- which is exactly the "everything looks
    messed up together" pattern reported.
  - Fixed in `market_data.py`: `fetch_dividend_yield` now prefers actual trailing-12mo
    cash dividend payments (`stock.dividends`, raw dollar amounts -- no
    percentage-vs-decimal ambiguity) over the fragile `.info` summary fields, falls back
    to `dividendRate/price` next, and only uses `dividendYield` as a last resort with a
    self-correcting retry (tries the decimal-fraction reading, then the percentage
    reading, keeps whichever is plausible). A shared sanity clamp (reject any yield
    ≥ 20%) now guards all three paths, so a future yfinance format change degrades to
    `q=0` instead of silently corrupting every price again.
  - Verified with 5 synthetic test cases: QQQ-like trailing dividends recovers ~0.41%
    correctly, the `dividendYield=0.41` fallback self-corrects to 0.41% instead of 41%,
    a real high-yielder (dividendYield=3.8 meaning 3.8%) still resolves correctly,
    garbage input (55.0) is rejected to 0.0 instead of propagating, and a true
    no-dividend ticker still cleanly returns 0.0.

## ✅ Completed (Follow-up: spot-price fetch crashing outside trading hours)

Jason hit `Critical Error: Could not fetch spot for 'MU' from PotatoHedge` and
flagged it as a new crash. Reproduced live and root-caused: **not a
regression, not ticker-specific** -- confirmed live at 04:53 CT that BOTH
`MU` and `AMD` had `bid=0.0000, ask=0.0000` from `stock_snapshot_quote`
simultaneously (no live 2-sided quote posted pre-market, a normal state for
this vendor's real-time NBBO feed outside regular trading hours, not a data
outage or invalid symbol). `market_data.py`'s existing `val > 0` guard
correctly rejected the resulting 0.0 rather than pricing off a fake $0
spot -- the guard wasn't the bug. The message telling Jason it was "most
likely" an invalid symbol WAS misleading for this specific cause, though.

**Fixed**: `ThetaDataController.fetch_spot_price` now falls back to the last
actual trade print (`stock_snapshot_trade`, a working endpoint not
previously wired up) when the live quote is unavailable/zero, printing a
clear `[ThetaData] ... using last trade print ... Not a live quote.` warning
rather than silently treating it as a live mid, or raising for something
that isn't "symbol invalid." Verified live: `MU` -> 852.93, `AMD` -> 476.53
(both via last-trade fallback), a genuinely bad ticker still raises cleanly.
This is a root-level fix in `thetadata_controller.py` -- every caller
(`market_data.py`, `vol_manager.py`, `MCHestonLSM.py`, `SABRModel.py`,
`VannaVolga.py`) goes through this one method, so all of them benefit
without individual changes.

Also caught in passing while fixing this: the old check
(`if key in quote and quote[key]`) treated the STRING `'0.0000'` as truthy
(non-empty string), so a live quote with a real zero bid/ask would have
"found" a price and returned `0.0` rather than continuing to check `ask`/
`last` -- harmless in practice (the caller's `val > 0` guard still rejected
it), but fixed to check `float(v) > 0` directly rather than relying on that
downstream guard to catch it.

## 🔴 Known Issues

### P1: Color's magnitude vs. the market's reported color needs a live re-check
Color is day-normalized again (`/365`, same as theta -- see Completed above). The
**sign** was independently checked against a from-scratch numerical derivative of
plain Black-Scholes Gamma (no dependency on the spreadsheet, which turned out to have
an unrelated bug in that formula): for the actual QQQ trade's parameters, Gamma
provably rises as expiry approaches (the standard, well-known behavior for a
near-the-money option), which matches our model's positive sign. So the sign looks
theoretically sound. What's now open is **magnitude**: day-normalized model color is
tiny (~0.0000124-0.0000149/day in the QQQ test case) against market's reported -0.0056
-- roughly 2.5 orders of magnitude apart. Possible explanations, untested: the data
vendor may quote color on a different per-unit convention (e.g. per 1-point or per
100-share-equivalent move, the way vega/rho are sometimes quoted per 1% rather than
per unit), or American-specific/skew effects the plain BS check above doesn't capture,
or the vendor's own color figure being noisy/unreliable (color is one of the least
standardized greeks across providers). Needs a live data point and ideally the
vendor's own greek-definition docs to resolve properly rather than guessing further.

### P2: Heston's own (non-fallback) calibration quality still needs live re-verification
A live QQQ run (see "Live validation" below) confirmed the **fallback** path works
correctly -- Heston's row matched Standard's row exactly, as expected when calibration
is unstable and it falls back to pricing with the Standard sigma. What's still
unverified is Heston's own calibrated stochastic-vol pricing (the non-fallback case) on
a name/strike where calibration is stable -- that needs a separate live check.

### P2b: Heston's `xi >= 4.5` instability guard may be over-aggressive (OPEN -- not fixed 2026-07-28)
Discovered while verifying the expiry-alignment fix above, deliberately left
alone. `run_heston_full` discards a calibration when `xi >= 4.5 or rmse > 0.25`.
Observed live 2026-07-28, **after** the expiry fix, on the correct contracts:
- TSLA 20260731 (T=0.0055, 109 strikes, IV 0.50-3.22): xi=4.5165, **rmse=0.0301**
- AAPL 20260821 (T=0.0630, 78 strikes, IV 0.28-1.52): xi=5.0000, **rmse=0.0437**

Both were thrown away and surfaced as `[Heston] Failed: ... Calibration unstable`
-> `N/A` in the report, purely on the xi term, despite rmse an order of magnitude
inside the 0.25 tolerance. xi=5.0000 is exactly the optimizer's own upper bound,
so the fit is pinned, not diverging -- and a steep short-dated smile may
genuinely require vol-of-vol above 5.0. Note the AAPL case is a fairly ordinary
~23-day expiry, so this is NOT confined to ultra-short-dated extremes. Also note
the calibration is multi-start and stochastic: a TSLA 20260731 run minutes
earlier converged to xi=1.1217/rmse=0.0369 on the same contract, so the pin is
intermittent.

Open questions for a future session (all need live evidence before any change):
does raising the xi upper bound above 5.0 produce a *better* rmse or just let it
wander? Should the guard be `xi pinned at bound AND rmse poor` rather than an
unconditional xi threshold? Is the wide strike range being fed to the calibrator
(AAPL: 110-590 against a $336.91 spot) inflating the required vol-of-vol? Do NOT
change the bound or the guard without reproducing and answering these -- same
"verify empirically before landing a magic-number change" standard as the SABR
beta-threshold and multi-start work.

### P1: SABR Rho calibration can still hit bounds
- Rho occasionally hits bounds (-0.99, 0.99), mostly in the free-beta pass where beta/rho
  trade off against each other. Multi-start helps but doesn't eliminate it. Not expected to
  affect ATM accuracy anymore (alpha is pinned independently of rho now), just a residual
  skew-shape calibration wrinkle worth watching.

### P1: Polyfit RankWarning
- Occurs for deep OTM options with few ITM paths
- Mitigated by dynamic degree

### P2: Second-order Greeks still noisier than first-order ones
- Vomma/vanna/color/speed are meaningfully more stable after the bump-floor widening
  (see above) but this is mitigation, not elimination -- it's an inherent property of
  differentiating a regression-based LSM price function. A real fix means pathwise or
  likelihood-ratio Greeks instead of bump-and-revalue (Level 1 below).
- Vomma/color specifically needed BOTH a much wider bump (0.8*sig / 0.25*T, vs. the
  0.15*sig / 0.08*T used for vanna) AND multi-seed CRN averaging to become usable --
  documented in the Completed section above with the before/after numbers. Vanna,
  which shares the narrower bump, was left alone per explicit instruction and hasn't
  been re-tested for the same instability -- worth checking if it's ever flagged.
- The wider vomma/color bumps add real runtime: ~13s per model for vomma+speed+color
  alone at production settings (sims=50000, steps=100) vs. a few seconds before, since
  each is now computed 3x (2x for Heston) instead of once. For "run all & compare"
  across 4-5 models this adds roughly a minute. Worth watching if it becomes annoying.

### RESOLVED (methodology change): Bump-and-revalue replaced with closed-form for vanna/vomma/color/speed/charm
Even after the CRN multi-seed averaging and empirically-widened bumps described above,
a live ThetaData comparison report (ticker MU) showed Color and Charm still 50-250x off
the market-quoted values, with Vanna/Vomma reasonably close. Rather than continue tuning
bump sizes/seed counts on a fundamentally noisy estimator, switched vanna, vomma, color,
speed, and charm in both `MC.py` and `MCHestonLSM.py` from bump-and-revalue-on-the-LSM-
price to closed-form Black-Scholes-with-carry (cost-of-carry `b = r - q`) analytic
formulas. Delta, Gamma, Vega, Rho, and Theta are untouched (still LSM finite-difference)
-- those were never the noisy ones.

**Why this is the right tradeoff**: bump-and-revalue on vanna/vomma/color/speed/charm
divides by a bump² or bump³ (or a product of two bumps), which massively amplifies any
residual non-smoothness in the LSM regression's continuation-value fit -- the fit is
piecewise as paths cross in/out of the ITM set, so it's never perfectly smooth in
S/sigma/T even under common random numbers. Closed-form formulas have zero simulation
noise by construction, at the cost of pricing the *European*-equivalent sensitivity
(the early-exercise premium's effect on 2nd/3rd-order curvature isn't captured). This
tradeoff was explicitly discussed and accepted rather than continuing to chase
diminishing returns on bump/seed tuning.

**Formula source and verification** (same two-step rigor bar used for the
Leisen-Reimer tree below): formulas were sourced from Wikipedia's Greeks (finance)
article (citing Haug, "The Complete Guide to Option Pricing Formulas"), then
independently verified via `/tmp/verify_greeks.py` -- central finite differences of a
from-scratch closed-form delta/gamma (a fully separate code path from the formulas
under test), across three varied cases (ATM 6mo, short-T/high-vol OTM, long-T/dividend
OTM) and both calls and puts:
- Vanna: `-e^(-qT)·N'(d1)·d2/sigma` -- **identical for calls and puts** (put-call parity).
  Matched finite difference to ~1e-7. Confirms an earlier reference implementation's
  `* sign(z)` term on vanna (from the user's `monte_carlo_pricer.py`) was a bug, not a
  real call/put asymmetry.
- Vomma: `Vega · d1 · d2 / sigma` (only one √T factor, via Vega's own). Matched to
  ~1e-5. Confirms that reference file's extra √T factor was a bug.
- Speed: `-Gamma/S · (d1/(sigma·√T) + 1)`. Matched to ~1e-10.
- Charm: `q·e^(-qT)·N(d1) - e^(-qT)·N'(d1)·(2(r-q)T - d2·sigma√T)/(2T·sigma√T)` for
  calls (put term: `-q·e^(-qT)·N(-d1)` instead of the leading term). Matched to
  ~1e-9/1e-12. **Differs between calls and puts** (unlike vanna).
- Color: `e^(-qT)·N'(d1)/(2·S·T·sigma·√T) · [2qT + 1 + ((2(r-q)T - d2·sigma√T)/(sigma√T))·d1]`.
  Matched the finite-difference check in *magnitude* but was exactly sign-flipped
  relative to this project's charm/color convention (`-d(...)/dT_remaining`, i.e.
  positive when the sensitivity rises as expiry approaches, same convention theta
  uses) -- corrected with a sign flip in `MC.py`'s `_closed_form_color`. Caught
  precisely *because* the finite-difference cross-check was against our own
  convention, not just "does it match a textbook" -- a reminder that sign convention
  bugs don't show up in magnitude-only checks.
- Charm and Color both remain day-normalized (`/365`), matching theta's convention,
  since they differentiate the same `T` theta does.

**MCHestonLSM.py specifics**: the closed-form formulas need a single scalar sigma
input, which Heston's stochastic-vol model doesn't have natively. Used
`sqrt(V0)` -- the calibrated instantaneous variance every simulated path starts
from -- as the representative "spot vol." This is a reasonable single-number
proxy, not a claim that these are genuinely Heston-consistent second-order Greeks;
documented as such in the code. Both files' closed-form helpers are shared (defined
once in `MC.py`, imported by `MCHestonLSM.py`) rather than duplicated.

**Verified**: sandbox-tested both files directly (`AmericanLSMPricer.calculate_greeks`
and `compute_greeks_heston`) with matching S/K/T/r/q/sigma inputs -- vanna/vomma/
color/speed came back numerically identical between the two files (as expected, same
underlying closed-form call with the same effective sigma), charm differed correctly
between calls and puts, and vanna/vomma/color/speed were confirmed identical between
calls and puts (as the formulas predict) while charm and delta/rho correctly differed.

**Deliberately NOT investigated as part of this fix** (per explicit instruction): the
separate question of whether ThetaData's own reported Color/Charm use a different
quoting convention or units than what these formulas produce. This fix addresses the
*computation method* (closed-form vs. noisy bump-and-revalue) only.

## 🚀 Future Expansion

### Level 1: Numerical Stability
- [ ] Antithetic Variates (variance reduction)
- [ ] Control Variates (European BS price as control)
- [ ] Laguerre polynomials for LSM regression
- [ ] Levenberg-Marquardt for SABR calibration

### Level 2: Advanced Calibration
- [ ] Full 3D volatility surface (strike × maturity)
- [ ] Bootstrap from ThetaData multiple expiries
- [ ] Live streaming via ThetaData

### Level 3: Model Expansion
- [ ] Heston stochastic volatility (code exists in MCHestonLSM.py)
- [ ] Jump-diffusion (Merton) for fat tails
- [ ] Multi-asset basket options with Cholesky correlation

### RESOLVED (round 2): Color/Charm still ~300x off after the closed-form switch -- root cause was two convention bugs, not the computation method
The closed-form Black-Scholes switch (documented above) fixed Vanna/Vomma cleanly, but
a live "Model Comparison Report" (MU call, K=880, S=865.46, T=0.33) showed Color and
Charm were STILL wildly off the market row -- Charm off by ~330x, Color off by ~325x
AND sign-flipped. Vanna (0.1119 model vs 0.1087 market) and Vomma (-14.78 vs -14.92)
were both within ~1-3%, which was the tell that the closed-form formulas themselves
were fine and something specific to Charm/Color's scaling/sign was wrong.

Root-caused by finite-differencing the closed-form's own delta/gamma at the exact live
parameters (S=865.46, K=880, T=0.330, r=0.046, q=0.0006, sigma=0.9361) and comparing
against the "Market" row directly, rather than trusting the textbook convention
assumed when the closed-form formulas were first written:

1. **Wrong day-count.** Charm/Color were divided by 365 (calendar-day convention, by
   analogy with Theta). Removing the `/365` brought Charm to -0.1908 (market: -0.1729,
   ~10% off) and Color's magnitude to 0.00136 (market: 0.0013, ~5% off) -- both far
   closer than the ~330x gap with `/365` in place. **This vendor's Charm/Color are
   per YEAR, not per calendar day**, unlike Theta.

2. **Color's sign was a genuine implementation bug.** The code comment on
   `_closed_form_color` claimed "sign flipped relative to the raw Haug/Wikipedia
   formula" but the code never actually applied that flip -- it returned the raw,
   un-negated bracket formula. Separately, re-deriving the sign relationship
   carefully (see the finite-difference cross-check in the MU-specific verification)
   showed the raw formula equals `-dGamma/dT_remaining`, and matching the live
   market row requires the OPPOSITE: `+dGamma/dT_remaining`. In other words Color, in
   this vendor's convention, uses the sign convention that's the reverse of both
   Charm's and Theta's ("-d/dT_remaining") -- an actual, confirmed asymmetry, not
   just a bug in this codebase. (Color is one of the least standardized Greeks
   across vendors -- see the P1 issue history above -- so this isn't shocking in
   hindsight, but it needed live data to pin down, not a textbook.)

Both fixes were folded directly into the American Leisen-Reimer rewrite described in
the next section rather than patched into the closed-form functions in isolation
(though the closed-form functions were also fixed, since they're kept as a documented
European-equivalent reference/diagnostic).

### RESOLVED (round 3): Vanna/vomma/color/speed/charm switched from closed-form (European) to Leisen-Reimer finite-difference (American) -- "what would it take to make all the sensitivities American? Just do it."
Delta/Gamma/Vega/Rho/Theta were already genuinely American (finite-difference on the
Longstaff-Schwartz price, which prices early exercise). Vanna/Vomma/Color/Speed/Charm
were not -- first as noisy LSM bump-and-revalue, then as closed-form Black-Scholes
(explicitly European-only, an accepted tradeoff at the time). Asked directly to close
that gap.

**Implementation**: `american_binomial.american_second_third_order_greeks(S, K, T, r,
sigma, q, cp, steps=401)` -- central finite differences (standard small bumps: dS=1%
of spot, dSigma=3% of sigma, dT=2% of T) on `leisen_reimer_american_price`, applying
early exercise at every tree node exactly like the CRR/LR IV solvers already do, using
the round-2-corrected Charm/Color sign and day-count convention baked in directly
(no `/365`; Color = `+dGamma/dT_remaining`, opposite Charm's `-dDelta/dT_remaining`).

**Why this avoids the tradeoff both earlier approaches faced**: bump-and-revalue on
the LSM price is American but the regression-based continuation-value fit isn't smooth
enough in S/sigma/T to differentiate cleanly twice or three times (the original P1/P2
noise problem). Closed-form Black-Scholes is smooth/deterministic but only prices the
European analog. The Leisen-Reimer tree is BOTH: American (backward induction applies
`max(continuation, intrinsic)` at every node) AND deterministic/smooth (Peizer-Pratt
inversion gives monotonic, low-oscillation convergence -- this project's own earlier
verification of LR against the Black-Scholes European limit already established this
smoothness; see the LR implementation section above).

**Stability verified empirically** (same live MU parameters) before trusting the
approach: vanna/vomma/speed/charm/color varied by under 0.15% across a 4x range of
finite-difference bump sizes (0.5%-2% of spot, 1%-5% of sigma) and a 4x range of tree
step counts (201-801) -- no multi-seed averaging or empirically-widened bumps needed
at all, unlike the retired LSM approach, which needed both and still wasn't good
enough.

**Live-parameter result** (MU call, same case throughout this investigation):

| Greek | Market | American LR (this fix) | Gap |
|---|---|---|---|
| Vanna | 0.1087 | 0.1119 | 2.9% |
| Vomma | -14.9185 | -14.7809 | 0.9% |
| Speed | ~0.000000 | -0.0000014 | negligible |
| Charm | -0.1729 | -0.1909 | 10.4% |
| Color | -0.0013 | -0.001363 | 4.9%, correct sign |

All five now sit in the same ~1-10% band as the first-order Greeks and each other,
consistent with ordinary model-to-model sigma/methodology differences rather than a
broken computation. `MCHestonLSM.py`'s `compute_greeks_heston` was updated the same
way, evaluating the LR tree at `sqrt(V0)` (the Heston calibration's instantaneous
vol) as before, but now via the American LR finite-difference rather than closed-form.

**Not changed**: Delta/Gamma/Vega/Rho/Theta stay on the LSM finite-difference path --
they were already American and were never the noisy/wrong ones. The closed-form
Black-Scholes functions in `MC.py` were kept (with both round-2 bugs fixed) as a
documented reference for quantifying the early-exercise premium's effect on a given
Greek, not as the active computation path.

**Performance note**: the LR tree at steps=401 is fast (pure numpy, deterministic, no
Monte Carlo), so this whole higher-order Greek set now computes in a small fraction of
a second per model, versus the multi-second-to-tens-of-seconds cost of the old
multi-seed LSM bump-and-revalue approach -- a nice side benefit on top of the accuracy
fix.

### RESOLVED (round 4): Rho wildly inconsistent across models -- moved ALL Greeks (not just 2nd/3rd order) onto the American LR-binomial finite-difference
A second live comparison report (same MU trade) showed Rho badly broken across
every model: Standard=30.37, Leisen-Reimer=161.45, Newton-Raphson=78.82,
VannaVolga=-8.63 (NEGATIVE, impossible for a call), Heston=86.68, vs. Market's
136.00 -- despite Standard and Leisen-Reimer's solved sigmas differing by only
0.04% (0.9361 vs 0.9365). Models that near-agree on their input should not give a
5x-apart (or sign-flipped) output for the same Greek; this was the tell that
something was structurally broken, not just imprecise.

Diagnosed via the same seed-sensitivity test used earlier for Vanna/Vomma: with
dR_rho=0.001 (the project's original bump, never revisited while other bumps were
tuned over the course of this project), Rho computed across 5 different CRN seeds
at IDENTICAL S/K/T/r/sigma ranged from -280 to +203 (mean~5, std~162) -- the
finite-difference signal was completely swamped by the Longstaff-Schwartz
regression price's own Monte Carlo noise. This is the exact same disease Vanna/
Vomma/Color/Speed/Charm had before being moved to closed-form and then to the
American LR-binomial approach -- Rho (and, less dramatically, Delta/Gamma/Vega/
Theta) simply hadn't been through that fix yet.

**Fix**: extended `american_second_third_order_greeks` into
`american_all_greeks(S, K, T, r, sigma, q, cp, steps)` (`american_binomial.py`),
covering all ten Greeks via finite differences on the deterministic Leisen-Reimer
American binomial price. `AmericanLSMPricer.calculate_greeks()` (`MC.py`) and
`compute_greeks_heston` (`MCHestonLSM.py`) both now call this single function for
their entire Greek set; `AmericanLSMPricer.price()` (the LSM Monte Carlo) is
unchanged and still supplies each row's displayed "Price". `compute_greeks_heston`
in particular got dramatically simpler -- it no longer re-runs the (heavy) Heston
LSM simulation dozens of times per Greek; everything comes from the LR tree at
`sqrt(V0)`.

**Verified**: Rho is now identical to 4 significant figures across a 20x range of
finite-difference bump sizes and a 4x range of tree step counts (same live MU
case) -- fully deterministic, unlike before. Standard/Leisen-Reimer/Newton-Raphson
(0.9361/0.9365/0.9365 sigma) now report near-identical Delta/Gamma/Vega/Rho/Theta
to each other (e.g. Rho 112.35/112.34/112.34), which they did not before. Runtime
also improved substantially -- each model's full Greek set now computes in
~0.1s (deterministic tree, no Monte Carlo), versus several seconds to tens of
seconds for the old multi-seed LSM bump approach.

**Bonus catch**: `compute_greeks_heston`'s old Vega was coming back NEGATIVE in
the live report (-7.706), which is impossible for a vanilla option -- likely
because it was finite-differencing the Heston vol-of-vol parameter (xi) rather
than a spot-vol-like quantity, compounding the same LSM noise problem. Resolved
by the same fix (Vega now bumps the LR tree's actual sigma input).

**Known caveat, documented in code**: `AmericanLSMPricer.price()`'s LSM price and
`american_all_greeks`' own LR-binomial price can differ by ~1-2% for identical
inputs (both are legitimate American pricers; they discretize the exercise
boundary differently). The Greeks are therefore not exact numerical derivatives
of the specific LSM price number shown next to them in the report -- they are
internally consistent across models and track the live market row about as
closely as the already-fixed higher-order Greeks. Fully unifying price and Greeks
onto one pricer was judged out of scope (not what was reported broken); flagged
as a natural follow-up in `american_all_greeks`' docstring if that ~1-2% gap ever
matters for a specific use case.

### PARTIALLY RESOLVED: SABR calibration producing a badly wrong sigma
The same live report showed SABR's row severely broken: Price=$2.54 vs Market's
$182.93 (~98.6% too low), Sigma=0.0163 vs every other method agreeing near
0.93-0.94 (~57x too low), with every downstream Greek (Vanna=2.61, Vomma=390.9,
Charm=-1.67, etc.) wildly distorted purely as fallout from that one bad sigma --
not independently broken Greek math (confirmed by checking that a near-zero-vol,
near-the-money option's Delta/Gamma/Vega shape is internally consistent with
sigma=0.0163, it's just the wrong sigma to begin with).

Two things fixed in `vol_manager.py`'s SABR branch (`VolManager.get_sigma`,
`method == 'SABR'`):
1. **Confirmed bug**: `model.get_vol(F=S, K=K, T=T)` evaluated the calibrated
   Hagan SABR formula at spot `S`, not at the forward `calibrator.forward =
   S*exp((r-q)*T)` that `alpha`/`rho`/`nu` were actually calibrated against
   (SABR's Hagan formula is inherently forward-based -- see
   `SABRCalibrator._solve_alpha_for_atm`/`_fetch_and_prepare`). Fixed to use
   `calibrator.forward`. This is a real correctness bug, but the forward/spot
   gap for this trade is only ~1.5% (small r-q, T=0.33) -- not by itself enough
   to explain a 57x sigma error, so it's necessary but very likely not sufficient.
2. **Sanity/fallback guard added, root cause NOT fully diagnosed**: couldn't
   reproduce the actual ~57x miscalibration offline -- this sandbox has no live
   ThetaData credentials, and the failure is data-dependent (needs the actual
   MU option chain snapshot that produced it, most likely something in
   `SABRCalibrator._fetch_and_prepare`'s parsed `market_vols`/`strikes`, or
   `_atm_market_vol`'s ATM-nearest-strike selection, going wrong for that
   specific chain). Rather than ship a fix that only patches the symptom
   blindly, added a guard mirroring the one `run_heston_full` already uses for
   unstable Heston calibration (xi>=4.5 or rmse>0.25 -> fall back to Standard):
   if the calibrated SABR sigma disagrees with the market's own quoted chain IV
   by more than 3x in either direction, treat it as a failed calibration and
   fall back to the chain IV directly, logging why. This stops a badly broken
   calibration from silently producing a nonsense report row again, but is a
   safety net, not a fix for whatever specific live-data condition triggers
   the miscalibration -- **needs a live re-run against the same MU chain to
   confirm whether the guard is now catching it (falling back cleanly) or
   whether the true root cause needs further investigation** (check the
   `[SABR Debug]` console output for `True ATM market vol` and the fetched
   strike/vol range on the next live run that hits this case).

### PARTIALLY RESOLVED: Rho still ~17-20% off Market after the noise fix -- one real bug fixed (T reconciliation), remaining gap not fully diagnosed
After round 4 (above) made Rho deterministic and internally consistent across
models, a follow-up live report still showed a persistent gap vs. Market's Rho
(135.01) that Vanna/Vomma/Color/Speed/Charm didn't share to the same degree
(those settled to ~1-10% off; Rho stayed ~17-20% off across every model).

Investigated in order:
1. **Confirmed our Rho computation itself is correct**, not a remaining bug: the
   textbook Black-Scholes closed-form Rho (`K*T*e^{-rT}*N(d2)`), computed by hand
   at Standard's exact solved sigma, gives ~112.4 -- matching our LR-binomial
   Rho (112.35) almost exactly. If our computation were wrong, it wouldn't agree
   with the independent closed-form this closely.
2. **Ruled out sigma as the cause**: scanning sigma from 0.90 to 1.05 (a wide
   band around Standard's 0.9361) moves Rho only from ~113.5 to ~108.8 -- barely
   any sensitivity, and moving in the WRONG direction to explain a gap to 135.
3. **Found T is the dominant sensitivity**: scanning T from 0.30 to 0.50 moves
   Rho from ~103 to ~161 -- roughly 5x more sensitive (in relative terms) than
   sigma. T=0.40 (vs. the trade's actual T=0.33) simultaneously fits Rho, Theta,
   and Delta all noticeably better, which is what pointed at T as the lever
   worth checking, not a coincidence limited to Rho alone.
4. **Found and fixed one real, concrete bug in this vein**: `T` was never
   reconciled to the REAL listed contract's actual calendar time-to-maturity
   after ThetaData's nearest-expiry matching resolves it. The user types a
   rough T (e.g. "0.33" for "about 4 months") purely to derive a target date for
   nearest-expiry lookup; the resolved real contract (e.g. 2026-11-20, 122 real
   days out = T=0.3342) was shown correctly in the report header but never fed
   back into the actual T used for every model's pricing/Greeks -- they kept
   using the user's rounded input. Fixed in `main.py`'s `choice == '6'` block:
   after `market_exp` resolves, T is recomputed from that contract's real
   calendar date and used for every model + Heston + the report from that point
   on.
5. **This does NOT fully close the gap.** The reconciliation above is only a
   ~1.3% T correction (0.33 -> 0.3342) in the case that was investigated, but
   the apparent fit required T~0.40 -- a ~21% difference. The T-reconciliation
   fix is real and worth having regardless (it makes every Greek marginally
   more correct, not just Rho), but it is NOT, by itself, the explanation for
   the full observed gap.

**Status: open.** The remaining gap's exact cause is undiagnosed -- same
epistemic wall as the SABR investigation above: this sandbox has no live
ThetaData access, so there's no way to inspect what T/r/sigma/day-count
convention the "Market" row's own bulk-greeks feed actually assumes internally
when ThetaData (or whatever computes their Rho) produces that 135.01 figure. It
may be a genuine vendor-side convention difference (which the user has
previously asked to defer investigating for Color/Charm) rather than anything
fixable in this codebase. Next step, if pursued: on a live run, compare the
"Market" row's raw `bulk` response fields directly (particularly whatever
`right`/`strike`/expiry the matched row actually carries) against what's
expected, to rule out a strike/expiry mismatch in the market data pull itself
before assuming it's a pure quoting-convention gap.

## 🛠️ How to Run
```bash
Options_Suite\options_suite.bat      # Windows
Options_Suite/options_suite.sh       # Linux/Mac
```
