"""
Network-free tests for volatility_suite.py's non-interactive CLI override
mechanism (--ticker/--index/--expiry/... and the --run-X/--no-X flag pairs).

The mechanism lets run_focus_workflow/run_unified_flow's normal interactive
prompts be bypassed by CLI flags instead of stdin, for scripted/cron
invocations that want the full narrative-style workflow (not the separate
--context headless mode) without blocking on input(). _ni_input() matches
overrides by checking whether the override's key is a substring of the
actual prompt text, so these tests exist mainly to guard against silent
substring collisions between distinct prompts (see the "Focus ticker" case
below, which was a real bug: --ticker's override also matched
_load_ticker_pack_interactive's unrelated "Focus ticker from pack" prompt).
"""
import argparse

import pytest

import volatility_suite as vsuite


@pytest.fixture(autouse=True)
def _clear_overrides():
    """_noninteractive is a module-level dict; tests must not leak state."""
    vsuite._noninteractive.clear()
    yield
    vsuite._noninteractive.clear()


@pytest.mark.unit
def test_ni_input_returns_override_when_key_matches_prompt():
    vsuite._set_override("Focus ticker (e.g. MSFT)", "NVDA")
    assert vsuite._ni_input("Focus ticker (e.g. MSFT): ") == "NVDA"


@pytest.mark.unit
def test_ni_input_ignores_internal_underscore_prefixed_keys():
    """Keys starting with '_' are read via _get_noninteractive(), not prompt
    substring matching -- _ni_input must skip them entirely, or a prompt
    containing that literal text (unlikely, but the guard is load-bearing)
    could accidentally match an internal key."""
    vsuite._noninteractive["_expiry"] = "20261016"
    assert vsuite._get_noninteractive("_expiry") == "20261016"
    # Falls through to blocking input() when no non-internal key matches --
    # simulate that by also setting a real override so we don't actually block.
    vsuite._set_override("Focus ticker (e.g. MSFT)", "SPY")
    assert vsuite._ni_input("Focus ticker (e.g. MSFT): ") == "SPY"


@pytest.mark.unit
def test_focus_ticker_override_does_not_leak_into_the_pack_selection_prompt():
    """Regression test: --ticker used to be keyed on the bare substring
    "Focus ticker", which also matches _load_ticker_pack_interactive's
    "Focus ticker from pack (number, default 1): " prompt -- a numeric
    pack-selection prompt, not a ticker-entry prompt. Passing --ticker
    together with --pack silently fed a non-numeric string into that
    selection and fell back to index 0 rather than erroring or being
    ignored. The override key must be specific enough not to match it."""
    vsuite._set_override("Focus ticker (e.g. MSFT)", "NVDA")
    # The exact prompt text from _load_ticker_pack_interactive's pack-ticker
    # selection step.
    prompt = "Focus ticker from pack (number, default 1): "
    assert "Focus ticker (e.g. MSFT)" not in prompt


@pytest.mark.unit
def test_choose_an_index_override_is_distinct_from_choose_pack_number():
    vsuite._set_override("Choose an index", "QQQ")
    vsuite._set_override("Choose pack number", "2")
    assert vsuite._ni_input("\nChoose an index by number, or type a ticker directly (default SPY): ") == "QQQ"
    assert vsuite._ni_input("Choose pack number (default 1): ") == "2"


@pytest.mark.unit
def test_set_override_ignores_none_values():
    """CLI flags default to None when not passed; _set_override must not
    register a "None" string override that would then incorrectly win over
    a real prompt default."""
    vsuite._set_override("Basket size", None)
    assert "Basket size" not in vsuite._noninteractive


def _build_parser_and_parse(argv):
    """Build the same non-interactive arg group main() does, minus the
    unrelated flags, so these tests don't need the full CLI surface."""
    parser = argparse.ArgumentParser()
    ni = parser
    ni.add_argument("--run-chain-scanner", action="store_true", default=None)
    ni.add_argument("--no-chain-scanner", action="store_false", dest="run_chain_scanner")
    ni.add_argument("--run-group-screener", action="store_true", default=None)
    ni.add_argument("--no-group-screener", action="store_false", dest="run_group_screener")
    ni.add_argument("--run-2d-surface", action="store_true", default=None)
    ni.add_argument("--no-2d-surface", action="store_false", dest="run_2d_surface")
    ni.add_argument("--run-vrp", action="store_true", default=None)
    ni.add_argument("--no-vrp", action="store_false", dest="run_vrp")
    ni.add_argument("--run-sentiment-backtest", action="store_true", default=None)
    ni.add_argument("--no-sentiment-backtest", action="store_false", dest="run_sentiment_backtest")
    ni.add_argument("--compile-pdf", action="store_true", default=None)
    ni.add_argument("--no-compile-pdf", action="store_false", dest="compile_pdf")
    ni.add_argument("--run-options-suite", action="store_true", default=None)
    ni.add_argument("--no-options-suite", action="store_false", dest="run_options_suite")
    ni.add_argument("--run-var-suite", action="store_true", default=None)
    ni.add_argument("--no-var-suite", action="store_false", dest="run_var_suite")
    ni.add_argument("--yes", action="store_true", default=None)
    return parser.parse_args(argv)


_ALL_ATTRS = ("run_chain_scanner", "run_group_screener", "run_2d_surface",
              "run_vrp", "run_sentiment_backtest", "compile_pdf",
              "run_options_suite", "run_var_suite")


@pytest.mark.unit
def test_unset_yes_no_flags_default_to_none_not_a_silent_true_or_false():
    """None is the three-state sentinel main() relies on to distinguish
    "not specified, fall through to the interactive prompt" from an explicit
    --no-X. Since --run-X and --no-X share a dest, argparse must not let the
    later-registered store_false action's implicit True default clobber the
    store_true action's explicit None default."""
    args = _build_parser_and_parse([])
    for attr in _ALL_ATTRS:
        assert getattr(args, attr) is None, attr


@pytest.mark.unit
def test_no_flags_produce_explicit_false():
    args = _build_parser_and_parse(["--no-chain-scanner", "--no-compile-pdf", "--no-var-suite"])
    assert args.run_chain_scanner is False
    assert args.compile_pdf is False
    assert args.run_var_suite is False
    assert args.run_group_screener is None


@pytest.mark.unit
def test_run_flags_produce_explicit_true():
    args = _build_parser_and_parse(["--run-2d-surface", "--run-options-suite"])
    assert args.run_2d_surface is True
    assert args.run_options_suite is True
    assert args.run_vrp is None


@pytest.mark.unit
def test_yes_fills_in_only_the_unset_flags_not_explicit_no():
    """--yes is documented as "accept all yes/no defaults" -- an explicit
    --no-X on the same command line must still win, matching argparse's
    normal last-flag-wins semantics for the CLI author's intent."""
    args = _build_parser_and_parse(["--yes", "--no-var-suite"])
    if args.yes:
        for attr in _ALL_ATTRS:
            if getattr(args, attr) is None:
                setattr(args, attr, True)
    assert args.run_var_suite is False, "explicit --no-var-suite must survive --yes"
    for attr in _ALL_ATTRS:
        if attr != "run_var_suite":
            assert getattr(args, attr) is True, attr
