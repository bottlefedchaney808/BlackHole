"""Vol_Suite's side of the context that VaR and the Models tab read back.

Two things are pinned here, both of which were silently broken:

1. `garch` publishes its conditional vol under the key VaR actually reads.
   It wrote `garch_vol`; VaR's `_resolve_vol` reads
   `garch_conditional_vol` out of the Context Store. With the two names
   never meeting, every VaR run fell through to a flat 0.25 no matter how
   many times GARCH had run on the same scope -- exactly the Context-Store
   threading regression CLAUDE.md's fragile-surfaces section warns about.

2. `correlation_matrix` exists at all, and publishes the matrix, the
   covariance and the per-name vols. Nothing wrote a correlation matrix
   anywhere before it, so VaR's `_resolve_corr` always fell back to
   identity -- i.e. every basket VaR on the desk was a zero-correlation
   simulation presented as a risk number.

The GARCH fit and the correlation engine's price history both need network,
so both are stubbed: what is under test is the wiring, not the math.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# `import Vol_Suite.module_registry`, NOT the flat `import module_registry`.
# Four suites each ship a top-level module_registry.py, so the flat name
# resolves to whichever suite's copy landed in sys.modules first -- run this
# file after VaR_Tools_Simulations/tests and the flat import silently hands
# back VaR's registry, and every assertion here fails with a baffling
# AttributeError. Same fragile surface CLAUDE.md documents, and the same
# form the sibling test_module_registry_* files already use.
import Vol_Suite.module_registry as vmr

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# garch -> garch_conditional_vol
# ---------------------------------------------------------------------------


class _GarchResult(tuple):
    """Stand-in for GarchModuleResult: a tuple subclass carrying `.error`."""

    error = None

    def __new__(cls, files, interp, vol):
        return super().__new__(cls, (files, interp, vol))


def _stub_garch(monkeypatch, vol):
    import garch_analysis

    monkeypatch.setattr(
        garch_analysis,
        "run_garch_module",
        lambda ticker, **kw: _GarchResult([], "stubbed", vol),
    )


def test_garch_publishes_the_key_var_actually_reads(monkeypatch, tmp_path):
    _stub_garch(monkeypatch, 0.1833)
    result = vmr._run_garch({"ticker": "SPY", "output_dir": str(tmp_path)})
    assert result.status == "ok"
    patch = result.context_patch
    # Both names, one number: `garch_vol` is what Vol_Suite's own
    # suite_context path has always used, `garch_conditional_vol` is what
    # VaR_Tools_Simulations/module_registry.py::_resolve_vol looks up.
    assert patch["garch_conditional_vol"] == pytest.approx(0.1833)
    assert patch["garch_vol"] == pytest.approx(0.1833)


def test_garch_publishes_nothing_when_the_fit_produced_no_vol(monkeypatch, tmp_path):
    """A missing number must not be published as a number."""
    _stub_garch(monkeypatch, None)
    result = vmr._run_garch({"ticker": "SPY", "output_dir": str(tmp_path)})
    assert result.status == "skipped"
    assert result.context_patch is None


def test_garch_key_matches_what_var_resolves():
    """Pin the two ends against each other rather than against a literal, so
    renaming one side fails here instead of silently re-breaking the chain."""
    import inspect
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from VaR_Tools_Simulations import module_registry as var_mr

    var_source = inspect.getsource(var_mr._resolve_vol)
    assert "garch_conditional_vol" in var_source
    assert "garch_conditional_vol" in inspect.getsource(vmr._run_garch)


# ---------------------------------------------------------------------------
# correlation_matrix
# ---------------------------------------------------------------------------


def test_correlation_matrix_is_registered_and_basket_scoped():
    spec = next(m for m in vmr.MODULES if m.slug == "correlation_matrix")
    assert spec.suite == "vol_suite"
    assert spec.inputs.basket == "required"
    assert {p.name for p in spec.params} == {"period", "market"}


def test_basket_resolution_accepts_every_key_the_desk_publishes():
    assert vmr._resolve_basket({"basket": ["spy", "qqq"]}) == ["SPY", "QQQ"]
    assert vmr._resolve_basket({"held_tickers": ["NOK"]}) == ["NOK"]
    assert vmr._resolve_basket({"tickers": "SPY, QQQ"}) == ["SPY", "QQQ"]
    assert vmr._resolve_basket({"ticker": "iwm"}) == ["IWM"]
    assert vmr._resolve_basket({}) == []


def test_basket_resolution_dedupes():
    assert vmr._resolve_basket({"basket": ["SPY", "spy", "QQQ"]}) == ["SPY", "QQQ"]


def test_weights_come_from_the_book_and_renormalize():
    book = {
        "positions": [
            {"ticker": "SPY", "market_value": 600},
            {"ticker": "NOK", "market_value": -200},
        ]
    }
    weights = vmr._resolve_weights({"positions": book}, ["SPY", "NOK"])
    # Absolute market value: a short leg is exposure, not negative weight.
    assert weights == [600.0, 200.0]


def test_weights_fall_back_to_equal_weight_without_a_book():
    """None means "the engine's own equal-weight default" -- reporting a
    basket vol for a weighting you do not hold would be worse than saying
    nothing."""
    assert vmr._resolve_weights({}, ["SPY", "NOK"]) is None
    assert vmr._resolve_weights({"positions": {"positions": []}}, ["SPY"]) is None


def test_unlabeled_weights_are_refused():
    with pytest.raises(ValueError, match="unlabeled"):
        vmr._resolve_weights({"weights": [0.80, 0.20]}, ["SPY", "QQQ"])


def test_unlabeled_weights_yield_to_the_book():
    book = {
        "positions": [
            {"ticker": "SPY", "market_value": 600},
            {"ticker": "QQQ", "market_value": 400},
        ]
    }
    weights = vmr._resolve_weights(
        {"weights": [0.80, 0.20], "positions": book}, ["SPY", "QQQ"]
    )
    assert weights == [600.0, 400.0]


def test_labeled_weights_cover_a_permuted_basket():
    weights = vmr._resolve_weights(
        {"weights": [0.80, 0.20], "weight_tickers": ["QQQ", "SPY"]},
        ["SPY", "QQQ"],
    )
    assert weights == [0.20, 0.80]


def test_incomplete_weight_labels_raise_without_a_book():
    with pytest.raises(ValueError, match="unlabeled"):
        vmr._resolve_weights(
            {"weights": [0.80, 0.20], "weight_tickers": ["QQQ", "IWM"]},
            ["SPY", "QQQ"],
        )


def test_correlation_matrix_refuses_a_single_name():
    """A 1x1 matrix of 1.0 is not a correlation measurement."""
    result = vmr._run_correlation_matrix({"ticker": "SPY"})
    assert result.status == "failed"
    assert "at least 2 tickers" in result.metrics["error"]


def test_correlation_matrix_publishes_what_var_reads(monkeypatch, tmp_path):
    import correlation_engine as ce

    class _Pair:
        ticker1, ticker2 = "SPY", "QQQ"
        correlation, covariance, beta, p_value, n_observations = (
            0.85,
            0.0002,
            1.1,
            0.0,
            500,
        )

    class _Stats:
        tickers = ["SPY", "QQQ"]
        correlation_matrix = [[1.0, 0.85], [0.85, 1.0]]
        covariance_matrix = [[0.02, 0.017], [0.017, 0.024]]
        basket_vol = 0.147
        basket_beta = 1.05
        basket_expected_return = 0.09
        basket_sharpe = 0.4
        basket_alpha = 0.01
        individual_vols = {"SPY": 0.14, "QQQ": 0.19}
        individual_returns = {"SPY": 0.1, "QQQ": 0.12}
        individual_betas = {"SPY": 1.0, "QQQ": 1.2}
        correlation_pairs = [_Pair()]
        dispersion_score = 0.3
        diversification_ratio = 1.08
        dropped_tickers = []

    monkeypatch.setattr(ce, "compute_basket_stats", lambda *a, **kw: _Stats())
    monkeypatch.setattr(
        ce, "run_correlation_engine", lambda *a, **kw: ([], "stub interp", _Stats())
    )

    result = vmr._run_correlation_matrix(
        {"basket": ["SPY", "QQQ"], "output_dir": str(tmp_path)}
    )
    assert result.status == "ok"
    patch = result.context_patch
    # These three keys are exactly what VaR's _resolve_corr / _resolve_vol
    # look for, plus the labels that make re-indexing onto another basket
    # ordering safe.
    assert patch["correlation_matrix"] == [[1.0, 0.85], [0.85, 1.0]]
    assert patch["covariance_matrix"] == [[0.02, 0.017], [0.017, 0.024]]
    assert patch["volatilities"] == [0.14, 0.19]
    assert patch["correlation_tickers"] == ["SPY", "QQQ"]


def test_correlation_matrix_renders_the_matrix_as_rows(monkeypatch, tmp_path):
    """A bare nested list renders as an unreadable JSON blob on a card."""
    import correlation_engine as ce

    class _Stats:
        tickers = ["SPY", "QQQ"]
        correlation_matrix = [[1.0, 0.85], [0.85, 1.0]]
        covariance_matrix = [[0.02, 0.017], [0.017, 0.024]]
        basket_vol = 0.147
        basket_beta = 1.05
        basket_sharpe = 0.4
        individual_vols = {"SPY": 0.14, "QQQ": 0.19}
        correlation_pairs = []
        dispersion_score = 0.3
        diversification_ratio = 1.08
        dropped_tickers = []

    monkeypatch.setattr(ce, "compute_basket_stats", lambda *a, **kw: _Stats())
    # run_correlation_engine returns (files, interp, stats); a 2-tuple stub
    # is exactly the stale contract that broke the live module.
    monkeypatch.setattr(
        ce, "run_correlation_engine", lambda *a, **kw: ([], "", _Stats())
    )
    result = vmr._run_correlation_matrix(
        {"basket": ["SPY", "QQQ"], "output_dir": str(tmp_path)}
    )
    rows = result.metrics["correlation_rows"]
    assert rows[0][""] == "SPY"
    assert rows[0]["QQQ"] == pytest.approx(0.85)
    assert rows[1]["SPY"] == pytest.approx(0.85)
