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
