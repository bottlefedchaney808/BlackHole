"""Tests for the orchestrator's use of the validation layer.

These exercise the wiring rather than the rules (the rules are covered in
test_suite_validation.py): that `run_suite` validates the marker a child wrote,
folds a validation FAIL into the same `error` key a crash produces, logs a
PASS/FAIL row to orchestrator_runs, and that `--fail-on-suite-error` stops the
chain instead of feeding known-bad output downstream.

No child process is ever launched: `subprocess.run` is replaced with a fake that
writes whatever the scenario needs into the run's output_dir and reports an exit
code, which is the entire contract run_suite has with a child.
"""

import json
import os

import pytest

import orchestrator
from shared.schemas import VOL_RESULT_SCHEMA_VERSION


TS = '2026-07-29T12:00:00Z'


# ── payload factories ────────────────────────────────────────────────────

def vol_marker(ticker='NVDA', output_dir='/tmp/run', files=None, **over):
    payload = {
        'schema_version': VOL_RESULT_SCHEMA_VERSION,
        'suite': 'vol', 'status': 'ok', 'ticker': ticker, 'timestamp': TS,
        'vol_surface': {
            'focus_ticker': ticker, 'index_ticker': 'SPY',
            'expiration': '2026-10-16', 'target_years': 0.25,
            'focus': {'ticker': ticker, 'fair_vol_pct': 42.0},
            'index': None,
            'basket': {'tickers': [ticker], 'weights': [1.0]},
        },
        'dealer_positioning': {'available': False, 'sign_model': '3'},
        'gamma_records': [],
        'output_dir': output_dir,
        'produced_files': (['NVDA_hedging_heatmap.png'] if files is None else files),
    }
    payload.update(over)
    return payload


def valid_context(output_dir):
    """A schema_version=1 suite_context that passes validate_suite_context.

    run_unified now folds the sentiment block under a context-mutation audit
    that revalidates the whole object, so the stub context these tests hand it
    has to be genuinely valid or every run aborts for the wrong reason.
    """
    return {
        'schema_version': 1,
        'run_id': 'unified-test',
        'created_at_utc': TS,
        'output_dir': str(output_dir),
        'focus': {'ticker': 'NVDA', 'option_type': 'call', 'strike': None,
                  'target_years': 0.25, 'expiration_date': '2026-10-16'},
        'basket': {'index_ticker': 'SPY', 'tickers': ['NVDA'], 'weights': [1.0]},
        'sentiment': {'manifest_path': '/data/latest_manifest.json',
                      'pack_json_path': None, 'group_id': None,
                      'ranked_tickers': []},
        'var': {'horizon_days': 1, 'confidence': 0.99, 'positions': None},
        'controls': {'run_options_suite': True, 'run_var_suite': True,
                     'compile_pdf': False},
        'paths': {'options_suite_root': 'Options_Suite',
                  'var_suite_root': 'VaR_Tools_Simulations',
                  'sentiment_suite_root': 'sentiment-scanner'},
        'swap_activity': [],
    }


# ── harness ──────────────────────────────────────────────────────────────

class FakeProc:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """Stub interpreter + suite roots + log capture, so run_suite can run.

    Returns a namespace with `output_dir`, `logged` (the orchestrator_runs rows
    that would have been written) and `set_child(name, fn)` to install the
    behaviour of the fake child process.
    """
    python = tmp_path / 'python.exe'
    python.write_text('', encoding='utf-8')
    monkeypatch.setattr(orchestrator, 'SHARED_PYTHON', str(python))

    roots = {}
    for name, spec in orchestrator._SUITE_SPECS.items():
        root = tmp_path / f'{name}_root'
        root.mkdir()
        (root / spec['entrypoint']).write_text('', encoding='utf-8')
        roots[name] = str(root)
    monkeypatch.setattr(orchestrator, 'SUITE_ROOTS', roots)

    logged = []

    def fake_log_run(run_type, focus, started_at, completed_at, status, results):
        logged.append({'run_type': run_type, 'status': status, 'results': results})
        return len(logged)

    monkeypatch.setattr(orchestrator, 'log_run', fake_log_run)

    output_dir = tmp_path / 'run_output'
    output_dir.mkdir()

    state = {'name': 'vol', 'child': lambda out_path, output_dir: FakeProc()}

    def fake_subprocess_run(command, **kwargs):
        # Hand the child the paths the orchestrator computed for it rather than
        # re-deriving them from argv, which differs per suite.
        out_path = os.path.join(str(output_dir),
                                orchestrator.marker_filename(state['name']))
        return state['child'](out_path, str(output_dir))

    monkeypatch.setattr(orchestrator.subprocess, 'run', fake_subprocess_run)

    class Env:
        pass

    env = Env()
    env.output_dir = str(output_dir)
    env.logged = logged

    def set_child(name, fn):
        state['name'] = name
        state['child'] = fn

    env.set_child = set_child
    env.context = valid_context(output_dir)
    return env


def write_vol_csvs(output_dir, ticker='NVDA'):
    """The CSV set a healthy Vol_Suite focus workflow leaves behind."""
    for name in (f'{ticker}_gamma_records_20260729_120000.csv',
                 'correlation_matrix_20260729_120000.csv',
                 'correlation_pairs_20260729_120000.csv'):
        with open(os.path.join(output_dir, name), 'w', encoding='utf-8') as f:
            f.write('Strike,Expiry\n100,2026-08-21\n')


def validation_rows(logged):
    return [row for row in logged if row['run_type'].startswith('validate:')]


# ── run_suite: Vol_Suite ─────────────────────────────────────────────────

@pytest.mark.unit
def test_run_suite_passes_a_complete_vol_run(fake_env):
    def child(out_path, output_dir):
        write_vol_csvs(output_dir)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir), f)
        return FakeProc(returncode=0, stdout='done')

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5)

    assert 'error' not in result
    assert result['_validation']['status'] == 'PASS'


@pytest.mark.unit
def test_run_suite_detects_vol_suite_that_wrote_no_marker(fake_env):
    """Exit 0, empty output_dir: no marker means no proof of completion."""
    def child(out_path, output_dir):
        return FakeProc(returncode=0,
                        stdout='Basket/correlation engine failed: ...')

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5)

    assert 'error' in result, "a Vol_Suite run producing nothing must not read as ok"
    assert 'vol_result.json' in result['error']


@pytest.mark.unit
def test_run_suite_detects_vol_suite_that_produced_no_files(fake_env):
    """Marker written and schema-clean, but every analysis step was swallowed."""
    def child(out_path, output_dir):
        write_vol_csvs(output_dir)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir, files=[]), f)
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5)

    assert 'error' in result
    assert 'validation FAILED' in result['error']
    assert result['validation']['status'] == 'FAIL'
    assert any('produced_files is empty' in e
               for e in result['validation']['errors'])


@pytest.mark.unit
def test_run_suite_detects_vol_suite_missing_required_csvs(fake_env):
    """Marker fine, PNGs rendered, dealer positioning never wrote its CSV."""
    def child(out_path, output_dir):
        with open(os.path.join(output_dir, 'NVDA_heatmap.png'), 'w') as f:
            f.write('x')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir), f)
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5)

    assert 'error' in result
    assert 'NVDA_gamma_records_*.csv' in result['validation']['missing_files']
    # The marker itself was fine: the failure is specifically the artifact set.
    assert all(c['status'] == 'PASS' for c in result['validation']['checks']
               if c['check'] == 'schema_valid')


@pytest.mark.unit
def test_run_suite_detects_truncated_vol_marker(fake_env):
    def child(out_path, output_dir):
        write_vol_csvs(output_dir)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('{"suite": "vol", "status": "o')
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5)

    assert 'error' in result


@pytest.mark.unit
def test_run_suite_validation_can_be_disabled(fake_env):
    def child(out_path, output_dir):
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir, files=[]), f)
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)

    result = orchestrator.run_suite('vol', fake_env.context, timeout=5,
                                    validate=False)

    assert 'error' not in result
    assert '_validation' not in result
    assert validation_rows(fake_env.logged) == []


# ── run_suite: context-out suites ────────────────────────────────────────

@pytest.mark.unit
def test_run_suite_passes_valid_options_marker(fake_env):
    def child(out_path, output_dir):
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'suite': 'options', 'status': 'ok', 'ticker': 'NVDA',
                       'method': 'CRR', 'sigma': 0.4, 'price': 12.0,
                       'greeks': {'delta': 0.5}, 'timestamp': TS}, f)
        return FakeProc(returncode=0)

    fake_env.set_child('options', child)

    result = orchestrator.run_suite('options', fake_env.context, timeout=5)

    assert 'error' not in result
    assert result['_validation']['status'] == 'PASS'


@pytest.mark.unit
def test_run_suite_rejects_structurally_invalid_marker(fake_env):
    """Exit 0 and a well-formed JSON file that is missing the greeks block."""
    def child(out_path, output_dir):
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'suite': 'options', 'status': 'ok', 'ticker': 'NVDA',
                       'method': 'CRR', 'sigma': 0.4, 'price': 12.0,
                       'timestamp': TS}, f)
        return FakeProc(returncode=0)

    fake_env.set_child('options', child)

    result = orchestrator.run_suite('options', fake_env.context, timeout=5)

    assert 'error' in result
    assert result['validation']['status'] == 'FAIL'


@pytest.mark.unit
def test_run_suite_marker_path_is_the_context_out_path(fake_env):
    """The `--context-out` argv value must be the file validation looks for."""
    seen = {}

    def fake_subprocess_run(command, **kwargs):
        seen['command'] = command
        out_path = os.path.join(fake_env.output_dir, 'options_result.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'suite': 'options', 'status': 'ok', 'ticker': 'NVDA',
                       'method': 'CRR', 'sigma': 0.4, 'price': 12.0,
                       'greeks': {'delta': 0.5}, 'timestamp': TS}, f)
        return FakeProc(returncode=0)

    orchestrator.subprocess.run = fake_subprocess_run
    try:
        orchestrator.run_suite('options', fake_env.context, timeout=5)
    finally:
        pass

    argv_out = seen['command'][seen['command'].index('--context-out') + 1]
    assert os.path.basename(argv_out) == orchestrator.marker_filename('options')


# ── orchestrator_runs logging ────────────────────────────────────────────

@pytest.mark.unit
def test_validation_verdict_is_logged_pass(fake_env):
    def child(out_path, output_dir):
        write_vol_csvs(output_dir)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir), f)
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)
    orchestrator.run_suite('vol', fake_env.context, timeout=5)

    rows = validation_rows(fake_env.logged)
    assert len(rows) == 1
    assert rows[0]['run_type'] == 'validate:vol'
    assert rows[0]['status'] == 'PASS'
    assert rows[0]['results']['checks'], "per-check breakdown must be logged"
    # The audit row has to survive the json.dumps log_run does.
    json.dumps(rows[0]['results'], default=str)


@pytest.mark.unit
def test_validation_verdict_is_logged_fail_with_suite_row_marked_invalid(fake_env):
    def child(out_path, output_dir):
        write_vol_csvs(output_dir)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(vol_marker(output_dir=output_dir, files=[]), f)
        return FakeProc(returncode=0)

    fake_env.set_child('vol', child)
    orchestrator.run_suite('vol', fake_env.context, timeout=5)

    rows = validation_rows(fake_env.logged)
    assert [r['status'] for r in rows] == ['FAIL']
    suite_rows = [r for r in fake_env.logged if r['run_type'] == 'suite:vol']
    assert [r['status'] for r in suite_rows] == ['invalid']


# ── run_unified: --fail-on-suite-error ───────────────────────────────────

@pytest.fixture
def unified_env(tmp_path, monkeypatch):
    """run_unified with build_context/run_suite/log_run stubbed out."""
    output_dir = tmp_path / 'unified_output'
    output_dir.mkdir()

    context = valid_context(output_dir)
    monkeypatch.setattr(orchestrator, 'build_context',
                        lambda focus, controls=None: context)
    monkeypatch.setattr(orchestrator, 'log_run', lambda *a, **k: None)
    # write_suite_context would re-serialize this stub context; the caller
    # already tolerates that failing, but keep the test output clean.
    monkeypatch.setattr(orchestrator, '_import_suite_context',
                        lambda: (_ for _ in ()).throw(RuntimeError('stubbed')))

    calls = []
    # The sentiment stage's payload is not inert here: stage 1b folds it into
    # the context under audit, and the audit rejects anything that is not a
    # valid schema_version=2 sentiment context export. So the default "this
    # stage succeeded" result for sentiment has to be a real one.
    outcomes = {
        'sentiment': {
            'suite': 'sentiment', 'status': 'ok',
            'schema_version': 2, 'run_id': 'sent-1', 'created_at_utc': TS,
            'sentiment': {
                'manifest_path': '/data/run_manifest.json',
                'pack_json_path': '/data/run_pack.json',
                'group_id': 'cns-threshold-alerts',
                'ranked_tickers': ['NVDA'],
            },
            '_validation': {'status': 'PASS'},
        },
    }

    def fake_run_suite(name, ctx, timeout=1800, validate=True):
        calls.append(name)
        return outcomes.get(name, {'suite': name, 'status': 'ok',
                                   '_validation': {'status': 'PASS'}})

    monkeypatch.setattr(orchestrator, 'run_suite', fake_run_suite)

    class Env:
        pass

    env = Env()
    env.calls = calls
    env.outcomes = outcomes
    env.context = context
    return env


def invalid(name):
    return {
        'suite': name,
        'error': f'{name} output validation FAILED: marker_present: missing',
        'validation': {'status': 'FAIL', 'errors': ['marker_present: missing']},
    }


@pytest.mark.unit
def test_unified_continues_past_a_failed_stage_by_default(unified_env):
    unified_env.outcomes['vol'] = invalid('vol')

    combined = orchestrator.run_unified({'ticker': 'NVDA'})

    assert unified_env.calls == ['sentiment', 'vol', 'options', 'var']
    assert combined['status'] == 'partial'
    assert combined['aborted_by'] is None


@pytest.mark.unit
def test_unified_aborts_on_invalid_vol_output_when_flag_set(unified_env):
    unified_env.outcomes['vol'] = invalid('vol')

    combined = orchestrator.run_unified({'ticker': 'NVDA'},
                                        fail_on_suite_error=True)

    assert unified_env.calls == ['sentiment', 'vol'], \
        "options/var must not run against unvalidated Vol_Suite output"
    assert combined['status'] == 'aborted'
    assert combined['aborted_by'] == 'vol'
    for stage in ('options', 'var'):
        assert combined['results'][stage]['skipped'] is True
        assert combined['results'][stage]['blocked_by'] == 'vol'
        assert 'error' in combined['results'][stage]


@pytest.mark.unit
def test_unified_aborts_at_sentiment_and_skips_everything_downstream(unified_env):
    unified_env.outcomes['sentiment'] = invalid('sentiment')

    combined = orchestrator.run_unified({'ticker': 'NVDA'},
                                        fail_on_suite_error=True)

    assert unified_env.calls == ['sentiment']
    assert combined['aborted_by'] == 'sentiment'
    assert all(combined['results'][s]['skipped'] for s in ('vol', 'options', 'var'))


@pytest.mark.unit
def test_unified_reads_flag_from_focus_when_not_passed_explicitly(unified_env):
    unified_env.outcomes['vol'] = invalid('vol')

    combined = orchestrator.run_unified({'ticker': 'NVDA',
                                         'fail_on_suite_error': True})

    assert combined['status'] == 'aborted'
    assert unified_env.calls == ['sentiment', 'vol']


@pytest.mark.unit
def test_unified_all_ok_records_per_stage_validation_status(unified_env):
    combined = orchestrator.run_unified({'ticker': 'NVDA'},
                                        fail_on_suite_error=True)

    assert combined['status'] == 'ok'
    assert combined['validation'] == {'sentiment': 'PASS', 'vol': 'PASS',
                                      'options': 'PASS', 'var': 'PASS'}


@pytest.mark.unit
def test_unified_does_not_fold_invalid_sentiment_block_into_context(unified_env):
    """A failed sentiment stage must not overwrite the context's manifest path."""
    bad = invalid('sentiment')
    bad['sentiment'] = {'manifest_path': '/half/written.json'}
    unified_env.outcomes['sentiment'] = bad

    orchestrator.run_unified({'ticker': 'NVDA'})

    assert unified_env.context['sentiment']['manifest_path'] == \
        '/data/latest_manifest.json'


@pytest.mark.unit
def test_unified_validate_false_is_threaded_to_every_stage(unified_env, monkeypatch):
    seen = []
    ok = unified_env.outcomes['sentiment']

    def fake_run_suite(name, ctx, timeout=1800, validate=True):
        seen.append((name, validate))
        return ok if name == 'sentiment' else {'suite': name, 'status': 'ok'}

    monkeypatch.setattr(orchestrator, 'run_suite', fake_run_suite)

    orchestrator.run_unified({'ticker': 'NVDA'}, validate=False)

    assert seen == [('sentiment', False), ('vol', False),
                    ('options', False), ('var', False)]


# ── CLI ──────────────────────────────────────────────────────────────────

def _combined_stub(focus, **over):
    combined = {
        'run_id': 'x', 'output_dir': '',
        'focus': {'ticker': 'NVDA', 'option_type': 'call', 'strike': None,
                  'expiration_date': '2026-10-16'},
        'swap_activity_rows': 0, 'results': {}, 'status': 'ok',
        'aborted_by': None,
    }
    combined.update(over)
    return combined


@pytest.mark.unit
def test_cli_exposes_fail_on_suite_error(monkeypatch):
    captured = {}

    def fake_run_unified(focus, fail_on_suite_error=None, validate=True):
        captured['fail'] = fail_on_suite_error
        captured['validate'] = validate
        captured['focus'] = focus
        return _combined_stub(focus)

    monkeypatch.setattr(orchestrator, 'run_unified', fake_run_unified)
    monkeypatch.setattr(orchestrator, '_warn_if_schema_outdated', lambda: None)

    rc = orchestrator.main(['--unified', '--ticker', 'NVDA',
                            '--fail-on-suite-error'])

    assert rc == 0
    assert captured['fail'] is True
    assert captured['validate'] is True
    assert captured['focus']['fail_on_suite_error'] is True


@pytest.mark.unit
def test_cli_defaults_to_not_failing_on_suite_error(monkeypatch):
    captured = {}

    def fake_run_unified(focus, fail_on_suite_error=None, validate=True):
        captured['fail'] = fail_on_suite_error
        return _combined_stub(focus)

    monkeypatch.setattr(orchestrator, 'run_unified', fake_run_unified)
    monkeypatch.setattr(orchestrator, '_warn_if_schema_outdated', lambda: None)

    orchestrator.main(['--unified', '--ticker', 'NVDA'])

    assert captured['fail'] is False


@pytest.mark.unit
def test_cli_rejects_no_validate_with_fail_on_suite_error(monkeypatch):
    monkeypatch.setattr(orchestrator, '_warn_if_schema_outdated', lambda: None)

    with pytest.raises(SystemExit) as exc:
        orchestrator.main(['--unified', '--ticker', 'NVDA',
                           '--fail-on-suite-error', '--no-validate'])

    assert exc.value.code == 2


@pytest.mark.unit
def test_cli_returns_nonzero_when_a_stage_is_invalid(monkeypatch):
    def fake_run_unified(focus, fail_on_suite_error=None, validate=True):
        return _combined_stub(focus, results={'vol': invalid('vol')},
                              status='aborted', aborted_by='vol')

    monkeypatch.setattr(orchestrator, 'run_unified', fake_run_unified)
    monkeypatch.setattr(orchestrator, '_warn_if_schema_outdated', lambda: None)

    rc = orchestrator.main(['--unified', '--ticker', 'NVDA',
                            '--fail-on-suite-error'])

    assert rc == 1


@pytest.mark.unit
def test_summarize_reports_skipped_stages_and_validation_errors():
    combined = _combined_stub(
        None,
        status='aborted', aborted_by='vol',
        results={
            'sentiment': {'suite': 'sentiment', 'status': 'ok',
                          '_validation': {'status': 'PASS'}},
            'vol': invalid('vol'),
            'options': {'suite': 'options', 'skipped': True,
                        'blocked_by': 'vol', 'error': 'skipped: ...'},
        })

    text = orchestrator._summarize(combined)

    assert 'aborted_by=vol' in text
    assert 'sentiment=ok' in text and 'validation=PASS' in text
    assert 'vol=FAILED' in text
    assert 'marker_present: missing' in text
    assert 'options=SKIPPED' in text


# ── run_unified: context mutation audit ──────────────────────────────────

def valid_sentiment_export(**over):
    """A sentiment stage result that satisfies the schema_version=2 contract."""
    payload = {
        'suite': 'sentiment',
        'schema_version': 2,
        'run_id': 'unified-test',
        'created_at_utc': '2026-07-29T12:00:00Z',
        'sentiment': {
            'manifest_path': '/run/latest_manifest.json',
            'pack_json_path': '/run/pack.json',
            'group_id': 'cns-threshold-alerts',
            'ranked_tickers': ['NVDA', 'GME'],
        },
        '_validation': {'status': 'PASS'},
    }
    payload.update(over)
    return payload


@pytest.mark.unit
def test_unified_audits_the_sentiment_fold(unified_env):
    unified_env.outcomes['sentiment'] = valid_sentiment_export()

    combined = orchestrator.run_unified({'ticker': 'NVDA'})

    audit = combined['context_audit']
    assert audit['validation_status'] == 'PASS'
    assert 'sentiment.manifest_path' in audit['mutations_detected']
    assert audit['before_context']['sha256'] != audit['after_context']['sha256']
    assert audit['rolled_back'] is False
    # The audit is not a suite: it must not count towards the per-suite tally.
    assert 'context_audit' not in combined['results']
    assert combined['status'] == 'ok'


@pytest.mark.unit
def test_unified_still_folds_the_producer_block_in(unified_env):
    unified_env.outcomes['sentiment'] = valid_sentiment_export()

    orchestrator.run_unified({'ticker': 'NVDA'})

    context = orchestrator.build_context({}, None)
    assert context['sentiment']['manifest_path'] == '/run/latest_manifest.json'
    assert context['sentiment']['ranked_tickers'] == ['NVDA', 'GME']


@pytest.mark.unit
def test_unified_rolls_back_and_fails_on_a_bad_mutation(unified_env, monkeypatch):
    import shared.context_audit as ca

    def wrecking_fold(ctx, payload):
        ctx['sentiment']['ranked_tickers'] = {'NVDA': 1}   # schema says list
        return ['sentiment.ranked_tickers']

    monkeypatch.setattr(ca, 'fold_sentiment_block', wrecking_fold)
    unified_env.outcomes['sentiment'] = valid_sentiment_export()

    combined = orchestrator.run_unified({'ticker': 'NVDA'})

    # A damaged context is not degradable: nothing downstream may consume it,
    # with or without --fail-on-suite-error.
    assert unified_env.calls == ['sentiment']
    assert combined['status'] == 'error'
    assert combined['aborted_by'] == 'context_audit'
    assert combined['context_audit']['rolled_back'] is True
    for stage in ('vol', 'options', 'var'):
        assert combined['results'][stage]['skipped'] is True
        assert 'rolled back' in combined['results'][stage]['error']

    # Rolled back in place, so the object the stages hold is the baseline again.
    context = orchestrator.build_context({}, None)
    assert context['sentiment']['ranked_tickers'] == []


@pytest.mark.unit
def test_unified_audit_passes_when_the_producer_stage_failed(unified_env):
    unified_env.outcomes['sentiment'] = invalid('sentiment')

    combined = orchestrator.run_unified({'ticker': 'NVDA'})

    # A scanner crash is not context corruption: the audit passes with zero
    # mutations and the chain continues on the untouched context.
    assert combined['context_audit']['validation_status'] == 'PASS'
    assert combined['context_audit']['mutations_detected'] == []
    assert combined['aborted_by'] is None
    assert unified_env.calls == ['sentiment', 'vol', 'options', 'var']


@pytest.mark.unit
def test_summarize_reports_the_context_audit():
    combined = _combined_stub(
        None,
        status='error', aborted_by='context_audit',
        context_audit={
            'validation_status': 'FAIL',
            'mutations_detected': ['sentiment.manifest_path'],
            'rolled_back': True,
            'validation_errors': ['post_mutation_schema: not a list'],
        })

    text = orchestrator._summarize(combined)

    assert 'context_audit=FAIL' in text
    assert 'ROLLED_BACK' in text
    assert 'mutated: sentiment.manifest_path' in text
    assert 'post_mutation_schema: not a list' in text
    assert 'context mutation audit FAILED' in text
