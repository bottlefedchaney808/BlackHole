"""
Task 6 (Modularization Overhaul, Phase 3) -- module-selection awareness for
run_context_mode's hand-off of the five _run_core_analysis gated-step
booleans (run_options_chain / run_group_screener / run_vol_surface_2d /
run_vrp_term_structure / run_sentiment_backtest).

Unlike test_context_mode.py (which stubs every real pipeline dependency to
exercise `_run_core_analysis` for real), these tests mock
`_run_core_analysis` itself -- it is a big function with real ThetaData
calls inside, and this file's whole job is to verify what run_context_mode
resolves the five booleans TO, not what the pipeline does with them. See
volatility_suite.py::_resolve_core_analysis_flags for the back-compat
contract this pins down:

- No `context["modules"]` key (or an empty list): byte-identical to the
  historical VS_RUN_* env-var-driven behavior, including
  VS_RUN_VRP_TERM_STRUCTURE's uniquely True default.
- A non-empty `context["modules"]` list: the five booleans come purely from
  slug membership in that list (env vars are not consulted at all for that
  call), including vrp_term_structure -- "selected modules run, nothing
  else does" was the deliberate judgment call documented in
  _resolve_core_analysis_flags's docstring and this task's report.
"""

import builtins
import json

import pytest
import volatility_suite as vsuite
from suite_context import build_suite_context


@pytest.fixture(autouse=True)
def _clear_noninteractive_overrides():
    vsuite._noninteractive.clear()
    yield
    vsuite._noninteractive.clear()


def _no_stdin(monkeypatch):
    def _boom(prompt: str = "") -> str:
        raise AssertionError(f"context mode read stdin -- prompt was {prompt!r}.")

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
    context_dict = build_suite_context(**kwargs)
    path = tmp_path / "suite_context.json"
    path.write_text(json.dumps(context_dict, indent=2), encoding="utf-8")
    return str(path)


def _write_context_with_modules(tmp_path, out_dir, modules, **overrides) -> str:
    """Same as _write_context, but injects a top-level "modules" key into
    the written JSON -- build_suite_context has no "modules" kwarg of its
    own (it's not part of the suite_context.json schema this suite owns;
    Task 2's run_selected_modules / dashboard POST /run/{kind} attach it to
    the context dict directly), so it's added after serialization here,
    the same way a real caller would.
    """
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
    context_dict = build_suite_context(**kwargs)
    if modules is not None:
        context_dict["modules"] = modules
    path = tmp_path / "suite_context.json"
    path.write_text(json.dumps(context_dict, indent=2), encoding="utf-8")
    return str(path)


def _install_core_analysis_stub(monkeypatch, calls: dict):
    """Mocks _run_core_analysis itself -- no real pipeline step runs.
    Captures the five gated-step booleans it was called with, plus
    ticker/target_years/expiration, and returns a minimal-but-valid
    (produced, sections, artifacts) tuple that lets run_context_mode reach
    _build_vol_result / _write_vol_result without any further stubbing."""

    def fake_run_core_analysis(**kwargs):
        calls["kwargs"] = kwargs
        artifacts = {
            "focus_ticker": kwargs["ticker"],
            "sign_model": kwargs["sign_model"],
            "dealer_positioning": {
                "available": True,
                "sign_model": kwargs["sign_model"],
            },
        }
        return [], [], artifacts

    monkeypatch.setattr(vsuite, "_run_core_analysis", fake_run_core_analysis)


# ---------------------------------------------------------------------------
# No "modules" key -- byte-identical env-var-driven back-compat path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "env,expected",
    [
        # All-default case: nothing set. VRP is the lone True default.
        (
            {},
            {
                "run_options_chain": False,
                "run_group_screener": False,
                "run_vol_surface_2d": False,
                "run_vrp_term_structure": True,
                "run_sentiment_backtest": False,
            },
        ),
        # Every VS_RUN_* flag flipped from its default.
        (
            {
                "VS_RUN_CHAIN_SCANNER": "1",
                "VS_RUN_GROUP_SCREENER": "1",
                "VS_RUN_VOL_SURFACE_2D": "1",
                "VS_RUN_VRP_TERM_STRUCTURE": "0",
                "VS_RUN_SENTIMENT_BACKTEST": "1",
            },
            {
                "run_options_chain": True,
                "run_group_screener": True,
                "run_vol_surface_2d": True,
                "run_vrp_term_structure": False,
                "run_sentiment_backtest": True,
            },
        ),
        # Only chain scanner opted in; VRP left at its True default.
        (
            {"VS_RUN_CHAIN_SCANNER": "1"},
            {
                "run_options_chain": True,
                "run_group_screener": False,
                "run_vol_surface_2d": False,
                "run_vrp_term_structure": True,
                "run_sentiment_backtest": False,
            },
        ),
    ],
    ids=["all-default", "all-flipped", "chain-scanner-only-env"],
)
def test_no_modules_key_matches_env_var_behavior_exactly(
    monkeypatch, tmp_path, env, expected
):
    for name in [
        "VS_RUN_CHAIN_SCANNER",
        "VS_RUN_GROUP_SCREENER",
        "VS_RUN_VOL_SURFACE_2D",
        "VS_RUN_VRP_TERM_STRUCTURE",
        "VS_RUN_SENTIMENT_BACKTEST",
    ]:
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context(tmp_path, out_dir)
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    for flag, value in expected.items():
        assert kwargs[flag] is value, f"{flag}: expected {value}, got {kwargs[flag]}"


# ---------------------------------------------------------------------------
# "modules" key present -- selection replaces env vars entirely
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_modules_subset_resolves_only_matching_booleans(monkeypatch, tmp_path):
    """A subset of the five slugs: only the matching booleans become True.
    Env vars set to the OPPOSITE of what's selected prove the env path is
    not consulted at all once "modules" is present."""
    monkeypatch.setenv("VS_RUN_CHAIN_SCANNER", "0")  # would be False by env
    monkeypatch.setenv("VS_RUN_GROUP_SCREENER", "0")
    monkeypatch.setenv("VS_RUN_VOL_SURFACE_2D", "0")
    monkeypatch.setenv(
        "VS_RUN_VRP_TERM_STRUCTURE", "0"
    )  # env default is True, forced off
    monkeypatch.setenv("VS_RUN_SENTIMENT_BACKTEST", "0")

    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(
        tmp_path, out_dir, ["chain_scanner", "vol_surface_2d"]
    )
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    assert kwargs["run_options_chain"] is True
    assert kwargs["run_vol_surface_2d"] is True
    assert kwargs["run_group_screener"] is False
    assert kwargs["run_vrp_term_structure"] is False
    assert kwargs["run_sentiment_backtest"] is False


@pytest.mark.unit
def test_modules_vrp_not_listed_defaults_off_under_selection(monkeypatch, tmp_path):
    """The VRP-default-under-module-selection judgment call, pinned down
    explicitly: context["modules"] = ["chain_scanner"] (VRP not listed).
    Chosen behavior: VRP is OFF, consistent with "selected modules run,
    nothing else does" -- the same explicit-selection contract Task 2's
    run_selected_modules already uses elsewhere. VS_RUN_VRP_TERM_STRUCTURE
    is left at its normal (unset -> True) state to prove the env default is
    genuinely not leaking into the selection path."""
    monkeypatch.delenv("VS_RUN_VRP_TERM_STRUCTURE", raising=False)

    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(tmp_path, out_dir, ["chain_scanner"])
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    assert kwargs["run_options_chain"] is True
    assert kwargs["run_vrp_term_structure"] is False
    assert kwargs["run_group_screener"] is False
    assert kwargs["run_vol_surface_2d"] is False
    assert kwargs["run_sentiment_backtest"] is False


@pytest.mark.unit
def test_modules_can_explicitly_select_vrp_term_structure(monkeypatch, tmp_path):
    """The flip side of the case above: listing "vrp_term_structure"
    explicitly turns it on under selection, same as any other slug."""
    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(
        tmp_path, out_dir, ["vrp_term_structure", "sentiment_backtest"]
    )
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    assert kwargs["run_vrp_term_structure"] is True
    assert kwargs["run_sentiment_backtest"] is True
    assert kwargs["run_options_chain"] is False
    assert kwargs["run_group_screener"] is False
    assert kwargs["run_vol_surface_2d"] is False


@pytest.mark.unit
def test_modules_all_five_slugs_selected(monkeypatch, tmp_path):
    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(
        tmp_path,
        out_dir,
        [
            "chain_scanner",
            "group_screener",
            "vol_surface_2d",
            "vrp_term_structure",
            "sentiment_backtest",
        ],
    )
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    assert kwargs["run_options_chain"] is True
    assert kwargs["run_group_screener"] is True
    assert kwargs["run_vol_surface_2d"] is True
    assert kwargs["run_vrp_term_structure"] is True
    assert kwargs["run_sentiment_backtest"] is True


@pytest.mark.unit
def test_modules_unrecognized_slug_is_simply_not_matched(monkeypatch, tmp_path):
    """A slug outside the fixed five (e.g. a Task 3/4/5 module name that
    isn't one of _run_core_analysis's gated steps) selects nothing here --
    it is silently not one of the five booleans, not an error. Other
    dashboard-selected modules from that same "modules" list are handled by
    their own ModuleSpec.run() elsewhere, not by run_context_mode."""
    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(
        tmp_path, out_dir, ["dealer_exposure", "chain_scanner"]
    )
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    assert kwargs["run_options_chain"] is True
    assert kwargs["run_group_screener"] is False
    assert kwargs["run_vol_surface_2d"] is False
    assert kwargs["run_vrp_term_structure"] is False
    assert kwargs["run_sentiment_backtest"] is False


# ---------------------------------------------------------------------------
# "modules" key present but empty -- falls back to env vars, same as absent
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_modules_empty_list_falls_back_to_env_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("VS_RUN_VRP_TERM_STRUCTURE", raising=False)
    monkeypatch.setenv("VS_RUN_CHAIN_SCANNER", "1")

    calls: dict = {}
    _install_core_analysis_stub(monkeypatch, calls)
    _no_stdin(monkeypatch)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    ctx_path = _write_context_with_modules(tmp_path, out_dir, [])
    out_json = tmp_path / "vol_result.json"

    rc = vsuite.run_context_mode(ctx_path, str(out_json))
    assert rc == 0

    kwargs = calls["kwargs"]
    # Empty "modules" treated exactly like an absent key: env vars decide.
    assert kwargs["run_options_chain"] is True  # from VS_RUN_CHAIN_SCANNER=1
    assert kwargs["run_group_screener"] is False
    assert kwargs["run_vol_surface_2d"] is False
    assert kwargs["run_vrp_term_structure"] is True  # env default, not forced off
    assert kwargs["run_sentiment_backtest"] is False


# ---------------------------------------------------------------------------
# Unit tests directly on _resolve_core_analysis_flags (no pipeline at all)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_resolve_core_analysis_flags_no_modules_key(monkeypatch):
    for name in [
        "VS_RUN_CHAIN_SCANNER",
        "VS_RUN_GROUP_SCREENER",
        "VS_RUN_VOL_SURFACE_2D",
        "VS_RUN_VRP_TERM_STRUCTURE",
        "VS_RUN_SENTIMENT_BACKTEST",
    ]:
        monkeypatch.delenv(name, raising=False)

    flags = vsuite._resolve_core_analysis_flags({})
    assert flags == {
        "run_options_chain": False,
        "run_group_screener": False,
        "run_vol_surface_2d": False,
        "run_vrp_term_structure": True,
        "run_sentiment_backtest": False,
    }


@pytest.mark.unit
def test_resolve_core_analysis_flags_modules_none_same_as_absent(monkeypatch):
    monkeypatch.delenv("VS_RUN_VRP_TERM_STRUCTURE", raising=False)
    assert vsuite._resolve_core_analysis_flags(
        {"modules": None}
    ) == vsuite._resolve_core_analysis_flags({})


@pytest.mark.unit
def test_resolve_core_analysis_flags_modules_membership():
    flags = vsuite._resolve_core_analysis_flags(
        {"modules": ["sentiment_backtest", "group_screener"]}
    )
    assert flags == {
        "run_options_chain": False,
        "run_group_screener": True,
        "run_vol_surface_2d": False,
        "run_vrp_term_structure": False,
        "run_sentiment_backtest": True,
    }


@pytest.mark.unit
def test_resolve_core_analysis_flags_modules_case_insensitive():
    flags = vsuite._resolve_core_analysis_flags({"modules": ["CHAIN_SCANNER"]})
    assert flags["run_options_chain"] is True
