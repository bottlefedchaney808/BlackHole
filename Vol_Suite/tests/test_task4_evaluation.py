import pytest
from run_task4_evaluation import (
    EvaluationInvalid,
    decision_ladder,
    evaluate_task4,
    power_diagnostics,
    unique_calendar_days,
)


def row(day, ticker, y=0.02, v=2.0, div=0.1, *, daily=-0.01, breach=0.02):
    return {
        "day": day, "ticker": ticker, "family": ticker,
        "comparison": {"status": "VALID", "input_hash": f"{day}{ticker}"},
        "provenance": {"causal_status": "CAUSAL_ELIGIBLE", "no_imputation": True},
        "pre_vanna": v, "delta_iv_pre_window": div, "gamma_burst": 1.0,
        "delta_s": 0.01, "market": 0.005, "a6_reflexivity": 0.2,
        "cross_family_spillover": 0.1, "event": int(day == "2026-01-02"),
        "daily_return": daily, "from_breach_return": breach,
        "forward_return_h": y, "placebo_return": 0.0, "reverse_return": 0.0,
        "opposite_convention_vanna": -v,
    }


def test_unique_calendar_day_counts_same_day_tickers_once_and_never_uses_zero_outcome():
    rows = [row("2026-01-01", "SPY"), row("2026-01-01", "QQQ"), row("2026-01-02", "SPY")]
    assert unique_calendar_days(rows) == 2
    result = evaluate_task4(rows, min_days=1)
    assert result["n_unique_days"] == 2
    assert result["causal"]["n"] == 2


def test_missing_outcome_is_invalid_not_imputed_zero():
    bad = row("2026-01-01", "SPY")
    del bad["forward_return_h"]
    with pytest.raises(EvaluationInvalid, match="outcome"):
        evaluate_task4([bad], min_days=1)


def test_causal_blocked_provenance_keeps_descriptive_and_blocks_causal():
    bad = row("2026-01-01", "SPY")
    bad["provenance"] = {"causal_status": "CAUSAL_BLOCKED", "no_imputation": True}
    result = evaluate_task4([bad], min_days=1)
    assert result["descriptive"]["status"] == "VALID"
    assert result["causal"]["status"] == "CAUSAL_BLOCKED"
    assert result["decision"] == "INDETERMINATE"


def test_placebo_and_reverse_are_reported_without_best_lag_selection():
    result = evaluate_task4([row(f"2026-01-{i:02d}", "SPY") for i in range(1, 8)], min_days=1)
    assert set(result["falsifiers"]) >= {"placebo", "reverse_lead_lag"}
    assert result["config"]["best_lag_selection"] is False


def test_power_interpretation_never_confuses_correlational_target_with_beta_power():
    diag = power_diagnostics(beta=1.0, se=0.1, n=29, p=10, target_days=257)
    assert diag["correlational_target_days"] == 29
    assert diag["beta_target_days"] == 257
    assert diag["reach_80"] is False
    assert diag["n_for_80"] is not None


def test_decision_ladder_is_explicit_and_never_promotes():
    assert decision_ladder(descriptive_ok=True, causal_ok=False, falsifiers_ok=True, powered=True) == "INDETERMINATE"
    assert decision_ladder(descriptive_ok=False, causal_ok=True, falsifiers_ok=True, powered=True) == "WORSE"
    assert decision_ladder(descriptive_ok=True, causal_ok=True, falsifiers_ok=True, powered=True) == "BETTER"
    assert decision_ladder(descriptive_ok=True, causal_ok=True, falsifiers_ok=False, powered=True) == "WORSE"


def test_invalid_comparison_artifact_fails_closed():
    bad = row("2026-01-01", "SPY")
    bad["comparison"] = {"status": "COMPARISON_INVALID"}
    with pytest.raises(EvaluationInvalid, match="comparison"):
        evaluate_task4([bad], min_days=1)


def test_opposite_convention_is_sensitivity_not_primary():
    result = evaluate_task4([row(f"2026-01-{i:02d}", "SPY") for i in range(1, 8)], min_days=1)
    assert result["sensitivity"]["opposite_convention"]["role"] == "SENSITIVITY"
    assert result["config"]["auto_promote"] is False


def test_underpowered_is_indeterminate_even_with_positive_estimate():
    result = evaluate_task4([row("2026-01-01", "SPY"), row("2026-01-02", "SPY")], min_days=1)
    assert result["power"]["underpowered"] is True
    assert result["decision"] == "INDETERMINATE"