import pytest

import expiry_book_exposure as ebe
from expiry_book_production import (
    ExpiryBookUnavailable,
    _merge_greeks_oi,
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


def test_normalizer_scales_sub_ten_dollar_theta_strikes():
    """SLS 2026-08-20 live chart: spot $13.85, OTM strikes 4000/10000
    (Theta thousandths = $4/$10) were left unscaled because the gate was
    abs(strike) > 10_000. Axis ran $0–$10,000 with spot glued to zero."""
    rows = normalize_snapshot_rows([
        {"strike": 4000, "right": "P", "open_interest": 100, "implied_vol": 0.80},
        {"strike": 10000, "right": "C", "open_interest": 200, "implied_vol": 0.50},
        {"strike": 13850, "right": "C", "open_interest": 50, "implied_vol": 0.40},
    ])
    assert [r["strike"] for r in rows] == [4.0, 10.0, 13.85]


def test_positive_gamma_uses_dealer_hedge_trade_direction():
    locus = ebe.ExecutionLocus(100.0, 100.0, 105.0, 95.0, 99.0, 101.0, 0.01, 0.0, 10.0)
    assert ebe.hedge_flow_at(locus, 103.0) < 0
    assert ebe.hedge_flow_at(locus, 97.0) > 0


def test_production_result_exposes_gex_walls_and_distinct_boundary():
    result = production_result_from_rows("MOCK", "20270115", 100.0, _rows(), dte=150)
    assert result.status == "available"
    assert result.units["gex"] == "dollar_gamma_per_1pct_move_imported_call_put"
    assert result.units["book_gamma"] == "dollar_gamma_per_1pct_svi_otm"
    assert result.svi_overlay is not None
    assert "imported" in result.provenance["book_sign"] or result.provenance["book_sign"].startswith("svi")
    assert result.execution_locus.local_gamma_boundary == result.execution_locus.zero_gamma
    assert result.execution_locus.call_gamma_wall != 0
    assert result.execution_locus.put_gamma_wall != 0
    assert result.provenance["source"] == "ThetaData snapshot"


def test_production_result_does_not_claim_accumulation_from_snapshot():
    result = production_result_from_rows("MOCK", "20270115", 100.0, _rows(), dte=150)
    assert result.structural.status == "unavailable"
    assert result.structural.reason == "insufficient_tenor_buckets"
    assert result.structural_regime is None
    assert result.accumulation_claimed is False


def test_interp_names_imported_gex_and_svi_book():
    from expiry_book_production import format_production_interp
    result = production_result_from_rows("MOCK", "20270115", 100.0, _rows(), dte=150)
    text = format_production_interp(result)
    assert "imported" in text.lower()
    assert "Book gamma" in text
    assert "Dealer GEX" not in text
    assert "SVI marks" in text
    assert result.vanna_flow_live is None
    assert result.vanna_flow_provenance == "delta_iv_missing"


def test_vendor_dealer_is_labeled_and_never_oi_multiplied():
    from expiry_book_production import _summarize_vendor_dealer, format_production_interp
    summary = _summarize_vendor_dealer({"net_gamma": -12.5, "oi": 999999})
    assert summary["oi_multiplied"] is False
    assert summary["net"] == -12.5
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150, vendor_dealer=summary,
    )
    text = format_production_interp(result)
    assert "not OI-multiplied" in text
    assert "dealer.positioning" in text


def test_fetch_prefers_weighted_greeks_summary():
    class _TD:
        def fetch_spot_price(self, t):
            return 100.0
        def option_bulk_greeks(self, t, e):
            return _rows()
        def option_bulk_oi(self, t, e):
            return [{"strike": r["strike"], "right": r["right"],
                     "open_interest": r["open_interest"]} for r in _rows()]
        def fetch_dividend_yield(self, t):
            return 0.0
        def list_expirations(self, t):
            return []
        def hist_stock_eod(self, t, a, b):
            return []
        def get_dealer_weighted_greeks_summary(self, t, a, b):
            return {"weighted_gamma": -42.0}

    from expiry_book_production import fetch_production_result
    result = fetch_production_result(_TD(), "MOCK", "20270115")
    assert result.vendor_dealer["endpoint"] == "dealer.weighted_greeks_summary"
    assert result.vendor_dealer["net"] == -42.0
    assert result.vendor_dealer["oi_multiplied"] is False


def test_surface_change_beats_atm_subtract_for_vanna_flow():
    class _TD:
        def fetch_spot_price(self, t):
            return 100.0
        def option_bulk_greeks(self, t, e):
            return _rows()
        def option_bulk_oi(self, t, e):
            return [{"strike": r["strike"], "right": r["right"],
                     "open_interest": r["open_interest"]} for r in _rows()]
        def fetch_dividend_yield(self, t):
            return 0.0
        def list_expirations(self, t):
            return []
        def hist_stock_eod(self, t, a, b):
            return []
        def get_iv_surface_change(self, t, exp, baseline, asof=None):
            return {"atm_change": 0.015}

    from expiry_book_production import fetch_production_result
    result = fetch_production_result(_TD(), "MOCK", "20270115")
    assert result.vanna_flow_provenance == "SURFACE_CHANGE"
    assert result.vanna_flow_live is not None


def test_measured_vanna_flow_deadbands_sub_point_div():
    from expiry_book_production import production_result_from_rows
    quiet = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        d_iv_measured=0.005, d_iv_source="SURFACE_CHANGE",
    )
    assert quiet.vanna_flow_live == 0.0
    assert quiet.vanna_flow_provenance.endswith(":deadband")
    loud = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        d_iv_measured=0.015, d_iv_source="SURFACE_CHANGE",
    )
    assert loud.vanna_flow_live != 0.0
    assert loud.vanna_flow_provenance == "SURFACE_CHANGE"


def test_book_gamma_not_aliased_to_imported_gex_on_cheap_puts():
    """Put-heavy OI makes imported GEX negative. Cheap OTM puts mark LONG,
    so book_gamma must not equal gex_reference (Cem R2 sign-and-magnitude pin)."""
    rows = []
    spot = 100.0
    for k in range(70, 131, 2):
        for right in ("C", "P"):
            m = k / spot
            iv = 0.22 + 0.25 * max(0.0, (1 - m))  # put skew, fit stays rich
            if right == "P" and k < spot:
                oi = 2000
            elif right == "C" and k > spot:
                oi = 80
            else:
                oi = 30
            rows.append({
                "strike": k * 1000, "right": right,
                "open_interest": oi, "implied_vol": iv,
            })
    # Dump two OTM puts well below the fit so they mark LONG.
    for k in (80, 84):
        for i, row in enumerate(rows):
            if row["strike"] == k * 1000 and row["right"] == "P":
                rows[i] = {**row, "implied_vol": 0.08}
    result = production_result_from_rows("MOCK", "20270115", spot, rows, dte=150)
    assert result.gex_reference < 0
    assert result.book_gamma != pytest.approx(result.gex_reference, rel=0, abs=1.0)
    # Budget gamma lead must follow the book, not imported GEX.
    up = result.scenario_budget.flow("up_1pct", "gamma")
    assert up == pytest.approx(-result.book_gamma, rel=0.05)
    assert result.svi_overlay is not None
    assert any(m[2] == "LONG" for m in result.svi_overlay.marks)


def test_normalizer_drops_unsolved_iv_rows_instead_of_aborting_book():
    """Live WMT 20261120: 5 deep-ITM calls had implied_vol=0.0000 and
    aborted the entire expiry-book fetch (no dealer charts). Unsolved
    vendor IV is not a missing field — drop those rows, keep the book."""
    rows = _rows() + [
        {"strike": 65_000, "right": "C", "open_interest": 11, "implied_vol": 0.0},
        {"strike": 70_000, "right": "C", "open_interest": 2, "implied_vol": "0.0000"},
    ]
    kept = normalize_snapshot_rows(rows)
    assert len(kept) == 3
    assert all(r["implied_vol"] > 0 for r in kept)
    result = production_result_from_rows("WMT", "20261120", 114.36, rows, dte=92)
    assert result.status == "available"
    assert len(result.snapshot.rows) == 3


def test_normalizer_still_fails_when_every_row_is_unusable():
    with pytest.raises(ExpiryBookUnavailable, match="no usable option rows"):
        normalize_snapshot_rows([
            {"strike": 65_000, "right": "C", "open_interest": 11, "implied_vol": 0.0},
        ])


def test_vanna_inventory_and_7d_flow_are_adjacent_not_added():
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        d_iv_measured=0.02, d_iv_source="SURFACE_CHANGE",
    )
    stock = result.residual_vanna_inventory
    flow = result.vanna_flow_live
    assert result.units["residual_vanna_inventory"] == "shares_per_vol_point"
    assert result.units["vanna_flow"] == "shares"
    assert stock is not None and flow is not None
    assert flow == pytest.approx(stock * (0.02 / 0.01))
    text = __import__("expiry_book_production", fromlist=["format_production_interp"]).format_production_interp(result)
    assert "neutral" not in text.lower()
    assert "Vanna inventory" in text
    assert "Vanna flow 7d" in text


def test_missing_vendor_div_does_not_atm_subtract():
    """Jason 2026-08-20: ATM-subtract manufactures flow. Fail closed."""
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=150,
        prior_atm_iv=0.18, prior_iv_asof="20260819",
    )
    assert result.vanna_flow_live is None
    assert result.vanna_flow_provenance in ("delta_iv_missing", "unavailable")
    assert result.residual_vanna_inventory is not None
    assert result.d_iv_used is None


def test_fetch_without_surface_change_does_not_atm_subtract():
    class _TD:
        def fetch_spot_price(self, t):
            return 100.0
        def option_bulk_greeks(self, t, e):
            return _rows()
        def option_bulk_oi(self, t, e):
            return [{"strike": r["strike"], "right": r["right"],
                     "open_interest": r["open_interest"]} for r in _rows()]
        def fetch_dividend_yield(self, t):
            return 0.0
        def list_expirations(self, t):
            return []
        def hist_stock_eod(self, t, a, b):
            return [{"close": 99.0}]
        def option_bulk_hist_eod_greeks(self, t, e, a, b):
            return [{"date": "20260819", "strike": 100000, "right": "C",
                     "implied_vol": 0.18, "oi": 500}]

    from expiry_book_production import fetch_production_result
    result = fetch_production_result(_TD(), "MOCK", "20270115")
    assert result.vanna_flow_live is None
    assert "PRIOR_CLOSE_ATM" not in result.vanna_flow_provenance
    assert result.residual_vanna_inventory is not None

def test_merge_then_normalize_does_not_double_scale_qqq_strikes():
    raw = [{"strike": 1_085_000, "right": "C", "open_interest": 10, "implied_vol": 0.20}]
    merged = _merge_greeks_oi(raw, raw)
    rows = normalize_snapshot_rows(merged)
    assert rows[0]["strike"] == 1085.0


def test_net_contracts_bid_ask_split():
    assert ebe.net_contracts_from_quote(100, 60, 40) == 20.0
    assert ebe.net_contracts_from_quote(100, 0, 0) == 0.0  # 50/50 → bought-sold=0


def test_prior_plus_flow_is_current(monkeypatch):
    prior = [
        {"strike": 100_000, "right": "C", "open_interest": 500, "implied_vol": 0.20},
        {"strike": 100_000, "right": "P", "open_interest": 400, "implied_vol": 0.22},
    ]
    quotes = [
        {"strike": 100.0, "right": "C", "volume": 100, "bid_size": 60, "ask_size": 40},
        {"strike": 100.0, "right": "P", "volume": 50, "bid_size": 10, "ask_size": 40},
    ]
    # Phase 6: flow layer now defaults OFF; this test's intent is the
    # prior-close book + intraday flow composition, which lives behind the
    # legacy toggle (deliberate default change, see PLAN §8.5 Phase 6).
    monkeypatch.setenv("EXPOSURE_BOOK_FLOW", "1")
    result = production_result_from_rows(
        "MOCK", "20270115", 101.0, prior, dte=150,
        quote_rows=quotes, prior_eod_rows=prior, prior_close=100.0, prior_asof="20260820",
    )
    assert result.prior_spot == 100.0
    assert result.flow_volume_rows == 2
    assert "prior_close" in result.flow_provenance
    call = next(r for r in result.snapshot.rows if r.right == "C")
    assert call.net_contracts == 20.0
    base = ebe.vannacharm_row(call, 100.0, "gamma")
    assert call.d_gex != 0.0
    current = base + call.d_gex
    assert current != base


def test_prior_eod_without_oi_keeps_live_book():
    live = [{"strike": 100_000, "right": "C", "open_interest": 500, "implied_vol": 0.20}]
    prior = [{"strike": 100000, "right": "C", "implied_vol": 0.20}]  # no OI
    result = production_result_from_rows(
        "MOCK", "20270115", 100.0, live, dte=150,
        prior_eod_rows=prior, prior_close=99.0, prior_asof="20260820",
    )
    assert result.prior_spot is None
    assert result.snapshot.rows[0].oi == 500.0
    assert result.snapshot.gex() != 0.0


def test_trades_to_quotes_buy_at_ask():
    from expiry_book_production import _trades_to_quote_rows
    trades = [{
        "expiration": "20261120", "strike_price": 345.0, "trade_right": "C",
        "size": 10, "price": 12.0, "bid": 11.0, "ask": 12.0,
    }]
    rows = _trades_to_quote_rows(trades, "20261120")
    assert len(rows) == 1
    assert rows[0]["volume"] == 10
    assert rows[0]["bid_size"] == 10  # bought
    assert rows[0]["ask_size"] == 0
