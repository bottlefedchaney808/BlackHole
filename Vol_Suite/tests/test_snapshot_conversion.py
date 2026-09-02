"""Network-free tests for Phase 6 snapshot conversion (plan §8.5).

The expiry exposure book is a PURE SNAPSHOT by default: the intraday
vannacharm flow layer is gated behind EXPOSURE_BOOK_FLOW=1 (legacy A/B and
back-compat mode). Follows the fake-rows pattern of test_band_wiring.py.
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


def _quote_rows():
    """Fake intraday trades matching snapshot strikes (dollar strikes)."""
    return [
        {"strike": 95.0, "right": "P", "volume": 120, "bid_size": 10, "ask_size": 5},
        {"strike": 100.0, "right": "C", "volume": 300, "bid_size": 20, "ask_size": 8},
        {"strike": 105.0, "right": "C", "volume": 0, "bid_size": 1, "ask_size": 1},
    ]


def _extra_rows():
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
    return {name: copy.deepcopy(getattr(result, name)) for name in PREEXISTING_FIELDS}


_KW = dict(
    extra_books=[{"expiry": "20271015", "rows": _extra_rows(), "dte": 120}],
    quote_rows=_quote_rows(),
)


def test_default_is_pure_snapshot(monkeypatch):
    """EXPOSURE_BOOK_FLOW unset: quote_rows are supplied but the flow layer
    is skipped — no flow stamped on the book, snapshot_only provenance."""
    monkeypatch.delenv("EXPOSURE_BOOK_FLOW", raising=False)
    result = _make_result(**_KW)
    assert result.status == "available"
    assert result.flow_volume_rows == 0
    assert result.flow_provenance.endswith("+snapshot_only")
    assert result.flow_layer == "snapshot_only"
    text = format_production_interp(result)
    assert "Intraday flow layer: ACTIVE" not in text
    # snapshot net_contracts untouched by the flow layer
    for r in result.snapshot.rows:
        assert r.net_contracts == 0
        assert r.d_gex == 0


def test_legacy_flow_toggle(monkeypatch):
    """EXPOSURE_BOOK_FLOW=1: byte-comparable to the pre-Phase-6 behavior."""
    monkeypatch.setenv("EXPOSURE_BOOK_FLOW", "1")
    result = _make_result(**_KW)
    assert result.status == "available"
    assert result.flow_volume_rows > 0  # 2 of the 3 fake quotes carry volume
    assert result.flow_layer == "legacy_flow"
    text = format_production_interp(result)
    assert "Intraday flow layer: ACTIVE (legacy mode)" in text


def test_legacy_flow_matches_pre_phase6_behavior(monkeypatch):
    """With the toggle ON, flow_volume_rows equals what apply_vannacharm_flow
    returns on the same quote rows directly (byte-comparable to old code)."""
    import expiry_book_exposure as ebe

    monkeypatch.setenv("EXPOSURE_BOOK_FLOW", "1")
    result = _make_result(**_KW)
    probe = ebe.build_net_exposure(
        __import__("expiry_book_production").normalize_snapshot_rows(_rows()),
        100.0,
        ticker="MOCK",
        expiry="20270115",
        T=30 / ebe.DEFAULT_A,
        dte=30,
        q=0.0,
    )
    expected = ebe.apply_vannacharm_flow(probe, _quote_rows(), 100.0)
    assert result.flow_volume_rows == expected
    assert expected == 2
    assert result.flow_provenance.endswith("+flow_analysis")


def test_legacy_flow_layer_composes_with_band(monkeypatch, fit_path):
    """Band + legacy flow compose: both layers populated simultaneously."""
    monkeypatch.setenv("EXPOSURE_BOOK_FLOW", "1")
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    result = _make_result(**_KW)
    assert result.band_n is not None
    assert result.flow_volume_rows > 0
    assert result.flow_layer == "legacy_flow"
    text = format_production_interp(result)
    assert "Band:" in text
    assert "Intraday flow layer: ACTIVE (legacy mode)" in text


def test_snapshot_layer_composes_with_band(monkeypatch, fit_path):
    """Band + snapshot compose: fields from both layers populated together."""
    monkeypatch.delenv("EXPOSURE_BOOK_FLOW", raising=False)
    monkeypatch.setenv("BAND_FIT_PATH", fit_path)
    result = _make_result(**_KW)
    assert result.status == "available"
    # band layer populated
    assert result.band_n is not None
    assert result.band_mu == pytest.approx(0.0)
    assert result.band_z == pytest.approx((result.band_n) / 1e9)
    assert result.band_regime in {
        "QUIET/ABSORBED",
        "EDGE APPROACH (volume regime)",
        "AT EDGE (release regime)",
    }
    # snapshot layer populated
    assert result.flow_volume_rows == 0
    assert result.flow_layer == "snapshot_only"
    assert result.flow_provenance.endswith("+snapshot_only")
    assert result.charm_1d == result.snapshot.net("charm")
    assert result.book_gamma == result.snapshot.book_gamma()
    text = format_production_interp(result)
    assert "Band:" in text
    assert "Intraday flow layer: ACTIVE" not in text


def test_flow_layer_default_on_dataclass():
    """Existing constructor sites stay valid: flow_layer defaults."""
    from expiry_book_production import ProductionDealerExposure

    assert ProductionDealerExposure.__dataclass_fields__["flow_layer"].default == (
        "snapshot_only"
    )


def test_preexisting_fields_identical_across_flow_layers(monkeypatch):
    """Default vs legacy differ ONLY in flow-layer fields — the rest of the
    book (LOCKED computations included) is untouched by the toggle."""
    monkeypatch.delenv("EXPOSURE_BOOK_FLOW", raising=False)
    snap = _make_result(**_KW)
    state_snap = _snapshot_state(snap)
    monkeypatch.setenv("EXPOSURE_BOOK_FLOW", "1")
    legacy = _make_result(**_KW)
    state_legacy = _snapshot_state(legacy)
    # flow fields intentionally differ
    assert state_snap.pop("flow_volume_rows") == 0
    assert state_legacy.pop("flow_volume_rows") > 0
    prov_s = state_snap.pop("flow_provenance")
    prov_l = state_legacy.pop("flow_provenance")
    assert prov_s.endswith("+snapshot_only")
    assert prov_l.endswith("+flow_analysis")
    # provenance dict differs only in intraday_flow — pop before comparing
    p_s = dict(state_snap.pop("provenance"))
    p_l = dict(state_legacy.pop("provenance"))
    assert p_s.pop("intraday_flow") != p_l.pop("intraday_flow")
    assert p_s == p_l
    assert state_snap.pop("units") == state_legacy.pop("units")
    # the snapshot object itself is stamped by the legacy layer
    # (net_contracts / d_gex / d_vex / d_cex); compare the snapshot via a
    # projection that excludes exactly those flow-stamped row fields.
    snap_obj = state_snap.pop("snapshot")
    leg_obj = state_legacy.pop("snapshot")
    FLOW_ROW_FIELDS = {"net_contracts", "d_gex", "d_vex", "d_cex"}

    def proj(s):
        return [
            {k: v for k, v in r.__dict__.items() if k not in FLOW_ROW_FIELDS}
            for r in s.rows
        ]

    assert proj(snap_obj) == proj(leg_obj)
    assert state_snap == state_legacy
