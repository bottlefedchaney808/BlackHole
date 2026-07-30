"""Tests for the ticker-pack cache/export module."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from scanner.ticker_pack import (
    _entry_is_fresh,
    _load_manifest,
    _safe_float,
    _safe_int,
    _save_manifest,
    _utc_now,
    export_alert_group,
)


class TestSafeConverters:
    """_safe_float and _safe_int helpers."""

    def test_safe_float_valid(self) -> None:
        assert _safe_float("3.14") == 3.14
        assert _safe_float(42) == 42.0
        assert _safe_float(None) == 0.0
        assert _safe_float("bad") == 0.0

    def test_safe_float_custom_default(self) -> None:
        assert _safe_float(None, default=-1.0) == -1.0

    def test_safe_int_valid(self) -> None:
        assert _safe_int("42") == 42
        assert _safe_int(3.9) == 3
        assert _safe_int(None) == 0
        assert _safe_int("bad") == 0


class TestManifestLoadSave:
    """_load_manifest / _save_manifest file round-trips."""

    def test_load_missing_returns_default(self) -> None:
        manifest = _load_manifest("/does/not/exist.json")
        assert manifest == {"version": 1, "updated_at": "", "packs": []}

    def test_round_trip(self, tmp_path: Path) -> None:
        path = str(tmp_path / "manifest.json")
        manifest = {"version": 1, "updated_at": "", "packs": [{"group_id": "test-abc123"}]}
        _save_manifest(path, manifest)
        loaded = _load_manifest(path)
        assert loaded["version"] == 1
        assert len(loaded["packs"]) == 1
        assert loaded["packs"][0]["group_id"] == "test-abc123"

    def test_save_sets_updated_at(self, tmp_path: Path) -> None:
        path = str(tmp_path / "manifest.json")
        _save_manifest(path, {"version": 1, "updated_at": "", "packs": []})
        loaded = _load_manifest(path)
        assert loaded["updated_at"] != ""

    def test_load_invalid_json_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{invalid json")
        manifest = _load_manifest(str(path))
        assert manifest == {"version": 1, "updated_at": "", "packs": []}

    def test_load_non_dict_json_returns_default(self, tmp_path: Path) -> None:
        path = tmp_path / "array.json"
        path.write_text('["not", "a", "dict"]')
        manifest = _load_manifest(str(path))
        assert manifest == {"version": 1, "updated_at": "", "packs": []}


class TestEntryIsFresh:
    """_entry_is_fresh — TTL-based staleness check."""

    def test_fresh_with_last_updated_at(self) -> None:
        now = _utc_now()
        entry = {"last_updated_at": now.isoformat()}
        assert _entry_is_fresh(entry, ttl_days=14)

    def test_fresh_with_created_at_fallback(self) -> None:
        now = _utc_now()
        entry = {"created_at": now.isoformat()}
        assert _entry_is_fresh(entry, ttl_days=14)

    def test_stale_with_old_timestamp(self) -> None:
        old = (_utc_now() - timedelta(days=30)).isoformat()
        entry = {"last_updated_at": old}
        assert not _entry_is_fresh(entry, ttl_days=14)

    def test_no_timestamp_returns_false(self) -> None:
        assert not _entry_is_fresh({}, ttl_days=14)

    def test_invalid_timestamp_returns_false(self) -> None:
        assert not _entry_is_fresh({"last_updated_at": "not-a-date"}, ttl_days=14)

    def test_naive_datetime_is_interpreted_as_utc(self) -> None:
        """A naive ISO string (no Z/+00:00) should be treated as UTC."""
        now_naive = datetime.now()  # no tzinfo
        entry = {"last_updated_at": now_naive.isoformat()}
        assert _entry_is_fresh(entry, ttl_days=14)

    def test_ttl_of_zero_rejects_all(self) -> None:
        now = _utc_now().isoformat()
        entry = {"last_updated_at": now}
        assert not _entry_is_fresh(entry, ttl_days=0)


class TestExportAlertGroup:
    """export_alert_group — end-to-end ticker pack export."""

    def test_empty_alerts_returns_empty_dict(self) -> None:
        result = export_alert_group([])
        assert result == {}

    def test_alerts_without_tickers_returns_empty(self) -> None:
        result = export_alert_group([{"war_score": 0.5}])
        assert result == {}

    def test_basic_export_structure(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(sample_alerts)
        assert "group_id" in result
        assert "pack_json_path" in result
        assert "csv_path" in result
        assert result["ticker_count"] == 3
        assert result["ranked_tickers"] == ["TSLA", "AAPL", "GME"]  # sorted by CNS desc

    def test_pack_json_has_expected_fields(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(sample_alerts)
        json_path = result["pack_json_path"]
        with open(json_path, "r", encoding="utf-8") as f:
            pack = json.load(f)

        assert pack["schema_version"] == 1
        assert pack["version"] == 1
        assert pack["group_name"] == "cns-threshold-alerts"
        assert "created_at" in pack
        assert pack["downstream_hints"]["volatility_suite"] is True

        # Check every ticker has required fields
        for ticker in pack["tickers"]:
            for field in ("symbol", "rank", "cns", "war_score",
                          "thesis_ratio", "pump_ratio", "volume",
                          "bullish_pct", "bearish_pct", "confidence",
                          "social_sources"):
                assert field in ticker, f"Missing {field} in {ticker['symbol']}"

            assert isinstance(ticker["symbol"], str)
            assert isinstance(ticker["rank"], int) and ticker["rank"] > 0
            assert isinstance(ticker["cns"], int)
            assert isinstance(ticker["confidence"], (int, float))
            assert 0.0 <= ticker["confidence"] <= 1.0
            assert ticker["social_sources"] == ["stocktwits"]

    def test_csv_is_written(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(sample_alerts)
        csv_path = Path(result["csv_path"])
        assert csv_path.exists()
        content = csv_path.read_text(encoding="utf-8")
        assert "symbol,rank,cns,war_score" in content
        assert "TSLA" in content
        assert "AAPL" in content
        assert "GME" in content

    def test_manifest_is_updated(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(sample_alerts)
        manifest_path = Path(result["manifest_path"])
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["packs"]) == 1
        assert manifest["packs"][0]["group_id"] == result["group_id"]
        assert manifest["packs"][0]["ticker_count"] == 3

    def test_consecutive_calls_dedupe_manifest(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        """Exporting the same alerts again should replace the manifest entry,
        not duplicate it."""
        result1 = export_alert_group(sample_alerts)
        result2 = export_alert_group(sample_alerts)

        assert result1["group_id"] == result2["group_id"]

        manifest_path = Path(result2["manifest_path"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["packs"]) == 1  # deduped

    def test_custom_group_name(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(sample_alerts, group_name="my-custom-alerts")
        assert "my-custom-alerts" in result["group_id"]
        assert "my-custom-alerts" in result.get("pack_json_path", "")
        json_path = Path(result["pack_json_path"])
        pack = json.loads(json_path.read_text(encoding="utf-8"))
        assert pack["group_name"] == "my-custom-alerts"

    def test_source_run_id_propagates(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        result = export_alert_group(
            sample_alerts, source_run_id="manual-run-001",
        )
        json_path = Path(result["pack_json_path"])
        pack = json.loads(json_path.read_text(encoding="utf-8"))
        assert pack["source_run_id"] == "manual-run-001"

    def test_social_sources_propagate(
        self, sample_alerts: List[Dict[str, Any]],
    ) -> None:
        sources = ["stocktwits", "reddit", "youtube"]
        result = export_alert_group(sample_alerts, social_sources=sources)
        json_path = Path(result["pack_json_path"])
        pack = json.loads(json_path.read_text(encoding="utf-8"))
        for ticker in pack["tickers"]:
            assert ticker["social_sources"] == sources


class TestExportAlertGroupEmpty:
    """Edge cases for export_alert_group with empty/noisy inputs."""

    def test_single_alert_single_ticker(self) -> None:
        alerts = [
            {
                "ticker": "SPY",
                "contested_narrative_score": 80,
                "war_score": 0.7,
                "thesis_ratio": 0.5,
                "pump_ratio": 0.1,
                "volume": 100,
                "bullish_pct": 80.0,
                "bearish_pct": 10.0,
            },
        ]
        result = export_alert_group(alerts)
        assert result["ticker_count"] == 1
        assert result["ranked_tickers"] == ["SPY"]

    def test_alerts_with_missing_fields_dont_crash(self) -> None:
        """Alerts with missing numeric fields should default to 0 gracefully."""
        alerts = [
            {"ticker": "SPY"},  # bare minimum
            {"ticker": "QQQ", "contested_narrative_score": None, "war_score": "bad"},
        ]
        result = export_alert_group(alerts)
        assert result["ticker_count"] == 2
        json_path = Path(result["pack_json_path"])
        pack = json.loads(json_path.read_text(encoding="utf-8"))
        spy = next(t for t in pack["tickers"] if t["symbol"] == "SPY")
        assert spy["cns"] == 0
        assert spy["war_score"] == 0.0