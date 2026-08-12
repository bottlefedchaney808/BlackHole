import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from Backtests.core import (
    bs_delta,
    bs_price,
    bias,
    mae,
    moneyness_bucket,
    normalize_row,
    pearson,
    rmse,
    rows_by_date,
    sign_agreement,
    strike_from_theta,
    tte_bucket,
    tte_years,
)
from Backtests.data import pick_expiry


def test_backtests_main_imports_without_wsl_path_hacks() -> None:
    import Backtests.main  # noqa: F401


def test_backtests_main_direct_windows_entrypoint_supports_help() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "Backtests\\main.py", "--help"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Backtest Tournament" in result.stdout


def test_pick_expiry_returns_none_when_no_expiry_meets_minimum() -> None:
    from Backtests.core import FakeController

    old_expiry = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    td = FakeController(expirations=[old_expiry])
    assert pick_expiry(td, "TST", min_days_out=5) is None


def test_backtests_package_files_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "main.py").exists()
    assert (root / "core.py").exists()
    assert (root / "tests" / "test_core.py").exists()


# ---------------------------------------------------------------------------
# Known-answer Black-Scholes fixtures (hand-computed)
# ---------------------------------------------------------------------------

def test_bs_price_known_answer_call():
    # S=100, K=100, T=1, r=0.05, q=0, sigma=0.2
    # d1 = (ln(1) + (0.05 + 0.5*0.04)*1)/(0.2) = 0.05/0.2 + 0.02/0.2 = 0.35
    # d2 = 0.15 ; N(0.35)=0.6368 , N(0.15)=0.5596
    # C = 100*0.6368 - 100*e^-0.05*0.5596 = 63.68 - 53.230 = 10.450
    c = bs_price(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert abs(c - 10.450) < 0.01


def test_bs_price_known_answer_put_parity():
    c = bs_price(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    p = bs_price(100, 100, 1.0, 0.05, 0.0, 0.2, False)
    # put-call parity: C - P = S - K*e^-rT
    assert abs((c - p) - (100 - 100 * math.exp(-0.05))) < 1e-9


def test_bs_delta_known_answer():
    # N(d1) with d1=0.35 -> 0.6368 (call, q=0)
    d = bs_delta(100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert abs(d - 0.6368) < 0.001


def test_tte_years():
    t = tte_years("20260918", "20260720")
    assert t is not None
    assert abs(t - (60.0 / 365.0)) < 1e-6
    assert tte_years("20260918", None) is None


# ---------------------------------------------------------------------------
# Normalization (shared ThetaData contract)
# ---------------------------------------------------------------------------

def test_normalize_theta_strike_and_string_numerics():
    row = {
        "strike": 727000, "right": "P", "date": "20260720",
        "implied_vol": "0.1757", "delta": "-0.0149", "bid": "0.1200", "ask": "0.1300",
        "expiration": 20260918,
    }
    n = normalize_row(row)
    assert n is not None
    assert n["strike"] == 727.0
    assert n["right"] == "P"
    assert abs(n["implied_vol"] - 0.1757) < 1e-9
    assert abs(n["delta"] + 0.0149) < 1e-9
    assert n["date"] == "20260720"  # extra field preserved
    assert n["expiration"] == 20260918


def test_normalize_full_word_rights_and_dollar_strike():
    n = normalize_row({"strike": "215.000", "right": "CALL", "implied_vol": "0.30"})
    assert n is not None
    assert n["strike"] == 215.0
    assert n["right"] == "C"


def test_normalize_current_chain_aliases():
    row = {
        "strike": 727000,
        "right": "CALL",
        "iv": "0.2",
        "mid": "1.25",
        "date": "20260720",
    }
    normalized = normalize_row(row)
    assert normalized is not None
    assert normalized["implied_vol"] == 0.2
    assert normalized["iv"] == 0.2
    assert normalized["mid"] == 1.25
    assert normalized["date"] == "20260720"


def test_normalize_drops_invalid():
    assert normalize_row({}) is None
    assert normalize_row({"strike": 0, "right": "C"}) is None
    assert normalize_row({"strike": 727000, "right": "X"}) is None


def test_strike_from_theta():
    assert strike_from_theta(727000) == 727.0


def test_rows_by_date():
    rows = [{"date": "20260720", "strike": 1}, {"date": "20260721", "strike": 2},
            {"date": "20260720", "strike": 3}]
    by = rows_by_date(rows)
    assert len(by["20260720"]) == 2
    assert len(by["20260721"]) == 1


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def test_moneyness_buckets():
    assert moneyness_bucket(100, 100, "C") == "ATM"
    assert moneyness_bucket(95, 100, "C") == "ITM"
    assert moneyness_bucket(110, 100, "C") == "OTM"
    assert moneyness_bucket(80, 100, "C") == "DeepITM"
    assert moneyness_bucket(120, 100, "C") == "DeepOTM"
    assert moneyness_bucket(110, 100, "P") == "ITM"  # put ITM when K>S
    assert moneyness_bucket(90, 100, "P") == "OTM"
    assert moneyness_bucket(99, 100, "P") == "ATM"


def test_tte_buckets():
    assert tte_bucket(0.05) == "<0.1y"
    assert tte_bucket(0.2) == "0.1-0.3y"
    assert tte_bucket(0.5) == ">0.3y"
    assert tte_bucket(None) == "?"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def test_mae_rmse_bias():
    pairs = [(2.0, 1.0), (4.0, 2.0), (6.0, 5.0)]
    assert abs(mae(pairs) - (1 + 2 + 1) / 3) < 1e-9
    assert abs(rmse(pairs) - math.sqrt((1 + 4 + 1) / 3)) < 1e-9
    b = bias(pairs)
    assert b is not None
    assert abs(b - (4 / 3)) < 1e-9  # model - market


def test_pearson():
    # perfectly increasing -> r = 1.0
    pairs = [(float(i), float(i)) for i in range(5)]
    r = pearson(pairs)
    assert r is not None
    assert abs(r - 1.0) < 1e-9
    # < 3 pairs -> None
    assert pearson([(1.0, 2.0), (2.0, 3.0)]) is None
    # flat variance -> None
    assert pearson([(1.0, 1.0)] * 4) is None


def test_sign_agreement():
    pairs = [(1.0, 2.0), (-1.0, -2.0), (1.0, -2.0), (3.0, 4.0)]
    sa = sign_agreement(pairs)
    assert sa is not None
    assert abs(sa - 0.75) < 1e-9
