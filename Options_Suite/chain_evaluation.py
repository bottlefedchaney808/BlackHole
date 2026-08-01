"""
chain_evaluation.py

Chain-wide (whole options chain, not just the single priced strike) model
comparison: builds each pricing model's own implied-vol SMILE across every
real strike in a market chain, for the "Smile Comparison" chart in the
PDF/CSV report (see reports.py).

Split out of main.py on 2026-07-25 -- this had been growing as one large
inline block inside the "Run all models & compare" branch, and is a natural
unit to keep developing on its own (a "full chain: run every model, every
strike" mode is the planned next step -- see NOTES.md in this same
directory for what's open here: Heston reliability, SABR's wing behavior on
steep short-dated skews, and CRR/LR/NR/MC's coverage gap vs. Market on
strikes with no live 2-sided quote).
"""
import time

import numpy as np

from smile_utils import fetch_market_smile
from bruteforceimpliedvol import brute_force_batch, brute_force_lr_batch, brute_force_mc
from barone_adesi_whaley import baw_american_price, baw_all_greeks, brute_force_baw
from american_binomial import (crr_american_price_batch, leisen_reimer_american_price_batch,
                               crr_all_greeks, lr_all_greeks)
from MC import AmericanLSMPricer, mc_all_greeks
from SABRModel import _sabr_vol_hagan_vec, sabr_all_greeks
from VannaVolga import get_vol_batch as vanna_volga_vol_batch, vv_all_greeks
import MCHestonLSM


# Greek key sets. The analytic/tree models publish the full set at every
# chain strike; MC and Heston publish only the 1st-order block per strike
# (their 2nd/3rd-order values are reported once, at the focus K -- see
# run_full_chain's docstring for the accuracy-vs-runtime reasoning).
FIRST_ORDER_GREEKS = ('delta', 'gamma', 'vega', 'rho', 'theta')
HIGHER_ORDER_GREEKS = ('vanna', 'vomma', 'speed', 'charm', 'color')


class _AlreadySolved(Exception):
    """Internal: this curve was supplied by the caller (precomputed_curves),
    so its solve block is skipped rather than reported as a failure."""


def _fetch_chain(ticker, market_exp, S, K, T, r, q):
    """Fetch the real market chain once and derive the strike grid used by
    the closed-form curves. Split out of build_smile_comparison so
    run_full_chain can share the SAME fetched chain (one ThetaData bulk
    call, one set of strikes/prices/rights) rather than fetching a second,
    possibly-different snapshot a few seconds later."""
    m_strikes, m_ivs, m_sources, m_forward, m_prices, m_rights = fetch_market_smile(
        ticker, market_exp, S, T, r, q)
    grid = np.unique(np.concatenate([m_strikes, [K]])) if len(m_strikes) else np.array([K])
    return {
        'strikes': m_strikes, 'ivs': m_ivs, 'sources': m_sources,
        'forward': m_forward, 'prices': m_prices, 'rights': m_rights,
        'grid': grid,
    }


def _usable_quote_mask(prices):
    """House filter for "this strike has a real, invertible market price".
    Same test _solve_chain/_solve_chain_batch already apply -- kept in one
    place so the smile curves and the full-chain per-model dicts agree on
    exactly which strikes are considered quoted."""
    return np.array([p is not None and np.isfinite(p) and p > 0 for p in prices], dtype=bool)


def build_smile_comparison(ticker, market_exp, S, K, T, r, q, models, vol_manager,
                            atm_vol_vv, rr25, bf25):
    """
    Build the chain-wide "Smile Comparison" dataset: market IV (vendor-
    preferred -- see smile_utils.fetch_market_smile) plus each model's OWN
    implied-vol curve across every real strike in the chain, not a single
    flat sigma at one priced K.

    CRR/Leisen-Reimer/Newton-Raphson/MC solve their OWN IV at every chain
    strike against that strike's own observed market price (the standard
    approach for a tree/simulation-based solver's smile -- exactly what
    smile_utils.fetch_market_smile's own vendor-IV-or-solved-from-price
    construction already does with the LR solver, just repeated here with
    each model's own solver instead). SABR and VannaVolga instead evaluate
    their already-calibrated closed-form formula across the strike grid.
    Heston does the same, IF its calibration succeeded (see models['Heston']
    below) -- if Heston failed upstream, its curve is silently omitted here
    (not re-attempted), matching main.py's own "no substituted number" policy.

    models: the same dict main.py builds for the single-K comparison table.
    Needs models['Heston']['calib'] (may be absent/empty if Heston failed --
    handled). SABR does NOT come from `models` -- it reuses
    vol_manager.last_sabr_calibration instead of re-instantiating
    SABRCalibrator, so this chart's SABR curve is guaranteed to be the SAME
    fit as the reported SABR row (a second independent multi-start L-BFGS-B
    calibration is not guaranteed to land on the same local optimum).

    Returns a dict shaped for reports.save_comparison_pdf/save_comparison_csv,
    or None if the chain couldn't be fetched/built at all.
    """
    try:
        chain = _fetch_chain(ticker, market_exp, S, K, T, r, q)
        return _build_smile_curves(chain, S, K, T, r, q, models, vol_manager, atm_vol_vv, rr25, bf25)
    except Exception as e:
        print(f"[Smile Chart] Could not build smile comparison: {e}")
        return None


def _build_smile_curves(chain, S, K, T, r, q, models, vol_manager, atm_vol_vv, rr25, bf25,
                        precomputed_curves=None):
    """The curve-building body of build_smile_comparison, factored out so
    run_full_chain returns the SAME 'smile' payload without duplicating any
    of this logic (spec: NOTES_chain_evaluation.md, "full chain" section).

    precomputed_curves: optional {label: (strikes, ivs)} for curves the
    caller has ALREADY solved. run_full_chain passes CRR / Leisen-Reimer /
    Newton-Raphson / MC here, because its per-strike pass solves those exact
    same IVs (identical solver, identical (price, strike, right) inputs) --
    re-solving them a second time would be pure duplicated work, and for MC
    (a ~40-iteration LSM bisection per strike) it would roughly double the
    whole report's runtime. SABR / VannaVolga / Heston are NOT precomputed:
    their curves are cheap vectorized formula evaluations over `grid`
    (which includes the focus K), so they stay exactly as before."""
    m_strikes = chain['strikes']
    m_prices = chain['prices']
    m_rights = chain['rights']
    grid = chain['grid']
    precomputed_curves = precomputed_curves or {}

    try:
        smile_curves = {}
        for _label, _curve in precomputed_curves.items():
            _ks, _ivs = _curve
            if len(_ks) >= 3:
                _order = np.argsort(_ks)
                smile_curves[_label] = (np.asarray(_ks, dtype=float)[_order],
                                        np.asarray(_ivs, dtype=float)[_order])
            else:
                print(f"[Smile Chart] {_label} chain-wide solve produced only {len(_ks)} usable points -- skipping curve.")

        def _solve_chain(label, solver_fn):
            """Solve one model's own IV at every real chain strike
            against that strike's own observed price -- not a single
            flat sigma. Per-strike failures are skipped (not faked),
            and if too few strikes solve at all, the curve is dropped
            with a printed reason rather than silently shown empty."""
            if label in precomputed_curves:
                return  # caller already solved this exact curve -- see precomputed_curves
            ks, ivs = [], []
            for k, p, right in zip(m_strikes, m_prices, m_rights):
                if p is None or not np.isfinite(p) or p <= 0:
                    continue
                cp = (right == 'C')
                try:
                    iv = solver_fn(p, k, cp)
                except Exception:
                    continue
                if iv is None or not np.isfinite(iv) or iv <= 0:
                    continue
                ks.append(k)
                ivs.append(iv)
            if len(ks) < 3:
                print(f"[Smile Chart] {label} chain-wide solve produced only {len(ks)} usable points -- skipping curve.")
                return
            order = np.argsort(ks)
            smile_curves[label] = (np.array(ks)[order], np.array(ivs)[order])

        def _solve_chain_batch(label, solver_batch_fn):
            """Batched counterpart to _solve_chain: same filtering
            and drop-if-too-few-points behavior, but calls
            solver_batch_fn ONCE with arrays of every valid strike's
            (price, strike, call/put) instead of once per strike.
            This is what turns a chain-wide CRR/LR/NR IV solve from
            ~150 strikes x ~50 bisection iterations of from-scratch
            Python-level tree builds into ~50 vectorized iterations
            total -- see brute_force_batch/brute_force_lr_batch and
            american_binomial.py's batched-pricer module note."""
            if label in precomputed_curves:
                return  # caller already solved this exact curve -- see precomputed_curves
            mask = _usable_quote_mask(m_prices)
            if not np.any(mask):
                print(f"[Smile Chart] {label} chain-wide solve produced 0 usable points -- skipping curve.")
                return
            ks = np.asarray(m_strikes, dtype=float)[mask]
            ps = np.asarray(m_prices, dtype=float)[mask]
            cps = np.array([right == 'C' for right in np.asarray(m_rights, dtype=object)[mask]])

            ivs = solver_batch_fn(ps, ks, cps)
            valid = np.isfinite(ivs) & (ivs > 0)
            ks, ivs = ks[valid], ivs[valid]
            if ks.shape[0] < 3:
                print(f"[Smile Chart] {label} chain-wide solve produced only {ks.shape[0]} usable points -- skipping curve.")
                return
            order = np.argsort(ks)
            smile_curves[label] = (ks[order], ivs[order])

        try:
            _solve_chain_batch('CRR', lambda p_arr, k_arr, cp_arr: brute_force_batch(p_arr, S, k_arr, T, r, cp_arr, q=q))
        except Exception as e:
            print(f"[Smile Chart] CRR curve failed: {e}")

        try:
            _solve_chain_batch('Leisen-Reimer',
                                lambda p_arr, k_arr, cp_arr: brute_force_lr_batch(p_arr, S, k_arr, T, r, cp_arr, q=q))
        except Exception as e:
            print(f"[Smile Chart] Leisen-Reimer curve failed: {e}")

        try:
            # Newton-Raphson's chart curve reuses the same batched
            # Leisen-Reimer bisection as the 'Leisen-Reimer' curve
            # above, rather than a separate batched-Newton solve.
            # Both invert the exact same leisen_reimer_american_price
            # against the exact same (market price, strike) pairs --
            # Newton and bisection are just two algorithms converging
            # on the same root of the same monotonic price(sigma), and
            # this was verified directly (several strikes, calls and
            # puts) to agree with implied_volatility_nr_american's
            # scalar Newton to ~1e-6 in sigma.
            _solve_chain_batch('Newton-Raphson',
                                lambda p_arr, k_arr, cp_arr: brute_force_lr_batch(p_arr, S, k_arr, T, r, cp_arr, q=q))
        except Exception as e:
            print(f"[Smile Chart] Newton-Raphson curve failed: {e}")

        try:
            if 'MC' in precomputed_curves:
                # Already solved by the caller (run_full_chain) -- skip before
                # even allocating the shared draw array below.
                raise _AlreadySolved()
            # Fewer sims/steps than the single-price MC model number
            # (pricing_config.simulations/steps) -- this bisects EVERY
            # chain strike independently (each its own ~40-iteration
            # bisection), which is why this is still the slowest curve
            # on the chart even after the fix below -- batching MC's
            # own Longstaff-Schwartz regression across a strike axis
            # (unlike the tree pricers above) would need per-strike
            # ITM-masked regressions of different sizes, which doesn't
            # vectorize as cleanly; left as a per-strike loop.
            #
            # What IS fixed: `seed` is fixed (42) everywhere in
            # brute_force_mc, so every one of its internal price
            # evaluations (lo probe, hi probe, each of 40 mid-bisection
            # steps) regenerated the SAME (steps, simulations) random
            # draw array from scratch -- and every strike in this loop
            # did that independently too. Generating that array ONCE
            # here and passing it through (`rand=`) makes every call
            # reuse the identical draws instead of recomputing them
            # thousands of times over the whole chain -- same "common
            # random numbers" property brute_force_mc's own docstring
            # already relies on, just hoisted out to its natural scope.
            _mc_sims, _mc_steps = 4000, 60
            _mc_rand = AmericanLSMPricer(S, K, T, r, q, 0.3, simulations=_mc_sims, steps=_mc_steps,
                                          option='call')._generate_rand(seed=42)
            _solve_chain('MC', lambda p, k, cp: brute_force_mc(
                p, S, k, T, r, cp, q=q, simulations=_mc_sims, steps=_mc_steps, rand=_mc_rand))
        except _AlreadySolved:
            pass
        except Exception as e:
            print(f"[Smile Chart] MC curve failed: {e}")

        try:
            # Reuse the EXACT calibration that produced the reported
            # "SABR" row (models['SABR']) -- do NOT re-instantiate and
            # re-run SABRCalibrator.calibrate() here. That was a real
            # bug: a second, independent multi-start L-BFGS-B
            # calibration is not guaranteed to land on the same local
            # optimum as the first, so the chart could show a
            # DIFFERENT SABR fit than the table. vol_manager caches
            # the calibration it just ran (see get_sigma's SABR
            # branch); if that's missing for any reason, this is a
            # real failure to surface, not a reason to silently
            # recalibrate from scratch.
            cached = vol_manager.last_sabr_calibration
            if not cached:
                raise RuntimeError("No cached SABR calibration from vol_manager.get_sigma -- cannot build a consistent curve.")
            sabr_cal = cached['calibrator']
            sabr_calib = cached['calib']
            # Vectorized (one numpy pass over all strikes) instead of
            # a Python-level call to the scalar sabr_vol_hagan per
            # strike -- same formula (see _sabr_vol_hagan_vec's
            # docstring in SABRModel.py: it only skips the separate
            # near-exact-ATM branch, which a real discrete strike grid
            # essentially never lands on).
            sabr_curve = _sabr_vol_hagan_vec(sabr_cal.forward, grid, T, sabr_calib['alpha'],
                                              sabr_calib['beta'], sabr_calib['rho'], sabr_calib['nu'])
            smile_curves['SABR'] = (grid, sabr_curve)
        except Exception as e:
            print(f"[Smile Chart] SABR curve failed: {e}")

        try:
            if atm_vol_vv:
                # Vectorized -- see VannaVolga.get_vol_batch. Also picks
                # up the RR/BF symmetry fix (get_vol used to apply BF
                # anti-symmetrically and RR symmetrically -- backwards --
                # which made the curve nearly flat regardless of how
                # strong the real 25-delta risk reversal was).
                vv_curve = vanna_volga_vol_batch(S, grid, T, r, q, atm_vol_vv, rr25, bf25)
                smile_curves['VannaVolga'] = (grid, vv_curve)
        except Exception as e:
            print(f"[Smile Chart] VannaVolga curve failed: {e}")

        try:
            calib_h = models.get('Heston', {}).get('calib') or {}
            if calib_h:
                # Vectorized -- one shared Gauss-Legendre quadrature
                # over all strikes (heston_call_prices_batch) plus one
                # vectorized Newton pass (_bs_iv_batch), instead of
                # looping heston_european_call_price's two adaptive
                # `quad` integrals PLUS a scalar Newton solve per
                # strike. Same batch machinery HestonCalibrator.calibrate()
                # already uses (see MCHestonLSM.heston_iv_smile_batch).
                heston_curve = MCHestonLSM.heston_iv_smile_batch(
                    S, grid, T, r, q, calib_h['v0'], calib_h['kappa'], calib_h['theta'],
                    calib_h['xi'], calib_h['rho'],
                )
                smile_curves['Heston'] = (grid, heston_curve)
        except Exception as e:
            print(f"[Smile Chart] Heston curve failed: {e}")

        return {
            'market_strikes': m_strikes, 'market_ivs': chain['ivs'], 'market_sources': chain['sources'],
            'market_rights': m_rights,
            'curves': smile_curves,
            'flats': {},
        }
    except Exception as e:
        print(f"[Smile Chart] Could not build smile comparison: {e}")
        return None


# ---------------------------------------------------------------------------
# Full-chain evaluation mode
# ---------------------------------------------------------------------------

def _greek_subset(greeks, keys):
    """Take only `keys` out of a model's own Greek dict, and only if every
    one of them is a finite number. Returns None otherwise, so the caller
    OMITS that strike rather than publishing a partially-populated (or
    NaN-bearing) Greek row -- the same "no fake numbers" rule the rest of
    this module follows."""
    if not greeks:
        return None
    out = {}
    for key in keys:
        val = greeks.get(key)
        try:
            val = float(val)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(val):
            return None
        out[key] = val
    return out


def _full_greeks(greeks):
    """Pass a model's own Greek dict through whole (1st + 2nd/3rd order,
    plus whatever extras that engine reports -- e.g. rho_euro /
    rho_ee_premium), but only once the spec'd 10 are all finite."""
    if _greek_subset(greeks, FIRST_ORDER_GREEKS + HIGHER_ORDER_GREEKS) is None:
        return None
    return dict(greeks)


def run_full_chain(ticker, market_exp, S, K, T, r, q, models, vol_manager,
                   atm_vol_vv, rr25, bf25, option_type,
                   mc_sims=4000, mc_steps=60,
                   heston_sims=8000, heston_steps=150,
                   heston_greek_sims=8000, heston_greek_steps=100,
                   greek_steps=401, verbose=True,
                   include_mc=True, include_heston=True):
    """Full-chain evaluation: every model, every real strike in the market
    chain -- price + IV + Greeks -- plus the existing smile-comparison
    payload. See NOTES_chain_evaluation.md, "SPEC (2026-07-28): 'full
    chain' evaluation mode", for the approved scope; this implements it.

    Each model uses ITS OWN pricer and ITS OWN Greek engine at every
    strike, never another model's (the same principle the single-K report
    enforces):

      CRR            crr_american_price_batch / crr_all_greeks
      Leisen-Reimer  leisen_reimer_american_price_batch / lr_all_greeks
      Newton-Raphson leisen_reimer_american_price_batch / lr_all_greeks
                     (NR prices through the LR tree -- same as the
                     single-K report's Newton-Raphson row)
      SABR           _sabr_vol_hagan_vec smile -> LR-tree price /
                     sabr_all_greeks (bumps propagate through the smile)
      VannaVolga     get_vol_batch smile -> LR-tree price / vv_all_greeks
      BAW            baw_american_price / baw_all_greeks
      MC             AmericanLSMPricer / mc_all_greeks (CRN)
      Heston         heston_lsm_price / heston_all_greeks (CRN)

    Sigma convention per strike, matching the single-K report exactly:
    the price-inverting models (CRR / LR / NR / MC / BAW) solve their OWN
    IV at each strike from THAT strike's own observed market price (so
    their price reproduces the market's, by construction -- that IS what
    vol_manager.get_sigma does at K today); the smile models (SABR /
    VannaVolga / Heston) evaluate their already-calibrated smile at the
    strike and price at that sigma, so their price genuinely differs from
    the market's.

    Right (call/put) per strike comes from the CHAIN, not from
    `option_type`: fetch_market_smile returns one OTM row per strike (put
    below the forward, call above), and that is the contract whose price
    is being inverted. `option_type` is used only for the focus-K block.

    2nd/3rd-order Greeks (Vanna/Vomma/Speed/Charm/Color) are published at
    every strike for CRR/LR/NR/SABR/VV/BAW (cheap -- FD on a tree or on an
    analytic pricer) but NOT for MC/Heston, whose per-strike dicts carry
    the 1st-order block only. Those two get their higher-order Greeks
    once, at the focus K, in 'focus_k_greeks' -- exactly what the single-K
    report already shows for them. This was an explicit, approved
    accuracy-vs-runtime tradeoff: MC/Heston Greeks are CRN
    bump-and-revalue on a full LSM re-simulation, so their per-strike call
    is already the dominant cost of this whole function.

    Heston reuses the ONE calibration in models['Heston']['calib'] at
    every strike -- never re-calibrated per strike (that would be both
    methodologically wrong, since the whole point is one calibration
    evaluated across strikes, and prohibitively slow). Same principle SABR
    uses via vol_manager.last_sabr_calibration.

    Simulation sizes (all caller-overridable):
      * heston_sims/steps default to config.PricingConfig's own
        heston_compare_sims/steps (8000/150) -- the same size the single-K
        report's Heston price uses -- and heston_greek_sims/steps default
        to heston_all_greeks' OWN defaults (8000/100). Do not lower these
        casually: at 3000/60 this chain's ATM Heston Gamma came back
        NEGATIVE (-0.001483) purely from LSM noise, vs +0.004690 at
        8000/100. The approved scope tradeoff was about WHICH Greeks get
        computed per strike, not about computing them less accurately.
      * mc_sims/steps default to 4000/60, matching the chain-wide MC
        precedent already set by _build_smile_curves' MC branch (mc_all_greeks'
        own single-strike default of 50000/100 costs ~2.9s per strike here
        vs ~0.14s, i.e. ~4 minutes of MC Greeks alone on this 89-strike
        chain). Measured spread at 4000/60 vs 50000/100 on this chain's ATM
        strike: Delta -0.40329 vs -0.40986, Vega 91.19 vs 90.05.

    include_mc / include_heston (default True, both -- 2026-07-28: Jason
    flagged the full-chain report as taking too long in practice; live
    timing on the 89-strike AMD chain was MC=26.1s + Heston=22.5s of a
    63.6s total, i.e. these two models were ~76% of the whole run). Set
    either to False to skip that model ENTIRELY from the chain -- not just
    its per-strike Greeks (already scoped to 1st-order only, see above),
    the price/IV solve too, AND its smile curve (MC's chain-wide IV curve is
    itself a ~40-iteration-per-strike bisection, independently expensive of
    its Greeks -- see the "Known limitations" note in
    NOTES_chain_evaluation.md; Heston's own curve is cheap/vectorized
    already, excluded anyway for consistency when asked to skip Heston).
    The model is simply absent from 'per_model'/'focus_k_greeks'/
    'smile'['curves'] when disabled -- not an empty/zero-filled entry.
    `main.py`'s choice == '10' passes both as False by default now; this
    function still defaults both to True so any other direct caller's
    behavior is unchanged.

    NO FAKE NUMBERS. A strike that fails for a model (exception,
    non-finite IV/price/Greek, non-positive price) is OMITTED from that
    model's dict -- never zero-filled, never borrowed from a neighbouring
    strike or another model. A model whose whole chain fails is omitted
    from 'per_model' with a printed reason.

    Returns:
        {
          'strikes': np.ndarray,          # every real chain strike, sorted
          'market': {strike: {'iv','price','right','source'}},
          'per_model': {
              'CRR' | 'Leisen-Reimer' | 'Newton-Raphson' | 'SABR' |
              'VannaVolga' | 'BAW':
                  {strike: {'price','iv','right',
                            'greeks': {delta,gamma,vega,rho,theta,
                                       vanna,vomma,speed,charm,color, ...}}},
              'MC' | 'Heston':
                  {strike: {'price','iv','right',
                            'greeks': {delta,gamma,vega,rho,theta}}},
          },
          'focus_k_greeks': {'MC': {vanna,vomma,speed,charm,color},
                             'Heston': {...}},   # focus K only; a model is
                                                 # absent if unavailable
          'smile': {...},                 # exactly build_smile_comparison's shape
          'meta': {'ticker','expiry','S','K','T','r','q','option_type',
                   'forward','n_strikes','elapsed_sec','timings'},
        }
    Returns None if the chain itself couldn't be fetched.
    """
    t_start = time.time()
    timings = {}

    def _log(msg):
        if verbose:
            print(f"[Full Chain] {msg}")

    try:
        chain = _fetch_chain(ticker, market_exp, S, K, T, r, q)
    except Exception as e:
        print(f"[Full Chain] Could not fetch market chain: {e}")
        return None

    m_strikes = chain['strikes']
    if len(m_strikes) == 0:
        print("[Full Chain] Market chain came back empty -- nothing to evaluate.")
        return None

    # Market block -- straight from the vendor chain, no model involved.
    market = {}
    for k, iv, price, right, src in zip(chain['strikes'], chain['ivs'], chain['prices'],
                                        chain['rights'], chain['sources']):
        market[float(k)] = {
            'iv': float(iv) if iv is not None and np.isfinite(iv) else None,
            'price': float(price) if price is not None and np.isfinite(price) else None,
            'right': right,
            'source': src,
        }

    # Strikes with a real, invertible quote -- the only ones the
    # price-inverting models can say anything about at all (see
    # NOTES_chain_evaluation.md "Known limitations": this is inherent, you
    # cannot invert a price that does not exist).
    mask = _usable_quote_mask(chain['prices'])
    ks_q = np.asarray(chain['strikes'], dtype=float)[mask]
    ps_q = np.asarray(chain['prices'], dtype=float)[mask]
    rights_q = np.asarray(chain['rights'], dtype=object)[mask]
    cps_q = np.array([rt == 'C' for rt in rights_q], dtype=bool)

    _log(f"{ticker} {market_exp}: {len(m_strikes)} chain strikes, "
         f"{ks_q.shape[0]} with an invertible quote. "
         f"S={S:.4f} T={T:.6f} r={r:.4f} q={q:.4f}")

    per_model = {}
    precomputed_curves = {}

    def _register(label, strikes_arr, ivs_arr, prices_arr, greeks_list, rights_arr,
                  curve_ivs=False):
        """Assemble one model's per-strike dict, dropping any strike whose
        price or Greek row did not come out clean (omitted, never faked).
        curve_ivs=True also records this model's solved IV curve so the
        smile chart can reuse it instead of re-solving it."""
        rows = {}
        ks_kept, ivs_kept = [], []
        n_in = 0
        for k, sig, px, greeks, rt in zip(strikes_arr, ivs_arr, prices_arr, greeks_list,
                                          rights_arr):
            n_in += 1
            if px is None or not np.isfinite(px) or px <= 0:
                continue
            if greeks is None:
                continue
            rows[float(k)] = {'price': float(px), 'iv': float(sig), 'right': rt,
                              'greeks': greeks}
            ks_kept.append(float(k))
            ivs_kept.append(float(sig))
        if not rows:
            print(f"[Full Chain] {label}: 0 strikes produced a usable price+Greek row "
                  f"-- omitting model (no substituted numbers).")
            return
        per_model[label] = rows
        if curve_ivs and len(ks_kept) >= 3:
            precomputed_curves[label] = (np.array(ks_kept), np.array(ivs_kept))
        _log(f"{label}: {len(rows)}/{n_in} strikes OK")

    # ---- CRR: own tree for price, own tree FD for Greeks -----------------
    t0 = time.time()
    try:
        ivs = brute_force_batch(ps_q, S, ks_q, T, r, cps_q, q=q)
        ok = np.isfinite(ivs) & (ivs > 0)
        ks_i, ivs_i, cps_i, rts_i = ks_q[ok], ivs[ok], cps_q[ok], rights_q[ok]
        prices = crr_american_price_batch(S, ks_i, T, r, ivs_i, q=q, cp=cps_i)
        greeks_list = []
        for k, sig, cp in zip(ks_i, ivs_i, cps_i):
            try:
                greeks_list.append(_full_greeks(crr_all_greeks(S, float(k), T, r, float(sig),
                                                               q, bool(cp), steps=greek_steps)))
            except Exception:
                greeks_list.append(None)
        _register('CRR', ks_i, ivs_i, prices, greeks_list, rts_i, curve_ivs=True)
    except Exception as e:
        print(f"[Full Chain] CRR failed: {e}")
    timings['CRR'] = time.time() - t0

    # ---- Leisen-Reimer and Newton-Raphson --------------------------------
    # Both price through the LR tree and both invert that same tree, so the
    # single batched LR bisection below IS genuinely each model's own solve
    # (see the Newton-Raphson note in _build_smile_curves: verified against
    # implied_volatility_nr_american's scalar Newton to ~1e-6 in sigma).
    # They stay two separate rows because that is how the single-K report
    # reports them.
    t0 = time.time()
    try:
        ivs = brute_force_lr_batch(ps_q, S, ks_q, T, r, cps_q, q=q)
        ok = np.isfinite(ivs) & (ivs > 0)
        ks_i, ivs_i, cps_i, rts_i = ks_q[ok], ivs[ok], cps_q[ok], rights_q[ok]
        prices = leisen_reimer_american_price_batch(S, ks_i, T, r, ivs_i, q=q, cp=cps_i)
        greeks_list = []
        for k, sig, cp in zip(ks_i, ivs_i, cps_i):
            try:
                greeks_list.append(_full_greeks(lr_all_greeks(S, float(k), T, r, float(sig),
                                                              q, bool(cp), steps=greek_steps)))
            except Exception:
                greeks_list.append(None)
        for label in ('Leisen-Reimer', 'Newton-Raphson'):
            _register(label, ks_i, ivs_i, prices, greeks_list, rts_i, curve_ivs=True)
    except Exception as e:
        print(f"[Full Chain] Leisen-Reimer / Newton-Raphson failed: {e}")
    timings['Leisen-Reimer+Newton-Raphson'] = time.time() - t0

    # ---- SABR: the ONE cached calibration, evaluated across the chain ----
    t0 = time.time()
    try:
        cached = getattr(vol_manager, 'last_sabr_calibration', None)
        if not cached or not cached.get('calib'):
            raise RuntimeError("No cached SABR calibration from vol_manager.get_sigma -- cannot "
                               "evaluate a consistent chain (a second multi-start L-BFGS-B fit "
                               "is not guaranteed to be the same one, so no re-calibration here).")
        sabr_cal, sabr_calib = cached['calibrator'], cached['calib']
        sig_sabr = _sabr_vol_hagan_vec(sabr_cal.forward, ks_q, T, sabr_calib['alpha'],
                                       sabr_calib['beta'], sabr_calib['rho'], sabr_calib['nu'])
        ok = np.isfinite(sig_sabr) & (sig_sabr > 0)
        ks_i, sig_i, cps_i, rts_i = ks_q[ok], sig_sabr[ok], cps_q[ok], rights_q[ok]
        # SABR prices through the LR tree at its own smile sigma -- same as
        # the single-K report's SABR row.
        prices = leisen_reimer_american_price_batch(S, ks_i, T, r, sig_i, q=q, cp=cps_i)
        greeks_list = []
        for k, cp in zip(ks_i, cps_i):
            try:
                greeks_list.append(_full_greeks(sabr_all_greeks(S, float(k), T, r, q, bool(cp),
                                                                sabr_calib, steps=greek_steps)))
            except Exception:
                greeks_list.append(None)
        _register('SABR', ks_i, sig_i, prices, greeks_list, rts_i)
    except Exception as e:
        print(f"[Full Chain] SABR failed: {e}")
    timings['SABR'] = time.time() - t0

    # ---- Vanna-Volga -----------------------------------------------------
    t0 = time.time()
    try:
        if atm_vol_vv is None or atm_vol_vv <= 0:
            raise RuntimeError("No atm_vol for Vanna-Volga (get_auto_rr_bf returned no market "
                               "read) -- no smile to evaluate, and no substitute vol.")
        sig_vv = vanna_volga_vol_batch(S, ks_q, T, r, q, atm_vol_vv, rr25, bf25)
        ok = np.isfinite(sig_vv) & (sig_vv > 0)
        ks_i, sig_i, cps_i, rts_i = ks_q[ok], sig_vv[ok], cps_q[ok], rights_q[ok]
        prices = leisen_reimer_american_price_batch(S, ks_i, T, r, sig_i, q=q, cp=cps_i)
        greeks_list = []
        for k, cp in zip(ks_i, cps_i):
            try:
                greeks_list.append(_full_greeks(vv_all_greeks(S, float(k), T, r, q, bool(cp),
                                                              atm_vol=atm_vol_vv, rr25=rr25,
                                                              bf25=bf25, steps=greek_steps)))
            except Exception:
                greeks_list.append(None)
        _register('VannaVolga', ks_i, sig_i, prices, greeks_list, rts_i)
    except Exception as e:
        print(f"[Full Chain] VannaVolga failed: {e}")
    timings['VannaVolga'] = time.time() - t0

    # ---- BAW: closed-form American, so per-strike is already cheap -------
    t0 = time.time()
    try:
        ks_i, ivs_i, prices_i, greeks_list, rts_i = [], [], [], [], []
        for k, p, cp, rt in zip(ks_q, ps_q, cps_q, rights_q):
            try:
                sig = brute_force_baw(float(p), S, float(k), T, r, q=q, cp=bool(cp))
                if sig is None or not np.isfinite(sig) or sig <= 0:
                    continue
                px = float(baw_american_price(S, float(k), T, r, float(sig), q, bool(cp)))
                g = _full_greeks(baw_all_greeks(S, float(k), T, r, float(sig), q, bool(cp)))
            except Exception:
                continue
            ks_i.append(k); ivs_i.append(sig); prices_i.append(px)
            greeks_list.append(g); rts_i.append(rt)
        _register('BAW', ks_i, ivs_i, prices_i, greeks_list, rts_i)
    except Exception as e:
        print(f"[Full Chain] BAW failed: {e}")
    timings['BAW'] = time.time() - t0

    # ---- MC (Longstaff-Schwartz): 1st-order Greeks per strike only -------
    # Skippable via include_mc=False -- see the docstring note above (this
    # was ~26s of a 63.6s live run, the single most expensive model here).
    t0 = time.time()
    if include_mc:
        try:
            # ONE shared (steps, simulations) draw array for the entire chain --
            # common random numbers, exactly as _build_smile_curves' MC branch
            # does. Its shape depends only on (steps, simulations), never on the
            # strike, so every strike's bisection and every CRN Greek bump
            # reuses the identical draws.
            mc_rand = AmericanLSMPricer(S, K, T, r, q, 0.3, simulations=mc_sims, steps=mc_steps,
                                        option='call')._generate_rand(seed=42)
            ks_i, ivs_i, prices_i, greeks_list, rts_i = [], [], [], [], []
            for k, p, cp, rt in zip(ks_q, ps_q, cps_q, rights_q):
                opt = 'call' if cp else 'put'
                try:
                    sig = brute_force_mc(float(p), S, float(k), T, r, bool(cp), q=q,
                                         simulations=mc_sims, steps=mc_steps, rand=mc_rand)
                    if sig is None or not np.isfinite(sig) or sig <= 0:
                        continue
                    pricer = AmericanLSMPricer(S, float(k), T, r, q, float(sig),
                                               simulations=mc_sims, steps=mc_steps, option=opt)
                    px = float(pricer.price_with_rand(mc_rand))
                    g = _greek_subset(mc_all_greeks(S, float(k), T, r, q, float(sig),
                                                    sims=mc_sims, steps=mc_steps, option=opt,
                                                    seed=42),
                                      FIRST_ORDER_GREEKS)
                except Exception:
                    continue
                ks_i.append(k); ivs_i.append(sig); prices_i.append(px)
                greeks_list.append(g); rts_i.append(rt)
            _register('MC', ks_i, ivs_i, prices_i, greeks_list, rts_i, curve_ivs=True)
        except Exception as e:
            print(f"[Full Chain] MC failed: {e}")
    else:
        _log("MC skipped (include_mc=False) -- also excludes MC's own smile-curve "
             "bisection, itself a separate ~40-iteration-per-strike cost.")
        # Tell _build_smile_curves this curve is "already handled" with zero
        # points, so it does NOT fall through to its own from-scratch MC
        # bisection (which is independently expensive of the Greeks loop
        # above -- see the docstring note). An empty pair fails the
        # len(ks)>=3 check there and is silently skipped, same as any other
        # curve with too few points.
        precomputed_curves['MC'] = (np.array([]), np.array([]))
    timings['MC'] = time.time() - t0

    # ---- Heston: ONE calibration, evaluated at every strike --------------
    # Skippable via include_heston=False -- see the docstring note above
    # (this was ~22.5s of a 63.6s live run).
    t0 = time.time()
    calib_h = (models.get('Heston') or {}).get('calib') or {}
    if include_heston:
        try:
            if not calib_h:
                raise RuntimeError("No Heston calibration in models['Heston']['calib'] -- Heston "
                                   "failed upstream. Reported as unavailable (never re-calibrated "
                                   "here, never substituted from another model).")
            v0, kappa = calib_h['v0'], calib_h['kappa']
            theta_h, xi, rho_h = calib_h['theta'], calib_h['xi'], calib_h['rho']
            # IVs: the SAME vectorized characteristic-function smile the chart
            # uses -- one shared Gauss-Legendre quadrature over all strikes.
            sig_h = MCHestonLSM.heston_iv_smile_batch(S, ks_q, T, r, q, v0, kappa, theta_h, xi, rho_h)
            ks_i, ivs_i, prices_i, greeks_list, rts_i = [], [], [], [], []
            for k, sig, cp, rt in zip(ks_q, sig_h, cps_q, rights_q):
                if not np.isfinite(sig) or sig <= 0:
                    continue
                opt = 'call' if cp else 'put'
                try:
                    # Price: Heston's own American LSM under the calibrated
                    # parameters -- NOT the European CF price used for the IV
                    # smile above, and not any tree.
                    px = float(MCHestonLSM.heston_lsm_price(
                        S0=S, K=float(k), T=T, r=r, q=q, V0=v0, kappa=kappa, theta=theta_h,
                        vol_sigma=xi, rho=rho_h, sims=heston_sims, steps=heston_steps,
                        option=opt, use_market_data=False, seed=42))
                    g = _greek_subset(MCHestonLSM.heston_all_greeks(
                        S, float(k), T, r, q, V0=v0, kappa=kappa, theta=theta_h, vol_sigma=xi,
                        rho=rho_h, sims=heston_greek_sims, steps=heston_greek_steps,
                        option=opt, seed=42), FIRST_ORDER_GREEKS)
                except Exception:
                    continue
                ks_i.append(k); ivs_i.append(sig); prices_i.append(px)
                greeks_list.append(g); rts_i.append(rt)
            _register('Heston', ks_i, ivs_i, prices_i, greeks_list, rts_i)
        except Exception as e:
            print(f"[Full Chain] Heston failed: {e}")
    else:
        _log("Heston skipped (include_heston=False).")
    timings['Heston'] = time.time() - t0

    # ---- MC / Heston higher-order Greeks: focus K ONLY -------------------
    # Deliberately not per-strike (approved scope decision above). Taken
    # from the single-K report's own already-computed Greek dicts when the
    # caller supplied them, so these ARE the numbers that report shows;
    # only recomputed here if they weren't supplied.
    focus_k_greeks = {}
    t0 = time.time()
    _focus_labels = (('MC',) if include_mc else ()) + (('Heston',) if include_heston else ())
    for label in _focus_labels:
        sub = _greek_subset((models.get(label) or {}).get('greeks'), HIGHER_ORDER_GREEKS)
        if sub is not None:
            focus_k_greeks[label] = sub
            continue
        try:
            if label == 'MC':
                sig_focus = (models.get('MC') or {}).get('sigma')
                if sig_focus is None:
                    raise RuntimeError("no MC sigma at the focus K in models['MC']")
                g = mc_all_greeks(S, K, T, r, q, float(sig_focus), sims=mc_sims, steps=mc_steps,
                                  option=option_type, seed=42)
            else:
                if not calib_h:
                    raise RuntimeError("no Heston calibration")
                g = MCHestonLSM.heston_all_greeks(
                    S, K, T, r, q, V0=calib_h['v0'], kappa=calib_h['kappa'],
                    theta=calib_h['theta'], vol_sigma=calib_h['xi'], rho=calib_h['rho'],
                    sims=heston_greek_sims, steps=heston_greek_steps,
                    option=option_type, seed=42)
            sub = _greek_subset(g, HIGHER_ORDER_GREEKS)
            if sub is not None:
                focus_k_greeks[label] = sub
        except Exception as e:
            print(f"[Full Chain] {label} focus-K higher-order Greeks unavailable: {e} "
                  f"-- omitted (no substituted number).")
    timings['focus_k_greeks'] = time.time() - t0

    # ---- Smile payload: the SAME builder build_smile_comparison uses -----
    t0 = time.time()
    smile = _build_smile_curves(chain, S, K, T, r, q, models, vol_manager, atm_vol_vv, rr25, bf25,
                                precomputed_curves=precomputed_curves)
    if not include_heston:
        # Heston's curve itself is cheap (vectorized quadrature, not worth
        # gating for runtime), but pruned anyway for consistency -- Heston
        # is fully excluded from this chain when include_heston=False, not
        # partially present via just the smile chart.
        smile.get('curves', {}).pop('Heston', None)
    timings['smile'] = time.time() - t0

    elapsed = time.time() - t_start
    _log("done in {:.1f}s -- {}".format(
        elapsed, ", ".join(f"{lbl}={secs:.1f}s" for lbl, secs in timings.items())))

    return {
        'strikes': np.asarray(chain['strikes'], dtype=float),
        'market': market,
        'per_model': per_model,
        'focus_k_greeks': focus_k_greeks,
        'smile': smile,
        'meta': {
            'ticker': ticker, 'expiry': market_exp, 'S': S, 'K': K, 'T': T, 'r': r, 'q': q,
            'option_type': option_type, 'forward': chain['forward'],
            'n_strikes': int(len(chain['strikes'])),
            'elapsed_sec': elapsed, 'timings': timings,
        },
    }
