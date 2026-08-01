"""
End-to-end wiring test for volatility_suite.py's non-interactive context mode
and the vol_result.json contract it publishes.

This is the counterpart to test_run_modes_smoke.py. That file proves modes 1
and 2 reach the same pipeline calls; this one proves the THIRD entry point --
`--context / --context-out`, the one the root orchestrator drives -- reaches
that same pipeline without touching stdin, and that what it writes out is
something a consumer can rely on.

Two properties are worth stating explicitly, because both were broken before
this mode existed:

1. **Nothing reads stdin.** The orchestrator launches children with
   stdin=DEVNULL. A single stray input() in the context path turns every
   headless run into an EOFError several minutes in. `input` is replaced with a
   function that fails the test rather than raising EOFError, so the failure
   names the prompt instead of surfacing as a generic exception.

2. **The result distinguishes success from silent failure.** Vol_Suite catches
   per-step exceptions and continues, so "exited 0" has never meant "produced
   something". vol_result.json carries the vol surface, the dealer positioning
   block with an explicit `available` flag, and the gamma ladder -- and a run
   where all of that came back empty is reported as status='error', not as a
   success with an empty payload.
"""
import builtins
import json
import math
import os
from typing import List

import pytest

import volatility_suite as vsuite
from shared.schemas import validate_vol_result
from suite_context import build_suite_context


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------

class FakeTD:
    def close(self):
        pass


class FakeGammaRecord:
    def __init__(self, strike, dollar_gamma):
        self.strike = strike
        self.expiry = "20261218"
        self.right = "C"
        self.gamma = 0.0123
        self.dollar_gamma = dollar_gamma
        self.iv = 0.31
        self.oi = 1500
        self.bid = 1.0
        self.ask = 1.1
        self.tte = 0.4
        # The greeks dealer_positioning cannot always pull come back as NaN,
        # never as a missing attribute -- see GammaRecord's own docstring. That
        # is the exact value json.dump would emit as a bare `NaN` token.
        self.delta = float("nan")
        self.vanna = float("nan")
        self.charm = float("nan")


class FakeDealerResult:
    """Mimics DealerPositioningResult closely enough to exercise the numpy /
    NaN handling in _dealer_positioning_summary and _gamma_records_payload."""

    def __init__(self, n_records=12):
        self.ticker = "TSLA"
        self.spot = 250.5
        self.forward = 251.2
        self.dividend_yield = 0.0
        self.total_net_gamma = -1.2e7
        self.total_net_dollar_gamma = -9.5e8
        self.hedge_requirement = 123456.0
        self.gamma_flip_level = 248.0
        self.highest_gamma_strike = 250.0
        self.total_gamma_exposure = 4.4e7
        self.num_expiries = 6
        self.num_records = n_records
        self.sign_model = "vol_surface_replication"
        self.has_delta_data = True
        self.has_vanna_data = False
        self.has_charm_data = False
        self.greek_days_window = 150
        self.hedge_equiv_option_strike = 250.0
        self.hedge_equiv_option_right = "C"
        # NaN on a top-level scalar, which is what json.dump chokes on.
        self.hedge_equiv_option_contracts = float("nan")
        self.gamma_records = [
            FakeGammaRecord(200.0 + 5 * i, dollar_gamma=(1000.0 * (i + 1)))
            for i in range(n_records)
        ]


def _install_stubs(monkeypatch, calls: dict, *, dealer_result=None,
                   variance_result=True):
    import thetadata_client
    monkeypatch.setattr(thetadata_client, "ThetaDataController", lambda: FakeTD())

    import correlation_engine as ce

    class FakeBasketStats:
        # dict_values of plain floats: enough for _json_safe and for the
        # opportunities read's beta sort.
        individual_betas = {"TSLA": 1.4, "INTC": 0.8, "SPCX": 1.1}
        dispersion_score = 0.42

    def fake_run_correlation_engine(tickers, weights=None, market="SPY",
                                    period="2y", output_dir=None, save_csv=True):
        calls["correlation_tickers"] = list(tickers)
        calls["correlation_weights"] = list(weights) if weights is not None else None
        calls["correlation_market"] = market
        return [], "Correlation completed", FakeBasketStats()

    monkeypatch.setattr(ce, "run_correlation_engine", fake_run_correlation_engine)

    import variance_swap_live as vsl

    def fake_run_variance_swap_live(ticker, target_years, output_dir=None, expiration=None):
        calls.setdefault("variance_swap_tickers", []).append(ticker)
        calls.setdefault("variance_swap_expirations", []).append(expiration)
        if not variance_result:
            raise RuntimeError(f"no chain for {ticker}")
        return [], f"{ticker} variance swap", {
            "S0": 250.0,
            "F": 251.0,
            "T_years": 0.4,
            "fair_variance_annualized": 0.09,
            "fair_variance_swap_strike_vol": 0.30,
            "fair_variance_swap_strike_vol_pct": 30.0 if ticker != "SPY" else 18.0,
            "atm_strike": 250.0,
            "atm_implied_vol": 0.29,
            "atm_implied_vol_pct": 29.0,
            "convexity_premium_vol_pct": 1.0,
            "num_strikes_used": 41,
            "K_min": 100.0,
            "K_max": 400.0,
            # Dropped on purpose by _variance_leg_summary: parallel arrays over
            # every strike, already exported to CSV.
            "strike_table": {"strikes": [1, 2, 3]},
        }

    monkeypatch.setattr(vsl, "run_variance_swap_live", fake_run_variance_swap_live)

    import garch_analysis as ga
    monkeypatch.setattr(ga, "run_garch_module",
                        lambda ticker, output_dir=None: ([], "GARCH done"))

    import variance_swap_screener as vss

    def fake_screen_ticker(ticker, target_years, expiration=None):
        calls.setdefault("screen_ticker_calls", []).append(ticker)
        return None

    def fake_run_variance_screener(tickers, target_years, output_dir=None):
        calls["group_screener_tickers"] = list(tickers)
        return [], f"Screened {len(tickers)} tickers"

    monkeypatch.setattr(vss, "screen_ticker", fake_screen_ticker)
    monkeypatch.setattr(vss, "run_variance_screener", fake_run_variance_screener)

    import dealer_positioning as dp

    def fake_run_dealer_positioning(ticker, target_years, output_dir=None,
                                    save_csv=True, expiration=None, sign_model=None):
        calls["dealer_positioning_sign_model"] = sign_model
        calls["dealer_positioning_expiration"] = expiration
        csv_path = os.path.join(output_dir or ".", f"{ticker}_gamma_records_20261218_000000.csv")
        return [csv_path], "Dealer positioning done", dealer_result

    monkeypatch.setattr(dp, "run_dealer_positioning", fake_run_dealer_positioning)

    import options_chain_scanner as ocs

    def fake_run_chain_scanner(ticker, target_years, expiration=None, output_dir=None):
        calls["chain_scanner_called"] = True
        raise AssertionError("chain scanner must be opt-in in context mode")

    monkeypatch.setattr(ocs, "run_chain_scanner", fake_run_chain_scanner)


def _no_stdin(monkeypatch):
    """Any input() in the context path is a bug, not an EOFError."""
    def _boom(prompt: str = "") -> str:
        raise AssertionError(
            f"context mode read stdin -- prompt was {prompt!r}. The orchestrator "
            f"launches this with stdin closed, so this would hang or EOF.")
    monkeypatch.setattr(builtins, "input", _boom)


def _write_context(tmp_path, out_dir, **overrides) -> str:
    kwargs = dict(
        output_dir=str(out_dir),
        run_id="vsuite_test_run",
        ticker="TSLA",
        option_type="call",
        strike=None,
        target_years=0.4,
        expiration_date="2026-12-18",
        index_ticker="SPY",
        basket_tickers=["TSLA", "INTC", "SPCX"],
        basket_weights=[0.5, 0.3, 0.2],
        sentiment_manifest_path=str(tmp_path / "manifest.json"),
        sentiment_ranked_tickers=["TSLA", "INTC", "SPCX"],
    )
    kwargs.update(overrides)
    context = build_suite_context(**kwargs)
    path = tmp_path / "suite_context.json"
    path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_context_mode_runs_the_pipeline_without_stdin(monkeypatch, tmp_path):
    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=FakeDealerResult())
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    # The basket came from the context verbatim -- context mode must not
    # re-derive it from index membership.
    assert calls["correlation_tickers"] == ["TSLA", "INTC", "SPCX"]
    assert calls["correlation_weights"] == [0.5, 0.3, 0.2]
    assert calls["correlation_market"] == "SPY"

    # Both replication legs ran, on the ONE pinned expiry, converted from the
    # context's ISO form to ThetaData's compact form exactly once.
    assert calls["variance_swap_tickers"] == ["SPY", "TSLA"]
    assert calls["variance_swap_expirations"] == ["20261218", "20261218"]
    assert calls["dealer_positioning_expiration"] == "20261218"
    assert calls["dealer_positioning_sign_model"] == "vol_surface_replication"

    # Opt-in only: neither the chain scanner nor the group screener should run
    # headless by default (the stubs assert/record if they do).
    assert "chain_scanner_called" not in calls
    assert "group_screener_tickers" not in calls


@pytest.mark.unit
def test_vol_result_is_schema_valid_and_json_strict(monkeypatch, tmp_path):
    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=FakeDealerResult())
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)
    out_json = tmp_path / "vol_result.json"

    assert vsuite.run_context_mode(ctx_path, str(out_json)) == 0
    assert out_json.exists()

    raw = out_json.read_text(encoding="utf-8")
    # NaN/Infinity are not JSON. Vol_Suite's modules return float('nan') for
    # "not available" all over the place, so this is the regression that
    # matters: a strict parser in any other language must be able to read it.
    assert "NaN" not in raw and "Infinity" not in raw
    payload = json.loads(raw)

    validate_vol_result(payload)          # raises on violation

    assert payload["suite"] == "vol"
    assert payload["status"] == "ok"
    assert payload["schema_version"] == 1
    assert payload["ticker"] == "TSLA"
    assert payload["run_id"] == "vsuite_test_run"

    surface = payload["vol_surface"]
    assert surface["focus"]["ticker"] == "TSLA"
    assert surface["focus"]["fair_vol_pct"] == 30.0
    assert surface["index"]["fair_vol_pct"] == 18.0
    assert surface["vol_spread_pts"] == pytest.approx(12.0)
    assert surface["basket"]["tickers"] == ["TSLA", "INTC", "SPCX"]
    assert surface["basket"]["dispersion_score"] == pytest.approx(0.42)
    # The per-strike arrays must not be inlined into the handoff.
    assert "strike_table" not in surface["focus"]

    dealer = payload["dealer_positioning"]
    assert dealer["available"] is True
    assert dealer["sign_model"] == "vol_surface_replication"
    assert dealer["gamma_flip_level"] == pytest.approx(248.0)
    # NaN scalar became null rather than an unparseable token.
    assert dealer["hedge_equiv_option_contracts"] is None

    records = payload["gamma_records"]
    assert len(records) == 12
    assert payload["gamma_records_total"] == 12
    assert payload["gamma_records_truncated"] is False
    assert records[0]["delta"] is None            # NaN greek -> null
    assert all(r["strike"] is not None for r in records)


@pytest.mark.unit
def test_gamma_records_are_capped_and_report_the_full_count(monkeypatch, tmp_path):
    """A liquid chain runs to thousands of rows. The JSON keeps the biggest
    |dollar gamma| slice and says how many it dropped; the full ladder stays in
    the CSV, whose path is carried in the payload."""
    monkeypatch.setenv("VS_VOL_RESULT_MAX_GAMMA_RECORDS", "5")
    monkeypatch.setattr(vsuite, "_MAX_GAMMA_RECORDS", 5)

    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=FakeDealerResult(n_records=40))
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)
    out_json = tmp_path / "vol_result.json"

    assert vsuite.run_context_mode(ctx_path, str(out_json)) == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    validate_vol_result(payload)

    assert len(payload["gamma_records"]) == 5
    assert payload["gamma_records_total"] == 40
    assert payload["gamma_records_truncated"] is True
    # Kept rows are the highest-|dollar gamma| ones, not the first five.
    assert payload["gamma_records"][0]["dollar_gamma"] == pytest.approx(40000.0)
    assert payload["gamma_records_csv"].endswith(".csv")


@pytest.mark.unit
def test_total_failure_is_reported_as_error_not_as_empty_success(monkeypatch, tmp_path):
    """Every leg failing used to be indistinguishable from a clean run, because
    the orchestrator inferred success from files on disk. A payload with no vol
    surface and no dealer positioning is status='error'."""
    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=None, variance_result=False)
    _no_stdin(monkeypatch)

    import dealer_positioning as dp

    def exploding_dealer_positioning(*a, **kw):
        raise RuntimeError("ThetaData unavailable")

    monkeypatch.setattr(dp, "run_dealer_positioning", exploding_dealer_positioning)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 1

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    validate_vol_result(payload)                   # error shape is still valid
    assert payload["status"] == "error"
    assert payload["error"]
    steps = {e["step"] for e in payload["errors"]}
    assert {"variance_swap_index", "variance_swap_focus", "dealer_positioning"} <= steps


@pytest.mark.unit
def test_unusable_context_still_writes_a_valid_error_result(tmp_path):
    """Exit code 2 with nothing on disk is what made the old integration so
    hard to debug. The caller reads the result file on the failure path too."""
    out_json = tmp_path / "vol_result.json"
    rc = vsuite.run_context_mode(str(tmp_path / "does_not_exist.json"), str(out_json))
    assert rc == 2
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    validate_vol_result(payload)
    assert payload["status"] == "error"
    assert "does_not_exist.json" in payload["error"]


@pytest.mark.unit
def test_context_out_defaults_to_the_context_output_dir(monkeypatch, tmp_path):
    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=FakeDealerResult())
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)

    assert vsuite.run_context_mode(ctx_path, None) == 0
    assert (out_dir / "vol_result.json").exists()


@pytest.mark.unit
def test_main_selects_context_mode_from_flags_and_from_env(monkeypatch, tmp_path):
    """The orchestrator passes both the flags and SUITE_CONTEXT_MODE/-PATH.
    Either alone must select context mode; neither may fall through to the
    interactive run-mode prompt."""
    calls: dict = {}
    _install_stubs(monkeypatch, calls, dealer_result=FakeDealerResult())
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)

    assert vsuite.main(["--context", ctx_path,
                        "--context-out", str(tmp_path / "a.json"),
                        "--no-loop"]) == 0
    assert (tmp_path / "a.json").exists()

    monkeypatch.setenv("SUITE_CONTEXT_MODE", "1")
    monkeypatch.setenv("SUITE_CONTEXT_PATH", ctx_path)
    assert vsuite.main([]) == 0
    assert (out_dir / "vol_result.json").exists()


@pytest.mark.unit
def test_json_safe_handles_nan_and_numpy(monkeypatch):
    np = pytest.importorskip("numpy")
    assert vsuite._json_safe(float("nan")) is None
    assert vsuite._json_safe(float("inf")) is None
    assert vsuite._json_safe(np.float64(1.5)) == 1.5
    assert vsuite._json_safe(np.array([1.0, 2.0])) == [1.0, 2.0]
    assert vsuite._json_safe(np.array([float("nan"), 2.0])) == [None, 2.0]
    assert vsuite._json_safe({"a": np.int64(3)}) == {"a": 3}
    assert math.isnan(float("nan"))   # sanity: the input really was NaN
