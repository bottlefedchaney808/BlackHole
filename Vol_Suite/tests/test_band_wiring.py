"""Network-free tests for Phase-2 band wiring in expiry_book_production.py.

The band is decoration on top of the exposure book (plan §3.3/§6.6):
- fit present -> band fields populated, interp gains one line;
- fit missing -> band fields None, provenance starts 'unavailable', book
  still status='available';
- every pre-existing field identical with and without a band fit
  (regression against silent mutation).
"""

import copy
import json

import pytest

from expiry_book_production import format_production_interp, production_result_from_rows


def _rows():
    return [
        {"strike": 95_000, "right": "P", "open_interest": 1000, "implied_vol": 0.25},
        {"strike": 100_000, "right": "C", "open_interest": 500, "implied_vol": 0.20},
        {"strike": 105_000, "right": "C", "open_interest": 2000, "implied_vol": 0.23},
    ]


def _extra_rows():
    """An 8+ row book so an extra bucket passes the len(erows) >= 8 gate.

    Carries explicit per-row 'delta' so net_delta_from_books sees the raw
    delta*oi*100 sum the band N is built from (ThetaData greeks rows carry
    delta; the normalized snapshot rows do not, but buckets.values() passes
    the normalized rows — see the wiring, which uses snapshot rows where
    delta lives on the per-strike greeks dict).
    """
    rows = []
    for i, k in enumerate(range(93_000, 108_000, 2_000)):
        right = "P" if k < 100_000 else "C"
        rows.append(
            {
                "strike": k,
                "right": right,
                "open_interest": 100 + 10 * i,
                "implied_vol": 0.22,
                "delta": -0.4 if right == "P" else 0.4,
            }
        )
    return rows


PREEXISTING_FIELDS = [
    "ticker",
    "expiry",
    "spot",
    "status",
    "snapshot",
    "execution_locus",
    "scenario_budget",
    "structural",
    "structural_regime",
    "svi_overlay",
    "vanna_flow_live",
    "vanna_flow_provenance",
    "charm_1d",
    "gex_reference",
    "book_gamma",
    "extra_books",
    "vendor_dealer",
    "d_iv_used",
    "residual_vanna_inventory",
    "flow_volume_rows",
    "flow_provenance",
    "prior_spot",
    "prior_asof",
    "units",
    "provenance",
    "accumulation_claimed",
]


@pytest.fixture
def fit_path(tmp_path):
    """A valid minimal bucketed_0_10 fit JSON; sigma chosen huge so the tiny
    synthetic book lands mid-band regardless of its N."""
    payload = {
        "definitions": {
            "bucketed_0_10": {
                "mu": 0.0,
                "sigma_eq": 1e9,
                "kappa": 41.6,
                "half_life_days": 4.2,
                "ar1": -0.2,
                "vr": {"2": 0.8, "5": 0.55},
                "n_days": 251,
            }
        },
        "active_for_live": "bucketed_0_10",
        "fit_window": {"first": "20250826", "last": "20260825"},
    }
    p = tmp_path / "ou_band_fit.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _make_result(**kwargs):
    return production_result_from_rows(
        "MOCK", "20270115", 100.0, _rows(), dte=30, **kwargs
    )


def _snapshot_state(result):
    """Comparable snapshot of every pre-existing field."""
    return {name: copy.deepcopy(getattr(result, name)) for name in PREEXISTING_FIELDS}


_KW = dict(extra_books=[{"expiry": "20271015", "rows": _extra_rows(), "dte": 120}])


def test_band_fields_default_absent_without_fit(tmp_path, monkeypatch):
    monkeypatch.setenv("BAND_FIT_PATH", str(tmp_path / "missing.json"))
    result = _make_result(**_KW)
    assert result.status == "available"
    for f in ("band_n", "band_mu", "band_sigma", "band_z", "band_regime"):
        assert getattr(result, f) is None
    assert result.band_fit_provenance.startswith("unavailable")
    # interp unchanged: no Band line when band unavailable
    assert "Band:" not in format_production_interp(result)
    # additive units/provenance entries always present
    assert result.units["band_n"] == "shares"
    assert "band_fit" in result.provenance


def test_band_fields_populated_with_fit(tmp_path, monkeypatch, fit_path):
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    result = _make_result(**_KW)
    assert result.status == "available"
    assert result.band_n is not None
    assert result.band_mu == pytest.approx(0.0)
    assert result.band_sigma == pytest.approx(1e9)
    assert result.band_z == pytest.approx(
        (result.band_n - result.band_mu) / result.band_sigma
    )
    assert result.band_regime in {
        "QUIET/ABSORBED",
        "EDGE APPROACH (volume regime)",
        "AT EDGE (release regime)",
    }
    prov = result.band_fit_provenance
    assert "band_fit_path=" in prov
    assert "band_fit_window=20260825" in prov
    assert "band_bucket_definition=0-10,20-45,80-180" in prov
    text = format_production_interp(result)
    assert "Band:" in text
    assert f"z={result.band_z:+.2f}" in text
    assert result.band_regime in text
    # mu/sd provenance read from the fit object: definition + fit window shown,
    # never a hardcoded full-book number
    assert "0-10,20-45,80-180" in text
    assert "thru 20260825" in text
    # the numeric values on the line are the fit-driven computation
    assert f"N=${result.band_n:,.0f}" in text


def test_preexisting_fields_identical_with_and_without_band(
    tmp_path, monkeypatch, fit_path
):
    """Same synthetic rows both ways; every non-band field must match."""
    monkeypatch.setenv("BAND_FIT_PATH", str(tmp_path / "nope.json"))
    a = _make_result(**_KW)
    state_a = _snapshot_state(a)
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    b = _make_result(**_KW)
    state_b = _snapshot_state(b)
    # provenance['band_fit'] is intentionally path-dependent (unavailable vs
    # fit provenance); every other pre-existing field must be identical.
    prov_a = dict(state_a.pop("provenance"))
    prov_b = dict(state_b.pop("provenance"))
    prov_a.pop("band_fit")
    prov_b.pop("band_fit")
    assert prov_a == prov_b
    assert state_a == state_b
    # and B actually exercised the band path
    assert b.band_n is not None
    assert a.band_fit_provenance.startswith("unavailable")
    # LOCKED computations byte-identical
    assert a.charm_1d == b.charm_1d
    assert a.gex_reference == b.gex_reference
    assert a.book_gamma == b.book_gamma


def test_charm_1d_unchanged_by_band_wiring(tmp_path, monkeypatch, fit_path):
    """charm_1d is LOCKED: identical with fit missing vs fit present."""
    monkeypatch.setenv("BAND_FIT_PATH", str(tmp_path / "missing2.json"))
    bare = _make_result()
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    fitted = _make_result()
    assert bare.charm_1d == fitted.charm_1d
    assert fitted.status == bare.status == "available"


def test_missing_fit_json_does_not_fail_book(tmp_path, monkeypatch):
    monkeypatch.setenv("BAND_FIT_PATH", str(tmp_path / "does_not_exist.json"))
    result = _make_result()
    assert result.status == "available"
    assert result.band_n is None
    assert result.band_fit_provenance.startswith("unavailable")


def test_corrupt_fit_json_does_not_fail_book(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("BAND_FIT_PATH", str(p))
    result = _make_result()
    assert result.status == "available"
    assert result.band_fit_provenance.startswith("unavailable")
    assert "Band:" not in format_production_interp(result)


def test_edge_line_when_abs_z_ge_1(tmp_path, monkeypatch):
    """Tiny sigma_eq forces |z| >= 1 -> the lumpy-hedge note must appear."""
    payload = {
        "definitions": {
            "bucketed_0_10": {
                "mu": 0.0,
                "sigma_eq": 1.0,
                "kappa": 50.0,
                "half_life_days": 5.0,
            }
        },
        "active_for_live": "bucketed_0_10",
        "fit_window": {"first": "20250826", "last": "20260824"},
    }
    p = tmp_path / "ou_band_fit.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("BAND_FIT_PATH", str(p))
    result = _make_result(**_KW)
    assert result.band_n is not None
    assert abs(result.band_z) >= 1
    text = format_production_interp(result)
    assert "Band:" in text
    assert "band edge: hedges go lumpy" in text
    assert "thru 20260824" in text
    assert "band_fit_window=20260824" in result.band_fit_provenance
    assert "band_bucket_definition=0-10,20-45,80-180" in result.band_fit_provenance


def test_quiet_regime_no_edge_line(tmp_path, monkeypatch, fit_path):
    """Huge sigma_eq keeps |z| < 1 -> Band line present, edge note absent."""
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    result = _make_result(**_KW)
    assert abs(result.band_z) < 1
    text = format_production_interp(result)
    assert "Band:" in text
    assert "hedges go lumpy" not in text
