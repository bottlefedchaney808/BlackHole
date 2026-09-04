"""GARCH seeding of the Market Signals stage's 1-year-out simulations.

The unified pipeline runs Vol_Suite (phase 1) *before* Market Signals (phase
2): Vol_Suite is the sole GARCH(1,1) fit for the ticker, and
`orchestrator._thread_vol_stats_into_context` copies its result into
`context['focus']['garch_conditional_vol']` before Market Signals runs. This
stage no longer fits GARCH itself -- it hands the real, shared `context`
straight to the mc_sim/copula/corr_sim builders, which prefer that context
value and only fall back to their own fit (see
`VaR_Tools_Simulations/main.py::_resolve_vol_and_quality`) when it's absent.
Fitting GARCH a second time here, redundant with Vol_Suite's own fit, used to
double ThetaData load for the ticker on every unified run.

Nothing here touches the network: the scanner import, the Direction suite and
the sim entry points are all stubbed.
"""

import pytest

import orchestrator


@pytest.fixture
def isolated_stage(monkeypatch):
    """Neuter every sub-piece of the stage except the sims under test."""
    def _boom(*a, **kw):
        raise RuntimeError('stubbed out')

    monkeypatch.setattr(orchestrator, '_import_sentiment_scanners', _boom)
    monkeypatch.setattr(orchestrator, '_import_direction_suite', _boom)
    return monkeypatch


class _FakeVarMain:
    """Stand-in for VaR_Tools_Simulations/main.py: records the payload each
    builder was handed."""

    def __init__(self):
        self.calls = []

    def _record(self, name):
        def builder(payload, ticker):
            self.calls.append((name, payload))
            return {'suite': 'var', 'status': 'ok', 'module': name}
        return builder

    def __getattr__(self, name):
        if name.startswith('_build_'):
            return self._record(name)
        raise AttributeError(name)


def _context(**over):
    ctx = {'focus': {'ticker': 'NVDA', 'garch_conditional_vol': None},
           'output_dir': None}
    ctx.update(over)
    return ctx


def test_stage_seeds_every_sim_from_the_real_context_vol_no_refit(isolated_stage):
    """Vol_Suite already fit GARCH (phase 1) and threaded 0.37 into context
    before this stage (phase 2) runs -- the stage must hand that same real
    context straight to every sim builder, not fit GARCH again itself."""
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)
    assert not hasattr(orchestrator, '_import_vol_garch'), \
        "the stage must not fit GARCH itself anymore -- Vol_Suite is the sole fit"

    context = _context(focus={'ticker': 'NVDA', 'garch_conditional_vol': 0.37})
    bundle = orchestrator.run_market_signals_stage('NVDA', context)

    assert len(fake_var.calls) == 3
    for _name, payload in fake_var.calls:
        assert payload is context
        assert payload['focus']['garch_conditional_vol'] == pytest.approx(0.37)
    assert set(bundle['simulations']) == {'mc_sim', 'copula', 'corr_sim'}


def test_stage_still_runs_its_sims_when_context_has_no_vol(isolated_stage):
    """Vol_Suite may have failed or found too little history -- the stage must
    still run its sims (each builder falls back to its own single fit)."""
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)

    context = _context()
    bundle = orchestrator.run_market_signals_stage('NVDA', context)

    assert len(fake_var.calls) == 3
    for _name, payload in fake_var.calls:
        assert payload['focus']['garch_conditional_vol'] is None
    assert all(sim.get('status') == 'ok' for sim in bundle['simulations'].values())


class _RecordingScanners:
    """Stand-in for the 4 option-chain scanners imported from sentiment-scanner.

    Records the exact (args, kwargs) each scan function was called with, so the
    test asserts on what `run_market_signals_stage` actually passes -- the
    `expiry=` wiring is the thing under test, not the scanner internals.
    """

    def __init__(self):
        self.calls = {}

    def _scan(self, key):
        def scan_fn(*args, **kwargs):
            self.calls[key] = (args, kwargs)
            return {'scanner': key}
        return scan_fn

    @staticmethod
    def _fmt(scan):
        return f"formatted {scan}"

    def as_import_tuple(self):
        # order must match _import_sentiment_scanners():
        # iv_rank, max_pain, skew, unusual_oi -- each (scan, format)
        return (self._scan('iv_rank'), self._fmt,
                self._scan('max_pain'), self._fmt,
                self._scan('skew'), self._fmt,
                self._scan('unusual_oi'), self._fmt)


def test_only_max_pain_is_pinned_to_the_run_expiry(monkeypatch):
    """Max Pain must be pinned to the expiry this run is analyzing; the other
    three scanners must still be called bare (they take no expiry)."""
    monkeypatch.setattr(orchestrator, '_import_direction_suite',
                        lambda: (_ for _ in ()).throw(RuntimeError('stubbed out')))
    monkeypatch.setattr(orchestrator, '_import_var_engine_builders',
                        lambda: _FakeVarMain())

    scanners = _RecordingScanners()
    # Phase 4: ensure hyphen-dir 'sentiment-scanner' is on path so 'scanner.*' (how
    # registry loads its sub scanners) is resolvable for monkeypatch; patch the
    # scan fns that the adapters call.
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent
    _sdir = _root / "sentiment-scanner"
    if str(_sdir) not in sys.path:
        sys.path.insert(0, str(_sdir))
    monkeypatch.setattr("scanner.iv_rank_scanner.scan_iv_rank", scanners._scan('iv_rank'))
    monkeypatch.setattr("scanner.max_pain_scanner.scan_max_pain", scanners._scan('max_pain'))
    monkeypatch.setattr("scanner.skew_scanner.scan_skew", scanners._scan('skew'))
    monkeypatch.setattr("scanner.unusual_oi_scanner.scan_unusual_oi", scanners._scan('unusual_oi'))

    context = _context(focus={'ticker': 'NVDA',
                              'garch_conditional_vol': None,
                              'expiration_date': '2026-10-16'})
    orchestrator.run_market_signals_stage('NVDA', context)

    assert set(scanners.calls) == {'iv_rank', 'max_pain', 'skew', 'unusual_oi'}

    mp_args, mp_kwargs = scanners.calls['max_pain']
    assert mp_args == ('NVDA',)
    assert mp_kwargs == {'expiry': '2026-10-16',
                         'garch_cond_vol_pct': None, 'fair_vol_pct': None}

    # iv_rank now takes the context GARCH/fair-vol kwargs (None here); skew and
    # unusual_oi stay bare.
    ir_args, ir_kwargs = scanners.calls['iv_rank']
    assert ir_args == ('NVDA',)
    assert ir_kwargs == {'garch_cond_vol_pct': None, 'fair_vol_pct': None}
    for key in ('skew', 'unusual_oi'):
        args, kwargs = scanners.calls[key]
        assert args == ('NVDA',), f"{key} should be called with just the ticker"
        assert kwargs == {}, f"{key} takes no expiry, got {kwargs}"


def test_max_pain_self_selects_when_the_context_carries_no_expiry(monkeypatch):
    """No expiry in context -> Max Pain still gets the kwarg, as None, so it
    falls back to its own nearest-~30DTE selection rather than erroring."""
    monkeypatch.setattr(orchestrator, '_import_direction_suite',
                        lambda: (_ for _ in ()).throw(RuntimeError('stubbed out')))
    monkeypatch.setattr(orchestrator, '_import_var_engine_builders',
                        lambda: _FakeVarMain())

    scanners = _RecordingScanners()
    # Phase 4: ensure hyphen-dir on path for 'scanner.*' resolvable
    import sys
    from pathlib import Path
    _root = Path(__file__).resolve().parent.parent
    _sdir = _root / "sentiment-scanner"
    if str(_sdir) not in sys.path:
        sys.path.insert(0, str(_sdir))
    monkeypatch.setattr("scanner.iv_rank_scanner.scan_iv_rank", scanners._scan('iv_rank'))
    monkeypatch.setattr("scanner.max_pain_scanner.scan_max_pain", scanners._scan('max_pain'))
    monkeypatch.setattr("scanner.skew_scanner.scan_skew", scanners._scan('skew'))
    monkeypatch.setattr("scanner.unusual_oi_scanner.scan_unusual_oi", scanners._scan('unusual_oi'))

    orchestrator.run_market_signals_stage('NVDA', _context())

    assert scanners.calls['max_pain'] == (
        ('NVDA',), {'expiry': None, 'garch_cond_vol_pct': None, 'fair_vol_pct': None})


def test_stage_never_mutates_the_context_vol(isolated_stage):
    """The stage only reads `context['focus']['garch_conditional_vol']` --
    it must never write to it (that's Vol_Suite's/`_thread_vol_stats_into_
    context`'s job, upstream of this stage)."""
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)

    context = _context(focus={'ticker': 'NVDA', 'garch_conditional_vol': 0.29})
    orchestrator.run_market_signals_stage('NVDA', context)

    assert context['focus']['garch_conditional_vol'] == pytest.approx(0.29)
    for _name, payload in fake_var.calls:
        assert payload['focus']['garch_conditional_vol'] == pytest.approx(0.29)


def test_vol_stats_fair_vol_flows_through_context_store(monkeypatch):
    """Phase 1 replaced `_thread_vol_stats_into_context` with the Context
    Store. fair_vol_pct/expected_return reach consumers via put/get under
    the ticker scope -- round-trip pinned here (was: direct context dict
    mutation by the removed suite runner)."""
    import os
    import tempfile

    from shared.context_store import ContextStore

    with tempfile.TemporaryDirectory() as td:
        store = ContextStore(db_path=os.path.join(td, "ctx.db"))
        # close before tmpdir cleanup: pooled connections hold the file open on Windows
        store.put(
            {"ticker": "NVDA"},
            "vol_stats",
            {"fair_vol_pct": 41.7, "expected_return": 0.12},
            source_slug="vol_suite",
        )
        got = store.get({"ticker": "NVDA"}, "vol_stats")
        assert got["fair_vol_pct"] == pytest.approx(41.7)
        assert got["expected_return"] == pytest.approx(0.12)
        assert not hasattr(orchestrator, "_thread_vol_stats_into_context")
        store.close()

