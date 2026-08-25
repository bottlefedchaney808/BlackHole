"""Phase 6 — Pre-registered falsifier (GEX flow -> forward returns).

Plan v2 §5 Phase 6: network-free on the offline seed corpus, with a _FakeTD
pattern for synthetic-ticker smoke tests. Pre-registered: primary GEX flow ->
forward 1-day return sign, n_tickers >= 12, n_days >= 100/ticker,
|corr| >= threshold, block-perm p < 0.05, Bonferroni/BH q < 0.10, and a
SPY/QQQ sign-consistency rule (a flip = INCONCLUSIVE/FAIL). The smoke tests
assert the harness runs and the verdict taxonomy is honored — they do NOT
bake a particular statistical verdict into a unit test.
"""

import os

import expiry_book_exposure as ebe


def _theta(k):
    return int(round(k * 1000))


def _build_multiday(n_days=120, seed=1):
    """Synthetic multi-day chain (network-free _FakeTD-style controller)."""
    import datetime
    import random

    rng = random.Random(seed)
    expiry = "20261201"
    strikes = list(range(70, 131))
    dates = [
        (datetime.date(2026, 1, 1) + datetime.timedelta(days=i)).strftime("%Y%m%d")
        for i in range(n_days)
    ]
    spot = 100.0
    spots = {}
    for d in dates:
        spot += rng.uniform(-1.5, 1.8)
        spots[d] = spot
    greeks, oi, spotrows = [], [], []
    base_oi = {k: 150 for k in strikes}
    for d in dates:
        s = spots[d]
        spotrows.append({"date": d, "close": s})
        for k in strikes:
            m = k / s
            iv = max(0.15 + 0.10 * max(0, 1 - m) + rng.uniform(-0.01, 0.01), 0.08)
            for right in ("C", "P"):
                greeks.append(
                    {"date": d, "strike": _theta(k), "right": right, "implied_vol": iv}
                )
            oi.append(
                {
                    "date": d,
                    "strike": _theta(k),
                    "right": "C",
                    "open_interest": max(base_oi[k] + rng.randint(-50, 400), 0),
                }
            )
            oi.append(
                {
                    "date": d,
                    "strike": _theta(k),
                    "right": "P",
                    "open_interest": base_oi[k],
                }
            )
            base_oi[k] = max(base_oi[k] + rng.randint(-50, 400) // 3, 50)
    return expiry, greeks, oi, spotrows


def test_daily_signals_built_from_synthetic_multiday():
    expiry, greeks, oi, spot = _build_multiday(n_days=120)
    sig = ebe.build_daily_signals(greeks, oi, spot, expiry, ticker="MOCK")
    assert sig.n_days() > 0
    assert len(sig.gex_flow) == len(sig.fwd_ret_sign) == sig.n_days()
    assert all(len(a) == sig.n_days() for a in (sig.vanna_flow, sig.charm))


def _synth_signals(ticker, polarity):
    n = 120
    fwd = [1.0 if i % 2 == 0 else -1.0 for i in range(n)]
    gex = [polarity * f for f in fwd]
    return ebe.DailySignals(
        ticker=ticker,
        dates=[f"D{i}" for i in range(n)],
        gex_flow=gex,
        vanna_flow=[0.0] * n,
        charm=[0.0] * n,
        fwd_ret=[0.0] * n,
        fwd_ret_sign=fwd,
        iv_shock=[0.0] * n,
    )


def test_falsifier_harness_runs_and_taxonomy_honored():
    """The harness runs end-to-end and every verdict is in the taxonomy."""
    tickers = {f"T{i:02d}": _synth_signals(f"T{i:02d}", +1.0) for i in range(12)}
    tickers["SPY"] = _synth_signals("SPY", +1.0)
    tickers["QQQ"] = _synth_signals("QQQ", +1.0)
    run = ebe.run_expiry_falsifier(tickers, n_perms=100, corr_threshold=0.15)
    assert run.overall in ebe.VERDICTS
    assert len(run.primary_gex) == len(tickers)
    for v in run.primary_gex.values():
        assert v.verdict in ebe.VERDICTS
        assert v.n_days == 120
    assert isinstance(run.spy_qqq_sign_consistent, bool)


def test_spy_qqq_sign_flip_is_sign_flip_never_pass():
    """A SPY/QQQ sign flip on the same construct = SIGN_FLIP, never a pass."""
    tickers = {"SPY": _synth_signals("SPY", +1.0), "QQQ": _synth_signals("QQQ", -1.0)}
    run = ebe.run_expiry_falsifier(
        tickers, n_perms=100, corr_threshold=0.15, min_tickers=1
    )
    assert run.overall == "SIGN_FLIP"
    assert run.spy_qqq_sign_consistent is False


def test_falsifier_force_bypasses_cache():
    """FALSIFIER_FORCE=1 bypasses the cache; cached runs are flagged."""
    import expiry_book_exposure as ebe

    tickers = {"SPY": _synth_signals("SPY", +1.0)}
    os.environ[ebe.FALSIFIER_FORCE] = "1"
    try:
        r1 = ebe.run_expiry_falsifier_cached(
            tickers, n_perms=100, corr_threshold=0.5, force_recompute=True
        )
        # write the cache, then a force run must NOT hit it
        r2 = ebe.run_expiry_falsifier_cached(
            tickers, n_perms=100, corr_threshold=0.5, force_recompute=True
        )
        assert "CACHE_HIT" not in r2.notes
    finally:
        os.environ.pop(ebe.FALSIFIER_FORCE, None)
    # now (no force) a second call hits the cache
    r3 = ebe.run_expiry_falsifier_cached(tickers, n_perms=100, corr_threshold=0.5)
    assert "CACHE_HIT" in r3.notes


def test_per_underline_classification():
    """Index tickers vs single names handled distinctly."""
    assert ebe.per_underline_class("SPY") == "index"
    assert ebe.per_underline_class("QQQ") == "index"
    assert ebe.per_underline_class("AAPL") == "single-name"


def test_event_gated_vanna_lead_arm_runs():
    expiry, greeks, oi, spot = _build_multiday(n_days=120)
    sig = ebe.build_daily_signals(greeks, oi, spot, expiry, ticker="MOCK")
    v = ebe.vanna_lead_arm(sig, iv_shock_threshold=0.0, n_perms=50)
    assert v.channel == "vanna_lead"
    assert v.verdict in ebe.VERDICTS
    assert v.n_days <= sig.n_days()


def test_opex_event_window_arm_runs():
    expiry, greeks, oi, spot = _build_multiday(n_days=120)
    sig = ebe.build_daily_signals(greeks, oi, spot, expiry, ticker="MOCK")
    v = ebe.opex_event_window_arm(sig, opex_dates=sig.dates[::10], k=1)
    assert v.channel == "opex_window"
    assert v.verdict in ebe.VERDICTS


def test_accumulated_overlay_retest_runs():
    expiry, greeks, oi, spot = _build_multiday(n_days=120)
    sig = ebe.build_daily_signals(greeks, oi, spot, expiry, ticker="MOCK")
    res = ebe.accumulated_overlay_retest(sig)
    assert "delta_r2" in res and isinstance(res["redundant"], bool)


def test_seed_corpus_build_and_falsifier_network_free():
    """The offline seed corpus builds DailySignals and the falsifier runs on
    it with no network."""
    spy = ebe.build_daily_signals_from_seed("SPY")
    qqq = ebe.build_daily_signals_from_seed("QQQ")
    assert spy.n_days() >= 100
    assert qqq.n_days() >= 100
    run = ebe.run_expiry_falsifier(
        {"SPY": spy, "QQQ": qqq}, n_perms=60, corr_threshold=0.15, min_tickers=1
    )
    assert run.overall in ebe.VERDICTS
    assert isinstance(run.spy_qqq_sign_consistent, bool)
