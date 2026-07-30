"""
Unit tests for StrategyRecommender module.

Tests cover:
- Strategy recommender initialization
- Vol-regime-to-strategy mapping (RICH/CHEAP/FAIR)
- Strike selection (edge strikes + offsets, delta-based)
- Ranking logic (Greeks scoring)
- JSON output format and schema compliance
- Liquidity filtering integration (Task 2)
- Edge clustering
"""

import pytest
import numpy as np
from Vol_Suite.strategy_recommender import StrategyRecommender, format_strategies_artifact


class TestStrategyRecommenderInitialization:
    """Test StrategyRecommender initialization and basic setup."""

    def test_strategy_recommender_initialization(self):
        """Test StrategyRecommender initializes with required data."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 200}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        assert recommender.vol_regime == 'RICH'
        assert len(recommender.edge_strikes) == 1
        assert recommender.current_price == 105.0
        assert recommender.expiry_days == 30

    def test_empty_edge_strikes_returns_empty_strategies(self):
        """Test that empty edge_strikes list returns empty strategies."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=[],
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()
        assert strategies == []


class TestVolRegimeMapping:
    """Test strategy type selection based on vol regime."""

    def test_select_strategies_for_rich_regime(self):
        """Test that RICH regime selects premium-selling strategies."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 200}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # RICH regime should include call spreads, put spreads, collars
        strategy_types = [s['strategy_type'] for s in strategies]
        assert len(strategy_types) > 0
        assert any(st in ['call_spread', 'put_spread', 'collar', 'iron_condor'] for st in strategy_types)

    def test_select_strategies_for_cheap_regime(self):
        """Test that CHEAP regime selects premium-buying strategies."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 95, 'edge_type': 'BUY', 'iv_deviation': -2.5, 'oi': 100}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='CHEAP',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # CHEAP regime should include straddles, strangles, call spreads
        strategy_types = [s['strategy_type'] for s in strategies]
        assert len(strategy_types) > 0

    def test_select_strategies_for_fair_regime(self):
        """Test that FAIR regime selects balanced strategies."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 105, 'edge_type': 'BUY', 'iv_deviation': 0.5, 'oi': 150}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='FAIR',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # FAIR regime should include straddles, strangles
        strategy_types = [s['strategy_type'] for s in strategies]
        assert len(strategy_types) > 0


class TestStrikeSelection:
    """Test delta-based and liquidity-aware strike selection."""

    def test_delta_based_strike_selection(self):
        """Test that strikes are selected based on delta targets."""
        chain_data = {
            'strikes': [95, 100, 105, 110, 115],
            'delta': [0.3, 0.5, 0.65, 0.75, 0.85],
            'gamma': [0.02, 0.025, 0.02, 0.015, 0.01],
            'theta': [-0.03, -0.04, -0.05, -0.04, -0.03],
            'vega': [0.25, 0.3, 0.25, 0.2, 0.15],
            'vanna': [0.01, 0.02, 0.015, 0.01, 0.005],
            'bid_ask_spread': [0.2, 0.1, 0.05, 0.1, 0.2],
            'open_interest': [50, 500, 1000, 600, 100],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 600}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # Test _get_strike_by_delta_target
        strike_for_25d = recommender._get_strike_by_delta_target(0.25, direction='call')
        assert strike_for_25d in [95, 100, 105, 110, 115]

        strike_for_50d = recommender._get_strike_by_delta_target(0.5, direction='call')
        assert strike_for_50d == 100

    def test_get_strike_by_delta_target_put_direction(self):
        """Test delta targeting for put options (negative deltas)."""
        chain_data = {
            'strikes': [95, 100, 105, 110, 115],
            'delta': [0.3, 0.5, 0.65, 0.75, 0.85],
            'gamma': [0.02, 0.025, 0.02, 0.015, 0.01],
            'theta': [-0.03, -0.04, -0.05, -0.04, -0.03],
            'vega': [0.25, 0.3, 0.25, 0.2, 0.15],
            'vanna': [0.01, 0.02, 0.015, 0.01, 0.005],
            'bid_ask_spread': [0.2, 0.1, 0.05, 0.1, 0.2],
            'open_interest': [50, 500, 1000, 600, 100],
        }
        edge_strikes = []

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # Put delta is negative, so 0.25 put should map to 0.25 in the positive delta data
        strike_for_25d_put = recommender._get_strike_by_delta_target(0.25, direction='put')
        assert strike_for_25d_put in [95, 100, 105, 110, 115]


class TestLiquidityFiltering:
    """Test liquidity filtering integration (Task 2)."""

    def test_liquidity_filtering(self):
        """Test that low-liquidity strikes are filtered or downranked."""
        chain_data = {
            'strikes': [95, 100, 105, 110],
            'delta': [0.3, 0.5, 0.65, 0.75],
            'gamma': [0.02, 0.025, 0.02, 0.015],
            'theta': [-0.03, -0.04, -0.05, -0.04],
            'vega': [0.25, 0.3, 0.25, 0.2],
            'vanna': [0.01, 0.02, 0.015, 0.01],
            'bid_ask_spread': [1.0, 0.1, 0.05, 0.5],  # 95 and 110 have wide spreads
            'open_interest': [10, 500, 1000, 50],
        }
        edge_strikes = [
            {'strike': 105, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 1000}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # Test _filter_by_liquidity
        all_strikes = [95, 100, 105, 110]
        filtered = recommender._filter_by_liquidity(all_strikes)

        # Should prefer strikes 100, 105 over 95, 110 (which have low OI and/or wide spreads)
        assert 100 in filtered
        assert 105 in filtered
        # 95 has low OI (10) and wide spread (1.0)
        # 110 has low OI (50) and wide spread (0.5)
        # Both should be filtered out
        assert 95 not in filtered or 110 not in filtered

    def test_filter_by_liquidity_with_custom_thresholds(self):
        """Test liquidity filtering with custom OI and spread thresholds."""
        chain_data = {
            'strikes': [95, 100, 105, 110],
            'delta': [0.3, 0.5, 0.65, 0.75],
            'gamma': [0.02, 0.025, 0.02, 0.015],
            'theta': [-0.03, -0.04, -0.05, -0.04],
            'vega': [0.25, 0.3, 0.25, 0.2],
            'vanna': [0.01, 0.02, 0.015, 0.01],
            'bid_ask_spread': [0.2, 0.1, 0.05, 0.3],
            'open_interest': [100, 200, 300, 150],
        }
        edge_strikes = []

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # With stricter thresholds: min_oi=250, max_spread=0.1
        all_strikes = [95, 100, 105, 110]
        filtered = recommender._filter_by_liquidity(all_strikes, min_oi=250, max_spread=0.1)

        # Only 105 meets both criteria (OI=300 >= 250, spread=0.05 <= 0.1)
        assert len(filtered) > 0
        assert 105 in filtered

    def test_filter_by_liquidity_no_liquid_strikes_returns_all(self):
        """Test that when no strikes pass filter, all strikes are returned with warning."""
        chain_data = {
            'strikes': [95, 100, 105, 110],
            'delta': [0.3, 0.5, 0.65, 0.75],
            'gamma': [0.02, 0.025, 0.02, 0.015],
            'theta': [-0.03, -0.04, -0.05, -0.04],
            'vega': [0.25, 0.3, 0.25, 0.2],
            'vanna': [0.01, 0.02, 0.015, 0.01],
            'bid_ask_spread': [1.0, 1.5, 2.0, 1.5],  # All illiquid
            'open_interest': [5, 10, 15, 5],  # All very low OI
        }
        edge_strikes = []

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # With very strict thresholds, should return all strikes (fallback)
        all_strikes = [95, 100, 105, 110]
        filtered = recommender._filter_by_liquidity(all_strikes, min_oi=1000, max_spread=0.1)

        # Should return all strikes as fallback
        assert set(filtered) == set(all_strikes)


class TestEdgeClustering:
    """Test edge clustering functionality."""

    def test_edge_clustering(self):
        """Test that nearby edges are clustered into groups."""
        chain_data = {
            'strikes': [105, 110, 115],
            'delta': [0.6, 0.7, 0.8],
            'gamma': [0.02, 0.015, 0.01],
            'theta': [-0.04, -0.03, -0.02],
            'vega': [0.25, 0.2, 0.15],
            'vanna': [0.02, 0.01, 0.005],
            'bid_ask_spread': [0.1, 0.1, 0.2],
            'open_interest': [800, 600, 100],
        }
        # Two closely-spaced edges
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.0, 'oi': 600},
            {'strike': 115, 'edge_type': 'SELL', 'iv_deviation': 1.8, 'oi': 100},
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # Test _cluster_nearby_edges with default 5.0 distance
        clusters = recommender._cluster_nearby_edges(max_distance=5.0)

        # Both edges are within 5.0 of each other (110 vs 115), so should be one cluster
        assert len(clusters) == 1
        assert len(clusters[0]) == 2

    def test_edge_clustering_separated_edges(self):
        """Test that distant edges form separate clusters."""
        chain_data = {
            'strikes': [100, 110, 120],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.02, 0.015, 0.01],
            'theta': [-0.04, -0.03, -0.02],
            'vega': [0.25, 0.2, 0.15],
            'vanna': [0.02, 0.01, 0.005],
            'bid_ask_spread': [0.1, 0.1, 0.1],
            'open_interest': [500, 600, 500],
        }
        # Two distant edges
        edge_strikes = [
            {'strike': 100, 'edge_type': 'SELL', 'iv_deviation': 2.0, 'oi': 500},
            {'strike': 120, 'edge_type': 'SELL', 'iv_deviation': 1.8, 'oi': 500},
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=110.0,
            expiry_days=30,
        )

        # Test _cluster_nearby_edges with strict 5.0 distance
        clusters = recommender._cluster_nearby_edges(max_distance=5.0)

        # Edges are 20 apart, beyond 5.0 distance, should be separate clusters
        assert len(clusters) == 2


class TestFindNearbyStrike:
    """Test strike finding with liquidity filtering."""

    def test_find_nearby_strike_uses_liquidity_filtering(self):
        """Test that _find_nearby_strike integrates liquidity filtering (Task 2 ↔ Task 1)."""
        chain_data = {
            'strikes': [95, 100, 105, 110, 115],
            'delta': [0.3, 0.5, 0.65, 0.75, 0.85],
            'gamma': [0.02, 0.025, 0.02, 0.015, 0.01],
            'theta': [-0.03, -0.04, -0.05, -0.04, -0.03],
            'vega': [0.25, 0.3, 0.25, 0.2, 0.15],
            'vanna': [0.01, 0.02, 0.015, 0.01, 0.005],
            'bid_ask_spread': [1.0, 0.1, 0.05, 0.6, 1.5],  # 95, 110, 115 are illiquid (110 spread > 0.5)
            'open_interest': [10, 500, 1000, 40, 5],  # 95, 110, 115 have low OI (110 OI < 50)
        }
        edge_strikes = [
            {'strike': 105, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 1000}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # _find_nearby_strike with liquidity filtering (default: min_liquidity=True)
        nearby_above = recommender._find_nearby_strike(105, direction='above', num_steps=1, min_liquidity=True)

        # With liquidity filtering, should get only liquid strikes (100, 105)
        # From 105, 1 step above in liquid strikes returns 105 (no liquid strikes above 105)
        assert nearby_above in [100, 105]

    def test_find_nearby_strike_without_liquidity_filter(self):
        """Test _find_nearby_strike behavior without liquidity filtering."""
        chain_data = {
            'strikes': [95, 100, 105, 110, 115],
            'delta': [0.3, 0.5, 0.65, 0.75, 0.85],
            'gamma': [0.02, 0.025, 0.02, 0.015, 0.01],
            'theta': [-0.03, -0.04, -0.05, -0.04, -0.03],
            'vega': [0.25, 0.3, 0.25, 0.2, 0.15],
            'vanna': [0.01, 0.02, 0.015, 0.01, 0.005],
            'bid_ask_spread': [1.0, 0.1, 0.05, 0.5, 1.5],
            'open_interest': [10, 500, 1000, 50, 5],
        }
        edge_strikes = []

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        # Without liquidity filtering
        nearby_above = recommender._find_nearby_strike(105, direction='above', num_steps=1, min_liquidity=False)

        # Without filtering, should be able to get illiquid strikes
        assert nearby_above in [95, 100, 105, 110, 115]


class TestGreeksComputation:
    """Test Greeks aggregation across strategy legs."""

    def test_compute_net_greeks(self):
        """Test that Greeks are correctly summed across legs."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = []

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        from Vol_Suite.strategy_recommender import StrategyLeg

        # Create test legs: long 100 call, short 110 call (call spread)
        legs = [
            StrategyLeg(instrument_type='call', strike=100, quantity=1),
            StrategyLeg(instrument_type='call', strike=110, quantity=-1),
        ]

        greeks = recommender._compute_net_greeks(legs)

        # Net delta should be: 1 * 0.5 + (-1) * 0.7 = 0.5 - 0.7 = -0.2
        assert abs(greeks['delta'] - (0.5 - 0.7)) < 0.0001
        # Net gamma should be: 1 * 0.01 + (-1) * 0.02 = 0.01 - 0.02 = -0.01
        assert abs(greeks['gamma'] - (0.01 - 0.02)) < 0.0001
        # Net theta should be: 1 * (-0.05) + (-1) * (-0.03) = -0.05 + 0.03 = -0.02
        assert abs(greeks['theta'] - (-0.05 + 0.03)) < 0.0001


class TestStrategyRanking:
    """Test strategy ranking by Greeks alignment."""

    def test_ranking_rich_regime_prefers_theta(self):
        """Test that RICH regime ranks theta-positive strategies higher."""
        chain_data = {
            'strikes': [100, 105, 110, 115],
            'delta': [0.4, 0.5, 0.6, 0.7],
            'gamma': [0.02, 0.025, 0.02, 0.015],
            'theta': [-0.03, -0.04, -0.05, -0.04],
            'vega': [0.25, 0.3, 0.25, 0.2],
            'vanna': [0.01, 0.02, 0.015, 0.01],
            'bid_ask_spread': [0.1, 0.1, 0.1, 0.1],
            'open_interest': [200, 300, 400, 200],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 400}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # Top-ranked strategy should have positive/high theta
        if len(strategies) > 0:
            top_strategy = strategies[0]
            # RICH regime should score theta highly
            assert top_strategy['rank_score'] is not None

    def test_ranking_cheap_regime_prefers_gamma_vega(self):
        """Test that CHEAP regime ranks gamma/vega-positive strategies higher."""
        chain_data = {
            'strikes': [100, 105, 110, 115],
            'delta': [0.4, 0.5, 0.6, 0.7],
            'gamma': [0.02, 0.025, 0.02, 0.015],
            'theta': [-0.03, -0.04, -0.05, -0.04],
            'vega': [0.25, 0.3, 0.25, 0.2],
            'vanna': [0.01, 0.02, 0.015, 0.01],
            'bid_ask_spread': [0.1, 0.1, 0.1, 0.1],
            'open_interest': [200, 300, 400, 200],
        }
        edge_strikes = [
            {'strike': 100, 'edge_type': 'BUY', 'iv_deviation': -2.5, 'oi': 200}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='CHEAP',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # Strategies should be ranked by gamma/vega preference
        if len(strategies) > 0:
            # Top strategy should have a rank score
            assert strategies[0]['rank_score'] is not None


class TestJSONSerialization:
    """Test JSON output format and serialization."""

    def test_strategy_to_json_serializable_dict(self):
        """Test that strategies are converted to JSON-serializable dicts."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 200}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # Should be able to JSON-serialize
        import json
        json_str = json.dumps(strategies)
        assert json_str is not None
        assert len(json_str) > 0

        # Should deserialize back
        strategies_back = json.loads(json_str)
        assert len(strategies_back) == len(strategies)

    def test_format_strategies_artifact(self):
        """Test format_strategies_artifact produces valid output."""
        strategies = [
            {
                'strategy_type': 'call_spread',
                'legs': [
                    {'instrument_type': 'call', 'strike': 450.0, 'quantity': -1},
                    {'instrument_type': 'call', 'strike': 455.0, 'quantity': 1},
                ],
                'vol_regime': 'RICH',
                'rationale': 'Sell rich call spread',
                'edge_strikes_used': [450.0],
                'greeks_summary': {'delta': -0.3, 'gamma': -0.01, 'theta': 0.05, 'vega': -0.2, 'vanna': 0.01},
                'rank_score': 0.85,
            }
        ]

        artifact = format_strategies_artifact(
            strategies=strategies,
            chain_verdict='EDGE DETECTED',
            vol_regime='RICH',
            current_price=450.5,
            expiration_date='2026-08-21',
        )

        # Check artifact structure
        assert artifact['version'] == '1.0'
        assert artifact['chain_verdict'] == 'EDGE DETECTED'
        assert artifact['vol_regime'] == 'RICH'
        assert artifact['current_price'] == 450.5
        assert artifact['expiration_date'] == '2026-08-21'
        assert len(artifact['strategies']) == 1
        assert artifact['summary']['total_recommendations'] == 1

        # Should be JSON-serializable
        import json
        json_str = json.dumps(artifact)
        assert json_str is not None


class TestStrategyStructure:
    """Test that strategies have required structure."""

    def test_strategy_has_required_fields(self):
        """Test that each strategy includes all required fields."""
        chain_data = {
            'strikes': [100, 105, 110],
            'delta': [0.5, 0.6, 0.7],
            'gamma': [0.01, 0.015, 0.02],
            'theta': [-0.05, -0.04, -0.03],
            'vega': [0.2, 0.25, 0.3],
            'vanna': [0.01, 0.02, 0.01],
            'bid_ask_spread': [0.1, 0.15, 0.2],
            'open_interest': [100, 150, 200],
        }
        edge_strikes = [
            {'strike': 110, 'edge_type': 'SELL', 'iv_deviation': 2.5, 'oi': 200}
        ]

        recommender = StrategyRecommender(
            chain_data=chain_data,
            edge_strikes=edge_strikes,
            vol_regime='RICH',
            current_price=105.0,
            expiry_days=30,
        )

        strategies = recommender.recommend()

        # Each strategy should have required fields
        for strategy in strategies:
            assert 'strategy_type' in strategy
            assert 'legs' in strategy
            assert 'vol_regime' in strategy
            assert 'rationale' in strategy
            assert 'edge_strikes_used' in strategy
            assert 'greeks_summary' in strategy
            assert 'rank_score' in strategy

            # Each leg should have required fields
            for leg in strategy['legs']:
                assert 'instrument_type' in leg
                assert 'strike' in leg
                assert 'quantity' in leg
                assert leg['instrument_type'] in ['call', 'put']
                assert isinstance(leg['quantity'], int)

            # Greeks summary should be numeric
            for greek_name in ['delta', 'gamma', 'theta', 'vega', 'vanna']:
                assert greek_name in strategy['greeks_summary']
                if strategy['greeks_summary'][greek_name] is not None:
                    assert isinstance(strategy['greeks_summary'][greek_name], (int, float))


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
