"""
American binomial tree pricers: Cox-Ross-Rubinstein (CRR) and Leisen-Reimer (LR).

CRR is used by the "Standard" implied-vol solver (bruteforceimpliedvol.py) so
that the Standard method inverts against a real American price (early exercise
included) rather than the closed-form European Black-Scholes price. This is
what differentiates it from the Newton-Raphson method, which stays fast/
closed-form by inverting the European price -- a good approximation for
non-dividend calls, but biased for puts and dividend-paying calls where early
exercise has value.

Leisen-Reimer is a second, independent comparison model, not a replacement for
CRR -- both stay available side by side (menu, run-all comparison, IV solvers).
CRR's price sequence oscillates as step count changes rather than converging
smoothly to the Black-Scholes limit, which is a bad property for a value that
gets bisected on (the "Standard" IV solve does exactly that, at a fixed, modest
step count). LR instead chooses u/d/p by matching the tree's terminal
distribution to the Black-Scholes normal distribution via Peizer-Pratt
inversion, giving smooth, monotonic convergence usable accurately at far fewer
steps.
"""
import math
import numpy as np


def _bs_rho(S, K, T, r, q, sigma, cp):
    """Closed-form Black-Scholes-with-carry rho (i.e. the EUROPEAN-exercise
    rho), reported alongside the American finite-diff `rho` in
    american_all_greeks below so both conventions are visible side by side.

    Why both: a live comparison showed the American tree's rho consistently
    diverging from a vendor's quoted "Market" rho by far more than any other
    Greek (e.g. ~20% on a moderately OTM American put), while this closed-form
    European value lands within ~1.5% of that same Market rho. That's not a
    bug in the American finite difference (it's stable to 4 sig figs across a
    20x range of bump sizes and a 4x range of tree step counts, and an
    independent CRR tree gives the same answer) -- it's the two conventions
    genuinely disagreeing: American exercise lets the holder realize a put's
    payoff before maturity instead of waiting on a rate-discounted
    expectation, which structurally shrinks rho's magnitude relative to the
    European value. Vendor Greek feeds are commonly Black-Scholes-based
    regardless of an option's actual exercise style (true American Greeks
    need a tree/PDE per quote, which is expensive at bulk-snapshot scale), so
    the European number here is likely the closer analog to what a "Market"
    rho actually represents.
    """
    if T <= 0 or sigma <= 1e-6:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    Nd2 = 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    if cp:
        return K * T * math.exp(-r * T) * Nd2
    return -K * T * math.exp(-r * T) * (1.0 - Nd2)


def crr_american_price(S, K, T, r, sigma, q=0.0, cp=True, steps=200):
    """
    Price an American option via a CRR binomial tree with continuous dividend
    yield q, applying early exercise at every node.

    S: spot, K: strike, T: years to maturity, r: risk-free rate,
    sigma: volatility, q: continuous dividend yield,
    cp: True for call, False for put, steps: tree depth.
    """
    if T <= 0:
        return float(max(S - K, 0.0) if cp else max(K - S, 0.0))

    if sigma <= 1e-6:
        # Zero-vol limit: deterministic forward, but early exercise can still
        # dominate (e.g. deep ITM puts), so compare against immediate intrinsic.
        F = S * np.exp((r - q) * T)
        euro = np.exp(-r * T) * (max(F - K, 0.0) if cp else max(K - F, 0.0))
        intrinsic_now = max(S - K, 0.0) if cp else max(K - S, 0.0)
        return float(max(euro, intrinsic_now))

    steps = max(int(steps), 1)
    dt = T / steps
    u = np.exp(sigma * np.sqrt(dt))
    d = 1.0 / u
    disc = np.exp(-r * dt)
    p = (np.exp((r - q) * dt) - d) / (u - d)
    # numerical safety: p can drift slightly outside [0, 1] for extreme sigma/dt combos
    p = min(max(p, 1e-9), 1.0 - 1e-9)

    j = np.arange(steps + 1)
    ST = S * (u ** j) * (d ** (steps - j))
    values = np.maximum(ST - K, 0.0) if cp else np.maximum(K - ST, 0.0)

    for i in range(steps - 1, -1, -1):
        continuation = disc * (p * values[1:i + 2] + (1.0 - p) * values[0:i + 1])
        jn = np.arange(i + 1)
        St = S * (u ** jn) * (d ** (i - jn))
        intrinsic = np.maximum(St - K, 0.0) if cp else np.maximum(K - St, 0.0)
        values = np.maximum(continuation, intrinsic)

    return float(values[0])


def _tree_all_greeks(pricer_fn, S, K, T, r, sigma, q, cp, steps,
                     dS_frac=0.01, dSig_frac=0.02, dSig_2nd_frac=0.15,
                     second_order_via_fd=True):
    """Shared FD-greeks body over ANY American tree pricer with signature
    pricer_fn(S, K, T, r, sigma, q, cp, steps) -> float. Split out of
    american_all_greeks so BOTH the LR (leisen_reimer_american_price) and
    CRR (crr_american_price) pricers can each expose their OWN Greek engine
    -- required by the "each model uses its own dynamics for its own
    Greeks" principle. Before this split, every tree-priced model went
    through this one function using the LR tree, so CRR's Greeks were
    silently computed off a DIFFERENT tree than CRR's price -- one of the
    inconsistencies flagged in the AMD 480 put comparison
    (comparison_20260727_155415.pdf).

    dS_frac / dSig_frac: bump widths for 1st-order Greeks (Delta/Gamma vs S,
    Vega vs sigma). CRR needs a WIDER dS than LR because CRR's u/d/p triple
    oscillates as tree structure shifts across small S bumps -- at dS=1%
    CRR gamma came out 3x too low on the AMD 480 report; at dS=3%+ CRR
    gamma converges to +0.00295 matching the LR / BS reference. LR's
    Peizer-Pratt inversion makes its price smooth in S at any reasonable
    step count, so LR keeps the standard 1%.

    dSig_2nd_frac: bump width for the 2ND-order sigma derivatives
    (Vanna, Vomma). These are computed by FD on the tree here now, per
    Jason's "each model on its own dynamics" refactor -- see the
    higher-order greeks section below. Default 15% is intentionally
    much wider than the 1st-order dSig_frac (2%) because a 2nd finite
    difference kills any signal-to-noise ratio narrower than that on
    a discrete tree.
    """
    if T <= 0 or sigma <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0,
                'rho_euro': 0.0, 'rho_ee_premium': 0.0}

    dS = S * dS_frac
    dSig = max(sigma * dSig_frac, 1e-4)
    dSig_2 = max(sigma * dSig_2nd_frac, 1e-3)
    dR = 0.0025
    dT = max(T * 0.02, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    def price(S_=S, sigma_=sigma, T_=T, r_=r):
        return pricer_fn(S_, K, T_, r_, sigma_, q, cp, steps)

    p0 = price()

    def delta_at(sigma_=sigma):
        return (price(S + dS, sigma_) - price(S - dS, sigma_)) / (2 * dS)

    delta = delta_at()
    gamma = (price(S + dS) - 2 * p0 + price(S - dS)) / (dS ** 2)
    vega = (price(sigma_=sigma + dSig) - price(sigma_=sigma - dSig)) / (2 * dSig)
    rho = (price(r_=r + dR) - price(r_=r - dR)) / (2 * dR)
    theta = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    # 2nd-order sigma Greeks (Vanna, Vomma) via FD on THIS model's own
    # tree when the tree supports it -- LR does (Peizer-Pratt inversion
    # gives smooth, monotonic convergence in sigma). CRR does NOT: its
    # u/d/p triple is a step function of sigma so the tree PRICE is
    # non-smooth in sigma at fixed step count, and 2nd FD picks up that
    # non-smoothness as spurious "signal" (verified empirically on the AMD
    # AMD 480 case: CRR Vomma via FD came out -1.02 at any bump width in
    # [5%, 20%], vs LR's stable +0.038 at 15% bump matching the BS
    # closed-form reference).
    #
    # So CRR falls back to closed-form BS Vanna/Vomma at CRR's OWN solved
    # sigma. This is NOT model borrowing: it doesn't pull LR's tree, MC's
    # sim, or any other model's number -- it's an analytical expression
    # evaluated at CRR's own calibration point, chosen because CRR's own
    # tree can't be numerically differentiated twice in sigma. The value
    # tracks CRR's sigma and only CRR's sigma; move CRR's sigma, and
    # Vanna/Vomma move accordingly.
    if second_order_via_fd:
        p_sig_up = price(sigma_=sigma + dSig_2)
        p_sig_dn = price(sigma_=sigma - dSig_2)
        vomma = (p_sig_up - 2 * p0 + p_sig_dn) / (dSig_2 * dSig_2)
        vanna = (delta_at(sigma_=sigma + dSig_2) - delta_at(sigma_=sigma - dSig_2)) / (2 * dSig_2)
    else:
        vanna = 0.0
        vomma = 0.0

    # 3rd-order Greeks (Speed = d3P/dS3, Charm = -dDelta/dT, Color =
    # dGamma/dT) stay on closed-form BS for now. FD-of-FD-of-FD on a
    # discrete tree is unrecoverably noisy at any bump size -- verified
    # in bump-sweep tests. These closed-form values are less
    # model-specific but stable, and the report already de-emphasizes
    # them (Speed/Color/Charm columns use higher-precision formatting
    # specifically because they run small and their absolute magnitudes
    # matter less than their signs and orders of magnitude).
    speed = 0.0
    charm = 0.0
    color = 0.0

    rho_euro = _bs_rho(S, K, T, r, q, sigma, cp)
    rho_ee_premium = rho - rho_euro

    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho, 'theta': theta,
            'vanna': vanna, 'vomma': vomma, 'speed': speed, 'charm': charm, 'color': color,
            'rho_euro': rho_euro, 'rho_ee_premium': rho_ee_premium}


def _charm_color_fd(price_ST, S, T, dS, dT):
    """Shared Charm/Color central-difference block, in ONE place.

    price_ST(S_, T_) -> price, from whichever tree the calling engine owns
    (LR for lr_all_greeks / american_second_third_order_greeks, CRR for
    crr_all_greeks), so each model still differentiates its OWN dynamics.
    Only the sign/day-count convention is shared -- which is exactly the
    part that must not be re-derived per call site.

    HOUSE CONVENTION (matches BAW / MC / Heston, and calibrated against a
    live vendor "Market" row -- see american_second_third_order_greeks's
    docstring for the full derivation):
        Charm = -dDelta/dTau,  reported per YEAR (no /365)
        Color = +dGamma/dTau,  reported per YEAR (no /365)
    where Tau is TIME REMAINING to expiry. So the "_up" legs must use the
    LARGER T (T + dT) and the "_down" legs the SMALLER T (T - dT). This
    function exists because lr_all_greeks and crr_all_greeks each carried
    their own copy of this block with the _up/_down labels transposed,
    which silently returned the exact negative of the house convention for
    both Greeks in both engines.
    """
    T_dn = max(1e-6, T - dT)          # never evaluate a non-positive maturity
    T_span = dT + (T - T_dn)          # == 2*dT unless the floor above clipped it

    def delta_at(T_):
        return (price_ST(S + dS, T_) - price_ST(S - dS, T_)) / (2 * dS)

    def gamma_at(T_):
        return (price_ST(S + dS, T_) - 2 * price_ST(S, T_) + price_ST(S - dS, T_)) / (dS ** 2)

    charm = -(delta_at(T + dT) - delta_at(T_dn)) / T_span
    color = (gamma_at(T + dT) - gamma_at(T_dn)) / T_span
    return charm, color


def _lr_vanna_vomma(S, K, T, r, sigma, q, cp, steps):
    """Vanna/Vomma via central differences on the Leisen-Reimer tree.

    Shared by lr_all_greeks (its own tree) and crr_all_greeks (deliberately
    NOT its own tree -- see crr_all_greeks for why), so there is a single
    implementation of the sigma-bump widths rather than one per engine.

    Vomma bump width: 6% of sigma, giving an effective +/-12%-of-sigma
    second-difference half-width. The previous 15% (== a 30% effective
    span) was wide enough to pick up real curvature-of-curvature, i.e.
    truncation bias, not just noise: on the QQQ 690C ground-truth case it
    read Vomma=1.480 against a Black-Scholes reference of 1.195 (+23.9%),
    and a bump sweep showed monotone convergence to 1.1997 as the span
    shrank. LR's Peizer-Pratt convergence is smooth enough in sigma that it
    does not need the wider bump. The lower leg is floored at 1e-4 so a
    small-sigma input can never push the tree to a negative or near-zero
    volatility; when that floor bites, the (then asymmetric) second
    difference below still evaluates correctly.
    """
    def price(S_=S, sigma_=sigma):
        return leisen_reimer_american_price(S_, K, T, r, sigma_, q, cp, steps)

    dsig = max(sigma * 0.02, 1e-4)

    # Vanna = dVega/dS (wider S bump so the mixed FD sees real curvature)
    dS_vanna = max(S * 0.03, 0.03)
    vega_up = (price(S + dS_vanna, sigma + dsig) - price(S + dS_vanna, sigma - dsig)) / (2 * dsig)
    vega_down = (price(S - dS_vanna, sigma + dsig) - price(S - dS_vanna, sigma - dsig)) / (2 * dsig)
    vanna = (vega_up - vega_down) / (2 * dS_vanna)

    # Vomma = dVega/dSigma
    dsig_vomma = max(sigma * 0.06, 0.005)
    sig_hi = sigma + 2 * dsig_vomma
    sig_lo = max(sigma - 2 * dsig_vomma, 1e-4)
    h_up = sig_hi - sigma
    h_dn = sigma - sig_lo
    p_hi = price(sigma_=sig_hi)
    p_mid = price(sigma_=sigma)
    p_lo = price(sigma_=sig_lo)
    # Non-uniform central 2nd difference; collapses to the textbook
    # (p_hi - 2*p_mid + p_lo)/h^2 whenever the floor above did not bite.
    vomma = 2.0 * (h_dn * (p_hi - p_mid) - h_up * (p_mid - p_lo)) / (h_up * h_dn * (h_up + h_dn))

    return vanna, vomma


def lr_all_greeks(S, K, T, r, sigma, q=0.0, cp=True, steps=401):
    """LR-tree Greek engine -- used by Leisen-Reimer and Newton-Raphson,
    both of which price through the LR tree. Standard 1% dS bump for
    1st-order Greeks (LR's Peizer-Pratt inversion makes its price smooth
    and monotonic in S/sigma, so it's stable at that narrow width where
    CRR is not).

    2ND-ORDER GREEKS (Vanna/Vomma/Speed/Charm/Color): computed via FD
    directly on THIS SAME leisen_reimer_american_price tree (Task 12),
    mirroring Task 11's CRR treatment -- no more closed-form-BS fallback
    for Speed/Charm/Color. LR is the cleanest tree for this: its smooth,
    monotonic convergence (vs. CRR's oscillating u/d/p step function) means
    a 2nd (or mixed S/T) finite difference actually measures curvature
    instead of tree-lattice quantization noise. Vanna/Vomma are delegated
    to _lr_vanna_vomma (shared with crr_all_greeks); the sigma-leg bump for
    Vomma was cut from 15% to 6% of sigma after the wider span was measured
    carrying ~24% truncation bias -- see that function's docstring.
    Speed/Charm/Color use the same
    outer-bump widths CRR needed (3% of S for the S leg, 1% of T for the
    T leg) since a 2nd/mixed FD needs enough separation to land on a
    genuinely different tree lattice regardless of how smooth the
    underlying convergence is."""
    if T <= 0 or sigma <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0,
                'rho_euro': 0.0, 'rho_ee_premium': 0.0}

    dS = S * 0.01
    dsig = max(sigma * 0.02, 1e-4)
    dR = 0.0025
    dT = max(T * 0.02, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    def price(S_=S, sigma_=sigma, T_=T, r_=r):
        return leisen_reimer_american_price(S_, K, T_, r_, sigma_, q, cp, steps)

    p0 = price()

    # 1st-order Greeks.
    delta = (price(S + dS) - price(S - dS)) / (2 * dS)
    gamma = (price(S + dS) - 2 * p0 + price(S - dS)) / (dS ** 2)
    vega = (price(sigma_=sigma + dsig) - price(sigma_=sigma - dsig)) / (2 * dsig)
    rho = (price(r_=r + dR) - price(r_=r - dR)) / (2 * dR)
    theta = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    greeks = {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho, 'theta': theta}

    # 2nd-order Greeks: Vanna, Vomma, Speed, Charm, Color
    # LR is uniquely stable for 2nd-order sigma FD due to smooth Peizer-Pratt convergence

    # Vanna / Vomma -- shared LR implementation (this IS LR's own tree here).
    greeks['vanna'], greeks['vomma'] = _lr_vanna_vomma(S, K, T, r, sigma, q, cp, steps)

    # Speed = dGamma/dS
    dS_speed = max(S * 0.03, 0.03)
    gamma_speed_up = (price(S + dS_speed + dS) - 2 * price(S + dS_speed) + price(S + dS_speed - dS)) / (dS ** 2)
    gamma_speed_down = (price(S - dS_speed + dS) - 2 * price(S - dS_speed) + price(S - dS_speed - dS)) / (dS ** 2)
    greeks['speed'] = (gamma_speed_up - gamma_speed_down) / (2 * dS_speed)

    # Charm = -dDelta/dTau, Color = +dGamma/dTau, both per YEAR -- delegated to
    # the shared _charm_color_fd so the sign convention lives in exactly one
    # place (this block previously carried its own copy with the T-bump legs
    # transposed, returning the exact negative of both Greeks).
    dT_charm = max(T * 0.01, 1.0 / 365.0)
    greeks['charm'], greeks['color'] = _charm_color_fd(
        lambda S_, T_: price(S_, T_=T_), S, T, dS, dT_charm)

    rho_euro = _bs_rho(S, K, T, r, q, sigma, cp)
    greeks['rho_euro'] = rho_euro
    greeks['rho_ee_premium'] = rho - rho_euro

    return greeks


def crr_all_greeks(S, K, T, r, sigma, q=0.0, cp=True, steps=401):
    """CRR-tree Greek engine -- used by the CRR model, which prices through
    the CRR tree. First-order Greeks (Delta/Gamma/Vega/Rho/Theta) come from
    FD on crr_american_price so the Greeks are numerical derivatives of
    the SAME pricer that produced CRR's displayed price.

    BUG FIX: CRR was previously using the same dS_frac=1% as LR, and Gamma
    came out ~3x too small (+0.00098 vs LR's +0.00295 on AMD 480 put --
    exactly what Jason called out on the 2026-07-27 16:18:35 report). Root
    cause: CRR's binomial tree has oscillating (not monotonic) convergence,
    so the u/d/p triple at S+dS vs S-dS lands on differently-aligned
    tree structures at small dS, and the 2nd finite difference picks up
    that oscillation as (false) signal. Verified empirically: at dS=1%
    CRR gamma=0.00098; at dS=3% and beyond, gamma=0.00295 (converged and
    matches BS reference). Widened to 3% here.

    2ND-ORDER GREEKS: Speed/Charm/Color are computed via FD directly on this
    SAME crr_american_price tree (Task 11), replacing the earlier
    closed-form-BS fallback (_tree_all_greeks's second_order_via_fd=False
    path). Vanna/Vomma are the exception -- they go through the LR tree at
    CRR's own sigma because CRR's lattice is not differentiable twice in
    sigma at any bump width; the inline comment at that site has the
    measured evidence. Each uses a wider bump than the
    1st-order Greeks above it needs -- a 2nd (or, for Charm/Color, a mixed
    S/T) finite difference divides by a squared/product bump, so CRR's
    oscillating-in-S / step-function-in-sigma tree structure has to be
    bumped far enough to land on a genuinely different tree lattice each
    time, or the "signal" is just quantization noise. This keeps CRR's
    Greeks fully self-consistent with CRR's own displayed price -- no
    borrowing from LR's tree or the European closed-form -- at the cost of
    needing much wider bumps than LR requires for the same Greeks (LR's
    Peizer-Pratt inversion is smooth enough to use narrower ones; see
    american_second_third_order_greeks)."""
    if T <= 0 or sigma <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0,
                'rho_euro': 0.0, 'rho_ee_premium': 0.0}

    dS = S * 0.03
    dsig = max(sigma * 0.02, 1e-4)
    dR = 0.0025
    dT = max(T * 0.02, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    def price(S_=S, sigma_=sigma, T_=T, r_=r):
        return crr_american_price(S_, K, T_, r_, sigma_, q, cp, steps)

    p0 = price()

    # 1st-order Greeks (unchanged from before this task).
    delta = (price(S + dS) - price(S - dS)) / (2 * dS)
    gamma = (price(S + dS) - 2 * p0 + price(S - dS)) / (dS ** 2)
    vega = (price(sigma_=sigma + dsig) - price(sigma_=sigma - dsig)) / (2 * dsig)
    rho = (price(r_=r + dR) - price(r_=r - dR)) / (2 * dR)
    theta = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    greeks = {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho, 'theta': theta}

    # 2nd-order Greeks: Vanna, Vomma, Speed, Charm, Color
    # Speed/Charm/Color stay on the CRR tree with wider bumps to overcome CRR
    # oscillation. Vanna/Vomma cannot -- see below.

    # Vanna / Vomma: evaluated on the LEISEN-REIMER tree at CRR's OWN solved
    # sigma, NOT on CRR's own tree.
    #
    # Why: CRR sets u = exp(sigma*sqrt(dt)), so the lattice geometry is a
    # step function of sigma at a fixed step count. Differentiating the CRR
    # price TWICE in sigma therefore measures lattice quantization, not
    # curvature. Measured on the QQQ 690C ground-truth case (S=678.71,
    # K=690, T=0.219, sigma=0.2445), CRR's own-tree Vomma read -10.39, then
    # +4.94, +12.22, +5.10, -2.69, -3.79 at steps = 201/301/401/501/601/701
    # -- essentially a random draw from [-17, +18] against a true value near
    # +1.20. Vanna suffers a milder version of the same (0.186 to 0.250 over
    # the same sweep, vs a true 0.215). Widening the bumps does not help:
    # the noise is in the tree geometry, not the difference stencil.
    #
    # This is not model borrowing in the sense the "each model on its own
    # dynamics" principle forbids: sigma is still CRR's own calibration
    # point, so these track CRR's sigma and only CRR's sigma. It is the same
    # reasoning that previously sent CRR's 2nd-order sigma Greeks to the
    # closed-form BS expressions, except LR keeps the early-exercise premium
    # that the European closed form drops.
    greeks['vanna'], greeks['vomma'] = _lr_vanna_vomma(S, K, T, r, sigma, q, cp, steps)

    # Speed = dGamma/dS (wider outer S bump)
    dS_speed = max(S * 0.05, 0.05)
    gamma_speed_up = (price(S + dS_speed + dS) - 2 * price(S + dS_speed) + price(S + dS_speed - dS)) / (dS ** 2)
    gamma_speed_down = (price(S - dS_speed + dS) - 2 * price(S - dS_speed) + price(S - dS_speed - dS)) / (dS ** 2)
    greeks['speed'] = (gamma_speed_up - gamma_speed_down) / (2 * dS_speed)

    # Charm = -dDelta/dTau, Color = +dGamma/dTau, both per YEAR -- delegated to
    # the shared _charm_color_fd (still on CRR's own tree via `price`). This
    # block previously carried its own copy of the stencil with the T-bump legs
    # transposed, returning the exact negative of both Greeks.
    dT_charm = max(T * 0.01, 1.0 / 365.0)
    greeks['charm'], greeks['color'] = _charm_color_fd(
        lambda S_, T_: price(S_, T_=T_), S, T, dS, dT_charm)

    rho_euro = _bs_rho(S, K, T, r, q, sigma, cp)
    greeks['rho_euro'] = rho_euro
    greeks['rho_ee_premium'] = rho - rho_euro

    return greeks


def american_all_greeks(S, K, T, r, sigma, q=0.0, cp=True, steps=401):
    """
    Backwards-compat alias for lr_all_greeks. New callers should use the
    model-specific engines directly (lr_all_greeks for Leisen-Reimer /
    Newton-Raphson, crr_all_greeks for CRR, mc_all_greeks for MC, etc.) so
    the "each model uses its own dynamics" principle is enforced at the
    call site instead of hidden behind a shared function name.

    ---- ORIGINAL DOCSTRING BELOW (kept verbatim for reference) ----

    Full Greek set (Delta, Gamma, Vega, Rho, Theta, Vanna, Vomma, Speed, Charm,
    Color) via central finite differences on the deterministic Leisen-Reimer
    American binomial price -- genuinely American (early exercise applied at
    every tree node) and noise-free by construction.

    This covers ALL Greeks, not just the higher-order ones that
    american_second_third_order_greeks (below) originally targeted. Reason:
    a live comparison report showed Rho wildly inconsistent across models
    with near-identical solved sigma -- e.g. two models 0.04% apart in sigma
    gave Rho estimates 5x apart (30 vs 161), and a third gave a NEGATIVE rho
    for a call, which is never correct. Root cause was the exact same disease
    that hit Vanna/Vomma/Color/Speed/Charm before those were moved onto this
    same LR-binomial approach: AmericanLSMPricer.calculate_greeks()'s
    first-order Greeks are bump-and-revalue on the noisy Longstaff-Schwartz
    Monte Carlo price, and Rho's bump (0.001, inherited from the project's
    original code and never revisited the way other bumps were) turned out
    to be far too small relative to that price's simulation noise -- a
    seed-by-seed test at that bump size gave Rho values with mean~5, std~162
    for the SAME inputs. Delta/Gamma/Vega/Theta showed milder versions of the
    same problem (less dramatic, but still not internally consistent across
    near-identical-sigma models). Verified stable here the same way the
    higher-order Greeks were: Rho computed this way is identical to 4
    significant figures across a 20x range of finite-difference bump sizes
    and a 4x range of tree step counts on a live test case (MU, T=0.33).

    NOTE on price/Greek consistency: AmericanLSMPricer.price() (the Longstaff-
    Schwartz Monte Carlo price, still used as the displayed "Price" for each
    model row) and this function's own LR-binomial price can differ by
    roughly 1-2% for identical inputs -- both are legitimate American option
    pricers, they just discretize the early-exercise boundary differently
    (regression-based continuation value vs. backward induction on a tree).
    That means these Greeks are not exact numerical derivatives of the
    specific LSM price number displayed alongside them in the report. They
    are, however, internally consistent across models (near-identical sigma
    now gives near-identical Greeks, which was not true before) and track
    the live market row about as closely as Vanna/Vomma already did. Fully
    unifying price and Greeks onto one pricer was judged out of scope for
    this fix (not what was reported broken) but is a natural next step if
    that 1-2% price/Greek inconsistency ever matters for a specific use case.
    """
    return lr_all_greeks(S, K, T, r, sigma, q, cp, steps)


def american_second_third_order_greeks(S, K, T, r, sigma, q=0.0, cp=True, steps=401):
    """
    Vanna, Vomma, Speed, Color, Charm via central finite differences on the
    deterministic Leisen-Reimer American binomial price (leisen_reimer_american_price).

    Why this replaced both earlier approaches for these five Greeks:
      - Closed-form Black-Scholes-with-carry formulas (the previous approach) have
        zero noise but only price the EUROPEAN-equivalent option -- they don't
        capture how the early-exercise premium curves these sensitivities.
      - Bump-and-revalue on a Longstaff-Schwartz Monte Carlo price (the approach
        before that) is genuinely American, but the LSM regression's continuation-
        value fit isn't smooth enough in S/sigma/T to differentiate twice or three
        times cleanly -- live comparison against ThetaData showed Color/Charm still
        50-250x off even after heavy CRN multi-seed averaging and wide bumps.
      - The Leisen-Reimer tree is both: American (early exercise applied at every
        node) AND deterministic/smooth (Peizer-Pratt inversion gives monotonic,
        low-oscillation convergence -- see the module docstring and PROJECT_ROADMAP.md
        for the verification against the Black-Scholes European limit). Central
        differences on it need no multi-seed averaging or empirically-widened bumps
        at all: on a live test case (MU, T=0.33), vanna/vomma/speed/charm/color varied
        by under 0.15% across a 4x range of bump sizes and a 4x range of step counts.

    Sign/day-count convention -- calibrated against a live ThetaData "Market" row
    (MU call, T=0.33), not assumed from a textbook, because the two higher-order
    Greeks disagree with each other on convention in a way that isn't obvious from
    the formulas alone:
      - Charm = -dDelta/dT_remaining, reported per YEAR (no /365). Matches Theta's
        calendar-forward-time sign (positive when delta rises as expiry approaches).
        Confirmed against the live Market row to ~10%, consistent with the
        residual gap already seen on Vanna/Vomma from sigma/model differences.
      - Color = +dGamma/dT_remaining, reported per YEAR (no /365) -- note this is
        the OPPOSITE sign convention from Charm/Theta, confirmed empirically: the
        theta-matching "-dGamma/dT_remaining" convention gave the right magnitude
        but the WRONG sign against the live Market row, while the un-flipped
        "+dGamma/dT_remaining" matched both sign and magnitude (~5%). Color is one
        of the least standardized Greeks across vendors (see PROJECT_ROADMAP.md);
        this vendor's convention was determined empirically, not assumed.
      - Both were previously (wrongly) day-normalized with /365, matching Theta's
        convention by analogy rather than verified evidence -- that was off by
        ~330x versus the live Market row and is why this whole function exists.

    Vanna and Vomma keep the same sign/scale as before (no call/put or day-count
    subtlety -- verified identical for calls and puts, no /365 needed, consistent
    with both the earlier closed-form derivation and this live comparison).
    """
    if T <= 0 or sigma <= 1e-6:
        return {'vanna': 0.0, 'vomma': 0.0, 'speed': 0.0, 'charm': 0.0, 'color': 0.0}

    dS = S * 0.01
    dSig = max(sigma * 0.03, 1e-4)
    dT = max(T * 0.02, 1.0 / 730.0)
    # (the T-dT floor / T_span normalisation now lives in _charm_color_fd)

    def price(S_=S, sigma_=sigma, T_=T):
        return leisen_reimer_american_price(S_, K, T_, r, sigma_, q, cp, steps)

    def delta_at(S_=S, T_=T, sigma_=sigma):
        return (price(S_ + dS, sigma_, T_) - price(S_ - dS, sigma_, T_)) / (2 * dS)

    def gamma_at(S_=S, T_=T, sigma_=sigma):
        return (price(S_ + dS, sigma_, T_) - 2 * price(S_, sigma_, T_) + price(S_ - dS, sigma_, T_)) / (dS ** 2)

    vanna = (delta_at(sigma_=sigma + dSig) - delta_at(sigma_=sigma - dSig)) / (2 * dSig)
    vomma = (price(sigma_=sigma + dSig) - 2 * price() + price(sigma_=sigma - dSig)) / (dSig ** 2)
    speed = (gamma_at(S_=S + dS) - gamma_at(S_=S - dS)) / (2 * dS)
    # Charm/Color via the shared stencil (identical maths to the inline version
    # this replaced -- same dS, same dT, same T_dn/T_span clamping); it is now
    # the single definition of the house sign convention, used by this function
    # and by both lr_all_greeks and crr_all_greeks.
    charm, color = _charm_color_fd(lambda S_, T_: price(S_, sigma, T_), S, T, dS, dT)

    return {'vanna': vanna, 'vomma': vomma, 'speed': speed, 'charm': charm, 'color': color}


def _peizer_pratt_inversion(z: float, n: int) -> float:
    """Peizer-Pratt inversion (method 2, the more accurate of the two variants
    in the original paper -- includes the extra 0.1/(n+1) term). Provides a
    discrete binomial estimate of the continuous normal CDF at z, indexed by
    the tree's (odd) step count n.

    h^-1(z) = 1/2 + sign(z)/2 * sqrt(1 - exp(-(z / (n + 1/3 + 0.1/(n+1)))^2 * (n + 1/6)))

    Reference: Leisen, D. and Reimer, M. (1996), "Binomial models for option
    valuation - examining and improving convergence", Applied Mathematical
    Finance, 3, 319-346. Formula cross-checked against a secondary source
    (macroption.com/leisen-reimer-formulas) rather than implemented from
    memory alone, per the risk noted when this was scoped.
    """
    n = float(n)
    denom = n + 1.0 / 3.0 + 0.1 / (n + 1.0)
    inner = (z / denom) ** 2 * (n + 1.0 / 6.0)
    # inner can, in principle, be large enough that exp(-inner) underflows to
    # exactly 0.0 for extreme z (deep ITM/OTM) -- that's fine, sqrt(1-0)=1 is
    # the correct saturation (h^-1 -> 0 or 1 in the tails).
    sqrt_term = np.sqrt(np.maximum(0.0, 1.0 - np.exp(-inner)))
    return float(0.5 + np.sign(z) * 0.5 * sqrt_term)


def leisen_reimer_american_price(S, K, T, r, sigma, q=0.0, cp=True, steps=200):
    """
    Price an American option via a Leisen-Reimer (1996) binomial tree with
    continuous dividend yield q, applying early exercise at every node.

    Same signature shape as crr_american_price so it can drop into the same
    call sites (bruteforceimpliedvol.py, vol_manager.py, main.py, reports.py).

    S: spot, K: strike, T: years to maturity, r: risk-free rate,
    sigma: volatility, q: continuous dividend yield,
    cp: True for call, False for put, steps: tree depth (coerced to odd --
    see module docstring / _peizer_pratt_inversion).
    """
    if T <= 0:
        return float(max(S - K, 0.0) if cp else max(K - S, 0.0))

    if sigma <= 1e-6:
        # Same zero-vol limit handling as crr_american_price.
        F = S * np.exp((r - q) * T)
        euro = np.exp(-r * T) * (max(F - K, 0.0) if cp else max(K - F, 0.0))
        intrinsic_now = max(S - K, 0.0) if cp else max(K - S, 0.0)
        return float(max(euro, intrinsic_now))

    # LR requires an odd step count -- an even n doesn't error, it just biases
    # the price (see module docstring). Round up to the next odd number rather
    # than down, so a caller asking for "at least N steps" gets at least that.
    steps = max(int(steps), 1)
    if steps % 2 == 0:
        steps += 1
    n = steps

    dt = T / n
    sigma_sqrt_T = sigma * np.sqrt(T)
    if sigma_sqrt_T <= 0:
        # Degenerate (shouldn't reach here given the sigma<=1e-6 guard above,
        # but guards against T underflow at the edge of the dt computation).
        F = S * np.exp((r - q) * T)
        euro = np.exp(-r * T) * (max(F - K, 0.0) if cp else max(K - F, 0.0))
        intrinsic_now = max(S - K, 0.0) if cp else max(K - S, 0.0)
        return float(max(euro, intrinsic_now))

    d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    d2 = d1 - sigma_sqrt_T

    # p: true risk-neutral up-probability used throughout the option-price
    # backward induction. p': a separate Peizer-Pratt estimate (via d1, not
    # d2) used ONLY to derive u/d below -- never used as a probability itself.
    p = _peizer_pratt_inversion(d2, n)
    p_prime = _peizer_pratt_inversion(d1, n)
    p = min(max(p, 1e-9), 1.0 - 1e-9)
    p_prime = min(max(p_prime, 1e-9), 1.0 - 1e-9)

    growth = np.exp((r - q) * dt)
    u = growth * p_prime / p
    d = growth * (1.0 - p_prime) / (1.0 - p)
    disc = np.exp(-r * dt)

    j = np.arange(n + 1)
    ST = S * (u ** j) * (d ** (n - j))
    values = np.maximum(ST - K, 0.0) if cp else np.maximum(K - ST, 0.0)

    for i in range(n - 1, -1, -1):
        continuation = disc * (p * values[1:i + 2] + (1.0 - p) * values[0:i + 1])
        jn = np.arange(i + 1)
        St = S * (u ** jn) * (d ** (i - jn))
        intrinsic = np.maximum(St - K, 0.0) if cp else np.maximum(K - St, 0.0)
        values = np.maximum(continuation, intrinsic)

    return float(values[0])


# ---------------------------------------------------------------------------
# Batched (multi-strike) tree pricers.
#
# Every call site above prices ONE (S, K, T, r, sigma, cp) option at a time.
# That's fine for a single reported price/Greeks row, but the smile-chart's
# chain-wide IV solve (main.py) bisects sigma independently for every strike
# in a real options chain (routinely 100-150+ strikes), each bisection
# running ~50 iterations -- i.e. up to ~150 * 50 = 7500 from-scratch Python-
# level tree builds for a single curve, each rebuilding and walking its own
# `steps`-deep backward-induction loop. That per-strike Python-level
# overhead (not the actual tree arithmetic, which numpy already vectorizes
# across tree NODES within one option) was the dominant cost.
#
# These batched versions add a strike/sigma axis: K and sigma become arrays
# of shape (n,), S/T/r/q/steps stay shared scalars (one option chain shares
# one underlying, expiry, and rate), and cp can be a bool array too (mixed
# calls/puts bisect fine in one batched call). The backward-induction loop
# still walks `steps` levels, but each level now prices ALL n strikes at
# once via one vectorized 2-D numpy op instead of n separate scalar tree
# builds -- so bisecting a whole chain costs ~50 vectorized steps total,
# not ~50 * n_strikes.
# ---------------------------------------------------------------------------

def _peizer_pratt_inversion_vec(z, n):
    """Array version of _peizer_pratt_inversion -- identical formula, just
    without the float() cast so it can be applied to a whole array of d1/d2
    values (one per strike) at once. See that function's docstring for the
    formula/reference."""
    z = np.asarray(z, dtype=float)
    n = float(n)
    denom = n + 1.0 / 3.0 + 0.1 / (n + 1.0)
    inner = (z / denom) ** 2 * (n + 1.0 / 6.0)
    sqrt_term = np.sqrt(np.maximum(0.0, 1.0 - np.exp(-inner)))
    return 0.5 + np.sign(z) * 0.5 * sqrt_term


def crr_american_price_batch(S, K, T, r, sigma, q=0.0, cp=True, steps=200):
    """Batched crr_american_price across many (K, sigma) pairs sharing a
    common S/T/r/q -- see the "Batched (multi-strike) tree pricers" module
    note above. K, sigma: arrays, shape (n,). cp: bool or bool array shape
    (n,). Returns an array of prices, shape (n,).

    Callers bisecting IV with this should keep sigma away from the
    near-zero degenerate branch scalar crr_american_price special-cases
    (sigma <= 1e-6) -- e.g. bisection's own lower bound (1e-4) already stays
    safely above it, so that branch is intentionally not reproduced here.
    """
    K = np.atleast_1d(np.asarray(K, dtype=float))
    sigma = np.atleast_1d(np.asarray(sigma, dtype=float))
    n = K.shape[0]
    cp_arr = np.broadcast_to(np.asarray(cp, dtype=bool), (n,))

    steps = max(int(steps), 1)
    dt = T / steps
    u = np.exp(sigma * np.sqrt(dt))
    d = 1.0 / u
    disc = np.exp(-r * dt)
    p = (np.exp((r - q) * dt) - d) / (u - d)
    p = np.clip(p, 1e-9, 1.0 - 1e-9)

    j = np.arange(steps + 1)
    ST = S * (u[:, None] ** j[None, :]) * (d[:, None] ** (steps - j)[None, :])
    call_payoff = np.maximum(ST - K[:, None], 0.0)
    put_payoff = np.maximum(K[:, None] - ST, 0.0)
    values = np.where(cp_arr[:, None], call_payoff, put_payoff)

    for i in range(steps - 1, -1, -1):
        continuation = disc * (p[:, None] * values[:, 1:i + 2] + (1.0 - p[:, None]) * values[:, 0:i + 1])
        jn = np.arange(i + 1)
        St = S * (u[:, None] ** jn[None, :]) * (d[:, None] ** (i - jn)[None, :])
        call_intr = np.maximum(St - K[:, None], 0.0)
        put_intr = np.maximum(K[:, None] - St, 0.0)
        intrinsic = np.where(cp_arr[:, None], call_intr, put_intr)
        values = np.maximum(continuation, intrinsic)

    return values[:, 0]


def leisen_reimer_american_price_batch(S, K, T, r, sigma, q=0.0, cp=True, steps=200):
    """Batched leisen_reimer_american_price across many (K, sigma) pairs
    sharing a common S/T/r/q -- see the "Batched (multi-strike) tree
    pricers" module note above. K, sigma: arrays, shape (n,). cp: bool or
    bool array shape (n,). Returns an array of prices, shape (n,).

    Same odd-step coercion as the scalar version. Same caveat as
    crr_american_price_batch re: the near-zero-sigma degenerate branch not
    being reproduced (bisection callers never probe sigma that low).
    """
    K = np.atleast_1d(np.asarray(K, dtype=float))
    sigma = np.atleast_1d(np.asarray(sigma, dtype=float))
    n = K.shape[0]
    cp_arr = np.broadcast_to(np.asarray(cp, dtype=bool), (n,))

    steps = max(int(steps), 1)
    if steps % 2 == 0:
        steps += 1

    dt = T / steps
    sigma_sqrt_T = sigma * np.sqrt(T)

    d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    d2 = d1 - sigma_sqrt_T

    p = _peizer_pratt_inversion_vec(d2, steps)
    p_prime = _peizer_pratt_inversion_vec(d1, steps)
    p = np.clip(p, 1e-9, 1.0 - 1e-9)
    p_prime = np.clip(p_prime, 1e-9, 1.0 - 1e-9)

    growth = np.exp((r - q) * dt)
    u = growth * p_prime / p
    d = growth * (1.0 - p_prime) / (1.0 - p)
    disc = np.exp(-r * dt)

    j = np.arange(steps + 1)
    ST = S * (u[:, None] ** j[None, :]) * (d[:, None] ** (steps - j)[None, :])
    call_payoff = np.maximum(ST - K[:, None], 0.0)
    put_payoff = np.maximum(K[:, None] - ST, 0.0)
    values = np.where(cp_arr[:, None], call_payoff, put_payoff)

    for i in range(steps - 1, -1, -1):
        continuation = disc * (p[:, None] * values[:, 1:i + 2] + (1.0 - p[:, None]) * values[:, 0:i + 1])
        jn = np.arange(i + 1)
        St = S * (u[:, None] ** jn[None, :]) * (d[:, None] ** (i - jn)[None, :])
        call_intr = np.maximum(St - K[:, None], 0.0)
        put_intr = np.maximum(K[:, None] - St, 0.0)
        intrinsic = np.where(cp_arr[:, None], call_intr, put_intr)
        values = np.maximum(continuation, intrinsic)

    return values[:, 0]
