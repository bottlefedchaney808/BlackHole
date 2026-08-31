from strategy_recommender import StrategyRecommender


def _chain_data():
    return {
        "strikes": [95.0, 100.0, 105.0],
        "delta": [0.6, 0.5, 0.4],
        "gamma": [0.05, 0.06, 0.05],
        "theta": [-0.03, -0.03, -0.03],
        "vega": [0.15, 0.16, 0.15],
        "vanna": [0.01, 0.01, 0.01],
        "bid_ask_spread": [0.05, 0.05, 0.05],
        "open_interest": [500, 500, 500],
    }


def test_recommender_accepts_jump_risk_signal_and_defaults_to_none():
    rec = StrategyRecommender(
        chain_data=_chain_data(),
        edge_strikes=[{"strike": 100.0, "edge_type": "BUY"}],
        vol_regime="FAIR",
        current_price=100.0,
        expiry_days=30,
    )
    assert rec.jump_risk_signal is None


def test_recommender_elevated_jump_share_boosts_gamma_strategy_rank():
    kwargs = dict(
        chain_data=_chain_data(),
        edge_strikes=[{"strike": 100.0, "edge_type": "BUY"}],
        vol_regime="FAIR",
        current_price=100.0,
        expiry_days=30,
    )
    baseline = StrategyRecommender(**kwargs).recommend()
    with_jump_risk = StrategyRecommender(
        **kwargs,
        jump_risk_signal={"jump_variance_share": 0.5},
    ).recommend()

    baseline_straddle = next(s for s in baseline if s["strategy_type"] == "straddle")
    boosted_straddle = next(
        s for s in with_jump_risk if s["strategy_type"] == "straddle"
    )
    assert boosted_straddle["rank_score"] > baseline_straddle["rank_score"]
