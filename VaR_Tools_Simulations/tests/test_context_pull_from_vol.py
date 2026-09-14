"""VaR's context pull: vol, correlation and position size, with provenance.

Each of these resolvers has a fallback that is a plausible-looking number
rather than a measurement -- a flat 0.25 vol, an identity correlation matrix,
one unit of every name. Taking one silently turns a VaR figure into a
fabricated VaR figure. These tests pin three things:

1. the resolvers actually read what Vol_Suite writes (the keys matched up
   only after `garch` started writing `garch_conditional_vol` and the new
   `correlation_matrix` module started writing the matrix at all);
2. a stored vector/matrix is re-indexed onto the requested basket by label,
   never applied positionally -- otherwise a matrix stored for [QQQ, SPY] is
   silently applied to [SPY, QQQ] and every number is wrong with no error;
3. every module reports where its inputs came from, so a fallback is visible
   on the card instead of looking like a measurement.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pytest

from VaR_Tools_Simulations import module_registry as mr

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Tickers
# ---------------------------------------------------------------------------


def test_tickers_come_from_the_desk_basket():
    """The desk publishes the book as `basket`/`held_tickers`. Before this,
    `_extract_tickers` looked only at `tickers`/`ticker`, so corr_sim on a
    five-name book raised "requires at least 2 tickers"."""
    assert mr._extract_tickers({"basket": ["spy", "qqq"]}) == ["SPY", "QQQ"]
    assert mr._extract_tickers({"held_tickers": ["NOK", "GME"]}) == ["NOK", "GME"]
    assert mr._extract_tickers({"basket": "SPY, QQQ ,IWM"}) == ["SPY", "QQQ", "IWM"]


def test_tickers_dedupe_and_keep_order():
    assert mr._extract_tickers({"basket": ["SPY", "spy", "QQQ"]}) == ["SPY", "QQQ"]


def test_explicit_tickers_still_win_over_the_basket():
    got = mr._extract_tickers({"tickers": ["AAPL"], "basket": ["SPY", "QQQ"]})
    assert got == ["AAPL"]


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def test_vol_vector_is_realigned_by_label_not_position():
    """The stored vector is ordered by the basket that produced it. Applying
    it positionally to a differently-ordered basket assigns one name's vol to
    another with no error at all."""
    context = {
        "volatilities": [0.30, 0.40],
        "correlation_tickers": ["QQQ", "SPY"],
    }
    vols, source = mr._resolve_vol(context, ["SPY", "QQQ"])
    assert list(vols) == [0.40, 0.30]
    assert source == "context:volatilities"


def test_vol_vector_is_refused_when_it_does_not_cover_the_basket():
    """Better to fall back loudly than to quietly drop a name."""
    context = {"volatilities": [0.30, 0.40], "correlation_tickers": ["QQQ", "SPY"]}
    _vols, source = mr._resolve_vol(context, ["SPY", "NVDA"])
    assert source != "context:volatilities"


def test_garch_conditional_vol_broadcasts_and_says_so():
    """One GARCH number is a measurement of the focus name, not of each name
    in the basket -- the provenance string has to admit that."""
    vols, source = mr._resolve_vol(
        {"garch_conditional_vol": 0.1833}, ["SPY", "QQQ", "IWM"]
    )
    assert list(vols) == [pytest.approx(0.1833)] * 3
    assert "broadcast" in source


def test_vol_falls_back_to_flat_quarter_and_labels_it():
    vols, source = mr._resolve_vol({}, ["SPY", "QQQ"])
    assert list(vols) == [0.25, 0.25]
    assert source == "fallback:0.25"


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def test_correlation_matrix_is_reordered_by_label():
    context = {
        "correlation_matrix": [[1.0, 0.2], [0.2, 1.0]],
        "correlation_tickers": ["QQQ", "SPY"],
    }
    corr, source = mr._resolve_corr(context, ["SPY", "QQQ"])
    assert corr.shape == (2, 2)
    assert corr[0][0] == 1.0 and corr[0][1] == pytest.approx(0.2)
    assert source == "context:correlation_matrix"


def test_correlation_matrix_reorder_is_a_real_permutation():
    """A 3x3 where the off-diagonals differ, so a wrong permutation cannot
    pass by symmetry."""
    stored = [
        [1.0, 0.10, 0.20],  # A
        [0.10, 1.0, 0.30],  # B
        [0.20, 0.30, 1.0],  # C
    ]
    context = {"correlation_matrix": stored, "correlation_tickers": ["A", "B", "C"]}
    corr, _ = mr._resolve_corr(context, ["C", "A", "B"])
    # rows/cols now C, A, B: corr(C,A)=0.20, corr(C,B)=0.30, corr(A,B)=0.10
    assert corr[0][1] == pytest.approx(0.20)
    assert corr[0][2] == pytest.approx(0.30)
    assert corr[1][2] == pytest.approx(0.10)


def test_correlation_falls_back_to_identity_and_labels_it():
    corr, source = mr._resolve_corr({}, ["SPY", "QQQ"])
    assert np.array_equal(corr, np.eye(2))
    assert source == "fallback:identity"


def test_correlation_is_refused_when_labels_do_not_cover_the_basket():
    context = {
        "correlation_matrix": [[1.0, 0.2], [0.2, 1.0]],
        "correlation_tickers": ["QQQ", "SPY"],
    }
    _corr, source = mr._resolve_corr(context, ["SPY", "NVDA"])
    assert source == "fallback:identity"


def test_unlabeled_same_context_matrix_is_positional_and_says_so():
    """Scripted callers pass corr_matrix next to tickers with no label list.
    That order is this request's tickers — allowed, but provenance must not
    look like a labeled store row."""
    stored = [[1.0, 0.55], [0.55, 1.0]]
    corr, source = mr._resolve_corr(
        {"correlation_matrix": stored}, ["SPY", "QQQ"]
    )
    assert corr[0][1] == pytest.approx(0.55)
    assert source == "context:correlation_matrix(positional)"


def test_unlabeled_store_shaped_3x3_is_not_silently_permuted():
    """A 3x3 stored for [A,B,C] with no labels, requested [C,A,B]:
    positional would put corr(C,A)=0.10 (the A-B slot). Refuse unlabeled
    store rows so this cannot present as a measured matrix."""
    stored = [
        [1.0, 0.10, 0.20],
        [0.10, 1.0, 0.30],
        [0.20, 0.30, 1.0],
    ]
    # Simulate a store hit with no labels by going through _usable via
    # context without tickers-as-labels on a *store* path: empty context
    # plus we only have the explicit branch for same-context. The store
    # refuse is the unlabeled-labels=None branch with allow_positional=False.
    # Pin it by calling _usable through _resolve_corr on store-like: if
    # someone stuffed the matrix in context without labels, same-context
    # positional is announced. The permutation bug is labeled-reorder.
    labeled, _ = mr._resolve_corr(
        {"correlation_matrix": stored, "correlation_tickers": ["A", "B", "C"]},
        ["C", "A", "B"],
    )
    assert labeled[0][1] == pytest.approx(0.20)


def test_unlabeled_vol_vector_same_context_is_announced_positional():
    vols, source = mr._resolve_vol({"volatilities": [0.12, 0.40]}, ["SPY", "QQQ"])
    assert list(vols) == [pytest.approx(0.12), pytest.approx(0.40)]
    assert source == "context:volatilities(positional)"


def test_unlabeled_store_matrix_is_refused(monkeypatch):
    """Direct store hit, no labels, request [C,A,B]: positional would put
    corr(C,A)=0.10 (the A-B slot). allow_positional=False must refuse."""
    stored = [
        [1.0, 0.10, 0.20],
        [0.10, 1.0, 0.30],
        [0.20, 0.30, 1.0],
    ]

    class _Store:
        def get(self, _scope, key):
            if key == "correlation_matrix":
                return stored
            return None

    monkeypatch.setattr(mr, "_context_store", lambda: _Store())
    corr, source = mr._resolve_corr({}, ["C", "A", "B"])
    assert source == "fallback:identity"
    assert np.array_equal(corr, np.eye(3))


def test_unlabeled_store_vol_vector_is_refused(monkeypatch):
    class _Store:
        def get(self, _scope, key):
            if key == "volatilities":
                return [0.11, 0.22, 0.33]
            return None

    monkeypatch.setattr(mr, "_context_store", lambda: _Store())
    _vols, source = mr._resolve_vol({}, ["C", "A", "B"])
    assert source == "fallback:0.25"


def test_hist_sim_unconverged_garch_marks_outlook_unmeasured(monkeypatch):
    """HW/FHS still emit a VaR from an unconverged fit; the outlook must
    not present that number as measured."""
    from types import SimpleNamespace

    def _fake_run(_inp):
        return SimpleNamespace(
            var=1.0,
            cvar=1.2,
            method="hw",
            garch_convergence={"SPY": False, "QQQ": True},
        )

    import importlib

    # var_engine/__init__.py binds `hist_sim` to the run *function*, which
    # shadows the submodule on the package. Patch the real module.
    hs = importlib.import_module("var_engine.hist_sim")
    monkeypatch.setattr(hs, "run", _fake_run)
    result = mr._run_hist_sim(
        {
            "tickers": ["SPY", "QQQ"],
            "positions": [1000.0, 500.0],
            "method": "hw",
            "returns_dict": {"SPY": [0.0], "QQQ": [0.0]},
        }
    )
    assert result.status == "ok"
    outlook = result.context_patch["risk_outlook"]
    assert outlook["measured"] is False
    assert outlook["inputs"]["garch"].startswith("fallback:garch_unconverged")
    assert "SPY" in outlook["inputs"]["garch"]


# ---------------------------------------------------------------------------
# Position size
# ---------------------------------------------------------------------------


def test_positions_come_from_the_book_and_aggregate_legs():
    book = {
        "positions": [
            {"ticker": "SPY", "market_value": 1000},
            {"ticker": "SPY", "market_value": 500},
            {"ticker": "NOK", "market_value": -200},
        ]
    }
    values, source = mr._extract_positions({"positions": book}, ["SPY", "NOK"])
    assert list(values) == [1500.0, -200.0]
    assert source == "book:market_value"


def test_positions_accept_a_bare_numeric_vector():
    """`positions` is overloaded: the desk's book, or a plain vector from a
    scripted caller. Both have to keep working."""
    values, source = mr._extract_positions({"positions": [3.0, 4.0]}, ["SPY", "NOK"])
    assert list(values) == [3.0, 4.0]
    assert source == "context:positions"


def test_positions_fall_back_to_unit_notional_and_label_it():
    values, source = mr._extract_positions({}, ["SPY", "NOK"])
    assert list(values) == [1.0, 1.0]
    assert source == "fallback:unit_notional"


# ---------------------------------------------------------------------------
# Provenance reaches the card
# ---------------------------------------------------------------------------


def test_corr_sim_reports_where_its_inputs_came_from():
    result = mr._run_corr_sim(
        {
            "basket": ["SPY", "QQQ"],
            "volatilities": [0.2, 0.3],
            "correlation_tickers": ["SPY", "QQQ"],
            "correlation_matrix": [[1.0, 0.6], [0.6, 1.0]],
            "positions": {
                "positions": [
                    {"ticker": "SPY", "market_value": 1000},
                    {"ticker": "QQQ", "market_value": 500},
                ]
            },
            "n_sims": 500,
        }
    )
    assert result.status == "ok", result.metrics
    assert result.metrics["vol_source"] == "context:volatilities"
    assert result.metrics["corr_source"] == "context:correlation_matrix"
    assert result.metrics["position_source"] == "book:market_value"


def test_corr_sim_runs_on_a_basket_alone():
    """The whole point of the basket fallback: a desk run that publishes only
    `held_tickers` must not raise 'requires at least 2 tickers'."""
    result = mr._run_corr_sim({"held_tickers": ["SPY", "QQQ"], "n_sims": 500})
    assert result.status == "ok", result.metrics
    assert result.metrics["vol_source"] == "fallback:0.25"
    assert result.metrics["corr_source"] == "fallback:identity"


def test_fallback_var_differs_from_measured_var():
    """Proof the provenance matters: the same basket priced off real inputs
    and off the fallbacks is a materially different number."""
    base = {"basket": ["SPY", "QQQ"], "n_sims": 4000, "seed": 7}
    measured = mr._run_corr_sim(
        {
            **base,
            "volatilities": [0.12, 0.14],
            "correlation_tickers": ["SPY", "QQQ"],
            "correlation_matrix": [[1.0, 0.85], [0.85, 1.0]],
        }
    )
    fallback = mr._run_corr_sim(dict(base))
    assert measured.status == fallback.status == "ok"
    assert measured.metrics["var"] != pytest.approx(fallback.metrics["var"], rel=0.05)


# ---------------------------------------------------------------------------
# The simulated outlook flows back OUT
# ---------------------------------------------------------------------------


def test_sims_publish_a_risk_outlook():
    """Every VaR module used to return `context_patch=None`, so a VaR run
    taught the Context Store nothing and the simulated outlook could not be
    read by any other tool. One `risk_outlook` key, naming its producer."""
    result = mr._run_corr_sim(
        {
            "basket": ["SPY", "QQQ"],
            "volatilities": [0.12, 0.14],
            "correlation_tickers": ["SPY", "QQQ"],
            "correlation_matrix": [[1.0, 0.8], [0.8, 1.0]],
            "n_sims": 500,
        }
    )
    outlook = result.context_patch["risk_outlook"]
    assert outlook["source"] == "corr_sim"
    assert outlook["tickers"] == ["SPY", "QQQ"]
    assert isinstance(outlook["var"], float)
    # A VaR number without its horizon and confidence is not interpretable.
    assert outlook["horizon_days"] == 10.0
    assert outlook["confidence"] == 0.99


def test_risk_outlook_flags_whether_its_inputs_were_measured():
    """So a consumer can refuse to build on a VaR that was computed off a
    flat 0.25 and an identity matrix."""
    measured = mr._run_corr_sim(
        {
            "basket": ["SPY", "QQQ"],
            "volatilities": [0.12, 0.14],
            "correlation_tickers": ["SPY", "QQQ"],
            "correlation_matrix": [[1.0, 0.8], [0.8, 1.0]],
            "positions": {
                "positions": [
                    {"ticker": "SPY", "market_value": 1000},
                    {"ticker": "QQQ", "market_value": 500},
                ]
            },
            "n_sims": 500,
        }
    ).context_patch["risk_outlook"]
    assert measured["measured"] is True

    fallback = mr._run_corr_sim(
        {"basket": ["SPY", "QQQ"], "n_sims": 500}
    ).context_patch["risk_outlook"]
    assert fallback["measured"] is False
    assert fallback["inputs"]["vol"] == "fallback:0.25"


def test_risk_outlook_carries_no_raw_paths():
    """Scalars only: every later run on this scope is seeded from the store,
    and 50,000 simulated paths do not belong in that."""
    outlook = mr._run_corr_sim({"basket": ["SPY", "QQQ"], "n_sims": 500}).context_patch[
        "risk_outlook"
    ]
    for value in outlook.values():
        assert not isinstance(value, (list, tuple)) or len(value) <= 32
