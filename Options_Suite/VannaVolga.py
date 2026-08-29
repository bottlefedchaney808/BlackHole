from datetime import datetime

import numpy as np
from scipy.stats import norm

try:
    from .MC import AmericanLSMPricer
except ImportError:
    from MC import AmericanLSMPricer

try:
    from thetadata_controller import ThetaDataController

    _THETADATA_AVAILABLE = True
except Exception:
    _THETADATA_AVAILABLE = False


def _bs_call_price(S, K, T, r, q, sigma):
    """Plain European BS call price -- the reference pricer the
    Castagna-Mercurio Vanna-Volga correction (get_vol/get_vol_batch) prices
    every pillar and every target strike against. sigma<=0 or T<=0
    degenerates to discounted intrinsic (the T->0/sigma->0 limit of BS)."""
    if sigma <= 0 or T <= 0:
        return max(S * np.exp(-q * T) - K * np.exp(-r * T), 0.0)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def _bs_vega(S, K, T, r, q, sigma):
    """d(call price)/d(sigma). 0 (not 1.0) when degenerate -- unlike the
    old bs_vega/_vega closures this replaces, callers here need a genuine
    zero to correctly zero out a pillar's Lagrange weight (see get_vol),
    not a nonzero placeholder."""
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    return S * np.exp(-q * T) * np.sqrt(T) * norm.pdf(d1)


def _bs_iv_newton(price, S, K, T, r, q, seed=0.2, tol=1e-6, max_iter=100):
    """Scalar Newton BS-IV inversion -- the last step of get_vol's
    Castagna-Mercurio correction: turn the replicated VV price back into
    the vol number the smile chart plots. Falls back to the seed if Newton
    can't make progress (vega collapses to ~0), same convention as this
    file's other solvers -- an imprecise point is preferable to a raised
    exception breaking the whole curve over one hard strike."""
    sigma = max(seed, 1e-4)
    floor = max(S * np.exp(-q * T) - K * np.exp(-r * T), 0.0)
    if price <= floor + 1e-10:
        return 1e-4  # at-or-below-intrinsic: no positive vol reproduces this price
    for _ in range(max_iter):
        p = _bs_call_price(S, K, T, r, q, sigma)
        diff = p - price
        if abs(diff) < tol:
            break
        vega = _bs_vega(S, K, T, r, q, sigma)
        if vega < 1e-10:
            break
        sigma = max(sigma - diff / vega, 1e-4)
    return sigma


def _forward_delta_strike(F, sigma, T, target_delta):
    """
    Compute the strike K such that forward call delta = target_delta.
    N(d1) = target_delta  =>  d1 = N^{-1}(target_delta)
    Then K = F * exp(-d1 * sigma * sqrt(T) + 0.5 * sigma^2 * T)
    """
    d1 = norm.ppf(target_delta)
    return F * np.exp(-d1 * sigma * np.sqrt(T) + 0.5 * sigma**2 * T)


def get_auto_rr_bf(ticker, expiry_date):
    """
    Fetch the market smile from PotatoHedge/ThetaData and compute suggested RR25
    and BF25 values from the vendor's OWN per-strike deltas (yahoo purged).
      RR25 = sigma(25dCall) - sigma(25dPut)                    (vol points)
      BF25 = (sigma(25dCall) + sigma(25dPut))/2 - sigma(ATM)   (vol points)

    Returns (rr25_suggested, bf25_suggested, atm_vol) in vol points (e.g. 7.0 = 7
    vol pts). This is a real 25-delta read -- it locates the strikes whose vendor
    call/put delta is nearest +/-0.25, instead of the old spot*1.2 / spot*0.8
    moneyness guess that could land far from a true 25-delta on a high-vol name.
    Returns (0,0,0) if PotatoHedge can't supply a smile (caller treats that as
    "no auto values, prompt for manual RR/BF").
    """
    if not _THETADATA_AVAILABLE:
        return 0.0, 0.0, 0.0
    try:
        td = ThetaDataController()
        try:
            spot = td.fetch_spot_price(ticker)
            exps = td.list_expirations(ticker)
            if not exps:
                return 0.0, 0.0, 0.0
            target = datetime.strptime(expiry_date, "%Y-%m-%d")
            parsed = sorted(
                (abs((datetime.strptime(str(e), "%Y%m%d") - target).days), str(e))
                if str(e).isdigit() and len(str(e)) == 8
                else (
                    abs((datetime.strptime(str(e), "%Y-%m-%d") - target).days),
                    str(e),
                )
                for e in exps
            )
            nearest = parsed[0][1]
            rows = td.option_bulk_greeks(ticker, nearest)
        finally:
            td.close()

        calls, puts = [], []  # each: (strike, iv, delta)
        for row in rows:
            try:
                right = str(row.get("right", "")).upper()
                strike = float(row.get("strike")) / 1000.0
                iv = row.get("implied_vol") or row.get("impliedVolatility")
                delta = row.get("delta")
                if iv is None or delta is None:
                    continue
                iv = float(iv)
                delta = float(delta)
                if iv <= 0 or iv > 5.0:
                    continue
                (calls if right == "C" else puts).append((strike, iv, delta))
            except Exception:
                continue
        if not calls or not puts:
            return 0.0, 0.0, 0.0

        # ATM vol: average of call & put IV at the strike nearest spot
        atm_c = min(calls, key=lambda x: abs(x[0] - spot))[1]
        atm_p = min(puts, key=lambda x: abs(x[0] - spot))[1]
        atm_vol = 0.5 * (atm_c + atm_p)

        # 25-delta call: vendor call delta nearest +0.25; 25-delta put: nearest -0.25
        call_25_vol = min(calls, key=lambda x: abs(x[2] - 0.25))[1]
        put_25_vol = min(puts, key=lambda x: abs(x[2] - (-0.25)))[1]

        rr25 = (call_25_vol - put_25_vol) * 100
        bf25 = ((call_25_vol + put_25_vol) / 2 - atm_vol) * 100
        return round(rr25, 2), round(bf25, 2), round(atm_vol * 100, 2)
    except Exception as e:
        print(f"[VV Auto-Calc] Could not fetch RR/BF from PotatoHedge: {e}")
        return 0.0, 0.0, 0.0


def get_vol(S, K, T, r, q, atm_vol, rr25, bf25, S_atm=None, S_pillars=None):
    """
    Vanna-Volga implied volatility using correct 25-delta strikes and safe convex weights.

    Parameters:
        rr25: Risk reversal in vol points (e.g., 7.0 means sigma_25C - sigma_25P = 7 vol pts)
        bf25: Butterfly in vol points (e.g., 5.0 means (sigma_25C + sigma_25P)/2 - sigma_ATM = 5 vol pts)
        S_atm: optional override for the spot used to anchor the ATM pillar's
            strike (K_atm). Defaults to S. vv_all_greeks's finite-difference
            Greek stencils pass the PRE-BUMP spot here so that repricing at a
            bumped S_ doesn't drag K_atm along with it -- K_atm tracking the
            bumped spot exactly would move the inverse-distance weight
            function's V-shaped kink (at strike == K_atm) in lockstep with
            the bump, and any stencil whose bump range straddles the option's
            actual strike K would then straddle that moving kink too,
            producing unstable, sign-flipping, order-of-magnitude-wrong
            second-order Greeks. Freezing K_atm at the unbumped spot keeps
            the kink at a fixed location independent of the bump variable,
            so sigma(S_) stays smooth over the stencil.
        S_pillars: optional override for the spot used in the forward-delta
            strike formula that locates the 25-delta pillars (K_25P, K_25C).
            Defaults to S (dynamic -- pillars track the bumped spot, which
            is the deliberate smile-aware design for Delta/Vanna/Gamma/
            Charm/Color: bumping S under a fixed VV smile is not the same
            shape as bumping it in the flat-sigma world, and that spot-
            response IS part of what those Greeks are meant to capture).
            Speed's stencil is the one exception: its wide outer bump
            (+/-3% of S) is wide enough to straddle K_25P/K_25C themselves
            (not just K_atm), and letting those pillar strikes track the
            bump reintroduces the same moving-kink instability that S_atm
            fixes for K_atm alone. vv_all_greeks passes the frozen pre-bump
            spot here ONLY for Speed's stencil, leaving every other Greek's
            pillars dynamic.
    """
    # Step 1: Unpack RR and BF into 25-delta call/put vols.
    #
    # BUG FIX: this had BF and RR's symmetry backwards. get_auto_rr_bf's own
    # docstring (this file, ~line 27) defines RR25 = sigma_25C - sigma_25P --
    # an ANTI-symmetric quantity that must flip sign between call and put --
    # and BF25 = (sigma_25C + sigma_25P)/2 - sigma_ATM, a SYMMETRIC quantity
    # that must apply with the SAME sign to both. The old code did the exact
    # opposite: bf25 flipped sign between the two lines (+bf for call, -bf
    # for put) while rr25 kept the SAME sign for both. Net effect: the real
    # skew driver (rr25 -- e.g. JPM's -3.87 vol points, a completely normal
    # equity risk reversal) shifted sigma_25C and sigma_25P DOWN/UP TOGETHER
    # instead of pulling them apart, contributing zero actual skew, while
    # only the (usually much smaller) bf25 value created any call/put spread
    # at all. That's exactly why the VannaVolga curve came back nearly flat
    # across AMD, BA, and JPM alike regardless of how strong the real 25d
    # risk reversal was -- not a data problem, not an inherent limitation of
    # 3-pillar vanna-volga, just RR and BF's symmetry swapped in the algebra.
    # Confirmed by re-deriving from the definitions: sigma_25C - sigma_25P
    # must equal rr25/100, and (sigma_25C+sigma_25P)/2 - sigma_ATM must equal
    # bf25/100 -- only true with bf25 applied symmetrically and rr25
    # applied anti-symmetrically, as below.
    sigma_ATM = atm_vol
    sigma_25C = sigma_ATM + bf25 / 100.0 + rr25 / 200.0
    sigma_25P = sigma_ATM + bf25 / 100.0 - rr25 / 200.0

    # Step 2: Compute actual 25-delta strikes using forward delta formula
    F = (S if S_pillars is None else S_pillars) * np.exp((r - q) * T)

    # For forward delta: delta_call = N(d1), target = 0.25
    # For forward delta: delta_put = -N(-d1), target = -0.25 => N(-d1) = 0.25
    K_25P = _forward_delta_strike(
        F, sigma_25P, T, 0.25
    )  # put: N(d1) maps to 0.25 for the negative
    # Actually for a put at -0.25 forward delta:
    # delta_put = -N(-d1) = -0.25 => N(-d1) = 0.25 => -d1 = N^{-1}(0.25) => d1 = -0.6745
    d1_put = -norm.ppf(0.25)  # -0.6745
    K_25P = F * np.exp(-d1_put * sigma_25P * np.sqrt(T) + 0.5 * sigma_25P**2 * T)

    d1_call = norm.ppf(0.25)  # 0.6745
    K_25C = F * np.exp(-d1_call * sigma_25C * np.sqrt(T) + 0.5 * sigma_25C**2 * T)

    K_atm = S if S_atm is None else S_atm  # ATM anchor strike; frozen at the
    # pre-bump spot when called from vv_all_greeks's finite-difference Greek
    # stencils via S_atm (see docstring above) to avoid a moving smile kink.

    # Step 3: the actual Vanna-Volga smile construction (Castagna &
    # Mercurio, "Consistent pricing and hedging of an FX options book",
    # 2007) -- a PRICE-space correction weighted by vega and Lagrange
    # log-strike weights, not a vol-space average or a bare polynomial fit.
    #
    # BUG FIX #2: an earlier pass here replaced a vega-weighted convex
    # combination (mathematically bounded within [min,max] of the 3 pillar
    # vols -- see the first bug fix this comment used to describe) with a
    # bare quadratic fit through the 3 (log-strike, vol) points. That
    # curves, but it isn't real Vanna-Volga either -- it's an arbitrary
    # parabola with no connection to the option's own vega/vanna/volga, so
    # it can (and did, confirmed live) swing to unrealistic values far from
    # the pillars since nothing constrains its curvature beyond 3 points.
    # VV is "smile-aware" precisely because it solves for a NEW STRIKE'S
    # vol from the market's actual price data at the 3 pillars via a
    # replication argument, not by extrapolating a fitted curve through
    # vol values. The real construction:
    #   1. Price a call at each pillar strike (K1=25dP, K2=ATM, K3=25dC)
    #      twice: once at the FLAT reference vol sigma_ATM (C0_i), once at
    #      that pillar's OWN market vol (Cmkt_i). K2's correction is
    #      trivially zero (K2==K_atm, sigma==sigma_ATM by construction).
    #   2. For the TARGET strike K, compute weights x1(K)/x3(K) -- a
    #      vega-ratio-scaled Lagrange interpolation in log-strike space
    #      (the standard closed-form simplification of the full vanna/volga
    #      replication weights; K2's weight is unneeded since its term is
    #      zero regardless).
    #   3. C_VV(K) = C_BS(K, sigma_ATM) + x1(K)*(Cmkt_1-C0_1) +
    #      x3(K)*(Cmkt_3-C0_3) -- the flat-vol price PLUS the market's own
    #      observed wing/skew information, weighted by how much this
    #      strike's vega resembles each pillar's.
    #   4. Invert C_VV(K) back to an implied vol via Black-Scholes Newton
    #      (this IS the "solve for IV at this strike" step the other
    #      models on the smile chart also do -- VV just solves against its
    #      OWN replicated price instead of a live market quote).
    K1, K3 = K_25P, K_25C
    logK1, logK2, logK3, logK = (
        np.log(K1),
        np.log(K_atm),
        np.log(K3),
        np.log(K),
    )

    def _bs_call(K_, sigma_):
        return _bs_call_price(S, K_, T, r, q, sigma_)

    def _vega(K_, sigma_):
        return _bs_vega(S, K_, T, r, q, sigma_)

    C0_1, C0_3 = _bs_call(K1, sigma_ATM), _bs_call(K3, sigma_ATM)
    Cmkt_1, Cmkt_3 = _bs_call(K1, sigma_25P), _bs_call(K3, sigma_25C)

    vega_K = _vega(K, sigma_ATM)
    vega_1, vega_3 = _vega(K1, sigma_ATM), _vega(K3, sigma_ATM)
    x1 = (
        (vega_K / vega_1)
        * (logK2 - logK)
        * (logK3 - logK)
        / ((logK2 - logK1) * (logK3 - logK1))
        if vega_1 > 1e-12
        else 0.0
    )
    x3 = (
        (vega_K / vega_3)
        * (logK - logK1)
        * (logK - logK2)
        / ((logK3 - logK1) * (logK3 - logK2))
        if vega_3 > 1e-12
        else 0.0
    )

    C_VV = _bs_call(K, sigma_ATM) + x1 * (Cmkt_1 - C0_1) + x3 * (Cmkt_3 - C0_3)
    sigma = _bs_iv_newton(C_VV, S, K, T, r, q, seed=sigma_ATM)

    # Final sanity check: never return negative or zero vol
    return max(float(sigma), 0.001)


def get_vol_batch(S, K, T, r, q, atm_vol, rr25, bf25):
    """Vectorized version of get_vol's Castagna-Mercurio price-space
    correction (see get_vol's docstring for the full derivation) -- same
    formula, evaluated for an array of strikes K at once instead of one
    Python-level call per strike.

    The three pillars (ATM/25P/25C), their reference-vol vegas, and their
    price corrections depend only on S/T/r/q/atm_vol/rr25/bf25, NOT on K,
    so they're computed exactly once here regardless of how many strikes
    are passed in.

    Returns NaN for strikes where the correction is numerically
    unidentifiable: the Lagrange weights are scaled by vega_K/vega_pillar,
    both evaluated at the FLAT reference sigma_ATM -- and reference vega
    genuinely collapses to ~0 for strikes far from the pillars in dollar
    terms (a deep ITM/OTM option has near-zero vega under a LOW assumed
    vol, even though its true required vol could be large). Confirmed
    live on SPY: strikes below ~460 (spot ~769, ~1 month expiry) had
    essentially zero reference vega, so the correction vanished and the
    reconstructed price reverted to pure intrinsic -- not a code bug, the
    same numerical-identifiability limit MCHestonLSM._bs_iv_batch's
    min_vega_frac already handles for Heston's smile, reused here rather
    than reinventing it. This is the honest domain boundary of the
    simplified vega-weighted VV approximation (designed for FX-style
    narrow delta bands, not a full equity dollar-strike chain) -- dropping
    those points is more honest than returning a fabricated near-zero vol.
    """
    from MCHestonLSM import _bs_iv_batch  # local import: avoids importing
    # MCHestonLSM's heavier dependency surface at VannaVolga's own import
    # time for callers (e.g. main.py's interactive prompts) that never
    # touch the smile chart at all.

    K = np.asarray(K, dtype=float)

    # Same RR/BF symmetry fix as get_vol -- see that function's comment.
    sigma_ATM = atm_vol
    sigma_25C = sigma_ATM + bf25 / 100.0 + rr25 / 200.0
    sigma_25P = sigma_ATM + bf25 / 100.0 - rr25 / 200.0

    F = S * np.exp((r - q) * T)
    d1_put = -norm.ppf(0.25)
    K_25P = F * np.exp(-d1_put * sigma_25P * np.sqrt(T) + 0.5 * sigma_25P**2 * T)
    d1_call = norm.ppf(0.25)
    K_25C = F * np.exp(-d1_call * sigma_25C * np.sqrt(T) + 0.5 * sigma_25C**2 * T)
    K_atm = S

    K1, K3 = K_25P, K_25C
    logK1, logK2, logK3 = np.log(K1), np.log(K_atm), np.log(K3)
    logK = np.log(K)

    C0_1 = _bs_call_price(S, K1, T, r, q, sigma_ATM)
    C0_3 = _bs_call_price(S, K3, T, r, q, sigma_ATM)
    Cmkt_1 = _bs_call_price(S, K1, T, r, q, sigma_25P)
    Cmkt_3 = _bs_call_price(S, K3, T, r, q, sigma_25C)

    vega_K = _bs_vega(S, K, T, r, q, sigma_ATM)
    vega_1 = _bs_vega(S, K1, T, r, q, sigma_ATM)
    vega_3 = _bs_vega(S, K3, T, r, q, sigma_ATM)

    x1 = np.where(
        vega_1 > 1e-12,
        (vega_K / max(vega_1, 1e-12))
        * (logK2 - logK)
        * (logK3 - logK)
        / ((logK2 - logK1) * (logK3 - logK1)),
        0.0,
    )
    x3 = np.where(
        vega_3 > 1e-12,
        (vega_K / max(vega_3, 1e-12))
        * (logK - logK1)
        * (logK - logK2)
        / ((logK3 - logK1) * (logK3 - logK2)),
        0.0,
    )

    C_VV = (
        _bs_call_price(S, K, T, r, q, sigma_ATM)
        + x1 * (Cmkt_1 - C0_1)
        + x3 * (Cmkt_3 - C0_3)
    )
    seed = np.full(K.shape, sigma_ATM)
    return _bs_iv_batch(C_VV, S, K, T, r, q, seed_vols=seed, min_vega_frac=1e-3)


def calculate_weight(K, K_atm, K_rr, K_bf, sigma_atm, sigma_rr, sigma_bf):
    """Legacy function kept for compatibility. Not used in new get_vol."""
    return 1.0, 0.0, 0.0


def calculate_price(S, K, T, r, q, sigma):
    pricer = AmericanLSMPricer(S, K, T, r, q, sigma)
    return pricer.price()


def vv_all_greeks(S, K, T, r, q, cp, atm_vol, rr25, bf25, steps=401):
    """Vanna-Volga's OWN Greek engine: every bump of S / T / r
    re-interpolates the VV 3-pillar smile via get_vol() at the bumped state
    and re-prices through the LR tree at the resulting smile-implied
    sigma. Vega bumps atm_vol (the parallel-shift level of the VV smile).

    Why this is different from just calling lr_all_greeks with VV's solved
    sigma:
      - VV's sigma at K is an interpolation of three market pillars
        (25dPut / ATM / 25dCall) whose STRIKES themselves are functions
        of the bumped state (25-delta strike locations depend on S / T /
        r / q via the forward-delta formula in _forward_delta_strike). So
        bumping S under a fixed VV smile is not the same shape as bumping
        S in the flat-sigma world -- the smile itself moves. Same issue
        as SABR (see sabr_all_greeks): a flat-sigma Delta was giving
        VannaVolga a Delta byte-for-byte identical to CRR/LR/NR before
        this refactor, hiding the smile-response contribution.
      - Vega bumps atm_vol (parallel shift of the whole VV smile).
        rr25/bf25 stay constant across the bump -- they are risk-reversal
        and butterfly quotes read from the market, not sensitivities of
        the pricing model itself. The right "VV Vega" is dP/d(atm_vol)
        holding the market shape (RR/BF) fixed, matching how a VV
        trader would actually hedge vol exposure.
      - Vanna and Vomma inherit the smile-awareness (2nd derivatives of
        the same bump-and-reprice pricing function).

    atm_vol / rr25 / bf25 come from the caller in the same convention
    get_vol expects (atm_vol as a decimal like 0.80, rr25/bf25 in vol
    POINTS like 7.0 = 7 vol pts). This is the same market read
    get_auto_rr_bf provides, passed through main.py's VannaVolga branch.
    """
    try:
        from .american_binomial import _bs_rho, leisen_reimer_american_price
    except ImportError:
        from american_binomial import _bs_rho, leisen_reimer_american_price

    def smile_sigma(S_, T_, r_, atm_vol_=None, freeze_pillars=False):
        av = atm_vol if atm_vol_ is None else atm_vol_
        # get_vol takes atm_vol as a decimal (0.80 not 80), rr25/bf25 in
        # vol POINTS. Same convention here.
        #
        # S_atm=S (the OUTER, pre-bump spot closed over from vv_all_greeks's
        # own arguments -- not S_, the possibly-bumped spot being repriced
        # at) freezes the ATM pillar's strike across every finite-difference
        # evaluation. Without this, K_atm tracked the bumped S_ exactly,
        # which moves the smile interpolation's V-shaped kink (located at
        # strike == K_atm) in lockstep with the bump; any stencil whose bump
        # range straddled the option's actual strike K then straddled that
        # moving kink too. This alone fixes every Greek EXCEPT Speed (see
        # S_pillars below), and is applied unconditionally here since it has
        # no effect on the smile-aware spot-response Delta/Vanna/etc. rely on
        # -- only on where the ATM interpolation kink sits.
        #
        # freeze_pillars additionally freezes the 25-delta pillar STRIKES
        # (K_25P/K_25C) at the pre-bump spot via S_pillars=S. This is NOT
        # applied by default: Delta/Vanna/Gamma/Charm/Color are deliberately
        # smile-aware (see vv_all_greeks docstring) and are meant to see the
        # pillars respond to a bumped spot. Only Speed's stencil (wide +/-3%
        # outer bump, wide enough to straddle K_25P/K_25C themselves, not
        # just K_atm) passes freeze_pillars=True.
        sig = float(
            get_vol(
                S_,
                K,
                T_,
                r_,
                q,
                av,
                rr25,
                bf25,
                S_atm=S,
                S_pillars=(S if freeze_pillars else None),
            )
        )
        return max(sig, 0.001)

    def price(S_=S, T_=T, r_=r, atm_vol_=None, freeze_pillars=False):
        sig = smile_sigma(S_, T_, r_, atm_vol_, freeze_pillars=freeze_pillars)
        return leisen_reimer_american_price(S_, K, T_, r_, sig, q, cp, steps)

    sigma_here = smile_sigma(S, T, r)

    if T <= 0 or sigma_here <= 1e-6:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "rho": 0.0,
            "theta": 0.0,
            "vanna": 0.0,
            "vomma": 0.0,
            "speed": 0.0,
            "charm": 0.0,
            "color": 0.0,
            "rho_euro": 0.0,
            "rho_ee_premium": 0.0,
            "sigma": sigma_here,
        }

    dS = S * 0.01
    d_atm = max(atm_vol * 0.02, 1e-4)
    dR = 0.0025
    dT = max(T * 0.02, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    p0 = price()
    delta = (price(S_=S + dS) - price(S_=S - dS)) / (2 * dS)
    gamma = (price(S_=S + dS) - 2 * p0 + price(S_=S - dS)) / (dS * dS)
    # Vega: dP / d(atm_vol) directly. atm_vol IS the vol convention here
    # (no alpha-to-sigma conversion needed like SABR).
    vega = (price(atm_vol_=atm_vol + d_atm) - price(atm_vol_=atm_vol - d_atm)) / (
        2 * d_atm
    )
    rho_am = (price(r_=r + dR) - price(r_=r - dR)) / (2 * dR)
    theta = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    # ---- 2nd-order Greeks: Vanna, Vomma, Speed, Charm, Color -------------
    # All VV-aware finite differences: every bumped evaluation goes back
    # through price() -> smile_sigma() -> get_vol(), so the smile is
    # RE-INTERPOLATED at each bumped (S, T, atm_vol). That is the whole
    # point of doing this here instead of calling the shared Black-Scholes
    # closed forms -- those froze sigma at sigma_here and threw away the
    # smile's own sensitivity (the dSigma/dS and dSigma/dVol terms).
    #
    # Sign/scale conventions deliberately match MC's _closed_form_* so this
    # is a methodology swap, not a convention change:
    #   vanna = dVega/dS, vomma = dVega/dVol, speed = dGamma/dS (per spot),
    #   charm = -dDelta/dT_remaining per YEAR,
    #   color = +dGamma/dT_remaining per YEAR (opposite sign from charm).

    def _delta_at(S_, T_):
        return (price(S_=S_ + dS, T_=T_) - price(S_=S_ - dS, T_=T_)) / (2 * dS)

    def _gamma_at(S_, T_, freeze_pillars=False):
        return (
            price(S_=S_ + dS, T_=T_, freeze_pillars=freeze_pillars)
            - 2 * price(S_=S_, T_=T_, freeze_pillars=freeze_pillars)
            + price(S_=S_ - dS, T_=T_, freeze_pillars=freeze_pillars)
        ) / (dS * dS)

    def _vega_at(S_, atm_center):
        return (
            price(S_=S_, atm_vol_=atm_center + d_atm)
            - price(S_=S_, atm_vol_=atm_center - d_atm)
        ) / (2 * d_atm)

    # Vanna = dVega/dS. Bump spot, re-interpolate the smile, re-vega.
    dS_vanna = max(S * 0.01, 0.01)
    vanna = (_vega_at(S + dS_vanna, atm_vol) - _vega_at(S - dS_vanna, atm_vol)) / (
        2 * dS_vanna
    )

    # Vomma = dVega/dATM_vol. Bump ATM vol only; RR/BF held fixed so the
    # smile shape rides along the way a real ATM-vol move would.
    vomma = (_vega_at(S, atm_vol + d_atm) - _vega_at(S, atm_vol - d_atm)) / (2 * d_atm)

    # Speed = dGamma/dS. Wider outer bump than the inner dS so the nested
    # difference doesn't collapse into binomial lattice noise.
    # freeze_pillars=True here (and only here): this bump is wide enough
    # (+/-3% of S) to straddle the 25-delta pillar strikes themselves, so
    # without freezing them the interpolation kink moves under the stencil
    # and Speed comes out unstable/sign-flipping, same failure mode S_atm
    # fixes for K_atm.
    dS_speed = max(S * 0.03, 0.03)
    speed = (
        _gamma_at(S + dS_speed, T, freeze_pillars=True)
        - _gamma_at(S - dS_speed, T, freeze_pillars=True)
    ) / (2 * dS_speed)

    # Charm / Color share the same maturity bumps.
    dT_2nd = max(T * 0.01, 1.0 / 365.0)
    T_lo = max(1e-6, T - dT_2nd)
    T_hi = T + dT_2nd
    T_span_2nd = T_hi - T_lo

    # House convention (confirmed correct by BAW/MC/Heston/the dead-code
    # american_second_third_order_greeks, and matching the docstring at the
    # top of this block): Charm = -dDelta/dTau, Color = +dGamma/dTau, both
    # per YEAR of time-to-maturity (Tau = T_remaining, so T_hi = more time
    # remaining, T_lo = less). Charm is negated w.r.t. dTau; Color is NOT --
    # it previously carried a stray negation justified by a reference to
    # "MC._closed_form_color", a function that no longer exists anywhere in
    # this repo (grep confirms it), so that justification cannot be checked
    # and the sign contradicted this very docstring. Removed the negation.
    charm = -(_delta_at(S, T_hi) - _delta_at(S, T_lo)) / T_span_2nd
    color = (_gamma_at(S, T_hi) - _gamma_at(S, T_lo)) / T_span_2nd

    rho_euro = _bs_rho(S, K, T, r, q, sigma_here, cp)
    rho_ee_premium = rho_am - rho_euro

    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "rho": rho_am,
        "theta": theta,
        "vanna": vanna,
        "vomma": vomma,
        "speed": speed,
        "charm": charm,
        "color": color,
        "rho_euro": rho_euro,
        "rho_ee_premium": rho_ee_premium,
        "sigma": sigma_here,
    }
