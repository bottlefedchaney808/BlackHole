"""GARCH seeding of the Market Signals stage's 1-year-out simulations.

The unified pipeline runs Market Signals (phase 1) *before* Vol_Suite (phase 2),
so `suite_context['focus']['garch_conditional_vol']` -- which Vol_Suite fills in
-- is still None when this stage's MC/copula/corr sims run. Rather than reorder
the pipeline, the stage fits GARCH once itself and hands the result to the sim
builders through a private copy of the context; the shared context object other
stages read must come back unmodified.

Nothing here touches the network: the scanner import, the Direction suite and
the GARCH/sim entry points are all stubbed.
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


def test_stage_fits_garch_once_and_seeds_every_sim(isolated_stage):
    fits = []

    def fake_run_garch_module(ticker, output_dir=None):
        fits.append(ticker)
        return ([], 'interpretation', 0.37)

    isolated_stage.setattr(orchestrator, '_import_vol_garch',
                           lambda: fake_run_garch_module)
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)

    context = _context()
    bundle = orchestrator.run_market_signals_stage('NVDA', context)

    # one fit, shared by all three sims
    assert fits == ['NVDA']
    assert len(fake_var.calls) == 3
    for _name, payload in fake_var.calls:
        assert payload['focus']['garch_conditional_vol'] == pytest.approx(0.37)

    # the caller's context is untouched -- later stages must not see this value
    assert context['focus']['garch_conditional_vol'] is None
    assert set(bundle['simulations']) == {'mc_sim', 'copula', 'corr_sim'}


def test_stage_survives_a_failing_garch_fit(isolated_stage):
    def exploding_garch(*a, **kw):
        raise RuntimeError('arch package missing')

    isolated_stage.setattr(orchestrator, '_import_vol_garch',
                           lambda: exploding_garch)
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)

    context = _context()
    bundle = orchestrator.run_market_signals_stage('NVDA', context)

    # sims still run, just without a context vol to prefer
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
    monkeypatch.setattr(orchestrator, '_import_vol_garch',
                        lambda: lambda ticker, output_dir=None: ([], '', None))
    monkeypatch.setattr(orchestrator, '_import_var_engine_builders',
                        lambda: _FakeVarMain())

    scanners = _RecordingScanners()
    monkeypatch.setattr(orchestrator, '_import_sentiment_scanners',
                        scanners.as_import_tuple)

    context = _context(focus={'ticker': 'NVDA',
                              'garch_conditional_vol': None,
                              'expiration_date': '2026-10-16'})
    orchestrator.run_market_signals_stage('NVDA', context)

    assert set(scanners.calls) == {'iv_rank', 'max_pain', 'skew', 'unusual_oi'}

    mp_args, mp_kwargs = scanners.calls['max_pain']
    assert mp_args == ('NVDA',)
    assert mp_kwargs == {'expiry': '2026-10-16'}

    for key in ('iv_rank', 'skew', 'unusual_oi'):
        args, kwargs = scanners.calls[key]
        assert args == ('NVDA',), f"{key} should be called with just the ticker"
        assert kwargs == {}, f"{key} takes no expiry, got {kwargs}"


def test_max_pain_self_selects_when_the_context_carries_no_expiry(monkeypatch):
    """No expiry in context -> Max Pain still gets the kwarg, as None, so it
    falls back to its own nearest-~30DTE selection rather than erroring."""
    monkeypatch.setattr(orchestrator, '_import_direction_suite',
                        lambda: (_ for _ in ()).throw(RuntimeError('stubbed out')))
    monkeypatch.setattr(orchestrator, '_import_vol_garch',
                        lambda: lambda ticker, output_dir=None: ([], '', None))
    monkeypatch.setattr(orchestrator, '_import_var_engine_builders',
                        lambda: _FakeVarMain())

    scanners = _RecordingScanners()
    monkeypatch.setattr(orchestrator, '_import_sentiment_scanners',
                        scanners.as_import_tuple)

    orchestrator.run_market_signals_stage('NVDA', _context())

    assert scanners.calls['max_pain'] == (('NVDA',), {'expiry': None})


def test_stage_keeps_an_existing_context_vol_when_the_fit_yields_nothing(isolated_stage):
    """A GARCH module that converged on no usable vol must not blank out a
    value the context already carried (e.g. a re-run over a filled context)."""
    isolated_stage.setattr(orchestrator, '_import_vol_garch',
                           lambda: lambda ticker, output_dir=None: ([], '', None))
    fake_var = _FakeVarMain()
    isolated_stage.setattr(orchestrator, '_import_var_engine_builders',
                           lambda: fake_var)

    context = _context(focus={'ticker': 'NVDA', 'garch_conditional_vol': 0.29})
    orchestrator.run_market_signals_stage('NVDA', context)

    for _name, payload in fake_var.calls:
        assert payload['focus']['garch_conditional_vol'] == pytest.approx(0.29)
