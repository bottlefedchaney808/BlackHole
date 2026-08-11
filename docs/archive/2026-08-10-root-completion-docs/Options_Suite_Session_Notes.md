# Options_Suite Session Notes — 2026-07-27 (superseding 2026-07-25 below)

Second round of fixes on top of the 2026-07-25 work. **Read this section first** — the 2026-07-25 notes further down are preserved verbatim for context but several claims there ("each model solves its own IV, from its own market data, priced through its own textbook engine — nothing borrowed") were only half true: pricing had been separated per-model, but Greeks were still all going through one shared `american_all_greeks(...)` (Leisen-Reimer tree FD) regardless of which model was on the row. This session finished the "each model on its own" refactor properly, added a Barone-Adesi-Whaley model, and fixed Heston.

## 1. Starting state (from a live AMD Put K=480 comparison, 2026-07-27 15:33)

- **Heston was N/A across every column** — single-start optimizer in 5D was failing outright on this high-vol short-dated regime, exactly the fragility §6 of the 2026-07-25 notes flagged as known-not-yet-fixed.
- **Vanna and Vomma looked wrong across every model** — Vomma sign-flipped between models even though they all solved sigmas within 4% of each other. Root cause: `american_all_greeks` used central-differences at 2% sigma bump on the LR tree, and for a near-ATM high-vol short-T put (d2 ≈ 0), the "true" analytical Vomma is order 0.03 in magnitude and SIGN-flips as sigma crosses a narrow band ~0.808. That kind of thing is not recoverable by finite differences at any bump width on any discrete pricer — the AMD case is a fundamental noise-limited regime for 2nd-order sigma FD.
- **Delta / Gamma / Vega / Rho / Theta read byte-for-byte identical across CRR / LR / NR / SABR / VV / MC** on the same report — because every one of those "model rows" routed through `american_all_greeks(...)`, i.e. the LR tree with each model's flat sigma. The names on the report suggested six different pricing methodologies giving their own Greeks; underneath, there was one Greek engine and the models only differed by which sigma they fed it.

## 2. The refactor: each model has its OWN Greek engine now

The 2026-07-25 pricing-side fix ("each model prices through its own real engine") is complete; the Greek-side equivalent is what this session added. Every model now differentiates via its OWN pricer, not a shared one:

| Model | Pricer (unchanged from 2026-07-25) | Greek engine (NEW) |
|---|---|---|
| **CRR** | `crr_american_price` (CRR binomial) | `crr_all_greeks` — FD on the CRR tree. `dS_frac=0.03` (wider than LR's 1%) because CRR's u/d/p oscillates in S at narrow bumps and produced spurious low Gamma at 1%. Vanna/Vomma via closed-form BS at CRR's own sigma — CRR's tree fundamentally cannot be FD-differentiated twice in sigma (bump-swept from 5% to 30%; Vomma stayed at -1.02 across every width, not tunable). |
| **Leisen-Reimer** | `leisen_reimer_american_price` (LR binomial) | `lr_all_greeks` — FD on the LR tree. 1st-order at standard 1-2% bumps; 2nd-order sigma (Vanna, Vomma) at 15% bump. Verified converged at that bump on AMD 480. |
| **Newton-Raphson** | `leisen_reimer_american_price` | `lr_all_greeks` — NR prices via the LR tree, so its Greeks are the LR tree's Greeks by construction. Not model borrowing — LR IS NR's pricer. |
| **SABR** | `leisen_reimer_american_price` at SABR's smile-sigma at K | `sabr_all_greeks` — smile-AWARE FD. Every S/T/r bump re-evaluates Hagan's formula at the bumped state, then LR-prices at THAT bumped smile-sigma. Vega bumps SABR's alpha param and chain-rules to sigma. Uses `vol_manager.last_sabr_calibration` — doesn't recalibrate. |
| **Vanna-Volga** | `leisen_reimer_american_price` at VV's smile-sigma at K | `vv_all_greeks` — smile-AWARE FD. Every bump re-interpolates the 3-pillar VV smile via `get_vol()` and LR-prices at the bumped smile-sigma. Vega bumps `atm_vol` (holding RR/BF fixed — how a VV trader hedges). |
| **MC** | `AmericanLSMPricer.price` (Longstaff-Schwartz LSM) | `mc_all_greeks` — CRN bump-and-revalue on `price_with_rand` with fixed randoms reused across every bump. Wider bumps than trees (`dS=3%`, `dSig=5%`, `dR=200bp`) because LSM's regression isn't smooth in bumped variables at trees' narrow bumps. Vanna/Vomma via closed-form BS at MC's sigma — attempted CRN FD gave Vomma=-1.03 because CRN cancels shock noise but not the regression polynomial's own discontinuous shift. |
| **BAW** *(new — 8th model)* | `baw_american_price` (Barone-Adesi-Whaley 1987 analytical American BS) | `baw_all_greeks` — FD on the analytical pricer (noise-free since BAW is closed-form). Higher-order via closed-form BS at BAW's sigma per its "does Market use BS?" design. |
| **Heston** | `heston_lsm_price` (LSM under Heston paths, unchanged) | `heston_all_greeks` — CRN on the Heston LSM using ALL calibrated params (κ,θ,ξ,ρ,V0). Requires new `_heston_lsm_price_crn` wrapper that takes pre-generated z1/z_prime arrays. Vega = dP/dV0 * 2*sigma_eff (chain rule, so units match). Replaces the prior `compute_greeks_heston` shortcut that mapped Heston down to `effective_sigma = sqrt(V0)` and called `american_all_greeks` — which meant κ/θ/ξ/ρ contributed NOTHING to reported Greeks, exactly the cross-model borrowing this refactor exists to prevent. |

The old `american_all_greeks` is kept as a backwards-compat alias for `lr_all_greeks` — nothing external breaks, but nothing internal should call it anymore. Every `american_all_greeks` reference in `main.py` was replaced with the appropriate per-model engine (both in the compare block and every single-model branch).

## 3. Heston: multi-start restart (the previously-known-not-fixed §6 issue)

- `HestonCalibrator.calibrate()` now sweeps a 37-point (kappa, xi, rho) seed grid, keeps the caller's suggested initial as one seed, runs `minimize()` from each, keeps the lowest-loss convergence.
- Verified on synthetic AMD-like smile: 31-35 of 37 restarts converge, rmse ~1e-5, and even an adversarial caller seed (kappa=0.5, xi=5.0, rho=+0.9, v0=0.1 — everything wrong) still recovers correct rho/xi.
- On the live AMD Put 480 case, Heston now returns real numbers instead of N/A: v0=0.7150 → σ_eff=0.846, Price 42.17 (vs. Market 42.88).

## 4. New model: Barone-Adesi-Whaley (menu option 8, "Run all" moved to 9)

- New file: `barone_adesi_whaley.py`. Contains `baw_american_price` (BAW 1987 analytical American BS with Newton iteration on the critical-price boundary, per Haug §7.4), `baw_all_greeks` (FD on analytical pricer for 1st-order + closed-form BS for higher-order), and `brute_force_baw` (bisection IV solver against BAW).
- Wired into `vol_manager` (`method='BAW'`), `main.py` menu, single-model branch (choice 8), and compare block.
- Menu now: `(1) CRR ... (7) MC (LSM), (8) Barone-Adesi-Whaley (American BS), (9) Run all models & compare` — "Run all" moved from 8 to 9.
- **Purpose: direct test of "does the vendor's Market Greek feed use a BS-based analytical American?"**. On AMD Put 480, BAW's Delta/Vega/Rho/Theta match Market to within 0.4%, while the tree-FD Rho was off by 12% at that same strike. Strong signal the vendor is using BAW or equivalent for its real-time Greek calc. Confirmed on INTC Call K=100 too (Price 6.88 matches Market 6.88 exactly, though for a q=0 call BAW reduces to European BS which the tree models also converge to closely, so INTC is a less discriminating test than the AMD put).

## 5. CRR Gamma bump fix

- On the first re-run after the per-model Greek engine split, CRR came back with Gamma=+0.00098 (about 3x too small vs LR's +0.00295). Root cause: `_tree_all_greeks` was using dS=1% for both trees, but CRR's u/d/p triple is a step function of sigma, so bumps at dS=1% put S+dS and S-dS on differently-aligned tree structures, and 2nd FD picked up that alignment noise as false signal.
- Fixed: CRR now uses dS_frac=0.03 (verified: dS=3%+ gives Gamma=+0.00295, converged and matching BS reference). LR keeps dS_frac=0.01 since Peizer-Pratt inversion is smooth-in-S at any bump.

## 6. What still needs work

- **True Heston-native higher-order Greeks** (Vanna/Vomma/Speed/Charm/Color). Currently fall back to closed-form BS at effective_sigma because 2nd-order sigma FD on LSM is fundamentally noise-limited (regression polynomial shifts discontinuously across sigma bumps, and CRN doesn't help because it cancels shock noise but not fit noise). Genuine model-native alternatives are analytical differentiation of the characteristic function or Malliavin calculus on the LSM paths. Same limitation applies to MC (LSM) and CRR (tree oscillation) — closed-form BS at each model's own sigma is the current fallback there too.
- **`compute_greeks_heston` still exists in `MCHestonLSM.py`** with the old shortcut behavior. `run_heston_full` no longer calls it (main.py explicitly calls `heston_all_greeks` on the calibrated params instead). Left in place to avoid breaking any external caller; consider removing in a future session.
- **Extend BAW to a smile fit** if a full BAW smile row on the smile chart becomes useful (currently BAW only solves per-strike; the smile chart uses `smile_utils.fetch_market_smile` which BAW isn't wired into).

## 7. Files touched this session

`main.py`, `vol_manager.py`, `american_binomial.py`, `MC.py`, `SABRModel.py`, `VannaVolga.py`, `MCHestonLSM.py`, `barone_adesi_whaley.py` (new), plus doc updates to `PROJECT_ROADMAP.md`, `HESTON_BRUTEFORCE_REMINDER.md`, and this file.

---

# Options_Suite Session Notes — 2026-07-25 (older; kept for historical context)

Summary of everything found and fixed in this session, why each change was made, and what's still open. Written as a reference for future debugging, not a changelog for its own sake.

## 1. Starting complaints

- Rho looked wrong compared to Market.
- Market beta was hard-locked to 1.0 in the startup printout.
- The volatility-method menu needed reordering ("run all" last).
- Newton-Raphson was labeled "(European)" — no European solvers wanted anywhere in the suite.
- After reviewing real GME/AAPL/TSLA reports: every model looked broken, not just cosmetically. The core demand that shaped the rest of the session: **every model solves its own IV, from its own market data, priced through its own textbook engine — nothing borrowed from another model, no fallbacks that substitute a default or another model's number when something fails.**

## 2. The root-cause bug: everything was pricing through the same Monte Carlo engine

Before this session, CRR, Leisen-Reimer, Newton-Raphson, SABR, and Vanna-Volga all displayed different names and different solved sigmas, but **every one of them priced through `MC.py`'s generic `AmericanLSMPricer` Longstaff-Schwartz Monte Carlo engine.** Only the sigma fed into that shared engine differed between "models." The names on the report suggested six different pricing methodologies; underneath, there was one.

Fixed: each model now prices through its own real engine.

| Model | IV solver | Pricer |
|---|---|---|
| **CRR** (renamed from "Standard" — it's Cox-Ross-Rubinstein, there is no generic "standard") | `brute_force` — bisection vs. the CRR tree | `crr_american_price` (CRR binomial tree) |
| **Leisen-Reimer** | `brute_force_lr` — bisection vs. the LR tree | `leisen_reimer_american_price` (Peizer-Pratt LR tree) |
| **Newton-Raphson** | `implied_volatility_nr_american` — Newton's method vs. the LR tree (numerical central-difference vega, since there's no closed-form American vega) | `leisen_reimer_american_price` (same tree LR uses) |
| **SABR** | Hagan-formula calibration (vega-weighted, ATM-pinned alpha) against the real market smile | `leisen_reimer_american_price` |
| **Vanna-Volga** | 3-pillar (25Δ put / ATM / 25Δ call) vol interpolation, using VV's own market read (`get_auto_rr_bf`) | `leisen_reimer_american_price` |
| **MC** — now its own selectable menu choice, not a hidden engine everything else silently used | `brute_force_mc` — bisection vs. AmericanLSMPricer's own simulated price (fixed-seed common random numbers so the price is smooth enough in sigma for bisection) | `AmericanLSMPricer` (Longstaff-Schwartz LSM) |
| **Heston** | Its own semi-analytic characteristic-function calibration (vega-weighted, 5 free parameters — see below) | `heston_lsm_price` (Longstaff-Schwartz LSM under Heston paths — genuinely American, early exercise included) |

Leisen-Reimer, Newton-Raphson, SABR, and Vanna-Volga all price via the LR tree because none of them has (or needs) its own distinct tree — it's neutral pricing machinery once sigma is solved, and it's the same tree their Greeks already use via `american_all_greeks`. CRR keeps its own CRR tree because CRR **is** the textbook method it's named after. MC is the only model that legitimately uses `AmericanLSMPricer` — it *is* the Monte Carlo model. Heston keeps its own distinct engine throughout.

## 3. "No fallbacks" — every silent substitution removed

Explicit direction: *"I'd rather it fail and we debug than be tricked. NO Fallbacks."* Every one of the following used to catch a failure and quietly return a plausible-looking default instead of raising. All now raise instead:

- `bruteforceimpliedvol.py` — bisection functions used to return `0.3` on any failure (no market price, pricer exception, no convergence). Now raises `ValueError`/lets exceptions propagate.
- `vol_manager.py`'s `get_sigma` — had a catch-all `try/except` around the entire method that caught any error and returned raw vendor IV or `0.3`. Removed. Each method branch now raises with a specific, actionable message if its own data requirement isn't met.
- SABR's ratio-based sanity check used to silently swap in the vendor's raw `implied_vol` field whenever the calibrated sigma looked "too far" from it. Removed — SABR now raises if calibration produces an invalid sigma, with the full calibration context (alpha/beta/rho/nu/rmse) in the error message.
- `SABRModel.py`'s `_make_fallback` — a flat, zero-skew synthetic smile that silently ran on **every single call** before the date-format bug (below) was found, because `_find_nearest_expiry` always failed and was caught silently. Removed entirely. Every failure path in `_fetch_and_prepare`/`calibrate` now raises.
- Vanna-Volga's `atm_vol` used to default to vendor IV or `0.3` if the caller didn't pass one. Now raises — VV requires its own real market read.
- `MCHestonLSM.py`: the flat-vol smile fallback, the "return initial guess as fallback" on optimizer failure, the quad-integration-failure fallback to a plain Black-Scholes price (a completely different, non-Heston number, unlabeled), and — the worst one — `run_heston_full`'s "if calibration looks unstable, silently reprice with CRR's sigma through `AmericanLSMPricer` and return it under the `'Heston'` key" block. That last one meant a real Heston failure could silently show up in a report labeled "Heston" while actually being a different model's number. All removed; all raise now.
- `main.py`'s Heston branch used to catch any failure (including a user Ctrl+C) and silently reprice with CRR instead. Removed.
- Invalid menu choices used to silently default to CRR. Now raises.

The one deliberate exception: inside the **run-all comparison report**, a genuine Heston failure is still caught so it doesn't abort the other 6 models' results — but it's reported as an honest `None` in the table, never a substituted number.

## 4. The date-format bug (the single biggest root cause)

`SABRModel.py`'s `_find_nearest_expiry` and `MCHestonLSM.py`'s equivalent were parsing ThetaData's expiration list with `strptime(d, "%Y-%m-%d")`, but ThetaData actually returns `YYYYMMDD` (no dashes). Every single call raised `ValueError`, silently swallowed by a bare `except: pass`, so SABR and Heston **always** fell through to the flat-vol fallback — confirmed live by the fact their "IV range" was always a single repeated value equal to `fallback_vol`, on every ticker, every run. Fixed to match ThetaData's actual format (which the sibling `Vol_Suite` project already had correct).

## 5. Market data quality — the smile was letting garbage strikes in

`smile_utils.fetch_market_smile` (shared by SABR and Heston calibration, and the market-IV reference dots on the chart) had three real problems, found in this order:

1. **Vendor IV trusted uncritically.** The function preferred ThetaData's own `implied_vol` field and only solved IV itself (via `brute_force_lr` off bid/ask) when that field was missing. Since vendors return `implied_vol` on nearly every row, this suite's own solver almost never actually ran — the "Market IV" everything was compared against, and calibrated against, was never verified against this suite's own methodology. **Fixed: flipped the priority.** Now it always solves from the observed price first, and only uses the vendor field as a genuine last resort when a strike has no price at all to solve against. Verified with a synthetic case where the vendor field was deliberately wrong: the real solve won.
2. **No cap on how wide a chain to use** (fixed earlier in the session) — an old `max_strikes=15` cap threw away the whole chain outside a narrow ATM band, which is why SABR/Heston extrapolated so badly on anything more than ~15 strikes from spot. Removed — full chain by default.
3. **No filter for untradeable far-wing quotes**, which is what the wide-chain fix exposed: confirmed live on ORCL and MU that a deep-OTM, minimum-tick-priced contract can report an "implied vol" of 100–480%, purely a numerical artifact of inverting a near-worthless, barely-priced option (the price is so insensitive to vol out there that a tiny quote spread maps to huge vol swings). Two filters now guard against this:
   - **Bid-ask relative-spread filter** (`MAX_RELATIVE_SPREAD = 0.60`) — drops a quote if its spread exceeds 60% of mid.
   - **Moneyness filter** (`MIN_MONEYNESS = 0.30`, `MAX_MONEYNESS = 3.00`, i.e. strike/forward) — added after confirming the spread filter alone wasn't catching it (a penny-wide market on a five-cent option still "looks" tight in relative-spread terms while being untradeable). This is a data-quality exclusion, not a skew truncation — it's judged by intentional design to be tunable and is explicitly commented as such.

Both filters print a count of what they dropped, and there's a diagnostic that prints a raw sample row if literally zero strikes produce a usable price, so a future "why is X% vendor / 0% solved" question can be answered from the actual payload instead of guessing.

## 6. Heston-specific fixes

- **Vega-weighted the calibration objective.** It was an unweighted sum of squared IV errors — unlike SABR's, which already vega-weights. On real MU data, unweighted Heston landed on a degenerate corner (rho pinned at 0.98, xi≈0.016) chasing far-wing noise, while SABR converged sensibly on the *same* data because it already discounts low-vega (far-OTM) points. Now weighted the same way SABR is.
- **Made `v0` a genuinely free, calibrated parameter.** Before this fix, `v0` was a *fixed input*, seeded from `initial_sigma**2` where `initial_sigma` came from `vol_manager.get_sigma(method='CRR')` — i.e., **CRR's own solved sigma.** Since Heston's reported Greeks are derived from `effective_sigma = sqrt(v0)`, and v0 never moved, Heston's entire Greeks row was **byte-for-byte identical to CRR's**, confirmed live in an MU report, regardless of what Heston's own kappa/theta/xi/rho calibration actually found. This was real cross-model borrowing that had gone unnoticed. Fixed: `v0` is now a 5th free parameter in the same vega-weighted objective, verified (on a synthetic case with a deliberately-wrong seed) to actually move away from its starting guess.
  - **Known caveat**: `HestonCalibrator.calibrate()` is a single `minimize()` call from one initial guess — no multi-start like SABR's grid search. Adding a free 5th dimension makes this more exposed to landing in a bad local optimum (reproduced on an adversarial synthetic test). In practice the seed is a reasonable seed (CRR's own solved vol for the same underlying), so this is less likely on real data, but if Heston's `xi`/`theta`/`v0` come back sitting right at a bound, that's this fragility. **Not yet fixed** — the real fix would be giving Heston SABR-style multi-start restarts.
- Heston still fails outright sometimes (confirmed on QQQ, this session's last test) — with the fallback removed, this now surfaces as `[Heston] Failed: ...` and an honest `N/A` row, not a masked substitution. That's by design; it means a real optimizer/data problem is visible instead of hidden.

## 7. The smile chart itself

- Originally plotted the full fetched strike range (e.g. 0–2500 on a $920 name), which crushed the actually-useful near-the-money region into an unreadable sliver, and let a handful of extreme far-wing points blow out both axes.
  - Fixed: the default view is now framed around spot/the priced strike (roughly 0.5x–1.6x spot, widened just enough to keep the priced K comfortably inside), with a footnote counting how many real market strikes are outside that view (nothing is dropped from calibration — only from the default plot).
- CRR, Leisen-Reimer, Newton-Raphson, and MC used to be drawn as flat horizontal lines, because they only ever solved one sigma at the single priced strike. **This is not how you build a model's smile.** Fixed: each of these four now solves its own IV solver at *every* real chain strike, against that strike's own observed market price (exposed by `fetch_market_smile`'s `prices`/`rights` outputs) — a real curve per model, not a placeholder line, put on equal footing with SABR/Vanna-Volga/Heston's own calibrated curves.
- Market IV points are now color-coded by put/call side (not just vendor-vs-solved marker shape), so a jump or kink right at the OTM put/call crossover (visible near the forward on both the MU and QQQ reports) is identifiable as a quoting-convention artifact rather than a mystery.
- **Found and fixed a real duplicate-calibration bug**: the chart used to re-instantiate `SABRCalibrator` and call `.calibrate()` a second, fully independent time just to get curve points — separate from the calibration that produced the actual "SABR" row in the comparison table. Since SABR's multi-start optimizer isn't guaranteed to land on the same local optimum twice, the chart's SABR curve wasn't guaranteed to match the table's SABR number at all. Fixed: `VolManager` now caches the exact calibration it just ran (`vol_manager.last_sabr_calibration`), and the chart reuses that object instead of recalibrating.
- Added a persistent "Calibration strike range: X - Y (N strikes total)" line directly on the chart (SABR and Heston always share this range, since both go through the identical filtered fetch), so this doesn't have to be dug out of console scrollback.
- Curves now use distinct linestyles + reduced linewidth/alpha instead of a uniform bold solid line, so a wide-swinging curve (e.g. SABR near a wing) doesn't visually bury flatter curves sitting on top of it.

## 8. Other fixes this session

- **Beta**: was hard-locked to display `1.0`; now shows the real fetched market beta (or `N/A` if unavailable).
- **Menu**: reordered so "Run all models & compare" is last; renamed "Standard" → "CRR" everywhere (menu text, `vol_manager.py` method keys, `run_context_mode`'s JSON payload).
- **Rho**: added the American (early-exercise-aware) Rho, the closed-form European Rho, and their difference (`RhoEEPrem`, the early-exercise premium) side by side, since vendor Greek feeds are commonly European/Black-Scholes-based even for American contracts — the European number is the fairer comparison to "Market" Rho. Confirmed working correctly in the latest QQQ report (Rho values are now sane and RhoEEPrem correctly shows ~0 for calls, where early exercise rarely has value).
- **Vanna-Volga negative IV**: the boundary clamp branches (`K <= K_25P`, `K >= K_25C`) were missing the `max(sigma, 0.001)` floor the interior branch had, so a strongly-skewed smile (confirmed live on GME) could produce a literally negative implied vol fed straight into the pricer. Fixed.
- **PDF report layout**: fixed a column-overlap bug where the table width didn't scale with the number of Greek columns.
- **MC.py GPU path**: added CuPy detection with automatic CPU fallback (`OPTIONS_SUITE_FORCE_CPU` escape hatch), verified correct on CPU. Real GPU use requires `pip install cupy-cuda12x` (or `cuda11x` depending on driver) inside the project's venv — not yet verified on an actual card.
- **MC RankWarning spam**: numpy's `polyfit` was printing a `RankWarning` on essentially every LSM regression step with few in-the-money paths (routine, already-handled — `_polyfit_polyval` has its own correctness fallback). It was drowning out real debug output across a 100+ strike report. Suppressed at the source.

## 9. Known, deliberately-not-fixed items

- **Weekend/holiday T mismatch**: T is computed from real wall-clock `datetime.now()` (calendar days to expiry), but ThetaData's snapshot is frozen at the last trading session's close when markets are shut. Running the tool on a weekend means every model's T ticks down through the weekend while the "Market" row's price/greeks reflect Friday's close — a small, real, explainable theta-driven divergence. **Agreed: known caveat, don't tune Greeks off a weekend run** — not fixing the T-source (e.g., deriving "now" from the market data's own timestamp) unless it becomes a real problem.
- **Vanna-Volga's inputs are inherently European-flavored** (25-delta strikes located via the Black-Scholes forward-delta formula, vega-weighted interpolation via plain BS vega) even though its output prices through the American LR tree. This is the same situation SABR is in (Hagan's formula is also a BS-proxy construction) and is currently accepted as consistent with how every "smile-only" model here works. A rigorous Castagna-Mercurio rewrite (real vanna/volga replication weights solved as a price correction, not a vol blend) was discussed but explicitly deferred — not requested.
- **Heston calibration robustness** (see §6) — single-start optimizer, no multi-start restarts. Flagged, not fixed.
- **MC's chain-wide smile solve** uses reduced simulation/step counts (4,000 sims / 60 steps vs. the production 50,000/100) to keep a 100+ strike chart tractable — the actual reported MC price/Greeks for the priced strike still use full production settings.

## 10. Files touched

`main.py`, `vol_manager.py`, `bruteforceimpliedvol.py`, `american_binomial.py`, `SABRModel.py`, `MCHestonLSM.py`, `VannaVolga.py`, `smile_utils.py` (new this session), `MC.py`, `reports.py`, `config.py`.
