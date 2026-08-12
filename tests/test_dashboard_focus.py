"""`_focus_from_body` — the dashboard's JSON/form body to orchestrator `focus` mapping.

The orchestrator reads `focus['var_horizon_days']` when building suite_context, so
anything the trigger form drops here is silently unreachable from the dashboard.
"""

import pytest

from dashboard.app import _focus_from_body


def test_focus_from_body_requires_ticker():
    focus, error = _focus_from_body({})
    assert focus == {}
    assert error == 'ticker is required'


def test_focus_from_body_passes_through_var_horizon_days():
    focus, error = _focus_from_body({'ticker': 'AAPL', 'var_horizon_days': '30'})
    assert error is None
    assert focus['var_horizon_days'] == 30


def test_focus_from_body_omits_var_horizon_days_when_absent():
    focus, error = _focus_from_body({'ticker': 'AAPL'})
    assert error is None
    assert 'var_horizon_days' not in focus


def test_focus_from_body_rejects_non_integer_var_horizon_days():
    focus, error = _focus_from_body({'ticker': 'AAPL', 'var_horizon_days': 'ten'})
    assert focus == {}
    assert error == 'var_horizon_days must be an integer'


@pytest.mark.parametrize('bad', [0, -5])
def test_focus_from_body_rejects_non_positive_var_horizon_days(bad):
    focus, error = _focus_from_body({'ticker': 'AAPL', 'var_horizon_days': bad})
    assert focus == {}
    assert error == 'var_horizon_days must be a positive integer'
