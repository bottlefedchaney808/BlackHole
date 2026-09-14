"""Pins for the 2026-09-12 desk fixes: the console book, the off-hours spot
quote, desk-ISO expiries, and a changeable jump-model default."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Console book
# ---------------------------------------------------------------------------


def test_console_book_infers_kind_and_validates_option_legs():
    from dashboard.console_book import ConsoleBookError, normalize_entry

    assert normalize_entry({"ticker": "pltr"})["kind"] == "ticker"
    assert normalize_entry({"ticker": "NVDA", "qty": -5})["kind"] == "equity"
    leg = normalize_entry(
        {"ticker": "SPY", "qty": 1, "strike": 760, "right": "put", "expiry": "20261016"}
    )
    assert (leg["kind"], leg["right"], leg["expiry"]) == ("option", "P", "2026-10-16")

    with pytest.raises(ConsoleBookError, match="non-zero qty"):
        normalize_entry({"ticker": "SPY", "kind": "equity"})
    with pytest.raises(ConsoleBookError, match="right C or P"):
        normalize_entry(
            {"ticker": "SPY", "qty": 1, "strike": 760, "expiry": "2026-10-16"}
        )
    with pytest.raises(ConsoleBookError, match="not a ticker"):
        normalize_entry({"ticker": "DROP TABLE"})


def test_console_book_add_is_all_or_nothing_and_dedupes_watch_tickers(tmp_path):
    from dashboard.console_book import ConsoleBook, ConsoleBookError

    book = ConsoleBook(str(tmp_path / "w.db"))
    with pytest.raises(ConsoleBookError):
        book.add([{"ticker": "AAPL"}, {"ticker": "SPY", "kind": "option", "qty": 1}])
    assert book.list() == []  # the valid first entry was not written either

    assert len(book.add([{"ticker": "AAPL"}, {"ticker": "AAPL"}])) == 1
    assert book.add([{"ticker": "AAPL"}]) == []  # clicking a pack twice
    assert (
        len(book.add([{"ticker": "AAPL", "qty": 10}])) == 1
    )  # a position is not a dup
    entry_id = book.list()[0]["id"]
    assert book.remove(entry_id) is True
    assert book.remove(entry_id) is False


def test_combined_book_tags_every_row_and_unions_tickers():
    from dashboard.console_book import combine_books

    book = combine_books(
        [{"ticker": "NVDA", "instrument_type": "equity", "qty": 1}],
        [
            {"id": 1, "kind": "ticker", "ticker": "PLTR"},
            {"id": 2, "kind": "option", "ticker": "NVDA", "qty": 1, "strike": 200},
        ],
    )
    assert [p["book"] for p in book["positions"]] == ["real", "console", "console"]
    assert book["held_tickers"] == ["NVDA", "PLTR"]
    assert book["real_tickers"] == ["NVDA"]
    assert book["console_positions"][0]["instrument_type"] == "watch"


def test_highlight_packs_no_longer_overwrite_the_book_basket(tmp_path, monkeypatch):
    """Packs used to publish under `held_tickers`, replacing your book as the
    basket every tool was seeded with."""
    import importlib.util
    import json
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "sentiment_module_registry_for_test",
        Path(__file__).resolve().parents[1]
        / "sentiment-scanner"
        / "module_registry.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    root = tmp_path / "packs"
    root.mkdir()
    (root / "latest_manifest.json").write_text(
        json.dumps({"packs": [{"group_name": "g", "tickers": ["AAPL", "BTC.X"]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_packs_root", lambda: root)
    result = mod._run_highlight_packs({})
    assert result.context_patch == {"pack_tickers": ["AAPL"]}
    assert "held_tickers" not in (result.context_patch or {})


# ---------------------------------------------------------------------------
# Off-hours stub quote
# ---------------------------------------------------------------------------


def test_spot_quote_refuses_a_crossed_or_stub_quote():
    from shared.thetadata import _usable_quote_price

    # Live 2026-09-12 SPY snapshot: last trade 764.48. The old loop returned 710.75.
    assert _usable_quote_price({"bid": "710.75", "ask": "774.00"}) is None
    assert _usable_quote_price({"bid": "764.40", "ask": "764.50"}) == pytest.approx(
        764.45
    )
    assert _usable_quote_price({"bid": "765", "ask": "764"}) is None  # crossed
    assert _usable_quote_price({"mid": "10.5"}) == 10.5
    assert _usable_quote_price({"bid": "0.0000", "ask": "0.0000"}) is None


# ---------------------------------------------------------------------------
# Desk-ISO expiry
# ---------------------------------------------------------------------------


def test_resolve_expiration_accepts_the_desk_iso_form(capsys):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Vol_Suite"))
    import expiry_selector as es

    class _TD:
        def list_expirations(self, _ticker):
            return ["20261016", "20261218"]

    exp, _T = es.resolve_expiration(_TD(), "SPY", "2026-10-16", 0.25)
    assert exp == "20261016"
    assert "isn't listed" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Jump-model default
# ---------------------------------------------------------------------------


def test_jump_model_default_comes_from_desk_settings(monkeypatch, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Vol_Suite"))
    import volatility_suite as vs

    from shared.desk_settings import set_setting

    monkeypatch.delenv("JUMP_MODEL_DEFAULT", raising=False)
    monkeypatch.setattr(vs, "JUMP_MODEL_DEFAULT", "Bates")
    monkeypatch.setenv("DESK_SETTINGS_PATH", str(tmp_path / "settings.json"))
    assert vs.resolve_jump_model_default() == "Bates"
    set_setting("jump_model_default", "Kou")
    assert vs.resolve_jump_model_default() == "Kou"
    monkeypatch.setenv("JUMP_MODEL_DEFAULT", "Merton")
    monkeypatch.setattr(vs, "JUMP_MODEL_DEFAULT", "Merton")
    assert vs.resolve_jump_model_default() == "Merton"  # an explicit env var still wins
