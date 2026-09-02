"""Network-free tests for Vol_Suite/delta_band.py (Phase 1, plan §3.2/Phase 1)."""

import json
import os

import pytest

from Vol_Suite.delta_band import (
    REGIME_AT_EDGE,
    REGIME_EDGE_APPROACH,
    REGIME_QUIET,
    BandFit,
    append_history,
    band_position,
    load_band_fit,
    net_delta_from_books,
)


def _write_fit_json(tmp_path, payload, name="ou_band_fit.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.fixture
def valid_fit_payload():
    return {
        "definitions": {
            "bucketed_0_10": {
                "mu": 100.0,
                "sigma_eq": 50.0,
                "kappa": 41.6,
                "half_life_days": 4.2,
                "ar1": -0.2,
                "vr": {"2": 0.8, "5": 0.55},
                "n_days": 251,
            },
            "full_book": {
                "mu": 200.0,
                "sigma_eq": 90.0,
                "kappa": 39.1,
                "half_life_days": 6.0,
                "ar1": -0.177,
                "vr": {"40": 0.21},
            },
        },
        "active_for_live": "bucketed_0_10",
        "fit_window": {"first": "20250826", "last": "20260825"},
    }


class TestLoadBandFit:
    def test_valid_load(self, tmp_path, valid_fit_payload):
        path = _write_fit_json(tmp_path, valid_fit_payload)
        fit = load_band_fit(path)
        assert isinstance(fit, BandFit)
        assert fit.mu == 100.0
        assert fit.sigma_eq == 50.0
        assert fit.kappa == 41.6
        assert fit.half_life_days == 4.2
        assert fit.definition == "bucketed_0_10"
        assert fit.fit_window == {"first": "20250826", "last": "20260825"}
        assert fit.ar1 == pytest.approx(-0.2)
        assert fit.vr["2"] == pytest.approx(0.8)

    def test_definition_selects_block(self, tmp_path, valid_fit_payload):
        path = _write_fit_json(tmp_path, valid_fit_payload)
        fit = load_band_fit(path, definition="full_book")
        assert fit.mu == 200.0
        assert fit.definition == "full_book"

    def test_unknown_definition_raises(self, tmp_path, valid_fit_payload):
        path = _write_fit_json(tmp_path, valid_fit_payload)
        with pytest.raises(ValueError, match="nope"):
            load_band_fit(path, definition="nope")

    def test_missing_definition_raises(self, tmp_path):
        path = _write_fit_json(tmp_path, {"definitions": {}, "active_for_live": "x"})
        with pytest.raises(ValueError):
            load_band_fit(path, definition="bucketed_0_10")

    def test_missing_required_key_raises(self, tmp_path, valid_fit_payload):
        del valid_fit_payload["definitions"]["bucketed_0_10"]["half_life_days"]
        path = _write_fit_json(tmp_path, valid_fit_payload)
        with pytest.raises(ValueError, match="half_life_days"):
            load_band_fit(path)

    def test_nonpositive_sigma_eq_raises(self, tmp_path, valid_fit_payload):
        valid_fit_payload["definitions"]["bucketed_0_10"]["sigma_eq"] = 0.0
        path = _write_fit_json(tmp_path, valid_fit_payload)
        with pytest.raises(ValueError, match="sigma_eq"):
            load_band_fit(path)

    def test_negative_sigma_eq_raises(self, tmp_path, valid_fit_payload):
        valid_fit_payload["definitions"]["bucketed_0_10"]["sigma_eq"] = -5.0
        path = _write_fit_json(tmp_path, valid_fit_payload)
        with pytest.raises(ValueError, match="sigma_eq"):
            load_band_fit(path)

    @pytest.mark.parametrize("bad_hl", [0.0, -1.0, 60.0, 61.0, 100.0])
    def test_half_life_out_of_range_raises(self, tmp_path, valid_fit_payload, bad_hl):
        valid_fit_payload["definitions"]["bucketed_0_10"]["half_life_days"] = bad_hl
        path = _write_fit_json(tmp_path, valid_fit_payload)
        with pytest.raises(ValueError, match="half_life_days"):
            load_band_fit(path)

    @pytest.mark.parametrize("good_hl", [0.001, 1.0, 59.999])
    def test_half_life_in_range_ok(self, tmp_path, valid_fit_payload, good_hl):
        valid_fit_payload["definitions"]["bucketed_0_10"]["half_life_days"] = good_hl
        path = _write_fit_json(tmp_path, valid_fit_payload)
        assert load_band_fit(path).half_life_days == pytest.approx(good_hl)


class TestNetDeltaFromBooks:
    def test_hand_computed_two_contracts(self):
        # Contract A: 0.25 * 400 * 100 = 10,000
        # Contract B: -0.10 * 2500 * 100 = -25,000  -> net -15,000
        books = [
            {
                "spot": 760.0,
                "rows": [
                    {"delta": 0.25, "open_interest": 400},
                    {"delta": -0.10, "open_interest": 2500},
                ],
            }
        ]
        assert net_delta_from_books(books) == pytest.approx(-15_000.0)

    def test_multiple_books_sum(self):
        books = [
            {"rows": [{"delta": 0.5, "oi": 100}]},          # 5,000
            {"rows": [{"delta": -0.5, "open_interest": 100}]},  # -5,000
        ]
        assert net_delta_from_books(books) == pytest.approx(0.0)

    def test_skips_bad_rows(self):
        books = [
            {
                "rows": [
                    {"delta": None, "open_interest": 100},      # skip
                    {"delta": "abc", "open_interest": 100},     # skip (non-numeric str)
                    {"delta": 0.2, "open_interest": None},      # skip
                    {"delta": 0.2},                             # skip (no oi)
                    {"open_interest": 300},                     # skip (no delta)
                    {"delta": "0.3", "oi": "200"},              # keep: numeric strings
                ]
            }
        ]
        assert net_delta_from_books(books) == pytest.approx(0.3 * 200 * 100)

    def test_empty_books(self):
        assert net_delta_from_books([]) == 0.0
        assert net_delta_from_books([{"rows": []}, {"spot": 760.0}]) == 0.0


class TestBandPosition:
    @pytest.fixture
    def fit(self):
        return BandFit(mu=100.0, sigma_eq=50.0, kappa=41.6, half_life_days=4.2,
                       definition="bucketed_0_10")

    @pytest.mark.parametrize(
        "n, expected_regime",
        [
            (100.0, REGIME_QUIET),        # z = 0
            (149.0, REGIME_QUIET),        # z = 0.98
            (150.0, REGIME_EDGE_APPROACH),  # z = exactly 1.0
            (151.0, REGIME_EDGE_APPROACH),  # z = 1.02
            (200.0, REGIME_AT_EDGE),        # z = exactly 2.0
            (201.0, REGIME_AT_EDGE),        # z = 2.02
            (50.0, REGIME_EDGE_APPROACH),   # z = exactly -1.0
            (0.0, REGIME_AT_EDGE),          # z = exactly -2.0
            (-1.0, REGIME_AT_EDGE),         # z = -2.02
        ],
    )
    def test_regime_boundaries_exact(self, fit, n, expected_regime):
        pos = band_position(n, fit)
        assert pos.regime == expected_regime

    def test_dev_and_z_values(self, fit):
        pos = band_position(25.0, fit)
        assert pos.dev == pytest.approx(-75.0)
        assert pos.z == pytest.approx(-1.5)
        assert pos.n == 25.0


class TestAppendHistory:
    def test_appends_one_jsonl_line(self, tmp_path):
        path = tmp_path / "band_history.jsonl"
        append_history(str(path), {"ts": "T1", "N": -9e6, "z": -0.93})
        append_history(str(path), {"ts": "T2", "N": 1e6, "z": 0.1})
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        recs = [json.loads(ln) for ln in lines]
        assert recs[0]["ts"] == "T1" and recs[0]["N"] == -9e6
        assert recs[1]["ts"] == "T2"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "history.jsonl"
        append_history(str(path), {"a": 1})
        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}

    def test_malformed_existing_file_does_not_crash(self, tmp_path):
        path = tmp_path / "band_history.jsonl"
        path.write_text("this is not json{{{\n", encoding="utf-8")
        append_history(str(path), {"ts": "T3", "N": 5.0})  # must not raise
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1]) == {"ts": "T3", "N": 5.0}
