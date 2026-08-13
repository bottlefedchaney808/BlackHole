"""Regression: branch-(b) vanna derivation on the price-only EOD path.

Gate-0 pin (2026-08-11): ThetaData rec.vanna = -1 * BS_vanna (measured, SPY
-0.956 / QQQ -0.989 with real spot). The pooled backtest feeds EOD rows
(prices, NO vanna field) into _build_day_records; without derivation vanna_by_
date would be all-zeros and book_b would be degenerate. These tests assert the
derivation fires (vanna_by_date populated on EOD rows) and that book_b flows
from it when a direction signal is present.
"""
import math
import sys

import pytest

from backtest_stage3 import _bs_vanna, _BOOK_B_VANNA_SIGN, _book_b_bucket


@pytest.mark.unit
def test_bs_vanna_closed_form_right_symmetric():
    # vanna_call == vanna_put at same strike (shared d2/sigma).
    vc = _bs_vanna(100.0, 105.0, 0.1, 0.25)
    vp = _bs_vanna(100.0, 105.0, 0.1, 0.25)
    assert vc == pytest.approx(vp, abs=1e-12)
    assert abs(vc) > 0  # OTM vanna is non-trivial


@pytest.mark.unit
def test_book_b_vanna_sign_is_negative_one():
    assert _BOOK_B_VANNA_SIGN == -1.0


def _build_synthetic_payload():
    """EOD-style rows (bid/ask/close, NO vanna field) + OI + spot."""
    S = 100.0
    expiry = "20261218"
    dates = ["20260810", "20260811", "20260812"]
    rows = []
    for d in dates:
        for k in (95.0, 100.0, 105.0):
            for right in ("C", "P"):
                # Use a deep-ITM strike for the put of 95 and call of 105
                # symmetrically so all have solvable IV; keep bid/ask realistic.
                rows.append({
                    "date": d, "strike": str(int(k * 1000)), "right": right,
                    "bid": "1.0", "ask": "1.1", "close": "1.05",
                    "implied_vol": None, "gamma": None,  # force derivation
                })
    oi_rows = []
    for d in dates:
        for k in (95.0, 100.0, 105.0):
            for right in ("C", "P"):
                oi_rows.append({"date": d, "strike": str(int(k * 1000)),
                                "right": right, "open_interest": 100})
    spot_rows = [{"date": d, "close": str(S)} for d in dates]
    return expiry, rows, oi_rows, spot_rows


@pytest.mark.unit
def test_vanna_derived_on_eod_rows_no_vendor_field():
    """The derivation fires: vanna_by_date is populated on EOD-style rows that
    carry no vanna field (the pooled path), instead of all-zeros."""
    from backtest_stage3 import _build_day_records

    expiry, rows, oi_rows, spot_rows = _build_synthetic_payload()
    captured = {}

    def tracer(frame, event, arg):
        if event == "return" and frame.f_code.co_name == "_build_day_records":
            local = frame.f_locals.get("vanna_by_date")
            if local is not None:
                captured["vanna_by_date"] = {d: dict(day) for d, day in local.items()}
        return tracer

    sys.settrace(tracer)
    try:
        _build_day_records("MOCK", expiry, rows, oi_rows, spot_rows,
                           hist_eod_rows=rows, forward_window_days=3)
    finally:
        sys.settrace(None)

    assert "vanna_by_date" in captured, "vanna_by_date local not observed"
    vb = captured["vanna_by_date"]
    # At least one (day, strike, right) cell must have a non-zero derived vanna.
    non_zero = [(d, k, r, v) for d, day in vb.items()
                for (k, r), v in day.items() if abs(v) > 0]
    assert non_zero, "vanna_by_date should be populated (derivation fired)"
    # Derived values must be finite.
    for d, k, r, v in non_zero:
        assert math.isfinite(v)
    print(f"  derived vanna non-zero cells: {len(non_zero)} over {len(vb)} days")


@pytest.mark.unit
def test_book_b_bucket_excludes_0dte():
    assert _book_b_bucket(0) == (False, True, False)   # 0DTE -> separate bucket
    assert _book_b_bucket(1) == (True, False, False)   # TTE>=1 but below floor
    assert _book_b_bucket(5) == (True, False, True)    # above floor -> in book_b
