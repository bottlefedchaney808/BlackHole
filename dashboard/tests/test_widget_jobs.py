"""Unit tests for the widgets 2-4 background tick functions
(dashboard/app.py). Every real data-source call (Vol_Suite's screener,
Tools/'s hedge_optimizer_tool/price_dist_tool/surface_explorer_tool) is
monkeypatched -- these tests must never touch ThetaData.
"""

import base64

import pytest

import dashboard.app as dashboard_app

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Widget 2 -- signals
# --------------------------------------------------------------------------


class _FakeScreenResult:
    def __init__(self, ticker, signal, score, vrp_pct, data_quality="ok"):
        self.ticker = ticker
        self.signal = signal
        self.score = score
        self.vrp_pct = vrp_pct
        self.data_quality = data_quality


def test_widget_signals_tick_writes_one_row_per_watchlist_ticker(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(dashboard_app, "OVERVIEW_WATCHLIST", ["SPX", "NDAQ"])

    import variance_swap_screener

    def fake_screen_ticker(ticker, target_years, expiration=None):
        return _FakeScreenResult(ticker, "BUY VOL", 3.5, 1.2)

    monkeypatch.setattr(variance_swap_screener, "screen_ticker", fake_screen_ticker)

    dashboard_app._widget_signals_tick()

    row = dashboard_app._widget_cache().get("signals")
    assert row["status"] == "ok"
    tickers = {t["ticker"]: t for t in row["payload"]["tickers"]}
    assert set(tickers) == {"SPX", "NDAQ"}
    assert tickers["SPX"]["signal"] == "BUY VOL"
    assert tickers["SPX"]["score"] == 3.5


def test_widget_signals_tick_handles_none_result(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(dashboard_app, "OVERVIEW_WATCHLIST", ["SPX"])

    import variance_swap_screener

    monkeypatch.setattr(variance_swap_screener, "screen_ticker", lambda *a, **k: None)

    dashboard_app._widget_signals_tick()

    row = dashboard_app._widget_cache().get("signals")
    assert row["payload"]["tickers"][0]["signal"] == "NO DATA"


def test_widget_signals_tick_handles_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(dashboard_app, "OVERVIEW_WATCHLIST", ["SPX"])

    import variance_swap_screener

    def boom(*a, **k):
        raise RuntimeError("thetadata unreachable")

    monkeypatch.setattr(variance_swap_screener, "screen_ticker", boom)

    dashboard_app._widget_signals_tick()

    row = dashboard_app._widget_cache().get("signals")
    entry = row["payload"]["tickers"][0]
    assert entry["signal"] == "ERROR"
    assert "thetadata unreachable" in entry["data_quality"]


# --------------------------------------------------------------------------
# Widget 3 -- per-position analysis
# --------------------------------------------------------------------------


def test_position_analysis_tick_no_positions(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    # No positions cache row written at all.
    dashboard_app._widget_position_analysis_tick()
    row = dashboard_app._widget_cache().get("position_analysis")
    assert row["status"] == "no_positions"
    assert row["payload"]["positions"] == []


def test_position_analysis_tick_dedupes_tickers_and_runs_all_modes(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    dashboard_app._widget_cache().set(
        "positions",
        {
            "positions": [
                {"account": "A", "ticker": "NVDA", "instrument_type": "equity"},
                {"account": "B", "ticker": "NVDA", "instrument_type": "equity"},
            ],
            "accounts": [],
        },
        status="ok",
    )

    from Tools.tools import hedge_optimizer_tool, price_dist_tool

    hedge_calls = []
    sim_calls = []

    def fake_hedge_run(context):
        hedge_calls.append(context)
        return {"mode": "options_hedge", "provenance": {}}

    def fake_sim_run(context):
        sim_calls.append(context["mode"])
        return {"mode": context["mode"], "bins": []}

    monkeypatch.setattr(hedge_optimizer_tool, "run", fake_hedge_run)
    monkeypatch.setattr(price_dist_tool, "run", fake_sim_run)

    dashboard_app._widget_position_analysis_tick()

    # NVDA held in both accounts -- must be analyzed once, not twice.
    assert len(hedge_calls) == 1
    assert hedge_calls[0]["ticker"] == "NVDA"
    assert sim_calls == ["price_dist", "mc_sim", "corr_sim"]

    row = dashboard_app._widget_cache().get("position_analysis")
    assert row["status"] == "ok"
    entry = row["payload"]["positions"][0]
    assert entry["ticker"] == "NVDA"
    assert entry["hedge"]["mode"] == "options_hedge"
    assert entry["price_dist"]["mode"] == "price_dist"
    assert entry["mc_sim"]["mode"] == "mc_sim"
    assert entry["corr_sim"]["mode"] == "corr_sim"


def test_position_analysis_tick_records_per_call_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    dashboard_app._widget_cache().set(
        "positions",
        {"positions": [{"account": "A", "ticker": "TGB"}], "accounts": []},
        status="ok",
    )

    from Tools.tools import hedge_optimizer_tool, price_dist_tool

    def boom_hedge(context):
        raise ValueError("no listed options")

    def boom_sim(context):
        raise RuntimeError("sim blew up")

    monkeypatch.setattr(hedge_optimizer_tool, "run", boom_hedge)
    monkeypatch.setattr(price_dist_tool, "run", boom_sim)

    dashboard_app._widget_position_analysis_tick()

    row = dashboard_app._widget_cache().get("position_analysis")
    entry = row["payload"]["positions"][0]
    assert "no listed options" in entry["hedge_error"]
    assert "sim blew up" in entry["price_dist_error"]
    assert "sim blew up" in entry["mc_sim_error"]
    assert "sim blew up" in entry["corr_sim_error"]


# --------------------------------------------------------------------------
# Widget 4 -- surfaces
# --------------------------------------------------------------------------


def test_surfaces_tick_encodes_chart_png_as_base64(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(
        dashboard_app, "WIDGET_SURFACES_OUTPUT_DIR", str(tmp_path / "surf")
    )

    from Tools.tools import surface_explorer_tool

    png_bytes = b"\x89PNG\r\n\x1a\nfake"

    def fake_run(context):
        mode = context["mode"]
        tag = context.get("greek", mode)
        path = tmp_path / f"{tag}.png"
        path.write_bytes(png_bytes)
        return {"mode": mode, "chart_path": str(path)}

    monkeypatch.setattr(surface_explorer_tool, "run", fake_run)

    dashboard_app._widget_surfaces_tick()

    row = dashboard_app._widget_cache().get("surfaces")
    assert row["status"] == "ok"
    assert row["payload"]["ticker"] == "SPX"
    surfaces = row["payload"]["surfaces"]
    assert set(surfaces) == {"iv", "vanna", "charm"}
    for key in ("iv", "vanna", "charm"):
        assert surfaces[key]["status"] == "ok"
        assert base64.b64decode(surfaces[key]["image_b64"]) == png_bytes


def test_surfaces_tick_handles_missing_chart(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(
        dashboard_app, "WIDGET_SURFACES_OUTPUT_DIR", str(tmp_path / "surf")
    )

    from Tools.tools import surface_explorer_tool

    monkeypatch.setattr(
        surface_explorer_tool, "run", lambda context: {"chart_path": None}
    )

    dashboard_app._widget_surfaces_tick()

    row = dashboard_app._widget_cache().get("surfaces")
    for key in ("iv", "vanna", "charm"):
        assert row["payload"]["surfaces"][key]["status"] == "no_chart"
        assert row["payload"]["surfaces"][key]["image_b64"] is None


def test_surfaces_tick_handles_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "w.db"))
    monkeypatch.setattr(
        dashboard_app, "WIDGET_SURFACES_OUTPUT_DIR", str(tmp_path / "surf")
    )

    from Tools.tools import surface_explorer_tool

    def boom(context):
        raise ValueError("no listed expirations")

    monkeypatch.setattr(surface_explorer_tool, "run", boom)

    dashboard_app._widget_surfaces_tick()

    row = dashboard_app._widget_cache().get("surfaces")
    for key in ("iv", "vanna", "charm"):
        assert row["payload"]["surfaces"][key]["status"] == "error"
        assert "no listed expirations" in row["payload"]["surfaces"][key]["error"]


# --------------------------------------------------------------------------
# Background jobs are opt-in only -- must never fire under a plain TestClient
# --------------------------------------------------------------------------


def test_widget_jobs_disabled_by_default():
    assert dashboard_app.WIDGET_JOBS_ENABLED is False


def test_lifespan_starts_no_tasks_when_disabled():
    import asyncio

    async def _run():
        async with dashboard_app._lifespan(dashboard_app.app):
            assert dashboard_app._WIDGET_JOB_TASKS == []

    asyncio.run(_run())
