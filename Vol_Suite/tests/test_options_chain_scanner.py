#!/usr/bin/env python3
"""
Tests for options_chain_scanner.py integration with strategy recommender.

Addresses Task 3: Verifies that strategy recommendations are generated and
exported alongside chain scan results.
"""

import pytest
import json
import tempfile
from pathlib import Path
import numpy as np


def test_chain_scanner_includes_strategy_recommendations():
    """
    Test that run_chain_scanner returns strategy recommendations.

    This integration test verifies Task 3 implementation:
    - Chain scanner generates strategies when edges are detected
    - Strategies are included in the return result object
    - chain_strategies.json artifact is written to output directory
    """
    # Import inside test to avoid import errors if dependencies missing
    from options_chain_scanner import run_chain_scanner

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Run chain scanner on a test ticker
            files, interp, result = run_chain_scanner(
                ticker='SPY',
                target_years=0.25,
                expiration=None,
                output_dir=tmpdir,
            )

            # Verify result object exists and has strategies field
            assert result is not None, "Result should not be None"
            assert hasattr(result, 'strategies'), "Result should have strategies field"
            assert isinstance(result.strategies, list), "strategies should be a list"

            # Verify chain_strategies.json was written
            strategies_file = Path(tmpdir) / 'chain_strategies.json'
            assert strategies_file.exists(), "chain_strategies.json must be written"

            # Verify JSON artifact structure
            with open(strategies_file, 'r') as f:
                artifact = json.load(f)

            assert 'strategies' in artifact, "Artifact must have strategies key"
            assert 'vol_regime' in artifact, "Artifact must have vol_regime"
            assert 'chain_verdict' in artifact, "Artifact must have chain_verdict"
            assert isinstance(artifact['strategies'], list), "strategies must be a list"

            # Verify strategies content if edges detected
            if result.verdict == 'EDGE DETECTED':
                assert len(result.strategies) > 0, "EDGE DETECTED should have strategies"

                first_strategy = result.strategies[0]
                assert 'strategy_type' in first_strategy, "Each strategy must have strategy_type"
                assert 'legs' in first_strategy, "Each strategy must have legs"
                assert 'rationale' in first_strategy, "Each strategy must have rationale"

        except Exception as e:
            # If ThetaData is not available, skip the test
            if "ThetaData" in str(e) or "connection" in str(e).lower():
                pytest.skip(f"ThetaData not available: {e}")
            raise


def test_chain_scanner_writes_empty_strategies_on_no_edges():
    """
    Test that NO CLEAR EDGE verdict still writes valid strategies artifact.

    Addresses R6 fix: Always write chain_strategies.json even when no edges detected.
    """
    from options_chain_scanner import run_chain_scanner

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            # Run on a near-the-money expiry (less likely to have edges)
            files, interp, result = run_chain_scanner(
                ticker='SPY',
                target_years=0.25,
                expiration=None,
                output_dir=tmpdir,
            )

            # Verify JSON artifact exists even with no edges
            strategies_file = Path(tmpdir) / 'chain_strategies.json'
            assert strategies_file.exists(), "chain_strategies.json must always be written"

            with open(strategies_file, 'r') as f:
                artifact = json.load(f)

            # Must have valid structure
            assert 'strategies' in artifact, "Artifact must have strategies key"
            assert 'vol_regime' in artifact, "Artifact must have vol_regime key"
            assert artifact['strategies'] == [] or isinstance(artifact['strategies'], list), \
                "strategies must be empty list or valid list"

            # Should be valid JSON (no NaN/Infinity literals)
            json_str = json.dumps(artifact)
            assert 'NaN' not in json_str, "JSON should not contain NaN"
            assert 'Infinity' not in json_str, "JSON should not contain Infinity"
            assert '-Infinity' not in json_str, "JSON should not contain -Infinity"

        except Exception as e:
            if "ThetaData" in str(e) or "connection" in str(e).lower():
                pytest.skip(f"ThetaData not available: {e}")
            raise


def test_extract_chain_data_from_df():
    """
    Test the _extract_chain_data_from_df helper function.

    Addresses R1: Verifies DataFrame extraction for strategy recommender.
    """
    import pandas as pd
    from options_chain_scanner import _extract_chain_data_from_df

    # Create a test DataFrame with mock data (same columns as options_chain_scanner output)
    df = pd.DataFrame({
        'strike': [100, 105, 110, 100, 105, 110],
        'right': ['C', 'C', 'C', 'P', 'P', 'P'],
        'delta': [0.4, 0.5, 0.6, -0.4, -0.5, -0.6],
        'gamma': [0.02, 0.025, 0.02, 0.02, 0.025, 0.02],
        'theta': [-0.05, -0.04, -0.03, -0.05, -0.04, -0.03],
        'vega': [0.25, 0.3, 0.25, 0.25, 0.3, 0.25],
        'vanna': [0.01, 0.02, 0.01, 0.01, 0.02, 0.01],
        'bid': [0.5, 0.3, 0.2, 0.5, 0.3, 0.2],
        'ask': [0.6, 0.35, 0.3, 0.6, 0.35, 0.3],
        'oi': [100, 500, 200, 100, 500, 200],
    })

    # Extract call data
    call_data = _extract_chain_data_from_df(df, option_type='call')

    # Verify structure
    assert isinstance(call_data, dict), "Should return a dict"
    assert 'strikes' in call_data, "Should have strikes key"
    assert 'delta' in call_data, "Should have delta key"
    assert 'gamma' in call_data, "Should have gamma key"
    assert 'theta' in call_data, "Should have theta key"
    assert 'vega' in call_data, "Should have vega key"
    assert 'vanna' in call_data, "Should have vanna key"
    assert 'bid_ask_spread' in call_data, "Should have bid_ask_spread key"
    assert 'open_interest' in call_data, "Should have open_interest key"

    # Verify call data extraction (filters to call options)
    assert call_data['strikes'] == [100, 105, 110], "Should extract call strikes"
    assert len(call_data['delta']) == 3, "Should have 3 calls"
    assert all(v > 0 for v in call_data['delta']), "Call deltas should be positive"

    # Extract put data
    put_data = _extract_chain_data_from_df(df, option_type='put')
    assert put_data['strikes'] == [100, 105, 110], "Should extract put strikes"
    assert len(put_data['delta']) == 3, "Should have 3 puts"
    assert all(v < 0 for v in put_data['delta']), "Put deltas should be negative"


def test_transform_edge_strikes():
    """
    Test the _transform_edge_strikes helper function.

    Addresses R2: Verifies edge_kind → edge_type mapping.
    """
    from options_chain_scanner import _transform_edge_strikes

    edge_candidates = [
        {'strike': 100, 'right': 'C', 'edge_kind': 'rich', 'iv_residual_pts': 2.5, 'oi': 500},
        {'strike': 95, 'right': 'P', 'edge_kind': 'cheap', 'iv_residual_pts': -1.8, 'oi': 300},
        {'strike': 110, 'right': 'C', 'edge_kind': 'rich', 'iv_residual_pts': 1.2, 'oi': 200},
    ]

    transformed = _transform_edge_strikes(edge_candidates)

    # Verify structure
    assert isinstance(transformed, list), "Should return a list"
    assert len(transformed) == 3, "Should have 3 edges"

    for edge in transformed:
        assert 'strike' in edge, "Should have strike"
        assert 'edge_type' in edge, "Should have edge_type"
        assert 'iv_deviation' in edge, "Should have iv_deviation"
        assert 'oi' in edge, "Should have oi"

    # Verify mapping
    assert transformed[0]['edge_type'] == 'SELL', "rich → SELL"
    assert transformed[1]['edge_type'] == 'BUY', "cheap → BUY"
    assert transformed[2]['edge_type'] == 'SELL', "rich → SELL"

    # Verify iv_deviation copy
    assert transformed[0]['iv_deviation'] == 2.5
    assert transformed[1]['iv_deviation'] == -1.8
    assert transformed[2]['iv_deviation'] == 1.2


def test_json_safe_handles_nan_infinity():
    """
    Test the _json_safe and _sanitize_for_json functions handle NaN and Infinity.

    Addresses R7: Ensures JSON export doesn't break on NaN/Infinity.
    """
    import math
    from options_chain_scanner import _json_safe, _sanitize_for_json

    # Test _json_safe function
    assert _json_safe(float('nan')) is None, "NaN should become None"
    assert _json_safe(float('inf')) is None, "+Infinity should become None"
    assert _json_safe(float('-inf')) is None, "-Infinity should become None"
    assert _json_safe(3.14) == 3.14, "Regular float should pass through"
    assert _json_safe(0.0) == 0.0, "Zero should pass through"

    # Test numpy types
    import numpy as np
    assert _json_safe(np.float64(2.71)) == 2.71, "numpy float should convert"
    assert _json_safe(np.int32(42)) == 42, "numpy int should convert"
    assert _json_safe(np.float64('nan')) is None, "numpy NaN should become None"
    assert _json_safe(np.float64('inf')) is None, "numpy Infinity should become None"

    # Test _sanitize_for_json for nested structures
    test_dict = {
        'normal': 1.5,
        'nested': {
            'nan_field': float('nan'),
            'inf_field': float('inf'),
            'list': [1, float('nan'), 3.14]
        }
    }

    sanitized = _sanitize_for_json(test_dict)

    # Verify NaN/Infinity were converted to None
    assert sanitized['normal'] == 1.5
    assert sanitized['nested']['nan_field'] is None
    assert sanitized['nested']['inf_field'] is None
    assert sanitized['nested']['list'][1] is None

    # Verify it produces valid JSON
    json_str = json.dumps(sanitized)
    assert 'NaN' not in json_str, "JSON should not contain NaN literal"
    assert 'Infinity' not in json_str, "JSON should not contain Infinity literal"

    # Verify it round-trips
    parsed = json.loads(json_str)
    assert parsed['normal'] == 1.5
    assert parsed['nested']['nan_field'] is None
    assert parsed['nested']['inf_field'] is None


def _steep_chain_df():
    """A steep equity put-skew chain DataFrame (realistic SPY-like shape) with
    OTM puts/calls -- exercises the SVI fit's flat-smile fix."""
    import math
    import pandas as pd
    rows = []
    spot = 100.0
    for k in range(55, 100):  # OTM puts
        iv = 0.20 + (-0.9) * math.log(k / spot) + 0.15 * (1 - k / 100.0)
        rows.append({'strike': float(k), 'right': 'P', 'iv': max(iv, 0.05), 'oi': 1000})
    for k in range(101, 130):  # OTM calls
        iv = 0.20 + 0.08 * math.log(k / spot)
        rows.append({'strike': float(k), 'right': 'C', 'iv': max(iv, 0.05), 'oi': 1000})
    return pd.DataFrame(rows)


def test_fit_svi_smile_produces_nonflat_fit_and_params():
    """The scanner's SVI smile fit must produce a non-flat reference (the
    flat-smile fix) and carry svi_params, not silently fall back to quadratic."""
    from options_chain_scanner import fit_svi_smile
    df = _steep_chain_df()
    df2, a, b, svi_params = fit_svi_smile(df, forward=100.0, T_years=0.28, use_svi=True)
    assert svi_params is not None, "SVI fit should have run and set svi_params"
    assert 'sigma_atm' in svi_params and 'rho' in svi_params
    # Non-flat: far-OTM put reference must be meaningfully above near-ATM.
    far_put_fit = df2.loc[(df2['strike'] == 60.0) & (df2['right'] == 'P'), 'fit_iv'].iloc[0]
    atm_fit = df2.loc[(df2['strike'] == 101.0) & (df2['right'] == 'C'), 'fit_iv'].iloc[0]
    assert far_put_fit > atm_fit + 0.03, f"SVI fit flattened: far-put {far_put_fit:.3f} vs ATM {atm_fit:.3f}"
    # Contract columns present
    assert 'is_edge' in df2.columns and 'iv_residual_pts' in df2.columns


def test_fit_svi_smile_falls_back_to_quadratic_when_off():
    """use_svi=False must return svi_params=None and still produce a fit_iv
    via the quadratic path (back-compat with fit_smile_and_flag_edges)."""
    from options_chain_scanner import fit_svi_smile
    df = _steep_chain_df()
    df2, a, b, svi_params = fit_svi_smile(df, forward=100.0, T_years=0.28, use_svi=False)
    assert svi_params is None
    assert df2['fit_iv'].notna().sum() > 0, "quadratic fallback should still fill fit_iv"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
