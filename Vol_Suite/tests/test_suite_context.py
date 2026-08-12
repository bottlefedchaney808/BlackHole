import pytest

# NOTE: no sys.path manipulation here. The project root is put on sys.path for
# the whole suite by `pythonpath = .` in pytest.ini. This file used to do that
# insert itself, which silently fixed imports for every test module collected
# after it alphabetically and left the ones before it broken -- see pytest.ini.
from suite_context import (
    build_suite_context,
    read_suite_context,
    validate_suite_context,
    write_suite_context,
)


def _ctx(tmp_path, **overrides):
    """A valid baseline context, shaped exactly like the one
    volatility_suite.run_unified_flow() builds."""
    output_dir = tmp_path / "outputs" / "20260724_160000"
    output_dir.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        output_dir=str(output_dir),
        run_id="vsuite_20260724_160000",
        ticker="SPY",
        option_type="call",
        strike=None,
        target_years=0.25,
        expiration_date="2026-10-16",
        index_ticker="SPY",
        basket_tickers=["SPY", "AAPL", "MSFT"],
        basket_weights=[0.5, 0.3, 0.2],
        sentiment_manifest_path=str(tmp_path / "latest_manifest.json"),
    )
    kwargs.update(overrides)
    return build_suite_context(**kwargs)


@pytest.mark.unit
def test_suite_context_round_trip(tmp_path):
    output_dir = tmp_path / "outputs" / "20260724_160000"
    output_dir.mkdir(parents=True)
    context = build_suite_context(
        output_dir=str(output_dir),
        run_id="vsuite_20260724_160000",
        ticker="SPY",
        option_type="call",
        strike=None,
        target_years=0.25,
        expiration_date="2026-10-16",
        index_ticker="SPY",
        basket_tickers=["SPY", "AAPL"],
        basket_weights=[0.5, 0.5],
        sentiment_manifest_path=str(tmp_path / "latest_manifest.json"),
    )
    path = write_suite_context(context, str(output_dir / "suite_context.json"))
    loaded = read_suite_context(path)

    assert loaded["schema_version"] == 1
    assert loaded["focus"]["ticker"] == "SPY"
    assert loaded["focus"]["strike"] is None
    assert loaded["basket"]["tickers"] == ["SPY", "AAPL"]
    assert loaded["basket"]["weights"] == [0.5, 0.5]


@pytest.mark.unit
def test_suite_context_validation_rejects_missing_required_fields(tmp_path):
    output_dir = tmp_path / "outputs" / "20260724_160100"
    output_dir.mkdir(parents=True)
    context = build_suite_context(
        output_dir=str(output_dir),
        run_id="vsuite_20260724_160100",
        ticker="SPY",
        option_type="call",
        strike=None,
        target_years=0.25,
        expiration_date="2026-10-16",
        index_ticker="SPY",
        basket_tickers=["SPY"],
        basket_weights=[1.0],
        sentiment_manifest_path=str(tmp_path / "latest_manifest.json"),
    )
    del context["focus"]["expiration_date"]

    with pytest.raises(ValueError, match=r"focus\.expiration_date"):
        write_suite_context(context, str(output_dir / "suite_context.json"))


# ---------------------------------------------------------------------------
# Cross-suite handoff contract.
#
# These encode what the CHILD suites actually require, verified by reading
# their context parsers directly (Options_Suite/main.py::_extract_context_fields
# and VaR_Tools_Simulations/main.py::_build_corr_sim_from_context). They are
# regression tests for real breaks found on 2026-07-24, not hypotheticals.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_var_positions_is_null_not_empty_list_when_unsupplied(tmp_path):
    """THE bug that made every unified VaR run fail.

    VaR reads var.positions as: None => "derive proportional notionals from
    the basket weights"; a list => "these are the notionals", which it then
    length-checks against the basket. An empty list is not None, so it took
    the list branch and died with `Field 'positions' length must match basket
    length (N)` -- every time, since the unified flow never collects
    positions at all.
    """
    context = _ctx(tmp_path)
    assert context["var"]["positions"] is None, (
        "var.positions must be null when unsupplied -- an empty list makes "
        "VaR_Tools_Simulations take its explicit-notionals branch and fail "
        "the basket-length check"
    )


@pytest.mark.unit
def test_var_positions_when_supplied_must_match_basket_length(tmp_path):
    context = _ctx(tmp_path, var_positions=[1.0, 2.0, 3.0])
    assert context["var"]["positions"] == [1.0, 2.0, 3.0]

    context["var"]["positions"] = [1.0, 2.0]        # basket has 3
    with pytest.raises(ValueError, match=r"var\.positions"):
        validate_suite_context(context)


@pytest.mark.unit
def test_confidence_must_be_a_probability_not_a_percentage(tmp_path):
    """99 instead of 0.99 is numeric, plausible-looking, and produces
    nonsense. Reject it at build time rather than one subprocess later."""
    with pytest.raises(ValueError, match=r"var\.confidence"):
        _ctx(tmp_path, var_confidence=99)
    with pytest.raises(ValueError, match=r"var\.confidence"):
        _ctx(tmp_path, var_confidence=0.0)


@pytest.mark.unit
def test_basket_weights_must_sum_positive(tmp_path):
    """VaR normalizes by the weight sum -- a zero total is a divide-by-zero
    inside a child process."""
    with pytest.raises(ValueError, match=r"basket\.weights"):
        _ctx(tmp_path, basket_tickers=["SPY", "AAPL"], basket_weights=[0.0, 0.0])


@pytest.mark.unit
def test_expiration_date_normalizes_thetadata_format_to_iso(tmp_path):
    """expiry_selector yields ThetaData's compact "20261016"; Options_Suite
    parses this field with datetime.fromisoformat(), which only accepts the
    compact form on Python 3.11+. Storing one normalized format removes that
    version dependence instead of relying on every reader to handle both.
    """
    assert _ctx(tmp_path, expiration_date="20261016")["focus"]["expiration_date"] == "2026-10-16"
    assert _ctx(tmp_path, expiration_date="2026-10-16")["focus"]["expiration_date"] == "2026-10-16"

    for bad in ("2026-13-45", "10/16/2026", "not-a-date", ""):
        with pytest.raises(ValueError):
            _ctx(tmp_path, expiration_date=bad)


@pytest.mark.unit
def test_horizon_days_rejects_bool(tmp_path):
    """bool subclasses int in Python, so a bare isinstance(x, int) check
    accepts True as "a positive integer"."""
    context = _ctx(tmp_path)
    context["var"]["horizon_days"] = True
    with pytest.raises(ValueError, match=r"var\.horizon_days"):
        validate_suite_context(context)


@pytest.mark.unit
def test_suite_roots_are_not_hardcoded_to_one_machine(tmp_path):
    """These were absolute C:\\Users\\<name>\\... literals, which is what broke
    when this folder was renamed. They now resolve relative to the parent
    directory of the repo."""
    import suite_context as sc
    for root in (sc.DEFAULT_OPTIONS_SUITE_ROOT,
                 sc.DEFAULT_VAR_SUITE_ROOT,
                 sc.DEFAULT_SENTIMENT_SUITE_ROOT):
        assert "bottl" not in root.lower() or "FinancialDevelopment" in root, root
    assert _ctx(tmp_path)["paths"]["options_suite_root"].endswith("Options_Suite")


# ---------------------------------------------------------------------------
# Task 4: Strategies field validation (chain scan integration)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_suite_context_includes_strategies_with_empty_list(tmp_path):
    """Test that suite context includes strategies field with empty list by default."""
    context = _ctx(tmp_path)
    assert "strategies" in context
    assert isinstance(context["strategies"], list)
    assert context["strategies"] == []


@pytest.mark.unit
def test_suite_context_validates_strategies_field(tmp_path):
    """Test that suite context validates strategies field using actual build_suite_context."""
    context = _ctx(tmp_path)
    # Add a valid strategy
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "call", "strike": 450.0, "quantity": -1},
                {"instrument_type": "call", "strike": 455.0, "quantity": 1},
            ],
            "vol_regime": "RICH",
            "rationale": "Sell rich 450.0 call, buy 455.0 call for protection",
            "edge_strikes_used": [450.0],
            "greeks_summary": {"delta": -0.3, "gamma": -0.01, "theta": 0.05, "vega": -0.2, "vanna": 0.01},
            "rank_score": 0.85,
        }
    ]
    # Should validate without error
    validate_suite_context(context)
    assert len(context["strategies"]) == 1
    assert context["strategies"][0]["strategy_type"] == "call_spread"


@pytest.mark.unit
def test_suite_context_with_empty_strategies(tmp_path):
    """Test that suite context handles empty strategies list (no edges detected)."""
    context = _ctx(tmp_path)
    context["strategies"] = []
    # Should validate without error
    validate_suite_context(context)
    assert context["strategies"] == []


@pytest.mark.unit
def test_suite_context_strategies_rejects_invalid_vol_regime(tmp_path):
    """Test that strategies validation rejects invalid vol_regime."""
    context = _ctx(tmp_path)
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "call", "strike": 450.0, "quantity": -1},
            ],
            "vol_regime": "INVALID",  # Should be RICH, CHEAP, or FAIR
            "rationale": "Test strategy",
        }
    ]
    with pytest.raises(ValueError, match=r"vol_regime"):
        validate_suite_context(context)


@pytest.mark.unit
def test_suite_context_strategies_rejects_invalid_instrument_type(tmp_path):
    """Test that leg validation rejects invalid instrument_type."""
    context = _ctx(tmp_path)
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "stock", "strike": 450.0, "quantity": -1},  # Should be call or put
            ],
            "vol_regime": "RICH",
            "rationale": "Test strategy",
        }
    ]
    with pytest.raises(ValueError, match=r"instrument_type"):
        validate_suite_context(context)


@pytest.mark.unit
def test_suite_context_strategies_rejects_non_numeric_strike(tmp_path):
    """Test that leg validation rejects non-numeric strike."""
    context = _ctx(tmp_path)
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "call", "strike": "invalid", "quantity": -1},  # Should be numeric
            ],
            "vol_regime": "RICH",
            "rationale": "Test strategy",
        }
    ]
    with pytest.raises(ValueError, match=r"strike"):
        validate_suite_context(context)


@pytest.mark.unit
def test_suite_context_strategies_rejects_non_integer_quantity(tmp_path):
    """Test that leg validation rejects non-integer quantity."""
    context = _ctx(tmp_path)
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "call", "strike": 450.0, "quantity": 1.5},  # Should be integer
            ],
            "vol_regime": "RICH",
            "rationale": "Test strategy",
        }
    ]
    with pytest.raises(ValueError, match=r"quantity"):
        validate_suite_context(context)


@pytest.mark.unit
def test_suite_context_strategies_with_multiple_regimes(tmp_path):
    """Test that suite context validates multiple strategies with different regimes."""
    context = _ctx(tmp_path)
    context["strategies"] = [
        {
            "strategy_type": "call_spread",
            "legs": [
                {"instrument_type": "call", "strike": 450.0, "quantity": -1},
                {"instrument_type": "call", "strike": 455.0, "quantity": 1},
            ],
            "vol_regime": "RICH",
            "rationale": "Sell rich call spread",
        },
        {
            "strategy_type": "straddle",
            "legs": [
                {"instrument_type": "call", "strike": 450.0, "quantity": 1},
                {"instrument_type": "put", "strike": 450.0, "quantity": 1},
            ],
            "vol_regime": "CHEAP",
            "rationale": "Buy cheap straddle",
        },
        {
            "strategy_type": "iron_condor",
            "legs": [
                {"instrument_type": "call", "strike": 460.0, "quantity": -1},
                {"instrument_type": "call", "strike": 465.0, "quantity": 1},
                {"instrument_type": "put", "strike": 440.0, "quantity": -1},
                {"instrument_type": "put", "strike": 435.0, "quantity": 1},
            ],
            "vol_regime": "FAIR",
            "rationale": "Fair vol iron condor",
        },
    ]
    # Should validate without error
    validate_suite_context(context)
    assert len(context["strategies"]) == 3


@pytest.mark.unit
def test_suite_context_round_trip_with_strategies(tmp_path):
    """Test that strategies survive a round-trip write/read cycle."""
    output_dir = tmp_path / "outputs" / "20260724_170000"
    output_dir.mkdir(parents=True)

    context = build_suite_context(
        output_dir=str(output_dir),
        run_id="vsuite_20260724_170000",
        ticker="SPY",
        option_type="call",
        strike=None,
        target_years=0.25,
        expiration_date="2026-10-16",
        index_ticker="SPY",
        basket_tickers=["SPY", "AAPL"],
        basket_weights=[0.5, 0.5],
        sentiment_manifest_path=str(tmp_path / "latest_manifest.json"),
    )

    # Add strategies to context
    context["strategies"] = [
        {
            "strategy_type": "put_spread",
            "legs": [
                {"instrument_type": "put", "strike": 440.0, "quantity": -1},
                {"instrument_type": "put", "strike": 435.0, "quantity": 1},
            ],
            "vol_regime": "RICH",
            "rationale": "Sell rich put spread",
            "edge_strikes_used": [440.0],
            "greeks_summary": {"delta": -0.4, "gamma": 0.01, "theta": 0.08, "vega": -0.15, "vanna": -0.02},
            "rank_score": 0.90,
        }
    ]

    # Write and read
    path = write_suite_context(context, str(output_dir / "suite_context.json"))
    loaded = read_suite_context(path)

    # Verify strategies survived
    assert "strategies" in loaded
    assert len(loaded["strategies"]) == 1
    assert loaded["strategies"][0]["strategy_type"] == "put_spread"
    assert loaded["strategies"][0]["vol_regime"] == "RICH"
    assert loaded["strategies"][0]["rank_score"] == 0.90
    assert len(loaded["strategies"][0]["legs"]) == 2


@pytest.mark.unit
def test_garch_conditional_vol_included_when_given(tmp_path):
    context = _ctx(tmp_path, garch_conditional_vol=0.31)
    assert context["focus"]["garch_conditional_vol"] == pytest.approx(0.31)
    validate_suite_context(context)


@pytest.mark.unit
def test_garch_conditional_vol_defaults_to_none(tmp_path):
    context = _ctx(tmp_path)
    assert context["focus"]["garch_conditional_vol"] is None
    validate_suite_context(context)


@pytest.mark.unit
def test_garch_conditional_vol_survives_round_trip(tmp_path):
    output_dir = tmp_path / "outputs" / "20260812_090000"
    output_dir.mkdir(parents=True)
    context = _ctx(tmp_path, output_dir=str(output_dir), garch_conditional_vol=0.2875)
    loaded = read_suite_context(
        write_suite_context(context, str(output_dir / "suite_context.json")))
    assert loaded["focus"]["garch_conditional_vol"] == pytest.approx(0.2875)


@pytest.mark.unit
def test_validate_suite_context_rejects_non_numeric_garch_conditional_vol(tmp_path):
    context = _ctx(tmp_path)
    context["focus"]["garch_conditional_vol"] = "0.31"
    with pytest.raises(ValueError, match="garch_conditional_vol"):
        validate_suite_context(context)
