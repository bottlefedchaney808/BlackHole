"""Unit tests for the SVI smile fitter in options_chain_scanner.

fit_svi_smile must produce the same contract as the SABR/quadratic paths
(fit_iv / iv_residual_pts / is_edge / edge_kind) using the reusable svi_rp
module, and fall back to quadratic on degenerate input.
"""
import math

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def chain_df():
    """A realistic OTM SPY-like chain (IV negative skew)."""
    spot = 100.0
    rows = []
    for k in range(80, 100):  # OTM puts, skew
        iv = 0.25 - 0.15 * math.log(k / spot)
        rows.append({"strike": float(k), "right": "P", "iv": max(iv, 0.05),
                     "oi": 1000, "is_otm": True})
    for k in range(102, 125):  # OTM calls
        iv = 0.25 + 0.05 * math.log(k / spot)
        rows.append({"strike": float(k), "right": "C", "iv": max(iv, 0.05),
                     "oi": 1000, "is_otm": True})
    return pd.DataFrame(rows)


def _import_scanner():
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from options_chain_scanner import fit_svi_smile
    return fit_svi_smile


@pytest.mark.unit
def test_fit_svi_smile_contract(chain_df):
    fit = _import_scanner()
    df, smile_a, smile_b, svi_params = fit(chain_df, 100.0, 0.25, use_svi=True)
    # SVI should succeed on a healthy chain
    assert svi_params is not None
    assert "theta_t" in svi_params and "rho" in svi_params
    # contract columns present
    for col in ("fit_iv", "iv_residual_pts", "is_edge", "edge_kind"):
        assert col in df.columns
    # fit_iv populated where IV is
    valid = df[df["iv"].notna()]
    assert valid["fit_iv"].notna().sum() == len(valid)
    # edge_kind is rich or cheap only on is_edge rows
    assert set(df.loc[df["is_edge"], "edge_kind"]).issubset({"rich", "cheap"})
    # residuals are in vol-points (market - ref)
    check = df[df["is_otm"] & df["iv"].notna()].iloc[0]
    assert check["iv_residual_pts"] == pytest.approx((check["iv"] - check["fit_iv"]) * 100.0)


@pytest.mark.unit
def test_fit_svi_smile_degenerate_falls_back_to_quadratic(chain_df):
    fit = _import_scanner()
    # too few OTM strikes -> SVI off, quadratic fallback
    tiny = chain_df.head(3).copy()
    df, smile_a, smile_b, svi_params = fit(tiny, 100.0, 0.25, use_svi=True)
    assert svi_params is None  # fell back
    assert "fit_iv" in df.columns
