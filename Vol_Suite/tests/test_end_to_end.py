"""
End-to-end integration tests for options strategy recommendations.

Tests cover:
- Full chain scan → edge detection → strategy recommendation flow
- JSON artifact generation and format validation
- Suite context integration with strategies field
- Options Suite format compatibility (R10)
- NaN/Infinity handling in JSON output
"""

import pytest
import json
import math
import tempfile
from pathlib import Path

import numpy as np

from Vol_Suite.options_chain_scanner import run_chain_scanner
from Vol_Suite.suite_context import build_suite_context, validate_suite_context


class TestEndToEndChainScanToStrategies:
    """Test complete flow from chain scan to strategy recommendations."""

    def test_end_to_end_chain_scan_to_strategies_with_edges(self):
        """Test full flow: chain scan → edges → strategies → JSON artifact."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Run chain scanner on a real ticker
            # run_chain_scanner returns (files, interp, result)
            files, interp, result = run_chain_scanner(
                ticker='QQQ',
                target_years=0.25,
                expiration=None,
                output_dir=output_dir,
            )

            # Verify chain scan completed
            assert result is not None
            assert hasattr(result, 'verdict')
            assert hasattr(result, 'regime')

            # Verify JSON artifact was written (even if no edges - R6 fix)
            strategies_file = Path(output_dir) / 'chain_strategies.json'
            assert strategies_file.exists(), "chain_strategies.json must always be written"

            with open(strategies_file, 'r') as f:
                strategies_artifact = json.load(f)

            # Verify artifact structure (always has these fields - R6)
            assert 'strategies' in strategies_artifact
            assert 'vol_regime' in strategies_artifact
            assert 'chain_verdict' in strategies_artifact
            assert isinstance(strategies_artifact['strategies'], list)

            # Verify strategies were generated if edges detected
            if result.verdict == 'EDGE DETECTED':
                assert hasattr(result, 'strategies')
                assert len(result.strategies) > 0, "EDGE DETECTED should have strategies"

                # Verify each strategy has required fields
                for strategy in strategies_artifact['strategies']:
                    assert 'strategy_type' in strategy
                    assert 'legs' in strategy
                    assert 'rationale' in strategy
                    assert 'greeks_summary' in strategy
                    assert len(strategy['legs']) >= 1
            else:
                # No edges: strategies should be empty but artifact exists (R6)
                assert len(result.strategies) == 0
                assert len(strategies_artifact['strategies']) == 0

    def test_end_to_end_no_edges_writes_empty_strategies(self):
        """Test that chain_strategies.json is always written with valid structure (addresses R6)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Run on a ticker - might or might not have edges depending on market
            files, interp, result = run_chain_scanner(
                ticker='SPY',
                target_years=0.25,
                expiration=None,
                output_dir=output_dir,
            )

            # Key R6 requirement: JSON artifact exists regardless of edge detection
            strategies_file = Path(output_dir) / 'chain_strategies.json'
            assert strategies_file.exists(), "chain_strategies.json must always be written"

            with open(strategies_file, 'r') as f:
                artifact = json.load(f)

            # Must have valid structure (even if strategies array is empty)
            assert 'strategies' in artifact, "Must have 'strategies' field"
            assert 'vol_regime' in artifact, "Must have 'vol_regime' field"
            assert 'chain_verdict' in artifact, "Must have 'chain_verdict' field"
            assert isinstance(artifact['strategies'], list), "strategies must be a list"

            # Strategies array content depends on whether edges detected, but structure is consistent
            # If no edges: strategies == []
            # If edges detected: strategies is non-empty with valid items
            if result.verdict == 'NO CLEAR EDGE':
                assert artifact['strategies'] == [], "Should have no strategies when no edges detected"

            # Should be valid JSON (no NaN/Infinity)
            json_str = json.dumps(artifact)
            assert 'NaN' not in json_str, "Should not contain NaN in JSON"
            assert 'Infinity' not in json_str, "Should not contain Infinity in JSON"


class TestStrategiesInSuiteContext:
    """Test that strategies flow from chain scan into suite context (addresses R5)."""

    def test_strategies_in_suite_context(self):
        """Test that strategies integrate with suite context using actual build_suite_context API."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Create a dummy sentiment manifest file
            sentiment_dir = Path(tmpdir) / "sentiment"
            sentiment_dir.mkdir()
            manifest_path = sentiment_dir / "manifest.json"
            manifest_path.write_text('{}')

            # Use actual build_suite_context API (R5 fix)
            context = build_suite_context(
                output_dir=output_dir,
                run_id='test_001',
                ticker='IWM',
                option_type='put',
                strike=None,
                target_years=0.25,
                expiration_date='2026-10-16',
                index_ticker='SPY',
                basket_tickers=['IWM'],
                basket_weights=[1.0],
                sentiment_manifest_path=str(manifest_path),
            )

            # Add strategies to context (simulating flow from chain_scan)
            context['strategies'] = [
                {
                    'strategy_type': 'put_spread',
                    'legs': [
                        {'instrument_type': 'put', 'strike': 200.0, 'quantity': -1},
                        {'instrument_type': 'put', 'strike': 195.0, 'quantity': 1},
                    ],
                    'vol_regime': 'RICH',
                    'rationale': 'Sell rich put spread',
                    'edge_strikes_used': [200.0],
                    'greeks_summary': {'delta': -0.4, 'gamma': 0.01, 'theta': 0.08, 'vega': -0.15, 'vanna': -0.02},
                    'rank_score': 0.90,
                }
            ]

            # Validate context (should pass R8 schema update)
            validate_suite_context(context)

            # Verify strategies preserved
            assert len(context['strategies']) == 1
            assert context['strategies'][0]['strategy_type'] == 'put_spread'

            # Should be JSON-serializable
            json_str = json.dumps(context)
            assert 'put_spread' in json_str

    def test_multiple_edges_generate_multiple_strategies(self):
        """Test that multiple edge strikes generate multiple strategy options."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            # Run on a high-vol ticker likely to have multiple edges
            files, interp, result = run_chain_scanner(
                ticker='AMD',
                target_years=0.25,
                expiration=None,
                output_dir=output_dir,
            )

            if result.verdict == 'EDGE DETECTED':
                strategies = result.strategies if hasattr(result, 'strategies') else []

                # With multiple edges, should have multiple strategy recommendations
                edge_candidates = result.edge_candidates if hasattr(result, 'edge_candidates') else []
                if len(edge_candidates) > 1:
                    assert len(strategies) >= len(edge_candidates), \
                        f"Expected {len(edge_candidates)} strategy groups, got {len(strategies)}"


class TestOptionssuiteIntegration:
    """Test integration with Options_Suite format (R10)."""

    def test_options_suite_can_parse_strategies_artifact(self):
        """
        Test that Options Suite can parse the strategies JSON artifact.

        Expected format for Options Suite consumption:
        - Each strategy has: strategy_type, legs, rationale, greeks_summary
        - Each leg has: instrument_type ('call'|'put'), strike, quantity
        - All numeric values are JSON-serializable (no NaN/Infinity)

        Reference: This format is designed to be consumed by Options_Suite
        for pricing and analysis of recommended strategies.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            files, interp, result = run_chain_scanner(
                ticker='TSLA',
                target_years=0.25,
                expiration=None,
                output_dir=output_dir,
            )

            strategies_file = Path(output_dir) / 'chain_strategies.json'
            assert strategies_file.exists()

            with open(strategies_file, 'r') as f:
                artifact = json.load(f)

            # Options Suite expected contract: strategies array
            assert isinstance(artifact['strategies'], list)

            for strategy in artifact.get('strategies', []):
                # Core fields for Options Suite pricing
                assert 'legs' in strategy, "Strategy must have 'legs' for Options Suite to price"
                assert 'strategy_type' in strategy
                assert 'rationale' in strategy

                # Legs must be priceable
                assert isinstance(strategy['legs'], list)
                for leg in strategy['legs']:
                    # Required fields for Options Suite pricing
                    assert 'instrument_type' in leg, "Leg must have instrument_type"
                    assert 'strike' in leg, "Leg must have strike"
                    assert 'quantity' in leg, "Leg must have quantity"

                    # Types must be correct
                    assert leg['instrument_type'] in ['call', 'put'], \
                        f"instrument_type must be 'call' or 'put', got {leg['instrument_type']}"
                    assert isinstance(leg['strike'], (int, float)), \
                        f"strike must be numeric, got {type(leg['strike'])}"
                    assert isinstance(leg['quantity'], int), \
                        f"quantity must be int, got {type(leg['quantity'])}"

                    # No NaN/Infinity (would break Options Suite pricing)
                    assert not (isinstance(leg['strike'], float) and (
                        math.isnan(leg['strike']) or math.isinf(leg['strike'])
                    )), "Strike contains NaN or Infinity"

                # Greeks should be numeric if present
                if 'greeks_summary' in strategy:
                    for greek_name, greek_value in strategy['greeks_summary'].items():
                        if greek_value is not None:
                            assert isinstance(greek_value, (int, float))
                            assert not (math.isnan(greek_value) or math.isinf(greek_value)), \
                                f"Greek {greek_name} contains NaN or Infinity"

    def test_strategies_artifact_json_clean(self):
        """Test that strategies artifact is JSON-clean (no NaN/Infinity - R10)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            files, interp, result = run_chain_scanner(
                ticker='IWM',
                target_years=0.25,
                expiration=None,
                output_dir=output_dir,
            )

            strategies_file = Path(output_dir) / 'chain_strategies.json'

            # Re-parse to ensure it's truly valid JSON (no NaN/Infinity)
            with open(strategies_file, 'r') as f:
                json_str = f.read()
                artifact = json.loads(json_str)

            # Should not contain invalid JSON literals
            assert 'NaN' not in json_str
            assert 'Infinity' not in json_str
            assert '-Infinity' not in json_str

            # Should round-trip cleanly
            json_str_2 = json.dumps(artifact)
            artifact_2 = json.loads(json_str_2)
            assert len(artifact['strategies']) == len(artifact_2['strategies'])
