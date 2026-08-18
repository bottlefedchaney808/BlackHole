"""Tests for analyze_scan.py's summarize()/print_table() logic -- the
re-viewer for main.py --universe's directional_scan_*.json output."""

import analyze_scan as az


def test_summarize_sorts_by_severity_then_highish_then_signal_count():
    results = {
        "LOW_TICK": {"severity": "LOW", "signals": ["A"], "narrative": {}, "scanners": {}, "oi": {}},
        "HIGH_TICK": {"severity": "HIGH", "signals": [], "narrative": {}, "scanners": {}, "oi": {}},
        "MED_TICK": {"severity": "MEDIUM", "signals": ["A", "B"], "narrative": {}, "scanners": {}, "oi": {}},
    }
    rows = az.summarize(results)
    assert [r["ticker"] for r in rows] == ["HIGH_TICK", "MED_TICK", "LOW_TICK"]


def test_summarize_counts_highish_signals_correctly():
    results = {
        "T": {"severity": "MEDIUM",
              "signals": ["GAMMA_SQUEEZE_RISK", "SOME_OTHER_SIGNAL"],
              "narrative": {}, "scanners": {}, "oi": {}},
    }
    rows = az.summarize(results)
    assert rows[0]["n_signals"] == 2
    assert rows[0]["n_highish"] == 1


def test_summarize_counts_ok_and_error_scanners():
    results = {
        "T": {"severity": "LOW", "signals": [], "narrative": {},
              "scanners": {
                  "gex": {"status": "ok"},
                  "unusual_oi": {"status": "error", "error": "boom"},
                  "iv_rank": {"status": "ok"},
              },
              "oi": {}},
    }
    rows = az.summarize(results)
    assert rows[0]["n_ok"] == 2
    assert rows[0]["n_err"] == 1
    assert rows[0]["scanners_ok"] == ["gex", "iv_rank"]


def test_summarize_reports_oi_ok_false_when_oi_snapshot_errored():
    results = {
        "T": {"severity": "LOW", "signals": [], "narrative": {}, "scanners": {},
              "oi": {"error": "no data"}},
    }
    rows = az.summarize(results)
    assert rows[0]["oi_ok"] is False


def test_summarize_handles_missing_narrative_gracefully():
    """narrative can be None (e.g. no_narrative_messages error case) --
    must not raise."""
    results = {
        "T": {"severity": "LOW", "signals": [], "narrative": None, "scanners": {}, "oi": {}},
    }
    rows = az.summarize(results)
    assert rows[0]["cns"] == 0
    assert rows[0]["war"] == 0


def test_print_table_does_not_raise_on_a_summarized_row(capsys):
    results = {
        "AAPL": {"severity": "HIGH", "signals": ["GAMMA_SQUEEZE_RISK"],
                 "narrative": {"cns": 80, "war": 0.5},
                 "scanners": {"gex": {"status": "ok"}}, "oi": {}},
    }
    az.print_table(az.summarize(results))
    out = capsys.readouterr().out
    assert "AAPL" in out
    assert "GAMMA_SQUEEZE_RISK" in out
