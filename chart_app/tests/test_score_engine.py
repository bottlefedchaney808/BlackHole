from datetime import datetime, timedelta
from shared.chart_data import CandleRecord
from chart_app.score_engine import classic_overlays, price_scores, gated_markers

def _series(n, start=100.0):
    t0 = datetime(2026, 1, 2)
    out = []
    px = start
    for i in range(n):
        px = start + i * 0.2
        out.append(CandleRecord(t0 + timedelta(days=i), px, px + 0.5, px - 0.5, px, 1000))
    return out

def test_classic_overlays_length_and_warmup():
    recs = _series(60)
    ov = classic_overlays(recs)
    assert set(ov) == {"ema20", "ema50", "vwap", "bb_mid", "bb_upper", "bb_lower"}
    assert all(len(ov[k]) == 60 for k in ov)
    assert ov["ema20"][18] is None and ov["ema20"][19] is not None
    assert ov["ema50"][48] is None and ov["ema50"][49] is not None

def test_price_scores_whale_and_liq_stay_false():
    recs = _series(80)
    rows = price_scores(recs)
    assert len(rows) == 80
    assert all(r["signals"]["whale"] is False and r["signals"]["liquidity"] is False for r in rows)
    assert all(0 <= r["score"] <= 3 for r in rows)  # only 3 price legs can be True

def test_gated_markers_need_a_long():
    entries = [{"score": s} for s in [0, 3, 4, 3, 0, 0]]
    assert gated_markers(entries) == ["none", "none", "buy", "hold", "sell", "none"]
