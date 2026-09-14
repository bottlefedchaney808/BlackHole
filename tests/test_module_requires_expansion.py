"""Declared dependencies: pulled in when needed, skipped when already known.

The other half of the desk's selection contract. A module that needs another
module's output must not be left to a silent fallback -- every VaR module in
`NEEDS_CORRELATION` resolved a flat 0.25 vol and an identity correlation
matrix when run alone, and reported the result as a risk number.

Declaring `requires` fixes that, but naively it would re-run a 2-year,
whole-basket EOD pull on every click of the dependent card, since the
dashboard has already seeded the stored matrix into the context. So an
AUTO-ADDED dependency whose `provides` keys are all present is skipped; an
explicitly selected one always runs.
"""

from __future__ import annotations

import pytest

from shared.module_execution import _expand_module_requires, _resolve_modules

NEEDS_CORRELATION = ("corr_sim", "mc_sim", "copulas", "var_agg", "hedge_optimizer")

CORR_KEYS = {
    "correlation_matrix": [[1.0, 0.3], [0.3, 1.0]],
    "covariance_matrix": [[0.04, 0.01], [0.01, 0.09]],
    "volatilities": [0.2, 0.3],
    "correlation_tickers": ["SPY", "NVDA"],
}


@pytest.mark.unit
@pytest.mark.parametrize("slug", NEEDS_CORRELATION)
def test_var_module_pulls_the_correlation_it_needs(slug):
    expanded = _expand_module_requires(_resolve_modules([slug]), {})
    assert "correlation_matrix" in {m.slug for m in expanded}


@pytest.mark.unit
@pytest.mark.parametrize("slug", NEEDS_CORRELATION)
def test_a_satisfied_dependency_is_not_re_run(slug):
    expanded = _expand_module_requires(_resolve_modules([slug]), dict(CORR_KEYS))
    assert {m.slug for m in expanded} == {slug}


@pytest.mark.unit
def test_a_partially_satisfied_dependency_still_runs():
    """Half a matrix is not a matrix -- don't trust a partial context."""
    partial = dict(CORR_KEYS)
    del partial["volatilities"]
    expanded = _expand_module_requires(_resolve_modules(["corr_sim"]), partial)
    assert "correlation_matrix" in {m.slug for m in expanded}


@pytest.mark.unit
def test_an_explicitly_selected_module_always_runs():
    """Skipping only ever applies to a dependency the caller didn't ask for."""
    expanded = _expand_module_requires(
        _resolve_modules(["corr_sim", "correlation_matrix"]), dict(CORR_KEYS)
    )
    assert {m.slug for m in expanded} == {"corr_sim", "correlation_matrix"}


@pytest.mark.unit
def test_forex_var_does_not_pull_an_equity_correlation_engine():
    """Its tickers are currency pairs; Vol_Suite's EOD engine can't price them."""
    expanded = _expand_module_requires(_resolve_modules(["forex_var"]), {})
    assert {m.slug for m in expanded} == {"forex_var"}


@pytest.mark.unit
def test_a_module_with_no_provides_is_never_skipped():
    """`provides` is the only thing that licenses a skip."""
    from shared.module_execution import _dependency_already_satisfied

    spec = _resolve_modules(["forex_var"])[0]
    assert spec.provides == ()
    assert not _dependency_already_satisfied(spec, {"anything": 1})
