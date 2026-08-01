import numpy as np
from scipy.stats import norm
from datetime import datetime
import data_source_config
try:
    from thetadata_controller import ThetaDataController, strike_from_theta
    _THETADATA_AVAILABLE = True
except Exception:
    _THETADATA_AVAILABLE = False
from scipy.optimize import minimize


class SABRModel:
    def __init__(self, alpha=0.3, beta=0.5, rho=-0.3, nu=0.5):
        self.alpha = alpha
        self.beta = beta
        self.rho = rho
        self.nu = nu

    def get_vol(self, F, K, T):
        eps = 1e-10
        logFK = np.log(F / K + eps)
        if abs(F - K) < eps:
            term1 = self.alpha / (F ** (1 - self.beta))
            term2 = 1 + (
                ((1 - self.beta) ** 2 / 24) * (self.alpha ** 2 / (F ** (2 - 2 * self.beta))) + 
                (self.rho * self.beta * self.nu * self.alpha) / (4 * F ** (1 - self.beta)) +
                (2 - 3 * self.rho ** 2) * self.nu ** 2 / 24
            ) * T
            return term1 * term2
        z = (self.nu / (self.alpha + eps)) * (F * K) ** ((1 - self.beta) / 2) * logFK
        x_z = np.log((np.sqrt(1 - 2 * self.rho * z + z ** 2 + eps) + z - self.rho) / (1 - self.rho + eps))
        num = self.alpha
        denom = (F * K) ** ((1 - self.beta) / 2) * (1 + ((1 - self.beta) ** 2 / 24) * logFK ** 2 + ((1 - self.beta) ** 4 / 1920) * logFK ** 4)
        z_over_xz = z / (x_z + eps)
        term3 = 1 + (
            ((1 - self.beta) ** 2 / 24) * (self.alpha ** 2 / (F * K) ** (1 - self.beta)) +
            (self.rho * self.beta * self.nu * self.alpha) / (4 * (F * K) ** ((1 - self.beta) / 2)) +
            (2 - 3 * self.rho ** 2) * self.nu ** 2 / 24
        ) * T
        return (num / (denom + eps)) * z_over_xz * term3

def sabr_vol_hagan(F, K, T, alpha, beta, rho, nu):
    eps = 1e-10
    logFK = np.log(F / K + eps)
    if abs(F - K) < eps:
        term1 = alpha / (F ** (1 - beta))
        term2 = 1 + (
            ((1 - beta) ** 2 / 24) * (alpha ** 2 / (F ** (2 - 2 * beta))) + 
            (rho * beta * nu * alpha) / (4 * F ** (1 - beta)) +
            (2 - 3 * rho ** 2) * nu ** 2 / 24
        ) * T
        return term1 * term2
    z = (nu / (alpha + eps)) * (F * K) ** ((1 - beta) / 2) * logFK
    x_z = np.log((np.sqrt(1 - 2 * rho * z + z ** 2 + eps) + z - rho) / (1 - rho + eps))
    num = alpha
    denom = (F * K) ** ((1 - beta) / 2) * (1 + ((1 - beta) ** 2 / 24) * logFK ** 2 + ((1 - beta) ** 4 / 1920) * logFK ** 4)
    z_over_xz = z / (x_z + eps)
    term3 = 1 + (
        ((1 - beta) ** 2 / 24) * (alpha ** 2 / (F * K) ** (1 - beta)) +
        (rho * beta * nu * alpha) / (4 * (F * K) ** ((1 - beta) / 2)) +
        (2 - 3 * rho ** 2) * nu ** 2 / 24
    ) * T
    return (num / (denom + eps)) * z_over_xz * term3

def bs_vega_sabr(F, K, T, sigma):
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
    return F * np.sqrt(T) * norm.pdf(d1)


def _sabr_vol_hagan_vec(F, K, T, alpha, beta, rho, nu):
    """Vectorized Hagan SABR vol over an array of strikes K -- used only
    inside SABRCalibrator's optimization objective (_weighted_error).

    Since smile_utils.fetch_market_smile stopped capping the chain at 15
    near-the-money strikes (see its docstring), calibration now typically
    sees 50-150+ real strikes instead of ~10. The old per-strike Python loop
    in _weighted_error called the scalar sabr_vol_hagan once per strike per
    objective evaluation; with 61 multi-start optimizer restarts x tens of
    internal iterations x 100+ strikes, that pure-Python loop became the
    dominant cost and pushed a single calibrate() call past a minute.
    Vectorizing it (one numpy pass over all strikes per objective
    evaluation, instead of a strikes-long Python loop) removes that cost
    without touching how many strikes are actually used.

    Uses the general (non-ATM) Hagan branch only -- skips sabr_vol_hagan's
    separate near-exact-ATM branch, using the standard z/x(z) -> 1 limit
    (Hagan et al. 2002) as strike approaches the forward instead. That
    branch only matters when a strike coincides with the forward to within
    1e-10; a real, discrete strike grid essentially never lands there, so
    this is a safe simplification for a performance-critical inner loop.
    The scalar sabr_vol_hagan used everywhere else (e.g. the smile chart's
    plotted curve) is untouched and keeps the exact near-ATM branch.
    """
    eps = 1e-10
    K = np.asarray(K, dtype=float)
    logFK = np.log(F / K + eps)
    z = (nu / (alpha + eps)) * (F * K) ** ((1 - beta) / 2) * logFK
    x_z = np.log((np.sqrt(1 - 2 * rho * z + z ** 2 + eps) + z - rho) / (1 - rho + eps))
    num = alpha
    denom = (F * K) ** ((1 - beta) / 2) * (1 + ((1 - beta) ** 2 / 24) * logFK ** 2 + ((1 - beta) ** 4 / 1920) * logFK ** 4)
    z_over_xz = np.where(np.abs(x_z) < eps, 1.0, z / (x_z + eps))
    term3 = 1 + (
        ((1 - beta) ** 2 / 24) * (alpha ** 2 / (F * K) ** (1 - beta)) +
        (rho * beta * nu * alpha) / (4 * (F * K) ** ((1 - beta) / 2)) +
        (2 - 3 * rho ** 2) * nu ** 2 / 24
    ) * T
    return (num / (denom + eps)) * z_over_xz * term3

class SABRCalibrator:
    def __init__(self, ticker, expiry_date, r=0.05, q=0.0, beta=0.5):
        self.ticker = ticker
        self.expiry_date = expiry_date
        self.r = r
        self.q = q
        self.beta = beta
        self.S = self._get_spot_price()
        self.T = self._get_time_to_maturity()
        print(f"\n[SABR Debug] Spot={self.S:.2f}, r={self.r:.4f}, q={self.q:.4f}, Target expiry={expiry_date}, T={self.T:.4f}yr")
        self.strikes, self.market_vols, self.forward = self._fetch_and_prepare()
        print(f"[SABR Debug] Prepared data: {len(self.strikes)} points")
        if len(self.strikes) > 0:
            print(f"[SABR Debug] Strike range: {self.strikes[0]:.1f} to {self.strikes[-1]:.1f}")
            print(f"[SABR Debug] IV range: {min(self.market_vols):.4f} to {max(self.market_vols):.4f}")
            print(f"[SABR Debug] Median IV: {np.median(self.market_vols):.4f}")
            print(f"[SABR Debug] Forward: {self.forward:.2f} (theoretical: S*exp((r-q)T))")

    def _get_spot_price(self):
        # PotatoHedge/ThetaData only (yahoo purged). Spot is essential to the fit.
        if _THETADATA_AVAILABLE:
            try:
                td = ThetaDataController()
                val = td.fetch_spot_price(self.ticker)
                td.close()
                if val and val > 0:
                    return float(val)
            except Exception:
                pass
        raise ValueError(f"[SABR] Could not fetch spot for {self.ticker} from PotatoHedge (yahoo removed).")

    def _get_time_to_maturity(self):
        expiry = datetime.strptime(self.expiry_date, "%Y-%m-%d")
        return max((expiry - datetime.now()).days / 365.0, 0.001)

    def _find_nearest_expiry(self):
        # PotatoHedge/ThetaData expirations only (yahoo purged).
        #
        # BUG FIX: list_expirations() returns dates as YYYYMMDD (e.g.
        # '20260727', no dashes -- verified against a live call), but this was
        # parsing them with strptime format "%Y-%m-%d" (dashed). That raised
        # ValueError on every single candidate `d`, every single call,
        # silently swallowed by the except below -- this method has always
        # returned None, and _fetch_and_prepare's flat-vol fallback ran on
        # every run regardless of real data availability (confirmed live:
        # SABR's own "IV range" debug line always showed a single repeated
        # value, e.g. "0.4258 to 0.4258", identical to fallback_vol). Fixed to
        # match the vendor's actual format.
        if _THETADATA_AVAILABLE:
            try:
                td = ThetaDataController()
                exps = td.list_expirations(self.ticker)
                td.close()
                if exps:
                    target = datetime.strptime(self.expiry_date, "%Y-%m-%d")
                    parsed = [(abs((datetime.strptime(d, "%Y%m%d") - target).days), d) for d in exps]
                    parsed.sort()
                    print(f"[SABR Debug] Nearest expiry: {parsed[0][1]} (diff={parsed[0][0]} days)")
                    return parsed[0][1]
            except Exception as e:
                print(f"[SABR Debug] _find_nearest_expiry failed: {e}")
        return None

    def _fetch_and_prepare(self):
        # NO FALLBACKS: this used to degrade to a flat synthetic smile
        # (_make_fallback, a straight line at a hardcoded fallback_vol) on
        # ANY failure -- no listed expiry found, an insufficient/failed
        # smile fetch, or any other exception. That flat-vol fallback is
        # exactly what silently ran on EVERY call before the YYYYMMDD
        # date-format bug in _find_nearest_expiry was found and fixed (see
        # that method's docstring) -- it was never a rare degrade path, it
        # was masking a real bug. Now every failure raises so it's visible
        # and debuggable instead of quietly producing a fake calibration.
        nearest = self._find_nearest_expiry()
        if nearest is None:
            raise RuntimeError(f"[SABR] Could not resolve a listed expiry near {self.expiry_date} for {self.ticker}. No fallback.")

        # Theoretical forward for SABR: F = S * exp((r - q) * T)
        forward = self.S * np.exp((self.r - self.q) * self.T)

        if not (getattr(data_source_config, 'PREFER_THETADATA', True) and _THETADATA_AVAILABLE):
            raise RuntimeError("[SABR] ThetaData unavailable (yahoo fallback removed) -- cannot fetch a real smile. No fallback.")

        from smile_utils import fetch_market_smile
        strikes, vols, sources, _fwd, _prices, _rights = fetch_market_smile(
            self.ticker, nearest, self.S, self.T, self.r, self.q,
        )
        if len(strikes) < 3:
            raise RuntimeError(
                f"[SABR] Insufficient smile points ({len(strikes)}) for {self.ticker} {nearest} "
                f"-- cannot calibrate. No fallback."
            )
        n_solved = sum(1 for s in sources if s == 'solved')
        print(f"[SABR Debug] Fetched {len(strikes)} smile points for {nearest} "
              f"({len(strikes) - n_solved} vendor IV, {n_solved} solved from price)")
        return strikes, vols, forward

    def _atm_market_vol(self):
        """
        True ATM market vol: the market vol at the strike nearest the forward.
        (Previously this used median(market_vols) across the whole ~9-strike
        window as an ATM proxy, which is not the same thing and was part of
        why calibration missed the ATM point.)
        """
        idx = int(np.argmin(np.abs(self.strikes - self.forward)))
        return float(self.market_vols[idx])

    def _solve_alpha_for_atm(self, target_atm_vol, beta, rho, nu):
        """
        Solve alpha so the Hagan SABR formula's ATM value (F == K) matches
        target_atm_vol exactly, via bounded bisection. The ATM formula is
        monotonically increasing in alpha for realistic parameter ranges, so
        this converges reliably and cheaply (closed-form eval, no simulation).

        alpha has units of vol * F^(1-beta), so its natural scale shifts a lot
        with beta and the forward level (e.g. beta=0.3 on a $100 forward needs
        alpha an order of magnitude larger than beta=1.0 does for the same
        vol). Bounds are scaled off a linear estimate rather than a fixed
        constant so low-beta / high-forward combinations don't get clipped.
        """
        F, T = self.forward, self.T

        def atm_vol(a):
            return sabr_vol_hagan(F, F, T, a, beta, rho, nu)

        alpha_scale = max(target_atm_vol * (F ** (1 - beta)), 1e-6)
        lo, hi = alpha_scale * 1e-4, alpha_scale * 50.0
        v_lo, v_hi = atm_vol(lo), atm_vol(hi)
        if v_hi <= v_lo:
            # non-monotonic edge case (extreme rho/nu/beta combo) -- fall back to a grid scan
            grid = np.linspace(lo, hi, 400)
            vals = np.array([atm_vol(a) for a in grid])
            return float(grid[int(np.argmin(np.abs(vals - target_atm_vol)))])
        if target_atm_vol <= v_lo:
            return lo
        if target_atm_vol >= v_hi:
            return hi
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            v_mid = atm_vol(mid)
            if abs(v_mid - target_atm_vol) < 1e-7:
                return mid
            if v_mid > target_atm_vol:
                hi = mid
            else:
                lo = mid
        return 0.5 * (lo + hi)

    def _weighted_error(self, alpha, beta, rho, nu):
        # Vectorized (see _sabr_vol_hagan_vec's docstring) -- this is called
        # once per optimizer iteration per multi-start restart, and the chain
        # is now the FULL observed strip (often 50-150+ strikes, not a
        # 15-strike cap), so a Python-level per-strike loop here was the
        # difference between a calibrate() call taking seconds vs minutes.
        strikes = np.asarray(self.strikes, dtype=float)
        vols = np.asarray(self.market_vols, dtype=float)
        valid = (strikes > 0) & (vols > 0)
        if not np.any(valid) or self.T <= 0:
            return 1e9
        K = strikes[valid]
        mv = vols[valid]
        try:
            sv = _sabr_vol_hagan_vec(self.forward, K, self.T, alpha, beta, rho, nu)
            if np.any(np.isnan(sv)) or np.any(np.isinf(sv)):
                return 1e9
            d1 = (np.log(self.forward / K) + 0.5 * mv ** 2 * self.T) / (mv * np.sqrt(self.T))
            vega = self.forward * np.sqrt(self.T) * norm.pdf(d1)
        except Exception:
            return 1e9
        w_mask = vega >= 1e-6
        if not np.any(w_mask):
            return 1e9
        err = float(np.sum(vega[w_mask] * (sv[w_mask] - mv[w_mask]) ** 2))
        w_sum = float(np.sum(vega[w_mask]))
        return err / w_sum if w_sum > 1e-6 else 1e9

    def _objective_fixed_beta(self, params, beta, target_atm_vol):
        rho, nu = params
        if not (-0.99 <= rho <= 0.99): return 1e9
        if not (0.01 <= nu <= 5.0): return 1e9
        alpha = self._solve_alpha_for_atm(target_atm_vol, beta, rho, nu)
        return self._weighted_error(alpha, beta, rho, nu)

    def _objective_free_beta(self, params, target_atm_vol):
        beta, rho, nu = params
        if not (0.1 <= beta <= 1.0): return 1e9
        if not (-0.99 <= rho <= 0.99): return 1e9
        if not (0.01 <= nu <= 5.0): return 1e9
        alpha = self._solve_alpha_for_atm(target_atm_vol, beta, rho, nu)
        return self._weighted_error(alpha, beta, rho, nu)

    def calibrate(self, calibrate_beta=True):
        """
        Two-stage calibration:
          1) alpha is no longer a free least-squares parameter -- it's solved
             analytically (via bisection against the closed-form ATM formula)
             to match the *true* ATM market vol exactly at every candidate
             (beta, rho, nu). This directly targets the ATM-fit error instead
             of letting alpha trade off ATM accuracy against the rest of the
             skew in a single global least-squares fit.
          2) beta=0.5 (fixed) is calibrated first as a safe baseline. If
             calibrate_beta=True, a second pass frees beta within [0.1, 1.0]
             with its own multi-start grid; the free-beta result is only kept
             if it actually beats the fixed-beta RMSE (freeing beta from a
             single smile snapshot is known to be poorly identified against
             rho, so this guards against it making things worse).
        """
        if len(self.strikes) < 3:
            raise RuntimeError(f"[SABR] Only {len(self.strikes)} strikes available -- cannot calibrate. No fallback.")

        target_atm_vol = self._atm_market_vol()
        print(f"[SABR Debug] True ATM market vol (nearest strike to forward) = {target_atm_vol:.4f}")

        # Pass 1: fixed beta, alpha ATM-pinned, calibrate (rho, nu)
        best_fixed, best_fixed_err = None, float('inf')
        for rho0 in [-0.6, -0.3, 0.0, 0.3, 0.6]:
            for nu0 in [0.2, 0.4, 0.6, 0.8, 1.0]:
                res = minimize(self._objective_fixed_beta, [rho0, nu0], args=(self.beta, target_atm_vol),
                               method='L-BFGS-B', bounds=[(-0.99, 0.99), (0.01, 5.0)],
                               options={'maxiter': 2000, 'ftol': 1e-12})
                if res.success and res.fun < best_fixed_err:
                    best_fixed_err, best_fixed = res.fun, res

        fixed_result = None
        if best_fixed is not None:
            rho_f, nu_f = best_fixed.x
            alpha_f = self._solve_alpha_for_atm(target_atm_vol, self.beta, rho_f, nu_f)
            fixed_result = {'alpha': float(alpha_f), 'beta': float(self.beta), 'rho': float(rho_f),
                             'nu': float(nu_f), 'rmse': float(np.sqrt(best_fixed_err))}
            print(f"[SABR Debug] Fixed-beta ({self.beta}) calibration: RMSE={fixed_result['rmse']:.6f}")

        best_result = fixed_result

        if calibrate_beta:
            # Pass 2: free beta, alpha ATM-pinned, calibrate (beta, rho, nu)
            best_free, best_free_err = None, float('inf')
            for beta0 in [0.3, 0.5, 0.7, 1.0]:
                for rho0 in [-0.5, 0.0, 0.5]:
                    for nu0 in [0.3, 0.6, 0.9]:
                        res = minimize(self._objective_free_beta, [beta0, rho0, nu0], args=(target_atm_vol,),
                                       method='L-BFGS-B', bounds=[(0.1, 1.0), (-0.99, 0.99), (0.01, 5.0)],
                                       options={'maxiter': 2000, 'ftol': 1e-12})
                        if res.success and res.fun < best_free_err:
                            best_free_err, best_free = res.fun, res

            if best_free is not None:
                beta_v, rho_v, nu_v = best_free.x
                alpha_v = self._solve_alpha_for_atm(target_atm_vol, beta_v, rho_v, nu_v)
                free_result = {'alpha': float(alpha_v), 'beta': float(beta_v), 'rho': float(rho_v),
                                'nu': float(nu_v), 'rmse': float(np.sqrt(best_free_err))}
                print(f"[SABR Debug] Free-beta calibration: beta={beta_v:.4f} RMSE={free_result['rmse']:.6f}")

                # Require a MEANINGFUL RMSE improvement (>=8% relative) before trusting
                # the free-beta result, not just any improvement no matter how tiny.
                # beta is poorly identified against rho from a single smile snapshot --
                # without this guard, the optimizer would happily walk beta all the way
                # to its bound (observed: beta 0.5 -> 0.12) chasing a near-zero RMSE
                # gain (observed: 0.039001 -> 0.038927, a 0.02% relative "improvement"),
                # which is curve-fitting noise, not a genuinely better-calibrated model.
                IMPROVEMENT_THRESHOLD = 0.92  # free-beta RMSE must be <= 92% of fixed-beta RMSE
                if fixed_result is None:
                    print(f"[SABR Debug] No fixed-beta baseline available; using free-beta result: beta={beta_v:.4f}")
                    best_result = free_result
                elif free_result['rmse'] < fixed_result['rmse'] * IMPROVEMENT_THRESHOLD:
                    print(f"[SABR Debug] Selecting free-beta result (meaningful improvement: "
                          f"{fixed_result['rmse']:.6f} -> {free_result['rmse']:.6f}): beta={beta_v:.4f} vs fixed beta={self.beta}")
                    best_result = free_result
                else:
                    print(f"[SABR Debug] Keeping fixed beta={self.beta} (free-beta RMSE "
                          f"{free_result['rmse']:.6f} vs fixed {fixed_result['rmse']:.6f} -- "
                          f"not a large enough improvement to trust an unidentified beta)")

        if best_result is None:
            # NO FALLBACK: both the fixed-beta and free-beta multi-start
            # optimizer passes failed to converge on ANY restart. That's a
            # genuine calibration failure worth seeing, not something to
            # paper over with a degenerate alpha/rho/nu=default guess.
            raise RuntimeError(
                f"[SABR] Calibration failed to converge on any multi-start restart for "
                f"{self.ticker} (target_atm_vol={target_atm_vol:.4f}, {len(self.strikes)} strikes). No fallback."
            )

        print(f"[SABR Debug] Calibration converged! Final beta={best_result['beta']:.4f} RMSE={best_result['rmse']:.6f}")
        return best_result


def sabr_all_greeks(S, K, T, r, q, cp, calibration, steps=401):
    """SABR's OWN Greek engine: bumps propagate through the SABR SMILE
    itself, not just through a single fixed sigma. Every bump of S / T / r
    re-evaluates the SABR Hagan formula at the bumped state and re-prices
    through the LR tree at THAT bumped smile-implied sigma.

    Why this is different from just calling lr_all_greeks with SABR's
    solved sigma:
      - Under SABR, sigma is a FUNCTION of the forward F and the strike K
        via sabr_vol_hagan(F, K, T, alpha, beta, rho, nu). Bumping S
        (which changes F = S*exp((r-q)T)) shifts sigma_SABR(K) too, so the
        "true" SABR Delta captures BOTH dP/dS at fixed sigma AND the
        smile's own response dP/dsigma * dsigma/dF * dF/dS. lr_all_greeks
        with a single frozen sigma bakes in ONLY the first term, so the
        SABR row's Delta was previously identical to the flat-vol tree
        rows' Delta -- confirmed live on AMD Put 480 (comparison
        20260727_155415.pdf).
      - Vega under SABR is a MODEL-PARAMETER derivative, not a
        market-parameter one: bump alpha (SABR's ATM level parameter),
        recompute the whole smile, price at K's new smile-sigma. This is
        the "SABR alpha vega" that market-makers actually hedge with, and
        it's meaningfully different in magnitude from a flat-sigma vega
        because SABR's smile-flattening interacts with the vega weighting.
      - Vanna and Vomma inherit the smile-awareness automatically because
        they're second derivatives of the same bump-and-reprice pricing
        function.

    calibration: dict with 'alpha', 'beta', 'rho', 'nu' from
    SABRCalibrator.calibrate(). Passed in explicitly (not looked up from a
    cache) so this function is a pure map from (state, params) to Greeks.
    """
    from american_binomial import leisen_reimer_american_price, _bs_rho

    alpha = float(calibration['alpha'])
    beta = float(calibration['beta'])
    rho_sabr = float(calibration['rho'])
    nu = float(calibration['nu'])

    def smile_sigma(S_, T_, r_, alpha_=None):
        F = S_ * np.exp((r_ - q) * T_)
        a = alpha if alpha_ is None else alpha_
        sig = float(sabr_vol_hagan(F, K, T_, a, beta, rho_sabr, nu))
        return max(sig, 0.001)

    def price(S_=S, T_=T, r_=r, alpha_=None):
        sig = smile_sigma(S_, T_, r_, alpha_)
        return leisen_reimer_american_price(S_, K, T_, r_, sig, q, cp, steps)

    # Base sigma at spot inputs -- also used as the "sigma" reported on the
    # row (SABR's own smile-implied vol AT K).
    sigma_here = smile_sigma(S, T, r)

    if T <= 0 or sigma_here <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0,
                'rho_euro': 0.0, 'rho_ee_premium': 0.0, 'sigma': sigma_here}

    dS = S * 0.01
    d_alpha = max(alpha * 0.02, 1e-5)
    dR = 0.0025
    dT = max(T * 0.02, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    p0 = price()
    delta = (price(S_=S + dS) - price(S_=S - dS)) / (2 * dS)
    gamma = (price(S_=S + dS) - 2 * p0 + price(S_=S - dS)) / (dS * dS)
    # Vega under SABR = dP/dalpha (SABR's own vol-level parameter). Report
    # the derivative WRT the ATM-vol-equivalent sigma change so units match
    # the other models' Vega -- SABR's alpha and the resulting ATM sigma
    # move nearly 1:1 for beta=1, roughly (F^(1-beta)) apart for beta<1.
    # Convert dP/dalpha to dP/dsigma via the linear approximation
    # dsigma/dalpha computed at the base point.
    sig_up = smile_sigma(S, T, r, alpha_=alpha + d_alpha)
    sig_dn = smile_sigma(S, T, r, alpha_=alpha - d_alpha)
    dsig_dalpha = (sig_up - sig_dn) / (2 * d_alpha)
    dp_dalpha = (price(alpha_=alpha + d_alpha) - price(alpha_=alpha - d_alpha)) / (2 * d_alpha)
    vega = dp_dalpha / dsig_dalpha if abs(dsig_dalpha) > 1e-9 else 0.0

    rho_am = (price(r_=r + dR) - price(r_=r - dR)) / (2 * dR)
    theta = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    # ------------------------------------------------------------------
    # 2nd-order Greeks: Vanna, Vomma, Speed, Charm, Color.
    # All SABR-aware finite-difference -- every bumped state re-runs the
    # Hagan smile before repricing through the LR tree, so the smile's own
    # response to the bump is captured. This replaces the previous
    # _closed_form_* (flat-vol European BS) fallback, which was shared with
    # the other engines and therefore made SABR's 2nd-order row identical
    # to theirs.
    #
    # Sign / unit conventions are the HOUSE ones (see MC._closed_form_*):
    #   vanna = dVega/dS            = d2P/(dS dsigma)
    #   vomma = dVega/dsigma        = d2P/dsigma2
    #   speed = dGamma/dS           = d3P/dS3           (no day-scaling)
    #   charm = -dDelta/dT_remaining, per YEAR
    #   color = +dGamma/dT_remaining, per YEAR
    #
    # NOTE on color's sign: charm and color take OPPOSITE signs on the raw
    # d/dT_remaining difference. That is the house convention, matching
    # MC.py's own charm/color block (MC.py: `color = (gamma_t_up -
    # gamma_t_dn) / T2_span`, unnegated) as well as BAW and Heston.
    # The comment that used to sit here justified NEGATING color by citing
    # MC._closed_form_color; that function no longer exists anywhere in the
    # repo, and the test point it cited was exactly ATM (S=K=100), where the
    # two candidate conventions cannot be told apart in a way that
    # generalises off the money.
    #
    # Ground truth (QQQ call, S=678.71, K=690, T=0.219y, r=0.0408, q=0.0045):
    # a BS central difference of +dGamma/dTau gives ~-0.0133 against a market
    # Color of -0.0117 -- same sign, right magnitude. The negated form gives
    # +0.0133, the wrong sign. So color is NOT negated here.
    #
    # Vol bumps go through alpha (SABR's vol-LEVEL parameter) and are then
    # divided by dsigma/dalpha, exactly as the 1st-order Vega above does.
    # That keeps vanna/vomma in "per unit of sigma" units so they are
    # directly comparable with the other engines' rows. Bumping nu instead
    # would give dVega/d(vol-of-vol), a different quantity in different
    # units, which would not be a Vomma.
    # ------------------------------------------------------------------

    # Larger bumps than the 1st-order ones: second/third differences divide
    # by h^2 / h^3, so tree quantisation noise is amplified and needs a
    # wider stencil to stay in the signal-dominated regime.
    d_alpha2 = max(alpha * 0.10, 1e-4)
    sig_up2 = smile_sigma(S, T, r, alpha_=alpha + d_alpha2)
    sig_dn2 = smile_sigma(S, T, r, alpha_=alpha - d_alpha2)
    dsig_dalpha2 = (sig_up2 - sig_dn2) / (2 * d_alpha2)
    # Hagan's sigma(alpha) is NOT affine in alpha (the z/x(z) skew factor and
    # the O(T) term3 correction both carry alpha), so the alpha->sigma change
    # of variables needs its SECOND derivative as well for any 2nd-order
    # Greek in sigma. sigma_here is smile_sigma at the base alpha, i.e. the
    # centre of this same +/-d_alpha2 stencil, so this is a plain central
    # second difference of sigma(alpha) itself.
    d2sig_dalpha2 = (sig_up2 - 2 * sigma_here + sig_dn2) / (d_alpha2 * d_alpha2)

    if abs(dsig_dalpha2) > 1e-9:
        # Vanna = d2P/(dS dalpha) / (dsigma/dalpha), 4-point cross difference.
        dS_v = max(S * 0.02, 0.02)
        cross = (price(S_=S + dS_v, alpha_=alpha + d_alpha2)
                 - price(S_=S + dS_v, alpha_=alpha - d_alpha2)
                 - price(S_=S - dS_v, alpha_=alpha + d_alpha2)
                 + price(S_=S - dS_v, alpha_=alpha - d_alpha2)) / (4 * dS_v * d_alpha2)
        vanna = cross / dsig_dalpha2

        # Vomma = d2P/dsigma2. With P(alpha) = P~(sigma(alpha)) the chain rule
        # is
        #   dP/dalpha   = P~' * sig'
        #   d2P/dalpha2 = P~'' * sig'^2 + P~' * sig''
        # so  P~'' = (d2P/dalpha2 - P~' * sig'') / sig'^2.
        # The -P~'*sig'' term is NOT optional: this used to apply only the
        # first-order chain rule (d2P/dalpha2 / sig'^2), which leaves a
        # spurious Vega*d2sigma/dalpha2 contribution in the result. That term
        # dominates and flips the sign -- the shipped range was -13.6..+1.55
        # against a true +1.2..+2.4.
        # P~' (= dP/dsigma, Vega in sigma units) comes from the FIRST-order
        # chain rule dP/dalpha / (dsigma/dalpha), which is valid on its own
        # precisely because it never needs d2sigma/dalpha2. It is evaluated
        # on this same wide +/-d_alpha2 stencil so both terms share a stencil
        # (and reuse the two bumped prices rather than re-bumping).
        p_alpha_up = price(alpha_=alpha + d_alpha2)
        p_alpha_dn = price(alpha_=alpha - d_alpha2)
        dp_dalpha2 = (p_alpha_up - p_alpha_dn) / (2 * d_alpha2)
        vega_sigma = dp_dalpha2 / dsig_dalpha2
        d2p_dalpha2 = (p_alpha_up - 2 * p0 + p_alpha_dn) / (d_alpha2 * d_alpha2)
        vomma = ((d2p_dalpha2 - vega_sigma * d2sig_dalpha2)
                 / (dsig_dalpha2 * dsig_dalpha2))
    else:
        vanna = 0.0
        vomma = 0.0

    # Speed = d3P/dS3 via the standard 5-point (4-evaluation) central third
    # difference. Wide bump because a third derivative off a tree is the
    # noisiest quantity here.
    dS_s = max(S * 0.03, 0.03)
    speed = (price(S_=S + 2 * dS_s) - 2 * price(S_=S + dS_s)
             + 2 * price(S_=S - dS_s) - price(S_=S - 2 * dS_s)) / (2 * dS_s ** 3)

    # Charm / Color: bump time to expiry, re-run the smile at the bumped T
    # (Hagan's sigma is T-dependent AND F = S*exp((r-q)T) moves), then take
    # Delta / Gamma at each bumped T.
    dT2 = max(T * 0.05, 2.0 / 365.0)
    T2_dn = max(1e-6, T - dT2)
    T2_span = dT2 + (T - T2_dn)

    p_up_c = price(S_=S + dS, T_=T + dT2)
    p_up_0 = price(T_=T + dT2)
    p_up_p = price(S_=S - dS, T_=T + dT2)
    p_dn_c = price(S_=S + dS, T_=T2_dn)
    p_dn_0 = price(T_=T2_dn)
    p_dn_p = price(S_=S - dS, T_=T2_dn)

    delta_T_up = (p_up_c - p_up_p) / (2 * dS)
    delta_T_dn = (p_dn_c - p_dn_p) / (2 * dS)
    gamma_T_up = (p_up_c - 2 * p_up_0 + p_up_p) / (dS * dS)
    gamma_T_dn = (p_dn_c - 2 * p_dn_0 + p_dn_p) / (dS * dS)

    # T + dT2 is MORE time remaining, so (up - dn)/span is d/dT_remaining.
    # House convention (see the block comment above): charm negates it,
    # color does NOT.
    charm = -(delta_T_up - delta_T_dn) / T2_span
    color = (gamma_T_up - gamma_T_dn) / T2_span

    rho_euro = _bs_rho(S, K, T, r, q, sigma_here, cp)
    rho_ee_premium = rho_am - rho_euro

    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho_am, 'theta': theta,
            'vanna': vanna, 'vomma': vomma, 'speed': speed, 'charm': charm, 'color': color,
            'rho_euro': rho_euro, 'rho_ee_premium': rho_ee_premium, 'sigma': sigma_here}
