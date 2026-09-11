def test_vrp_term_point_has_model_implied_vrp_field_defaulting_to_none():
    from vrp_term_structure import VrpTermPoint

    point = VrpTermPoint(
        expiry_label="1mo",
        expiry_date="20270101",
        T_years=1 / 12,
        fair_vol_pct=20.0,
        atm_iv_pct=19.0,
        convexity_pct=1.0,
        rv_30d_pct=18.0,
    )
    assert point.model_implied_vrp_pct is None


def test_vrp_term_point_accepts_model_implied_vrp():
    from vrp_term_structure import VrpTermPoint

    point = VrpTermPoint(
        expiry_label="1mo",
        expiry_date="20270101",
        T_years=1 / 12,
        fair_vol_pct=20.0,
        atm_iv_pct=19.0,
        convexity_pct=1.0,
        rv_30d_pct=18.0,
        model_implied_vrp_pct=1.3,
    )
    assert point.model_implied_vrp_pct == 1.3
