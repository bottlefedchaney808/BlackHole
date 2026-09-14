"""A module's artifacts must be its own, and its output dir must not leak.

Two ways a card ended up showing a neighbour's chart, both from modules that
report their output by LISTING a directory or by reading a process-global
env var while the dashboard runs modules concurrently in a threadpool:

* `garch_analysis.run_garch_module` globbed `{ticker}_garch_*` out of
  out_dir, so a previous run's PNGs came back as this run's -- confirmed
  live on SPY (six artifacts, three of them 11 seconds older).
* It also left `VS_OUTPUT_DIR` pointing at its own run directory forever
  after, and every other Vol_Suite chart writer reads that as its fallback.
"""

from __future__ import annotations

import os

import garch_analysis
import pytest


@pytest.mark.unit
def test_only_charts_this_run_wrote_are_reported(tmp_path, monkeypatch):
    stale = tmp_path / "SPY_garch_diagnostics_20260101_000000.png"
    stale.write_bytes(b"")
    fresh = tmp_path / "SPY_garch_diagnostics_20260102_000000.png"

    class _Res:
        conditional_volatility = None

    def _fake_run(ticker, start=None, end=None, **kwargs):
        fresh.write_bytes(b"")
        return _Res()

    monkeypatch.setattr(garch_analysis, "run_garch_analysis", _fake_run)
    result = garch_analysis.run_garch_module("SPY", output_dir=str(tmp_path))

    files, _interp, _vol = result
    assert [os.path.basename(f) for f in files] == [fresh.name]


@pytest.mark.unit
def test_vs_output_dir_is_restored(tmp_path, monkeypatch):
    """Leaving it set redirected every later module in the process."""

    class _Res:
        conditional_volatility = None

    monkeypatch.setattr(garch_analysis, "run_garch_analysis", lambda *a, **k: _Res())
    monkeypatch.setenv("VS_OUTPUT_DIR", "/somewhere/else")

    garch_analysis.run_garch_module("SPY", output_dir=str(tmp_path))
    assert os.environ["VS_OUTPUT_DIR"] == "/somewhere/else"


@pytest.mark.unit
def test_vs_output_dir_is_removed_when_it_was_unset(tmp_path, monkeypatch):
    class _Res:
        conditional_volatility = None

    monkeypatch.setattr(garch_analysis, "run_garch_analysis", lambda *a, **k: _Res())
    monkeypatch.delenv("VS_OUTPUT_DIR", raising=False)

    garch_analysis.run_garch_module("SPY", output_dir=str(tmp_path))
    assert "VS_OUTPUT_DIR" not in os.environ


@pytest.mark.unit
def test_variance_swap_plot_takes_an_explicit_out_dir():
    """The PNG used to be placed via the env var while the same call's CSVs
    took out_dir directly -- so the two halves of one result could land in
    two different run directories."""
    import inspect

    import variance_swap_live

    assert "out_dir" in inspect.signature(variance_swap_live.generate_plots).parameters
