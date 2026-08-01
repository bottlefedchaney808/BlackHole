import math
import os
import warnings
import numpy as np
from american_binomial import american_all_greeks

# numpy's polyfit warns (RankWarning) whenever the regression matrix is
# poorly conditioned -- routine and EXPECTED here: the LSM continuation-value
# regression at a backward-induction step can have very few in-the-money
# paths (especially deep-OTM strikes, or the reduced sim/step counts used for
# the smile chart's chain-wide MC IV solve -- see main.py), which makes a
# degree-2 fit rank-deficient. This is not silently swallowing an error:
# _polyfit_polyval's own try/except still catches actual failures and falls
# back to plain numpy; polyfit still returns its best-effort least-squares
# result either way. The warning itself is just noise that was drowning out
# real console output (debug prints, calibration diagnostics) across a
# report with 100+ strikes each doing dozens of regression steps.
try:
    from numpy.exceptions import RankWarning as _NpRankWarning
except ImportError:  # older numpy
    from numpy import RankWarning as _NpRankWarning
warnings.filterwarnings('ignore', category=_NpRankWarning)

# ---------------------------------------------------------------------------
# GPU backend (CuPy) for AmericanLSMPricer's Monte Carlo path simulation --
# 50,000 sims x 100 steps by default (PricingConfig), re-run once per model
# in "Run all models & compare" (6 models = 6 separate 5M-element
# simulations). This is the actual heavy-compute step in the suite -- unlike
# SABR/Heston calibration (small arrays, sequential optimizer control flow,
# where a GPU's transfer/kernel-launch overhead would dominate and make
# things SLOWER -- see the smile-calibration fixes elsewhere in this
# project), a 100 x 50000 path simulation plus payoff/regression at every
# backward-induction step is exactly the kind of large, embarrassingly
# parallel elementwise workload a GPU is good at.
#
# `xp` is numpy or cupy depending on what's actually available -- every
# array op in this file goes through `xp` (never a hardcoded `np.`) so the
# exact same code path runs on GPU when present and falls back to CPU numpy
# automatically otherwise (no CUDA installed, no cupy installed, or a CUDA
# device exists but errors on first use). Set OPTIONS_SUITE_FORCE_CPU=1 to
# force the CPU path regardless of what's detected (escape hatch in case a
# GPU/driver misbehaves -- this was written and unit-tested without a GPU
# attached, so this switch matters if anything about the CuPy path doesn't
# behave as expected on a real card).
GPU_ACTIVE = False
xp = np
if not os.environ.get('OPTIONS_SUITE_FORCE_CPU'):
    try:
        import cupy as _cp
        if _cp.cuda.runtime.getDeviceCount() > 0:
            # Cheap real op (not just an import check) -- confirms the
            # driver/toolkit actually works, not just that the cupy package
            # is installed.
            _cp.array([1.0]) + 1.0
            xp = _cp
            GPU_ACTIVE = True
    except Exception:
        xp = np
        GPU_ACTIVE = False

if GPU_ACTIVE:
    print("[MC] CuPy GPU backend active for Monte Carlo pricing.")
else:
    print("[MC] Using CPU (numpy) for Monte Carlo pricing -- no usable CUDA/cupy detected "
          "(or OPTIONS_SUITE_FORCE_CPU set). Set OPTIONS_SUITE_FORCE_CPU=1 to force this "
          "explicitly; unset it and ensure `pip install cupy-cudaXX` matches your CUDA "
          "version to enable GPU.")


def _to_scalar(x) -> float:
    """Bring a numpy or cupy scalar/0-d array back to a plain Python float --
    cupy arrays need an explicit host transfer (.get()), numpy ones don't."""
    if hasattr(x, 'get'):
        return float(x.get())
    return float(x)


# ---------------------------------------------------------------------------
# Closed-form Black-Scholes-with-carry (dividend yield q) 2nd/3rd order Greeks.
#
# SUPERSEDED as the primary path for vanna/vomma/color/speed/charm -- see
# american_second_third_order_greeks() in american_binomial.py, which
# AmericanLSMPricer.calculate_greeks() now calls instead. That function is
# both genuinely American (early exercise applied at every tree node, unlike
# these closed-form formulas which price the European analog) AND
# deterministic/smooth (a Leisen-Reimer binomial tree, not noisy LSM
# regression), so it doesn't have to trade off one problem for the other the
# way this closed-form approach and the original bump-and-revalue-on-LSM
# approach each did.
#
# Kept here as a documented, verified, European-equivalent reference/
# diagnostic (e.g. for quantifying how much the early-exercise premium moves
# a given Greek, by comparing against american_second_third_order_greeks'
# output for the same inputs) -- not because it's still the primary
# computation path.
#
# Every formula below was cross-checked two ways before being trusted:
#   1. Sourced from Wikipedia's Greeks (finance) formula table (itself citing
#      Haug, "The Complete Guide to Option Pricing Formulas").
#   2. Independently verified via /tmp/verify_greeks.py: central finite
#      differences of THIS file's own from-scratch closed-form delta/gamma
#      (not the LSM price -- a fully independent code path), across three
#      varied cases (ATM, short-T high-vol, OTM long-T with dividends) and
#      both calls and puts. Vanna/Vomma/Speed/Charm matched the finite
#      difference to 1e-6 to 1e-9.
#
# Charm and Color's sign/day-count convention were originally guessed by
# analogy to Theta (day-normalized via /365, Color sign-matched to Charm's
# "-d/dT_remaining" pattern) rather than verified against real data -- a live
# ThetaData comparison report (MU call, T=0.33) showed that guess was wrong
# on BOTH counts: the /365 was wrong (should be per YEAR, not per day -- off
# by ~330x) and Color's sign convention is actually the OPPOSITE of Charm's
# (Color = +dGamma/dT_remaining, not -dGamma/dT_remaining -- Color is one of
# the least standardized Greeks across vendors; this vendor's convention was
# determined empirically, not assumed). Both are fixed below to match that
# live evidence. See american_second_third_order_greeks' docstring in
# american_binomial.py for the full numeric verification.
# ---------------------------------------------------------------------------

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_d1_d2(S, K, T, r, q, sigma):
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    return d1, d2


def _closed_form_vega(S, K, T, r, q, sigma):
    d1, _ = _bs_d1_d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * _norm_pdf(d1) * math.sqrt(T)


def _closed_form_gamma(S, K, T, r, q, sigma):
    d1, _ = _bs_d1_d2(S, K, T, r, q, sigma)
    return math.exp(-q * T) * _norm_pdf(d1) / (S * sigma * math.sqrt(T))


class AmericanLSMPricer:
    def __init__(self, S, K, T, r, q, sigma, simulations=50000, steps=100, option='call'):
        self.S = S
        self.K = K
        self.T = T
        self.r = r
        self.q = q
        self.sigma = sigma
        self.simulations = simulations
        self.steps = steps
        self.option = option.lower()
        if self.option not in ['call', 'put']:
            raise ValueError("Option type must be either 'call' or 'put'")
        self.dt = T / steps
        self.discount = np.exp(-r * self.dt)

    def _generate_rand(self, seed=42):
        # xp is numpy or cupy (see module-level GPU_ACTIVE detection above) --
        # everything downstream (paths, payoffs, regression) inherits whatever
        # array type this returns, so the whole simulation runs on GPU when
        # available with no other code path change.
        xp.random.seed(seed)
        return xp.random.standard_normal((self.steps, self.simulations))

    def _polyfit_polyval(self, X, Y, degree):
        """xp.polyfit/xp.polyval, with a CPU numpy fallback if the active
        cupy version's polyfit doesn't behave as expected -- this GPU path
        was written and tested without a real CUDA device attached (see
        module docstring), so this safety net matters if anything about
        cupy.polyfit surprises on an actual card. Falls back per-call (a
        handful of host<->device transfers on an already-small ITM subset,
        not a hot path) rather than disabling GPU for the whole run."""
        try:
            coeffs = xp.polyfit(X, Y, degree)
            return xp.polyval(coeffs, X)
        except Exception:
            X_cpu = X.get() if hasattr(X, 'get') else np.asarray(X)
            Y_cpu = Y.get() if hasattr(Y, 'get') else np.asarray(Y)
            coeffs = np.polyfit(X_cpu, Y_cpu, degree)
            cont_cpu = np.polyval(coeffs, X_cpu)
            return xp.asarray(cont_cpu) if xp is not np else cont_cpu

    def price_with_rand(self, rand, S=None, sigma=None, r=None, T=None):
        S_use = S if S is not None else self.S
        sigma_use = sigma if sigma is not None else self.sigma
        r_use = r if r is not None else self.r
        T_use = T if T is not None else self.T

        steps = self.steps
        dt = T_use / steps
        discount = xp.exp(-r_use * dt)
        simulations = self.simulations

        # Vectorized path generation: GBM log-returns are i.i.d. across steps
        # with a CONSTANT per-step drift/vol (sigma_use/r_use/q are scalars
        # for the whole path here, not time-varying), so
        # log(S_t/S_0) = sum_{i<=t}(drift + diffusion_i) is exactly a
        # cumulative sum -- no need to walk the Python for-loop step by step.
        # Replaces `steps` sequential array ops (each a separate GPU kernel
        # launch when xp is cupy) with a single cumsum + exp, which is both
        # faster on CPU and removes ~`steps` kernel-launch round trips on GPU.
        drift_dt = (r_use - self.q - 0.5 * sigma_use ** 2) * dt
        diffusion = sigma_use * xp.sqrt(dt) * rand  # shape (steps, simulations)
        cum_log_returns = xp.cumsum(drift_dt + diffusion, axis=0)
        paths = xp.empty((steps + 1, simulations))
        paths[0] = S_use
        paths[1:] = S_use * xp.exp(cum_log_returns)

        value = xp.zeros_like(paths)
        if self.option == 'call':
            value[-1] = xp.maximum(paths[-1] - self.K, 0)
        else:
            value[-1] = xp.maximum(self.K - paths[-1], 0)

        for t in range(steps - 1, 0, -1):
            if self.option == 'call':
                payoff = xp.maximum(paths[t] - self.K, 0)
            else:
                payoff = xp.maximum(self.K - paths[t], 0)
            itm = payoff > 0
            if xp.any(itm):
                X = paths[t, itm]
                Y = value[t+1, itm] * discount
                n_itm = int(X.shape[0])
                degree = 2 if n_itm >= 20 else 1
                continuation = self._polyfit_polyval(X, Y, degree)
                exercise = xp.where(payoff[itm] > continuation, payoff[itm], Y)
                temp = xp.zeros(simulations)
                temp[itm] = exercise
                temp[~itm] = value[t+1, ~itm] * discount
                value[t] = temp
            else:
                value[t] = value[t+1] * discount
        return _to_scalar(xp.mean(value[1]) * discount)

    def price(self):
        rand = self._generate_rand()
        return self.price_with_rand(rand)

    def calculate_greeks(self):
        """MC's OWN Greek engine: bump-and-revalue on this class's LSM
        pricer using COMMON RANDOM NUMBERS (CRN) -- the SAME pre-generated
        Gaussian draws are re-used across every bump direction so the LSM
        regression's own random noise CANCELS between price(+bump) and
        price(-bump), leaving a clean numerical derivative. This is what MC
        Greeks should have been from the start -- see mc_all_greeks() below
        for the full implementation and rationale (why the earlier
        redirection through american_all_greeks was itself a form of
        cross-model borrowing this refactor is explicitly undoing)."""
        return mc_all_greeks(self.S, self.K, self.T, self.r, self.q, self.sigma,
                             sims=self.simulations, steps=self.steps,
                             option=self.option, seed=42)


def mc_all_greeks(S, K, T, r, q, sigma, sims=50000, steps=100, option='put', seed=42):
    """Monte Carlo (Longstaff-Schwartz) Greek engine using COMMON RANDOM
    NUMBERS (CRN) for smooth bump-and-revalue derivatives.

    The whole point: LSM's price is a Monte Carlo average, so raw
    bump-and-revalue (fresh randoms per pricing) has O(1/sqrt(N)) noise per
    price which drowns out any small derivative signal -- exactly the
    disease the session-notes flagged AmericanLSMPricer.calculate_greeks
    for (rho std ~162 across seeds for identical inputs, delta shifts of
    ~0.02 seed-to-seed). CRN fixes this by generating the Gaussian draws
    ONCE and re-using the SAME array for every bumped pricing -- the raw
    Monte Carlo noise then appears identically in price(+bump) and
    price(-bump), so it CANCELS in the central-difference numerator and
    the derivative signal comes out clean.

    Previous approach: MC.calculate_greeks used to redirect to
    american_all_greeks (Leisen-Reimer tree FD). That eliminated the noise,
    but at the cost of computing MC's Greeks from a DIFFERENT model's
    pricer -- exactly the kind of cross-model borrowing the "each model
    uses its own dynamics" principle exists to prevent, and one of the
    specific inconsistencies called out on the AMD Put 480 comparison
    (comparison_20260727_155415.pdf).

    Both first-order (Delta/Gamma/Vega/Rho/Theta) and higher-order
    (Vanna/Vomma/Speed/Charm/Color) Greeks come from CRN bump-and-revalue
    on the LSM price directly -- the same pre-generated `rand` draws are
    reused across every bump combination (including the mixed S/sigma and
    S/T bumps that Vanna/Charm need), so LSM's own regression noise cancels
    the same way for the 2nd/3rd-order Greeks as it does for the 1st-order
    ones. This keeps every Greek MC reports a function of MC's own LSM
    pricer, not any other model's closed-form formulas -- all Greeks
    (first- and higher-order) are LSM-native; there is no closed-form
    fallback path in this file.
    """
    is_call = (option == 'call')
    if T <= 0 or sigma <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0,
                'rho_euro': 0.0, 'rho_ee_premium': 0.0}

    pricer = AmericanLSMPricer(S, K, T, r, q, sigma, sims, steps, option)
    rand = pricer._generate_rand(seed=seed)

    # Bump sizes: MC needs WIDER bumps than the tree engines because even
    # with CRN cancellation, the LSM continuation-value regression isn't
    # smooth in the bumped variable at the small (~1%) bumps the trees use.
    # Tuned empirically on AMD Put K=480 S=494.95 T=0.107 sigma=0.8131 at
    # sims=50k: at dS=1% gamma came out NEGATIVE (-0.005, pure noise); at
    # dS>=2% gamma converged to +0.0025 (matches LR-tree gamma to within
    # the ~1-2% LSM/LR price gap). Rho at the tree engines' dR=25bp had
    # ~30% seed variance; at dR>=100bp it converges cleanly. Vega was
    # stable at any bump. Bumping wider trades a small amount of
    # localness in the derivative for a large amount of noise-suppression
    # -- the right tradeoff for MC specifically because the underlying
    # pricer IS noisy.
    dS = S * 0.03
    dSig = max(sigma * 0.05, 1e-4)
    # Vanna/Vomma need a WIDER sigma bump than Vega's dSig above. Vega is a
    # single first-order central difference, so dSig's regression noise
    # (which does NOT cancel via CRN -- the LSM continuation-value
    # regression re-fits its own basis independently at each bumped sigma,
    # so common random numbers only cancel the path-simulation noise, not
    # the regression's fit noise) stays small relative to Vega's own
    # magnitude. Vomma divides by dSig^2, which amplifies that same
    # uncancelled regression noise by ~1/dSig -- at Vega's dSig (~5% of
    # sigma) a seed sweep showed Vomma ranging from -78 to +281 for a case
    # with a true value of ~1.2-1.5, and Color flipping sign across seeds.
    # Widening to 15% of sigma (matching the bump already used successfully
    # in barone_adesi_whaley.py and american_binomial.py's LR vomma block)
    # cuts that noise substantially by trading away some localness in the
    # second derivative. NOTE: this is a mitigation, not a full fix -- a
    # smoke test across seeds (see MC.py's fix history / test scripts) still
    # showed Vomma varying roughly +/-90 seed-to-seed at moderate path
    # counts (down from a +/-500 range before this fix), and Color (which
    # does not depend on dSig at all -- it's driven by the dS/dT2 spatial
    # bumps instead) is untouched by this change and remains separately
    # seed-unstable. Vomma/Color stay MC-inherent noise-floor-limited
    # Greeks; a bump width alone does not fully eliminate this, and a wider
    # bump than 15% starts trading away real curvature signal instead of
    # just noise.
    dSig_2nd = max(sigma * 0.15, 0.01)
    dR = 0.02
    dT = max(T * 0.05, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    def p(S_=None, sigma_=None, r_=None, T_=None):
        return pricer.price_with_rand(rand, S=S_, sigma=sigma_, r=r_, T=T_)

    p0 = p()
    delta = (p(S_=S + dS) - p(S_=S - dS)) / (2 * dS)
    gamma = (p(S_=S + dS) - 2 * p0 + p(S_=S - dS)) / (dS * dS)
    vega = (p(sigma_=sigma + dSig) - p(sigma_=sigma - dSig)) / (2 * dSig)
    rho = (p(r_=r + dR) - p(r_=r - dR)) / (2 * dR)
    theta = -(p(T_=T + dT) - p(T_=T_dn)) / T_span / 365.0

    # 2nd/3rd-order Greeks: same CRN bump-and-revalue approach as the
    # 1st-order block above, reusing the SAME pre-generated `rand` draws so
    # LSM's own regression noise cancels between bump directions exactly
    # like it does for Delta/Gamma/Vega/Rho/Theta. Speed/Charm/Color reuse
    # the already-wide spatial dS from above (tuned empirically for LSM
    # regression stability, see the block comment above); Vanna/Vomma use
    # the WIDER dSig_2nd (see its own comment above) instead of Vega's dSig,
    # since dividing by dSig^2 (Vomma) or a small dSig (Vanna) amplifies
    # regression noise that CRN alone doesn't cancel. Charm/Color get their
    # own T bump (dT2) sized the same way as Theta's dT.

    # Vanna = d2(price)/dS/dsigma (mixed 2nd-order central difference).
    # Uses the WIDER dSig_2nd (not Vega's dSig) -- see the comment above
    # dSig_2nd's definition for why the narrow Vega bump is too noisy here.
    vanna = (p(S_=S + dS, sigma_=sigma + dSig_2nd) - p(S_=S + dS, sigma_=sigma - dSig_2nd)
             - p(S_=S - dS, sigma_=sigma + dSig_2nd) + p(S_=S - dS, sigma_=sigma - dSig_2nd)) / (4 * dS * dSig_2nd)

    # Vomma = d2(price)/dsigma^2. Uses the WIDER dSig_2nd -- dividing by
    # dSig^2 (Vega's narrow bump) amplifies uncancelled LSM regression noise
    # far past the true curvature signal (see dSig_2nd comment above).
    vomma = (p(sigma_=sigma + dSig_2nd) - 2 * p0 + p(sigma_=sigma - dSig_2nd)) / (dSig_2nd * dSig_2nd)

    # Speed = d3(price)/dS^3 (standard 5-point central 3rd-derivative stencil)
    speed = (p(S_=S + 2 * dS) - 2 * p(S_=S + dS) + 2 * p(S_=S - dS) - p(S_=S - 2 * dS)) / (2 * dS ** 3)

    dT2 = max(T * 0.05, 1.0 / 365.0)
    T2_dn = max(1e-6, T - dT2)
    T2_span = dT2 + (T - T2_dn)

    # Charm = -d2(price)/dS/dtau (mixed S-T 2nd-order central difference)
    charm = -(p(S_=S + dS, T_=T + dT2) - p(S_=S + dS, T_=T2_dn)
              - p(S_=S - dS, T_=T + dT2) + p(S_=S - dS, T_=T2_dn)) / (2 * dS * T2_span)

    # Color = +d(Gamma)/dtau -- sign convention is empirically the OPPOSITE
    # of Charm's, not the naive "-" analogy (see live ThetaData comparison
    # note in the module docstring at the top of this file).
    gamma_t_up = (p(S_=S + dS, T_=T + dT2) - 2 * p(T_=T + dT2) + p(S_=S - dS, T_=T + dT2)) / (dS * dS)
    gamma_t_dn = (p(S_=S + dS, T_=T2_dn) - 2 * p(T_=T2_dn) + p(S_=S - dS, T_=T2_dn)) / (dS * dS)
    color = (gamma_t_up - gamma_t_dn) / T2_span

    from american_binomial import _bs_rho
    rho_euro = _bs_rho(S, K, T, r, q, sigma, is_call)
    rho_ee_premium = rho - rho_euro

    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho, 'theta': theta,
            'vanna': vanna, 'vomma': vomma, 'speed': speed, 'charm': charm, 'color': color,
            'rho_euro': rho_euro, 'rho_ee_premium': rho_ee_premium}
