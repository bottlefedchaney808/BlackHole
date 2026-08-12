import json
import os
from Backtests.backtest_signals import (
    load_packs,
    run_signal_tournament,
    _score_for,
)
from Backtests.core import FakeController


def _write_pack(pack_dir, day, group, tickers):
    os.makedirs(os.path.join(pack_dir, day), exist_ok=True)
    pack = {
        "schema_version": 1,
        "group_id": group,
        "created_at": f"{day}T12:00:00Z",
        "tickers": [
            {"symbol": s, "cns": c, "war_score": w, "rank": i + 1}
            for i, (s, c, w) in enumerate(tickers)
        ],
    }
    with open(os.path.join(pack_dir, day, f"{group}.json"), "w") as f:
        json.dump(pack, f)


def _make_eod(symbol, start, base, drift):
    # daily closes from start, base + drift*i per trading day; real ThetaData
    # stock EOD rows carry 'created' (ISO) not 'date', and string closes.
    rows = []
    for i in range(8):
        day = f"{start + i:08d}"
        rows.append({
            "symbol": symbol,
            "created": f"{day[:4]}-{day[4:6]}-{day[6:8]}T17:15:00.000",
            "close": f"{base + drift * i:.4f}",
        })
    return rows


def _build_controller():
    # High-CNS names rise strongly; low-CNS names fall. Distinct symbols.
    eod = []
    eod += _make_eod(100000, 20260724, 100.0, 2.0)   # high score -> up
    eod += _make_eod(100001, 20260724, 100.0, 1.5)
    eod += _make_eod(100002, 20260724, 100.0, 1.0)
    eod += _make_eod(100003, 20260724, 100.0, 0.5)
    eod += _make_eod(100004, 20260724, 100.0, -1.0)  # low score -> down
    eod += _make_eod(100005, 20260724, 100.0, -1.5)
    eod += _make_eod(100006, 20260724, 100.0, -2.0)
    return FakeController(eod=eod)


def test_score_for():
    t = {"cns": 90, "war_score": 0.8, "rank": 3}
    assert _score_for(t, "CNS") == 90
    assert _score_for(t, "WAR") == 0.8
    assert _score_for(t, "RANK") == -3
    assert _score_for({}, "CNS") is None


def test_load_packs_dedup(tmp_path):
    _write_pack(str(tmp_path), "20260724", "a", [("AAA", 80, 0.7)])
    _write_pack(str(tmp_path), "20260724", "b", [("AAA", 90, 0.9)])
    _write_pack(str(tmp_path), "20260725", "c", [("BBB", 60, 0.5)])
    packs = load_packs(str(tmp_path))
    keys = {(d, s) for d, s, _ in packs}
    assert ("20260724", "AAA") in keys  # deduped across packs same day
    assert ("20260725", "BBB") in keys
    assert sum(1 for d, s, _ in packs if d == "20260724" and s == "AAA") == 1


def test_signal_tournament_known_relationship(tmp_path):
    _write_pack(str(tmp_path), "20260724", "g1", [
        ("100000", 95, 0.9), ("100001", 90, 0.8), ("100002", 85, 0.7), ("100003", 80, 0.6),
        ("100004", 20, 0.1), ("100005", 15, 0.05), ("100006", 10, 0.01),
    ])
    td = _build_controller()
    res = run_signal_tournament(td, str(tmp_path), forward_days=3)
    cns = res["strategies"]["CNS"]
    assert cns["degenerate"] is False
    assert cns["hit_rate"] is not None and cns["hit_rate"] > 0
    assert cns["corr"] is not None and cns["corr"] > 0.5
    assert res["n_pack_signals"] == 7


def test_signal_tournament_null_relationship(tmp_path):
    # all names rise equally -> hit rate ~ 0, corr ~ 0
    _write_pack(str(tmp_path), "20260724", "g2", [
        ("100000", 95, 0.9), ("100001", 90, 0.8), ("100002", 85, 0.7),
        ("100003", 20, 0.1), ("100004", 15, 0.05), ("100005", 10, 0.01),
    ])
    eod = []
    for s in range(100000, 100006):
        eod += _make_eod(s, 20260724, 100.0, 0.5)
    td = FakeController(eod=eod)
    res = run_signal_tournament(td, str(tmp_path), forward_days=3)
    cns = res["strategies"]["CNS"]
    assert abs(cns["hit_rate"]) < 1e-6


def test_signal_tournament_missing_pack_dir(tmp_path):
    td = _build_controller()
    res = run_signal_tournament(td, str(tmp_path / "nope"), forward_days=3)
    assert res["n_pack_signals"] == 0
    for s in res["strategies"].values():
        assert s["degenerate"] is True


def test_signal_harness_uses_current_pack_layout(tmp_path):
    pack_dir = tmp_path / "packs"
    day = "20260724"
    os.makedirs(pack_dir / day)
    ticker = {
        "symbol": "100000",
        "rank": 1,
        "cns": 90,
        "war_score": 0.8,
        "thesis_ratio": 0.2,
        "pump_ratio": 0.1,
        "volume": 10,
        "bullish_pct": 60.0,
        "bearish_pct": 20.0,
        "confidence": 0.7,
        "social_sources": ["stocktwits"],
    }
    pack = {
        "schema_version": 1,
        "version": 1,
        "group_id": "cns-threshold-alerts-example",
        "group_name": "cns-threshold-alerts",
        "created_at": "2026-07-24T12:00:00+00:00",
        "tickers": [ticker],
    }
    with open(pack_dir / day / "pack.json", "w", encoding="utf-8") as f:
        json.dump(pack, f)

    td = FakeController(eod=[
        {
            "created": "2026-07-24T17:15:00.000",
            "close": "100.0",
        },
        {
            "created": "2026-07-27T17:15:00.000",
            "close": "103.0",
        },
    ])
    result = run_signal_tournament(td, str(pack_dir), forward_days=1)
    assert result["n_pack_signals"] == 1
    assert result["strategies"]["CNS"]["n_valid"] == 1


def test_signal_harness_accepts_compact_created_timestamp(tmp_path):
    pack_dir = tmp_path / "packs"
    day = "20260724"
    os.makedirs(pack_dir / day)
    pack = {
        "created_at": "20260724T120000",
        "tickers": [{"symbol": "100000", "cns": 90}],
    }
    with open(pack_dir / day / "pack.json", "w", encoding="utf-8") as f:
        json.dump(pack, f)

    td = FakeController(eod=[
        {"created": "20260724171500", "close": "100.0"},
        {"created": "20260727171500", "close": "103.0"},
    ])
    result = run_signal_tournament(td, str(pack_dir), forward_days=1)

    assert result["strategies"]["CNS"]["n_valid"] == 1
