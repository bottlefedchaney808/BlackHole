import pytest

import expiry_book_exposure as ebe
from expiry_book_production import (
    ExpiryBookUnavailable,
    normalize_snapshot_rows,
    production_result_from_rows,
)


def _rows():
    return [
        {"strike": 95_000, "right": "P", "open_interest": 1000, "implied_vol": 0.25},
        {"strike": 100_000, "right": "C", "open_interest": 500, "implied_vol": 0.20},
        {"strike": 105_000, "right": "C", "open_interest": 2000, "implied_vol": 0.23},
    ]


def test_normalizer_rejects_missing_required_market_fields():
    with pytest.raises(ExpiryBookUnavailable, match="open interest"):
        normalize_snapshot_rows([{"strike": 100_000, "right": "C", "implied_vol": 0.2}])


def test_normalizer_converts_theta_strikes_and_rights():
    rows = normalize_snapshot_rows(_rows())
    assert rows[0]["strike"] == 95.0
    assert rows[0]["right"] == "P"
    assert rows[0]["oi"] == 1000.0


def test_positive_gamma_uses_dealer_hedge_trade_direction():
    locus = ebe.ExecutionLocus(100.0, 100.0, 105.0, 95.0, 99.0, 101.0, 0.01, 0.0, 10.0)
    assert ebe.hedge_flow_at(locus, 103.0) < 0
    assert ebe.hedge_flow_at(locus, 97.0) > 0


def test_production_result_exposes_gex_walls_and_distinct_boundary():
    result = production_result_from_rows("MOCK", "20270115", 100.0, _rows(), dte=150)
    assert result.status == "available"
    assert result.units["gex"] == "dollar_gamma_per_1pct_move"
    assert result.execution_locus.local_gamma_boundary == result.execution_locus.zero_gamma
    assert result.execution_locus.call_gamma_wall != 0
    assert result.execution_locus.put_gamma_wall != 0
    assert result.provenance["source"] == "ThetaData snapshot"


def test_production_result_does_not_claim_accumulation_from_snapshot():
    result = production_result_from_rows("MOCK", "20270115", 100.0, _rows(), dte=150)
    assert result.structural.status == "unavailable"
    assert result.structural.reason == "multi_expiry_book_required"
    assert result.accumulation_claimed is False