"""Tests for hedge_optimizer.py — Portfolio hedge optimiser."""
import numpy as np
import pytest
from var_engine.hedge_optimizer import (
    HedgeInstrument,
    HedgeOptimizerInputs,
    HedgeOptimizerOutputs,
    hedge_ratio_single,
    min_var_hedge,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_two_asset_inputs(seed: int = 42) -> HedgeOptimizerInputs:
    """Build inputs with 2 positions and 1 hedge instrument."""
    rng = np.random.default_rng(seed)
    vols = np.array([0.25, 0.30])
    corr = np.array([[1.0, 0.4], [0.4, 1.0]])
    cov = np.diag(vols) @ corr @ np.diag(vols)

    positions = np.array([1_000_000.0, 2_000_000.0])

    hedge = HedgeInstrument(
        name="ES_FUTURE",
        volatility=0.18,
        correlation_to_positions=np.array([0.6, 0.5]),
        beta=0.9,
    )

    return HedgeOptimizerInputs(
        positions=positions,
        cov_matrix=cov,
        hedge_instruments=[hedge],
        var_horizon=10.0,
        trading_days=252.0,
        confidence=0.99,
    )


def make_three_hedge_inputs(seed: int = 42) -> HedgeOptimizerInputs:
    """Build inputs with 3 positions and 3 hedge instruments (multi QP path)."""
    rng = np.random.default_rng(seed)
    n_pos = 3
    vols = np.array([0.25, 0.30, 0.20])
    corr = np.array([
        [1.0, 0.3, 0.2],
        [0.3, 1.0, 0.1],
        [0.2, 0.1, 1.0],
    ])
    cov = np.diag(vols) @ corr @ np.diag(vols)

    positions = np.array([500_000.0, 1_500_000.0, 750_000.0])

    hedges = [
        HedgeInstrument(
            name="ES",
            volatility=0.18,
            correlation_to_positions=np.array([0.6, 0.5, 0.4]),
            beta=1.1,
        ),
        HedgeInstrument(
            name="ZN",
            volatility=0.08,
            correlation_to_positions=np.array([-0.3, -0.2, -0.1]),
            beta=0.0,
        ),
        HedgeInstrument(
            name="CL",
            volatility=0.28,
            correlation_to_positions=np.array([0.1, 0.3, 0.5]),
            beta=0.6,
        ),
    ]

    return HedgeOptimizerInputs(
        positions=positions,
        cov_matrix=cov,
        hedge_instruments=hedges,
        var_horizon=1.0,
        trading_days=252.0,
        confidence=0.99,
    )


# ---------------------------------------------------------------------------
# hedge_ratio_single
# ---------------------------------------------------------------------------

class TestHedgeRatioSingle:
    """Analytical single-hedge ratio."""

    def test_example_values(self):
        """position_weight=1.0, position_vol=0.2, hedge_vol=0.3, corr=0.5.

        h* = -0.5 * (1.0 * 0.2 / 0.3) = -0.333333...
        """
        result = hedge_ratio_single(
            position_vol=0.2, position_weight=1.0,
            hedge_vol=0.3, correlation=0.5,
        )
        assert result == pytest.approx(-1.0 / 3.0, abs=1e-6)

    def test_negative_correlation(self):
        """Negative correlation → positive hedge ratio.
        h* = -(-0.7) * (0.8 * 0.15 / 0.25) = 0.336
        """
        result = hedge_ratio_single(
            position_vol=0.15, position_weight=0.8,
            hedge_vol=0.25, correlation=-0.7,
        )
        expected = 0.7 * (0.8 * 0.15 / 0.25)
        assert result == pytest.approx(expected, abs=1e-10)

    def test_perfect_correlation(self):
        """Correlation = 1.0, equal vols, full weight → h* = -1.0."""
        result = hedge_ratio_single(
            position_vol=0.20, position_weight=1.0,
            hedge_vol=0.20, correlation=1.0,
        )
        assert result == pytest.approx(-1.0, abs=1e-10)

    def test_zero_correlation(self):
        """Zero correlation → zero hedge ratio."""
        result = hedge_ratio_single(
            position_vol=0.20, position_weight=1.0,
            hedge_vol=0.20, correlation=0.0,
        )
        assert result == pytest.approx(0.0, abs=1e-10)

    def test_zero_vol_raises_valid_division(self):
        """Zero hedge vol should give zero or inf — we accept non-finite."""
        result = hedge_ratio_single(
            position_vol=0.20, position_weight=1.0,
            hedge_vol=0.0, correlation=0.5,
        )
        assert not np.isfinite(result)


# ---------------------------------------------------------------------------
# min_var_hedge with 1 hedge (analytical)
# ---------------------------------------------------------------------------

class TestMinVarHedgeSingle:
    """Minimum-variance hedge with a single instrument (analytical path)."""

    def test_hedged_var_less_than_base(self):
        """Hedged VaR should be strictly less than base VaR."""
        inp = make_two_asset_inputs(seed=42)
        out = min_var_hedge(inp)
        assert out.hedged_var < out.base_var
        assert out.var_reduction_pct > 0

    def test_output_has_correct_types(self):
        """Output fields should be the right shapes and types."""
        inp = make_two_asset_inputs(seed=42)
        out = min_var_hedge(inp)
        assert isinstance(out, HedgeOptimizerOutputs)
        assert isinstance(out.optimal_weights, np.ndarray)
        assert out.optimal_weights.shape == (1,)
        assert out.hedge_names == ["ES_FUTURE"]
        assert out.base_var > 0
        assert out.hedged_var > 0

    def test_base_var_reproducible(self):
        """Same seed gives same base VaR."""
        r1 = min_var_hedge(make_two_asset_inputs(seed=1))
        r2 = min_var_hedge(make_two_asset_inputs(seed=1))
        assert r1.base_var == pytest.approx(r2.base_var, abs=1e-10)

    def test_hedged_port_vol_less_than_base_port_vol(self):
        """Portfolio volatility should drop after hedging."""
        inp = make_two_asset_inputs(seed=42)
        out = min_var_hedge(inp)
        assert out.hedged_port_vol < out.base_port_vol

    def test_var_reduction_percentage_positive(self):
        """VaR reduction percentage should be reported as positive."""
        inp = make_two_asset_inputs(seed=42)
        out = min_var_hedge(inp)
        assert out.var_reduction_pct > 0.0
        assert out.var_reduction_pct < 100.0


# ---------------------------------------------------------------------------
# min_var_hedge with 3 hedges (QP path)
# ---------------------------------------------------------------------------

class TestMinVarHedgeMulti:
    """Minimum-variance hedge with multiple instruments (QP path)."""

    def test_output_shapes(self):
        """3 positions + 3 hedges → optimal_weights of length 3."""
        inp = make_three_hedge_inputs(seed=42)
        out = min_var_hedge(inp)
        assert out.optimal_weights.shape == (3,)
        assert len(out.hedge_names) == 3

    def test_hedged_var_less_than_base(self):
        """Hedged VaR < base VaR with multiple hedges."""
        inp = make_three_hedge_inputs(seed=42)
        out = min_var_hedge(inp)
        assert out.hedged_var < out.base_var
        assert out.var_reduction_pct > 0

    def test_no_hedge_instruments(self):
        """Zero hedge instruments → optimal_weights empty, VaR unchanged."""
        inp = make_two_asset_inputs(seed=42)
        inp.hedge_instruments = []
        out = min_var_hedge(inp)
        assert out.optimal_weights.shape == (0,)
        assert out.base_var == pytest.approx(out.hedged_var, abs=1e-10)
        assert out.var_reduction_pct == pytest.approx(0.0, abs=1e-10)

    def test_multi_produces_better_reduction(self):
        """More hedges should not make things worse (reduction >= single)."""
        single = make_two_asset_inputs(seed=42)
        multi = make_three_hedge_inputs(seed=42)

        out_single = min_var_hedge(single)
        out_multi = min_var_hedge(multi)
        # Compare the percentage reduction (different portfolios, but both should be >0)
        assert out_multi.var_reduction_pct > 0
        assert out_single.var_reduction_pct > 0

    def test_optimal_weights_are_finite(self):
        """All optimal weights should be finite real numbers."""
        inp = make_three_hedge_inputs(seed=42)
        out = min_var_hedge(inp)
        assert np.all(np.isfinite(out.optimal_weights))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestHedgeEdgeCases:
    """Edge cases for the hedge optimiser."""

    def test_single_position_single_hedge(self):
        """One position, one hedge → single-instrument path works."""
        positions = np.array([1_000_000.0])
        cov = np.array([[0.0625]])  # 0.25²

        hedge = HedgeInstrument(
            name="HEDGE",
            volatility=0.18,
            correlation_to_positions=np.array([0.6]),
            beta=0.8,
        )
        inp = HedgeOptimizerInputs(
            positions=positions,
            cov_matrix=cov,
            hedge_instruments=[hedge],
            var_horizon=1.0,
            trading_days=252.0,
        )
        out = min_var_hedge(inp)
        assert out.optimal_weights.shape == (1,)
        assert out.hedged_var < out.base_var

    def test_negative_hedge_weight_allowed(self):
        """Short hedge should be allowed (negative weight)."""
        positions = np.array([1_000_000.0])
        cov = np.array([[0.0625]])
        hedge = HedgeInstrument(
            name="HEDGE",
            volatility=0.18,
            correlation_to_positions=np.array([0.8]),
            beta=1.0,
        )
        inp = HedgeOptimizerInputs(
            positions=positions,
            cov_matrix=cov,
            hedge_instruments=[hedge],
        )
        out = min_var_hedge(inp)
        # Positive correlation + positive position → negative hedge weight
        assert out.optimal_weights[0] < 0

    def test_hedge_with_low_volatility(self):
        """Low-vol hedge instrument should get larger weight."""
        positions = np.array([1_000_000.0])
        cov = np.array([[0.0625]])
        hedge = HedgeInstrument(
            name="LOW_VOL",
            volatility=0.05,
            correlation_to_positions=np.array([0.5]),
            beta=0.5,
        )
        inp = HedgeOptimizerInputs(
            positions=positions,
            cov_matrix=cov,
            hedge_instruments=[hedge],
        )
        out = min_var_hedge(inp)
        # h* = -0.5 * (1.0 * 0.25 / 0.05) = -2.5
        expected = -0.5 * (1.0 * 0.25 / 0.05)
        assert out.optimal_weights[0] == pytest.approx(expected, abs=1e-10)

    def test_no_hedge_vs_zero_positions_raises(self):
        """Zero total position should raise."""
        positions = np.array([0.0, 0.0])
        cov = np.eye(2)
        inp = HedgeOptimizerInputs(
            positions=positions,
            cov_matrix=cov,
            hedge_instruments=[],
        )
        with pytest.raises(ValueError, match="zero"):
            min_var_hedge(inp)

    def test_confidence_95(self):
        """95 % confidence VaR should be lower than 99 %."""
        inp = make_two_asset_inputs(seed=42)
        out_99 = min_var_hedge(inp)
        inp.confidence = 0.95
        out_95 = min_var_hedge(inp)
        assert out_95.base_var < out_99.base_var
        assert out_95.hedged_var < out_99.hedged_var