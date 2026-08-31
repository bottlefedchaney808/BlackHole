def test_production_dealer_exposure_carries_jump_variance_share():
    from expiry_book_production import ProductionDealerExposure

    # Construct with the minimum required fields plus the new optional one --
    # dataclass fields have no runtime type validation, so trivial/mock values
    # (None, {}) are fine for the required-but-untyped-at-runtime fields, matching
    # how test_expiry_book_phase1_greeks.py exercises other pieces of this module.
    exposure = ProductionDealerExposure(
        ticker="SPY",
        expiry="20270101",
        spot=500.0,
        status="ok",
        snapshot=None,
        execution_locus=None,
        scenario_budget=None,
        structural=None,
        units={},
        provenance={},
        jump_variance_share=0.35,
    )
    assert exposure.jump_variance_share == 0.35


def test_production_dealer_exposure_jump_variance_share_defaults_to_none():
    from expiry_book_production import ProductionDealerExposure

    exposure = ProductionDealerExposure(
        ticker="SPY",
        expiry="20270101",
        spot=500.0,
        status="ok",
        snapshot=None,
        execution_locus=None,
        scenario_budget=None,
        structural=None,
        units={},
        provenance={},
    )
    assert exposure.jump_variance_share is None
