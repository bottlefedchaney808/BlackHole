"""The equity runner's pure pieces. No broker, no network."""

from __future__ import annotations

import pytest

from chart_app import run_live_equity as rle
from launch_dock import launch
from shared.config import robinhood_agentic_account

# Made-up numbers. The real agentic account lives in `.env`
# (ROBINHOOD_AGENTIC_ACCOUNT) and never in this public repo.
AGENTIC_ACCOUNT = "100000001"
PERSONAL_ACCOUNT = "200000002"


def test_the_dock_and_runner_pin_the_same_account():
    # One source for both: `.env`, via shared.config.
    assert rle.AGENTIC_ACCOUNT == launch.AGENTIC_ACCOUNT == robinhood_agentic_account()


def test_runner_refuses_any_other_account(monkeypatch):
    monkeypatch.setattr(rle, "AGENTIC_ACCOUNT", AGENTIC_ACCOUNT)
    with pytest.raises(SystemExit, match="agentic"):
        rle.main(["--ticker", "FCEL", "--cap-fraction", "0.2", "--account", PERSONAL_ACCOUNT])


def test_runner_refuses_to_trade_with_no_account_configured(monkeypatch):
    monkeypatch.setattr(rle, "AGENTIC_ACCOUNT", "")
    with pytest.raises(SystemExit, match="not set"):
        rle.main(["--ticker", "FCEL", "--cap-fraction", "0.2", "--account", PERSONAL_ACCOUNT])


def test_budget_counts_the_shares_already_held():
    # Fully in: BP ~0. The old `bp + pnl` budget read 0 here and the book's
    # target became "sell everything".
    assert rle.sleeve_budget(cap=500.0, buying_power=0.4, held=30, price=16.0) == pytest.approx(480.4)
    assert rle.sleeve_budget(cap=100.0, buying_power=900.0, held=0, price=16.0) == 100.0


def test_stop_cancels_entries_but_keeps_the_protective_stop():
    inst = "https://api.robinhood.com/instruments/fcel/"
    acct = f"https://api.robinhood.com/accounts/{AGENTIC_ACCOUNT}/"
    raws = {
        "buy": {"instrument": inst, "account": acct, "state": "confirmed", "trigger": "immediate"},
        "stop": {"instrument": inst, "account": acct, "state": "confirmed", "trigger": "stop",
                 "stop_price": "15.10"},
        "done": {"instrument": inst, "account": acct, "state": "filled", "trigger": "immediate"},
        "other": {"instrument": "https://api.robinhood.com/instruments/gme/", "account": acct,
                  "state": "confirmed", "trigger": "immediate"},
        "mine": {"instrument": inst,
                 "account": f"https://api.robinhood.com/accounts/{PERSONAL_ACCOUNT}/",
                 "state": "confirmed", "trigger": "immediate"},
    }
    assert rle.resting_entries(raws, inst, AGENTIC_ACCOUNT) == ["buy"]
