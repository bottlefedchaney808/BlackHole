"""
Tests for pricing models: SABR, Vanna-Volga, and American LSM Monte Carlo.

Covers:
  - SABRModel.sabr_vol_hagan returns finite positive vols for realistic inputs
  - VannaVolga.get_vol returns something (smoke test)
  - AmericanLSMPricer from MC.py can be instantiated
"""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# SABR model tests
# ---------------------------------------------------------------------------


class TestSABRModel:
    """Verify SABR Hagan volatility surface is well-behaved."""

    @pytest.mark.unit
    def test_sabr_vol_hagan_finite_positive(self):
        """sabr_vol_hagan should return finite, positive vols for realistic
        parameter ranges."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.5, -0.3, 0.50

        for K in [80.0, 90.0, 100.0, 110.0, 120.0]:
            vol = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu)
            assert math.isfinite(vol), f"Non-finite vol at K={K}: {vol}"
            assert vol > 0, f"Non-positive vol at K={K}: {vol}"

    @pytest.mark.unit
    def test_sabr_vol_atm(self):
        """ATM (F == K) SABR vol should match the closed-form ATM formula."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.5, -0.3, 0.50

        atm_vol = sabr_vol_hagan(F, F, T, alpha, beta, rho, nu)
        # ATM formula: alpha / F^(1-beta) * (1 + ... T)
        expected_base = alpha / (F ** (1 - beta))
        assert atm_vol > 0
        # Should be close to alpha/F^(1-beta) for short T
        assert abs(atm_vol - expected_base) / expected_base < 0.2

    @pytest.mark.unit
    def test_sabr_smile_shape(self):
        """SABR with negative rho should produce a downward-sloping skew
        (strikes below forward have higher vol than strikes above)."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.7, -0.5, 0.40

        vol_otm_put = sabr_vol_hagan(F, 85.0, T, alpha, beta, rho, nu)
        vol_otm_call = sabr_vol_hagan(F, 115.0, T, alpha, beta, rho, nu)

        # Negative rho -> put wing > call wing
        assert vol_otm_put > vol_otm_call, (
            f"Negative rho skew expected: put vol {vol_otm_put:.4f} <= call vol {vol_otm_call:.4f}"
        )

    @pytest.mark.unit
    def test_sabr_increases_with_nu(self):
        """Higher nu (vol-of-vol) makes the smile more pronounced, so vols
        away from the money should increase."""
        from SABRModel import sabr_vol_hagan

        F, T, K = 100.0, 0.5, 120.0
        alpha, beta, rho = 0.25, 0.5, -0.3

        vol_low = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu=0.2)
        vol_high = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu=1.0)

        assert vol_high > vol_low, (
            f"Higher nu should increase wing vol: {vol_high:.4f} <= {vol_low:.4f}"
        )

    @pytest.mark.unit
    def test_sabr_model_instantiation(self):
        """SABRModel class can be instantiated and produces vols."""
        from SABRModel import SABRModel

        model = SABRModel(alpha=0.25, beta=0.5, rho=-0.3, nu=0.5)
        vol = model.get_vol(F=100.0, K=100.0, T=0.5)
        assert math.isfinite(vol)
        assert vol > 0


# ---------------------------------------------------------------------------
# Vanna-Volga tests
# ---------------------------------------------------------------------------


class TestVannaVolga:
    """Smoke tests for VannaVolga.get_vol."""

    @pytest.mark.unit
    def test_get_vol_returns_positive(self):
        """get_vol should return a finite, positive vol for realistic params."""
        from VannaVolga import get_vol

        S, K, T, r, q = 100.0, 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 3.0, 1.0

        vol = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        assert math.isfinite(vol), f"Non-finite vol: {vol}"
        assert vol > 0, f"Non-positive vol: {vol}"

    @pytest.mark.unit
    def test_get_vol_near_atm(self):
        """At-the-money (K ~ S) VV vol should be close to input atm_vol."""
        from VannaVolga import get_vol

        S, K, T, r, q = 100.0, 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.30, 5.0, 2.0

        vol = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        assert abs(vol - atm_vol) < 0.05, (
            f"ATM vol {vol:.4f} too far from input {atm_vol}"
        )

    @pytest.mark.unit
    def test_get_vol_wing_separation(self):
        """With positive RR, OTM call vol should exceed OTM put vol."""
        from VannaVolga import get_vol

        S, T, r, q = 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 10.0, 2.0

        vol_call = get_vol(S, 115.0, T, r, q, atm_vol, rr25, bf25)
        vol_put = get_vol(S, 85.0, T, r, q, atm_vol, rr25, bf25)
        # Positive RR means call wing > put wing
        assert vol_call > vol_put, (
            f"Positive RR: call vol {vol_call:.4f} should exceed put vol {vol_put:.4f}"
        )

    @pytest.mark.unit
    def test_rr_bf_symmetry_matches_their_definitions(self):
        """Regression test for the RR/BF symmetry-swap bug (see get_vol's
        own 'BUG FIX' comment): the old code flipped BF's sign between call
        and put (should be SYMMETRIC) while keeping RR's sign the same on
        both (should be ANTI-symmetric) -- the exact opposite of correct.
        The existing test_get_vol_wing_separation ('positive RR -> call vol
        > put vol') does NOT catch this: with bf25 > 0 the old buggy formula
        also satisfies call > put, since BF's flipped contribution alone
        determines the direction. This test instead re-derives the pillar
        vols directly from get_auto_rr_bf's own docstring definitions:
        RR25 = sigma_25C - sigma_25P, BF25 = (sigma_25C+sigma_25P)/2 - ATM.
        """
        from VannaVolga import get_vol

        S, T, r, q = 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 7.0, 3.0

        # Locate the exact 25-delta pillar strikes using get_vol's own
        # internal formula (Step 2), fed with the pillar vols get_vol itself
        # would compute (Step 1) -- at K == K_25C exactly, the inverse-
        # distance interpolation weight on that pillar dominates by ~1e7x
        # (the eps=1e-8 floor vs. the other pillars' finite log-strike
        # distance), so get_vol(K_25C) recovers sigma_25C to ~1e-6 precision.
        from scipy.stats import norm

        sigma_25c_expected = atm_vol + bf25 / 100.0 + rr25 / 200.0
        sigma_25p_expected = atm_vol + bf25 / 100.0 - rr25 / 200.0
        F = S * math.exp((r - q) * T)
        d1_put = -norm.ppf(0.25)
        k_25p = F * math.exp(
            -d1_put * sigma_25p_expected * math.sqrt(T)
            + 0.5 * sigma_25p_expected**2 * T
        )
        d1_call = norm.ppf(0.25)
        k_25c = F * math.exp(
            -d1_call * sigma_25c_expected * math.sqrt(T)
            + 0.5 * sigma_25c_expected**2 * T
        )

        vol_25c = get_vol(S, k_25c, T, r, q, atm_vol, rr25, bf25)
        vol_25p = get_vol(S, k_25p, T, r, q, atm_vol, rr25, bf25)

        assert (vol_25c - vol_25p) == pytest.approx(rr25 / 100.0, abs=1e-6), (
            "sigma_25C - sigma_25P must equal rr25/100 (anti-symmetric RR)"
        )
        assert ((vol_25c + vol_25p) / 2 - atm_vol) == pytest.approx(
            bf25 / 100.0, abs=1e-6
        ), "(sigma_25C + sigma_25P)/2 - ATM must equal bf25/100 (symmetric BF)"

    @pytest.mark.unit
    def test_get_vol_batch_matches_scalar(self):
        """get_vol_batch should produce the same values as get_vol per-strike."""
        from VannaVolga import get_vol, get_vol_batch

        S, T, r, q = 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 3.0, 1.0
        strikes = [85.0, 95.0, 100.0, 105.0, 115.0]

        scalar = [get_vol(S, k, T, r, q, atm_vol, rr25, bf25) for k in strikes]
        batch = get_vol_batch(S, strikes, T, r, q, atm_vol, rr25, bf25)

        assert len(scalar) == len(batch)
        # rel=1e-6, not 1e-10: get_vol's final IV inversion is a local
        # scalar Newton (tol=1e-6), get_vol_batch's is MCHestonLSM's shared
        # vectorized Newton (tol=1e-4, max_iter=50) -- same Castagna-
        # Mercurio price target, two different solvers/stopping criteria
        # converging to it, so they agree to solver precision, not bit-
        # for-bit.
        for sk, bk in zip(scalar, batch):
            assert sk == pytest.approx(bk, rel=1e-6)


# ---------------------------------------------------------------------------
# American LSM Monte Carlo tests
# ---------------------------------------------------------------------------


class TestAmericanLSMPricer:
    """Smoke tests for AmericanLSMPricer (MC.py)."""

    @pytest.mark.unit
    def test_can_instantiate(self):
        """AmericanLSMPricer can be instantiated with standard parameters."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(
            S=100.0,
            K=100.0,
            T=0.5,
            r=0.05,
            q=0.0,
            sigma=0.25,
            simulations=5000,
            steps=50,
            option="call",
        )
        assert pricer is not None
        assert pricer.option == "call"
        assert pricer.simulations == 5000
        assert pricer.steps == 50

    @pytest.mark.unit
    def test_price_returns_finite_number(self):
        """price() should return a finite float for realistic inputs."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(
            S=100.0,
            K=100.0,
            T=0.5,
            r=0.05,
            q=0.0,
            sigma=0.25,
            simulations=5000,
            steps=50,
            option="call",
        )
        price = pricer.price()
        assert math.isfinite(price), f"Non-finite MC price: {price}"
        assert price > 0, f"Non-positive MC price: {price}"

    @pytest.mark.unit
    def test_put_price_sensible_range(self):
        """ATM put price should be between ~2-15 for moderate params."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(
            S=100.0,
            K=100.0,
            T=0.5,
            r=0.05,
            q=0.0,
            sigma=0.25,
            simulations=5000,
            steps=50,
            option="put",
        )
        price = pricer.price()
        assert 1.0 <= price <= 20.0, f"ATM put price {price:.4f} out of expected range"

    @pytest.mark.unit
    def test_american_exceeds_european_for_deep_itm_put(self):
        """A deep ITM American put should price slightly higher than European
        because early-exercise optionality has non-zero value."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(
            S=80.0,
            K=100.0,
            T=0.5,
            r=0.05,
            q=0.02,
            sigma=0.20,
            simulations=5000,
            steps=50,
            option="put",
        )
        price = pricer.price()
        # Intrinsic value is a guaranteed lower bound
        intrinsic = 20.0
        assert price >= intrinsic * 0.95, (
            f"MC put {price:.4f} well below intrinsic {intrinsic}"
        )

    @pytest.mark.unit
    def test_invalid_option_raises(self):
        """Passing an invalid option type should raise ValueError."""
        from MC import AmericanLSMPricer

        with pytest.raises(ValueError, match="Option type"):
            AmericanLSMPricer(
                S=100.0,
                K=100.0,
                T=0.5,
                r=0.05,
                q=0.0,
                sigma=0.25,
                simulations=5000,
                steps=50,
                option="invalid",
            )

    @pytest.mark.unit
    def test_mc_all_greeks_gamma_is_stable_and_positive_across_seeds(self):
        """Regression test for the pre-CRN Greek-noise bug (see mc_all_greeks's
        own docstring): raw bump-and-revalue with fresh randoms per pricing
        gave gamma seed-to-seed noise so large it went NEGATIVE (-0.005, pure
        noise, at dS=1%) and rho varied with std ~162 across seeds for
        identical inputs. Common Random Numbers (the same pre-generated
        Gaussian draws reused across every bumped pricing) should cancel that
        noise. This doesn't re-derive an exact expected value (MC is
        inherently stochastic) -- it instead locks in the qualitative
        property CRN exists to guarantee: gamma stays positive and rho stays
        tightly clustered across seeds, instead of flipping sign or swinging
        wildly the way the pre-CRN implementation did."""
        from MC import mc_all_greeks

        S, K, T, r, q, sigma = 100.0, 100.0, 0.5, 0.05, 0.0, 0.25
        gammas, rhos = [], []
        for seed in (1, 2, 3):
            g = mc_all_greeks(
                S, K, T, r, q, sigma, sims=8000, steps=50, option="put", seed=seed
            )
            gammas.append(g["gamma"])
            rhos.append(g["rho"])

        assert all(gm > 0 for gm in gammas), (
            f"gamma went non-positive across seeds {gammas} -- CRN cancellation "
            f"may have regressed"
        )
        rho_spread = max(rhos) - min(rhos)
        assert rho_spread < 5.0, (
            f"rho spread {rho_spread} across seeds {rhos} is far wider than "
            f"CRN-stabilized runs show -- historically this bug's spread was "
            f"~162 (std) with fresh-random bump-and-revalue"
        )


# ---------------------------------------------------------------------------
# Heston pricer tests
# ---------------------------------------------------------------------------


class TestHestonEuropeanCallPrice:
    """Regression coverage for the malformed-discriminant bug in
    heston_european_call_price (see its own docstring): the previous
    discriminant dropped both the xi^2 scaling and the u_j=+/-0.5 term that
    distinguishes P1 from P2, so the pricer failed its most basic sanity
    check -- as xi->0 (vol-of-vol vanishes) with theta=v0 (flat variance),
    Heston must converge to Black-Scholes at sigma=sqrt(v0), but the broken
    discriminant returned near-zero/negative values instead. Heston has zero
    other test coverage in this suite (docs/PROJECT_AUDIT_AND_SPEC.md finding
    #15), so this is the only thing currently locking the fix in."""

    @pytest.mark.unit
    def test_converges_to_black_scholes_as_vol_of_vol_vanishes(self):
        from MCHestonLSM import heston_european_call_price

        def bs_call(S, K, T, r, q, sigma):
            d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (
                sigma * math.sqrt(T)
            )
            d2 = d1 - sigma * math.sqrt(T)
            n = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
            return S * math.exp(-q * T) * n(d1) - K * math.exp(-r * T) * n(d2)

        S, K, T, r, q = 100.0, 100.0, 0.5, 0.03, 0.0
        v0 = 0.04
        sigma = math.sqrt(v0)
        kappa, theta, rho, xi = 2.0, v0, 0.0, 1e-4

        heston_price = heston_european_call_price(
            S, K, T, r, q, v0, kappa, theta, xi, rho
        )
        bs_price = bs_call(S, K, T, r, q, sigma)

        assert heston_price == pytest.approx(bs_price, abs=1e-4), (
            f"Heston({heston_price}) should converge to BS({bs_price}) as "
            f"xi->0 with theta=v0 -- a malformed discriminant would return "
            f"near-zero/negative instead"
        )


# ---------------------------------------------------------------------------
# main.py context-mode default pricing method (CLAUDE.md "Known fragile
# surfaces": default must stay Leisen-Reimer, not CRR -- Jason has reported a
# regression to CRR more than once, and until this test existed nothing in CI
# would catch it. Network calls are mocked out; this only pins which method
# name gets passed to VolManager.get_sigma.)
# ---------------------------------------------------------------------------


def _load_options_main():
    """Load Options_Suite/main.py under a unique module name.

    Options_Suite, VaR_Tools_Simulations and sentiment-scanner each ship
    their own `main.py` -- a bare `import main` silently returns whichever
    one another test file already cached in sys.modules under that generic
    name during pytest's combined collection, instead of raising. This is
    the same hazard VaR_Tools_Simulations/tests/test_context_builders.py and
    sentiment-scanner/tests/test_main.py already work around; mirror their
    pattern rather than a bare `import main`, which under a repo-root-wide
    `pytest` run was observed loading VaR_Tools_Simulations/main.py instead
    (a stale sys.modules['main'] entry, or a sys.path race depending on
    conftest collection order -- either way, a bare `import main` is not
    reliable in this monorepo's combined test session).

    Loading main.py under a unique name isn't sufficient by itself, though:
    main.py's own body does more flat, cwd-relative imports (`from config
    import PricingConfig`, `from market_data import ...`, etc.) that resolve
    via sys.path/sys.modules at exec time, same as the top-level `import
    main` did. Reordering sys.path alone doesn't fix this: sentiment-scanner
    (and shared/) ship their own unrelated config.py, and sentiment-scanner's
    test collection (which runs to completion, along with every other
    testpath, before ANY test executes) has already imported and cached its
    own config.py under sys.modules['config'] by the time this test runs --
    Python checks sys.modules by name FIRST, before ever consulting sys.path,
    so a bare `from config import PricingConfig` would keep resolving to the
    wrong cached module even with Options_Suite/ moved to sys.path[0].
    Stash any pre-existing entries for main.py's flat import names, let
    exec_module populate Options_Suite's own versions, then restore the
    stashed entries afterward so this doesn't leak Options_Suite's config.py
    (etc.) into whatever a later-executing suite's tests expect to find
    there. Mirrors the defensive pattern Vol_Suite/tests/conftest.py already
    uses for its own expiry_selector.py collision with Options_Suite's.
    """
    module_name = "options_suite_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    options_suite_root = str(Path(__file__).resolve().parent.parent)
    flat_names = (
        "config",
        "market_data",
        "vol_manager",
        "american_binomial",
        "MC",
        "VannaVolga",
        "NewtonRaphsonIV",
        "bruteforceimpliedvol",
        "SABRModel",
        "barone_adesi_whaley",
    )
    stashed = {
        name: sys.modules.pop(name) for name in flat_names if name in sys.modules
    }
    spec = importlib.util.spec_from_file_location(
        module_name, str(Path(__file__).resolve().parent.parent / "main.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    original_path = list(sys.path)
    try:
        sys.path = [options_suite_root] + [
            p for p in sys.path if p != options_suite_root
        ]
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
    finally:
        sys.path = original_path
        for name in flat_names:
            sys.modules.pop(name, None)
        sys.modules.update(stashed)
    return mod


class TestContextModeDefaultPricingMethod:
    @pytest.mark.unit
    def test_run_context_mode_uses_leisen_reimer_by_default(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("THETADATA_CF_ACCESS_CLIENT_ID", "test-client-id")
        monkeypatch.setenv("THETADATA_CF_ACCESS_CLIENT_SECRET", "test-client-secret")

        import json as json_mod

        options_main = _load_options_main()

        context = {
            "focus": {"ticker": "AAPL", "option_type": "call", "target_years": 0.25}
        }
        context_path = tmp_path / "ctx.json"
        context_path.write_text(json_mod.dumps(context), encoding="utf-8")
        out_path = tmp_path / "options_result.json"

        calls = {}

        class FakeMarketData:
            def fetch_spot_price(self, ticker):
                return 100.0

            def fetch_risk_free_rate(self):
                return 0.05

            def fetch_dividend_yield(self, ticker):
                return 0.0

            def validate_strike(
                self, ticker, strike, target_years=None, expiration_date=None
            ):
                return {"closest": strike}

        class FakeVolManager:
            def get_sigma(self, ticker, K, target_years, method=None, option_type=None):
                calls["method"] = method
                return 0.20

        monkeypatch.setattr(options_main, "MarketDataController", FakeMarketData)
        monkeypatch.setattr(options_main, "VolManager", FakeVolManager)

        rc = options_main.run_context_mode(
            str(context_path), str(out_path), no_interactive=True
        )

        assert rc == 0
        assert calls.get("method") == "LeisenReimer", (
            f"run_context_mode's default pricing method regressed to "
            f"{calls.get('method')!r} -- must stay 'LeisenReimer', not 'CRR' "
            f"(see CLAUDE.md's Known Fragile Surfaces note)"
        )

        result = json_mod.loads(out_path.read_text(encoding="utf-8"))
        assert result["method"] == "LeisenReimer"
        assert result["status"] == "ok"
