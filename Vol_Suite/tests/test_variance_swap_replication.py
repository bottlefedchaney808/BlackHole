"""
Synthetic correctness test for the Demeterfi/DDKZ (1999) variance-swap static
replication math -- the core arithmetic behind Layer 1b of the dealer
positioning v2 design (see ../DEALER_POSITIONING_V2_DESIGN.md).

This is deliberately network-free and ticker-free: it reproduces the paper's
own Figure 3 (Appendix A discrete recursion -> per-strike weights -> "variance
vega" surface d(Price)/d(sigma^2), evaluated across three strike-chain shapes)
under a flat-vol Black-Scholes world where the "right answer" is known in
closed form. If this fails, the bug is in our replication math, full stop --
nothing about ThetaData, real chains, or live tickers is involved yet.

Three qualitative claims from the paper are checked, one per chain shape:

  (a) continuum (fine, wide chain proxy): the variance-vega surface should
      collapse to the closed-form V(tau) = tau/T, flat across spot.
  (b) narrow/dense chain ($75-125 x $1): range truncation -- the surface
      sags away from the continuum ideal near the edges of the strike range,
      and that sag is LARGER in absolute terms far from expiry (more
      remaining time = more room for spot to drift into the untruncated
      region, and the ideal target itself is larger far from expiry).
  (c) wide/sparse chain ($20-200 x $10): spacing corrugation -- ripples
      appear between strikes because each option's own vega is localized
      near its strike. The ABSOLUTE ripple size shrinks near expiry (overall
      vega scale shrinks toward zero), but the RELATIVE ripple (ripple size
      divided by the local average level) grows sharply near expiry -- this
      is what the paper means by corrugations "growing more pronounced
      closer to expiration." Note this is the OPPOSITE tau-directionality
      from (b) in absolute terms; the two failure modes are genuinely
      distinct and must each be measured the way they actually manifest.

Run with: pytest tests/test_variance_swap_replication.py -v
"""
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Core replication math (Demeterfi, Derman, Kamal, Zou 1999, Appendix A)
# ---------------------------------------------------------------------------

def _norm_pdf(x):
    return np.exp(-0.5 * x**2) / np.sqrt(2.0 * np.pi)


def _f_payoff(ST, Sstar, T):
    """The log-payoff replication target: (2/T) * [(ST-S*)/S* - ln(ST/S*)]."""
    return (2.0 / T) * ((ST - Sstar) / Sstar - np.log(ST / Sstar))


def _build_weights(K_list, Sstar, T, side):
    """Appendix A discrete recursion for one side (calls or puts) of the chain.

    Every weight is guaranteed >= 0 because f(ST) is convex with its minimum
    (exactly 0) at Sstar -- the replicating strip is long-only by
    construction. That non-negativity is itself checked below as a
    convexity/superhedge sanity property, independent of the three
    Figure-3 checks.
    """
    nodes = [Sstar] + list(K_list)
    fvals = [_f_payoff(k, Sstar, T) for k in nodes]
    strikes, weights = [], []
    cum = 0.0
    for i in range(1, len(nodes)):
        Ki, Kim1 = nodes[i], nodes[i - 1]
        denom = (Ki - Kim1) if side == 'call' else (Kim1 - Ki)
        slope = (fvals[i] - fvals[i - 1]) / denom
        w = slope - cum
        strikes.append(Ki)
        weights.append(w)
        cum += w
    return np.array(strikes), np.array(weights)


def _chain(strikes, Sstar, T):
    strikes = np.asarray(strikes, dtype=float)
    calls = np.sort(strikes[strikes > Sstar])
    puts = np.sort(strikes[strikes < Sstar])[::-1]
    Kc, Wc = _build_weights(calls, Sstar, T, 'call')
    Kp, Wp = _build_weights(puts, Sstar, T, 'put')
    return np.concatenate([Kc, Kp]), np.concatenate([Wc, Wp])


def _variance_vega_surface(K, W, S_grid, tau_grid, r, q, sigma):
    """d(Price)/d(sigma^2) for the weighted strip, across a (S, tau) grid.

    NOTE: this is the "variance vega," not standard Black-Scholes vega
    d(Price)/d(sigma). They differ by the chain-rule factor 1/(2*sigma) --
    getting this factor wrong is exactly the bug that made panel (a)
    mismatch the closed-form ideal by ~30% during development; with it,
    panel (a) matches to ~2%.
    """
    S = S_grid[:, None, None]
    tau = tau_grid[None, :, None]
    Kk = K[None, None, :]
    with np.errstate(divide='ignore', invalid='ignore'):
        d1 = (np.log(S / Kk) + (r - q + 0.5 * sigma**2) * tau) / (sigma * np.sqrt(tau))
        vega = S * np.exp(-q * tau) * _norm_pdf(d1) * np.sqrt(tau)
    vega = np.nan_to_num(vega, nan=0.0, posinf=0.0, neginf=0.0)
    bs_vega_sum = np.tensordot(vega, W, axes=([2], [0]))
    return bs_vega_sum / (2.0 * sigma)


# ---------------------------------------------------------------------------
# Fixed test market: flat vol, no rates/div, 3-month variance swap
# ---------------------------------------------------------------------------

SSTAR = 100.0
TENOR_T = 0.25
R, Q, SIGMA = 0.0, 0.0, 0.20

S_GRID = np.linspace(60, 145, 60)
TAU_GRID = np.linspace(0.005, TENOR_T, 40)


@pytest.fixture(scope="module")
def continuum_chain():
    """(a) Fine, wide chain -- proxy for the theoretical continuum."""
    return _chain(np.arange(5, 500, 1.0), SSTAR, TENOR_T)


@pytest.fixture(scope="module")
def narrow_dense_chain():
    """(b) $75-125 in $1 steps -- narrow range, fine spacing."""
    return _chain(np.arange(75, 126, 1.0), SSTAR, TENOR_T)


@pytest.fixture(scope="module")
def wide_sparse_chain():
    """(c) $20-200 in $10 steps -- wide range, coarse spacing."""
    return _chain(np.arange(20, 201, 10.0), SSTAR, TENOR_T)


@pytest.fixture(scope="module")
def continuum_surface(continuum_chain):
    K, W = continuum_chain
    return _variance_vega_surface(K, W, S_GRID, TAU_GRID, R, Q, SIGMA)


@pytest.fixture(scope="module")
def narrow_dense_surface(narrow_dense_chain):
    K, W = narrow_dense_chain
    return _variance_vega_surface(K, W, S_GRID, TAU_GRID, R, Q, SIGMA)


@pytest.fixture(scope="module")
def wide_sparse_surface(wide_sparse_chain):
    K, W = wide_sparse_chain
    return _variance_vega_surface(K, W, S_GRID, TAU_GRID, R, Q, SIGMA)


# ---------------------------------------------------------------------------
# Convexity / superhedge property -- independent of chain shape
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("chain_fixture", [
    "continuum_chain", "narrow_dense_chain", "wide_sparse_chain",
])
def test_replication_weights_are_nonnegative(chain_fixture, request):
    """Every option weight in the recursion must be >= 0 (long-only strip).

    This is a structural property of f(ST) being convex with minimum 0 at
    S*, true for ANY strike chain -- if this ever fails it means the
    recursion itself is broken, not a chain-shape artifact.
    """
    _, W = request.getfixturevalue(chain_fixture)
    assert np.all(W > -1e-9), f"found negative weight(s): min={W.min():.6f}"


# ---------------------------------------------------------------------------
# (a) Continuum matches the closed-form ideal V(tau) = tau / T, flat across S
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_continuum_matches_closed_form_ideal(continuum_surface):
    ideal = TAU_GRID / TENOR_T
    mean_over_S = np.mean(continuum_surface, axis=0)
    mean_abs_error = np.mean(np.abs(mean_over_S - ideal))
    assert mean_abs_error < 0.03, f"mean abs error vs tau/T = {mean_abs_error:.4f}"


@pytest.mark.unit
def test_continuum_is_flat_across_spot(continuum_surface):
    flatness = np.max(np.std(continuum_surface, axis=0))
    assert flatness < 0.05, f"max std-across-S = {flatness:.4f} (should be ~flat)"


# ---------------------------------------------------------------------------
# (b) Narrow/dense chain: range-truncation sag, larger far from expiry
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_range_truncation_sag_worse_far_from_expiry(narrow_dense_surface):
    center_idx = np.argmin(np.abs(S_GRID - 100))
    edge_idx = np.argmin(np.abs(S_GRID - 78))
    sag_far = abs(narrow_dense_surface[center_idx, -1] - narrow_dense_surface[edge_idx, -1])
    sag_near = abs(narrow_dense_surface[center_idx, 2] - narrow_dense_surface[edge_idx, 2])
    assert sag_far > sag_near, (
        f"expected far-from-expiry sag ({sag_far:.4f}) > near-expiry sag "
        f"({sag_near:.4f}) -- paper: deviation is greater at earlier times "
        f"(i.e. far from expiry, more remaining time to breach the range)"
    )


# ---------------------------------------------------------------------------
# (c) Wide/sparse chain: spacing corrugation, worse near expiry -- RELATIVE
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_spacing_corrugation_worse_near_expiry_relative(wide_sparse_surface):
    mask = (S_GRID > 85) & (S_GRID < 115)

    def relative_corrugation(tau_idx):
        v = wide_sparse_surface[mask, tau_idx]
        return np.std(np.diff(v)) / (np.mean(np.abs(v)) + 1e-9)

    corrug_far = relative_corrugation(-1)
    corrug_near = relative_corrugation(2)
    assert corrug_near > corrug_far, (
        f"expected near-expiry relative corrugation ({corrug_near:.4f}) > "
        f"far-from-expiry ({corrug_far:.4f}) -- paper: corrugations grow "
        f"more pronounced closer to expiration (true only in RELATIVE terms; "
        f"the absolute ripple actually shrinks near expiry as the overall "
        f"vega scale shrinks -- do not use an absolute std-of-diff metric here)"
    )


@pytest.mark.unit
def test_corrugation_absolute_and_relative_directions_are_opposite(wide_sparse_surface):
    """Documents the counterintuitive finding directly: the same surface
    gives opposite verdicts depending on whether the ripple is measured in
    absolute or relative terms. This guards against silently "fixing" the
    metric back to an absolute one in a future refactor.
    """
    mask = (S_GRID > 85) & (S_GRID < 115)
    v_far = wide_sparse_surface[mask, -1]
    v_near = wide_sparse_surface[mask, 2]

    abs_far = np.std(np.diff(v_far))
    abs_near = np.std(np.diff(v_near))
    rel_far = abs_far / (np.mean(np.abs(v_far)) + 1e-9)
    rel_near = abs_near / (np.mean(np.abs(v_near)) + 1e-9)

    assert abs_near < abs_far, "absolute ripple should shrink near expiry"
    assert rel_near > rel_far, "relative ripple should grow near expiry"
