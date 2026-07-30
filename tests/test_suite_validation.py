"""Tests for explicit suite-output validation.

The centre of gravity here is Vol_Suite. It is the one suite that writes no
result JSON of its own, catches its own per-step exceptions, and can therefore
exit 0 having produced nothing -- which is precisely the failure the validation
layer exists to catch. Everything below marked `vol` is a variation on "the
child said it was fine; the filesystem disagrees".
"""

import json
import os

import pytest

from shared.schemas import VOL_RESULT_SCHEMA_VERSION
from shared.suite_validation import (
    FAIL,
    PASS,
    WARN,
    SUITE_REQUIREMENTS,
    SuiteValidationError,
    canonical_suite_name,
    marker_filename,
    require_suite_output,
    validate_suite_output,
)

TS = '2026-07-29T12:00:00Z'


# ── payload factories ────────────────────────────────────────────────────

def vol_payload(ticker='NVDA', output_dir='/tmp/run', files=None, **over):
    """A minimal but schema-valid `vol_result.json`, as run_context_mode writes.

    One resolved replication leg and dealer positioning unavailable is the
    thinnest payload `shared.schemas.validate_vol_result` accepts on
    status='ok' -- which keeps these tests about the orchestration-level checks
    (marker presence, produced_files, CSV globs) rather than re-testing the
    payload schema that module already owns.
    """
    payload = {
        'schema_version': VOL_RESULT_SCHEMA_VERSION,
        'suite': 'vol',
        'status': 'ok',
        'ticker': ticker,
        'timestamp': TS,
        'vol_surface': {
            'focus_ticker': ticker,
            'index_ticker': 'SPY',
            'expiration': '2026-10-16',
            'target_years': 0.25,
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


def options_payload(**over):
    payload = {
        'suite': 'options', 'status': 'ok', 'ticker': 'NVDA', 'method': 'CRR',
        'sigma': 0.43, 'price': 26.6, 'greeks': {'delta': 0.72}, 'timestamp': TS,
    }
    payload.update(over)
    return payload


def var_payload(**over):
    payload = {
        'suite': 'var', 'status': 'ok', 'module': 'corr_sim', 'var': 36693.8,
        'cvar': 41994.8, 'confidence': 0.99, 'horizon_days': 1, 'timestamp': TS,
    }
    payload.update(over)
    return payload


def sentiment_payload(**over):
    payload = {
        'schema_version': 2,
        'run_id': 'run-1',
        'created_at_utc': TS,
        'sentiment': {
            'manifest_path': '/data/latest_manifest.json',
            'pack_json_path': '/data/pack.json',
            'group_id': 'cns-threshold-alerts',
            'ranked_tickers': ['NVDA', 'SOFI'],
        },
    }
    payload.update(over)
    return payload


# ── filesystem helpers ───────────────────────────────────────────────────

def write_marker(output_dir, suite, payload, raw=None):
    path = os.path.join(str(output_dir), marker_filename(suite))
    with open(path, 'w', encoding='utf-8') as f:
        if raw is not None:
            f.write(raw)
        else:
            json.dump(payload, f)
    return path


def write_vol_csvs(output_dir, ticker='NVDA',
                   gamma=True, corr_matrix=True, corr_pairs=True):
    """Drop the CSV set a healthy Vol_Suite focus workflow leaves behind."""
    names = []
    if gamma:
        names.append(f'{ticker}_gamma_records_20260729_120000.csv')
    if corr_matrix:
        names.append('correlation_matrix_20260729_120000.csv')
    if corr_pairs:
        names.append('correlation_pairs_20260729_120000.csv')
    for name in names:
        with open(os.path.join(str(output_dir), name), 'w', encoding='utf-8') as f:
            f.write('Strike,Expiry\n100,2026-08-21\n')
    return names


def check_status(result, name_prefix):
    """Status of the first check whose name starts with *name_prefix*."""
    for check in result.checks:
        if check.name.startswith(name_prefix):
            return check.status
    return None


# ── name resolution ──────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize('alias,expected', [
    ('vol', 'vol'), ('Vol_Suite', 'vol'), ('vol-suite', 'vol'),
    ('VOLATILITY_SUITE', 'vol'),
    ('options', 'options'), ('Options_Suite', 'options'),
    ('var', 'var'), ('VaR_Tools', 'var'), ('VaR_Tools_Simulations', 'var'),
    ('sentiment', 'sentiment'), ('sentiment-scanner', 'sentiment'),
])
def test_canonical_suite_name_accepts_directory_aliases(alias, expected):
    assert canonical_suite_name(alias) == expected


@pytest.mark.unit
def test_canonical_suite_name_rejects_unknown():
    with pytest.raises(KeyError):
        canonical_suite_name('Vol_Suit')


@pytest.mark.unit
def test_marker_filenames_match_orchestrator_context_out_names():
    # run_suite derives its --context-out path from marker_filename(), so these
    # two must agree or the child writes somewhere validation never looks.
    assert marker_filename('Vol_Suite') == 'vol_result.json'
    assert marker_filename('Options_Suite') == 'options_result.json'
    assert marker_filename('VaR_Tools') == 'var_result.json'
    assert marker_filename('sentiment-scanner') == 'sentiment_result.json'
    for key, requirement in SUITE_REQUIREMENTS.items():
        assert requirement.marker == f'{key}_result.json'


# ── Vol_Suite: the happy path ────────────────────────────────────────────

@pytest.mark.unit
def test_vol_passes_with_marker_and_all_required_csvs(tmp_path):
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert result.passed
    assert result.status == PASS
    assert result.errors == []
    assert result.missing_files == []
    assert check_status(result, 'required_file[NVDA_gamma_records') == PASS


# ── Vol_Suite: failure detection ─────────────────────────────────────────

@pytest.mark.unit
def test_vol_fails_when_marker_missing(tmp_path):
    """Vol_Suite exited 0 and wrote nothing at all."""
    write_vol_csvs(tmp_path)   # CSVs alone are not proof of completion

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert 'vol_result.json' in result.missing_files
    assert any('marker' in e for e in result.errors)


@pytest.mark.unit
def test_vol_fails_when_produced_files_empty(tmp_path):
    """Marker written, every analysis step silently swallowed its exception."""
    write_marker(tmp_path, 'vol',
                 vol_payload(output_dir=str(tmp_path), files=[]))
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert check_status(result, 'schema_valid') == FAIL
    assert any('produced_files is empty' in e for e in result.errors)


@pytest.mark.unit
def test_vol_fails_when_gamma_records_csv_missing(tmp_path):
    """Dealer positioning blew up; heatmap PNGs exist but no gamma CSV does."""
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, gamma=False)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert 'NVDA_gamma_records_*.csv' in result.missing_files
    assert check_status(result, 'required_file[NVDA_gamma_records') == FAIL
    # The marker itself was fine -- the failure is specifically the artifact.
    assert check_status(result, 'schema_valid') == PASS


@pytest.mark.unit
def test_vol_fails_when_correlation_csvs_missing(tmp_path):
    """Basket/correlation step failed; only dealer positioning survived."""
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, corr_matrix=False, corr_pairs=False)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert set(result.missing_files) == {
        'correlation_matrix_*.csv', 'correlation_pairs_*.csv'}


@pytest.mark.unit
def test_vol_gamma_csv_for_a_different_ticker_does_not_count(tmp_path):
    """A leftover CSV from a previous run's ticker must not satisfy this run."""
    write_marker(tmp_path, 'vol', vol_payload(ticker='NVDA',
                                              output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, ticker='SPY')

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert 'NVDA_gamma_records_*.csv' in result.missing_files


@pytest.mark.unit
def test_vol_ticker_falls_back_to_marker_payload(tmp_path):
    """No ticker passed in -- the marker's own ticker drives the glob."""
    write_marker(tmp_path, 'vol', vol_payload(ticker='TSLA',
                                              output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, ticker='TSLA')

    result = validate_suite_output('Vol_Suite', str(tmp_path))

    assert result.passed


@pytest.mark.unit
def test_vol_fails_on_zero_byte_marker(tmp_path):
    open(os.path.join(str(tmp_path), 'vol_result.json'), 'w').close()
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert check_status(result, 'marker_non_empty') == FAIL


@pytest.mark.unit
def test_vol_fails_on_truncated_marker(tmp_path):
    """Child killed mid-write: the JSON is half a file."""
    write_marker(tmp_path, 'vol', None, raw='{"suite": "vol", "status": "o')
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert check_status(result, 'marker_parses') == FAIL


@pytest.mark.unit
def test_vol_fails_when_marker_reports_error_status(tmp_path):
    write_marker(tmp_path, 'vol', {
        'schema_version': VOL_RESULT_SCHEMA_VERSION,
        'suite': 'vol', 'status': 'error', 'error': 'EOFError on prompt 3',
        'ticker': 'NVDA', 'timestamp': TS,
        'vol_surface': {}, 'dealer_positioning': {}, 'gamma_records': [],
    })
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert not result.passed
    assert check_status(result, 'reported_status') == FAIL
    assert any('EOFError' in e for e in result.errors)


@pytest.mark.unit
def test_vol_fails_when_output_dir_does_not_exist(tmp_path):
    missing = os.path.join(str(tmp_path), 'never_created')

    result = validate_suite_output('Vol_Suite', missing, ticker='NVDA')

    assert not result.passed
    assert check_status(result, 'output_dir_exists') == FAIL


@pytest.mark.unit
def test_vol_non_strict_downgrades_missing_csvs_to_warnings(tmp_path):
    """SUITE_VALIDATION_STRICT=0: artifact gaps warn, marker checks still bind."""
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, corr_matrix=False, corr_pairs=False)

    result = validate_suite_output('Vol_Suite', str(tmp_path),
                                   ticker='NVDA', strict=False)

    assert result.passed
    assert result.errors == []
    assert len(result.warnings) == 2
    assert check_status(result, 'required_file[correlation_matrix') == WARN


@pytest.mark.unit
def test_vol_non_strict_still_fails_a_missing_marker(tmp_path):
    write_vol_csvs(tmp_path)

    result = validate_suite_output('Vol_Suite', str(tmp_path),
                                   ticker='NVDA', strict=False)

    assert not result.passed


@pytest.mark.unit
def test_strict_default_reads_environment(tmp_path, monkeypatch):
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, gamma=False)

    monkeypatch.setenv('SUITE_VALIDATION_STRICT', '0')
    assert validate_suite_output('vol', str(tmp_path), ticker='NVDA').passed

    monkeypatch.setenv('SUITE_VALIDATION_STRICT', '1')
    assert not validate_suite_output('vol', str(tmp_path), ticker='NVDA').passed


@pytest.mark.unit
def test_supplied_payload_is_validated_but_marker_still_required(tmp_path):
    """Passing the in-memory payload does not waive the on-disk marker."""
    write_vol_csvs(tmp_path)
    payload = vol_payload(output_dir=str(tmp_path))

    result = validate_suite_output('vol', str(tmp_path), payload=payload,
                                   ticker='NVDA')
    assert not result.passed

    write_marker(tmp_path, 'vol', payload)
    assert validate_suite_output('vol', str(tmp_path), payload=payload,
                                 ticker='NVDA').passed


# ── other suites ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_options_marker_passes(tmp_path):
    write_marker(tmp_path, 'options', options_payload())
    assert validate_suite_output('Options_Suite', str(tmp_path)).passed


@pytest.mark.unit
def test_options_marker_without_greeks_fails(tmp_path):
    write_marker(tmp_path, 'options', options_payload(greeks={}))

    result = validate_suite_output('Options_Suite', str(tmp_path))

    assert not result.passed
    assert check_status(result, 'schema_valid') == FAIL


@pytest.mark.unit
def test_var_marker_passes(tmp_path):
    write_marker(tmp_path, 'var', var_payload())
    assert validate_suite_output('VaR_Tools', str(tmp_path)).passed


@pytest.mark.unit
def test_var_marker_with_percentage_confidence_fails(tmp_path):
    """99 instead of 0.99 -- the classic unit slip, caught at the boundary."""
    write_marker(tmp_path, 'var', var_payload(confidence=99))

    result = validate_suite_output('VaR_Tools', str(tmp_path))

    assert not result.passed
    assert check_status(result, 'schema_valid') == FAIL


@pytest.mark.unit
def test_sentiment_marker_passes(tmp_path):
    write_marker(tmp_path, 'sentiment', sentiment_payload())
    result = validate_suite_output('sentiment-scanner', str(tmp_path))
    assert result.passed
    assert check_status(result, 'schema_version') == PASS


@pytest.mark.unit
def test_sentiment_marker_with_wrong_schema_version_fails(tmp_path):
    write_marker(tmp_path, 'sentiment', sentiment_payload(schema_version=1))

    result = validate_suite_output('sentiment-scanner', str(tmp_path))

    assert not result.passed
    assert check_status(result, 'schema_version') == FAIL


@pytest.mark.unit
def test_sentiment_marker_missing_group_id_fails(tmp_path):
    payload = sentiment_payload()
    payload['sentiment']['group_id'] = ''
    write_marker(tmp_path, 'sentiment', payload)

    result = validate_suite_output('sentiment-scanner', str(tmp_path))

    assert not result.passed


@pytest.mark.unit
def test_marker_that_is_a_json_list_fails(tmp_path):
    write_marker(tmp_path, 'options', None, raw='[1, 2, 3]')

    result = validate_suite_output('options', str(tmp_path))

    assert not result.passed
    assert check_status(result, 'marker_is_object') == FAIL


# ── require_suite_output ─────────────────────────────────────────────────

@pytest.mark.unit
def test_require_suite_output_raises_with_result_attached(tmp_path):
    write_vol_csvs(tmp_path)

    with pytest.raises(SuiteValidationError) as exc:
        require_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert exc.value.result.status == FAIL
    assert exc.value.result.suite == 'vol'
    assert 'vol output validation FAILED' in str(exc.value)


@pytest.mark.unit
def test_require_suite_output_returns_on_pass(tmp_path):
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path)

    result = require_suite_output('Vol_Suite', str(tmp_path), ticker='NVDA')

    assert result.passed


# ── serialization (this is what lands in orchestrator_runs) ──────────────

@pytest.mark.unit
def test_result_to_dict_is_json_serializable_and_carries_checks(tmp_path):
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path, gamma=False)

    payload = validate_suite_output('vol', str(tmp_path), ticker='NVDA').to_dict()
    round_tripped = json.loads(json.dumps(payload))

    assert round_tripped['suite'] == 'vol'
    assert round_tripped['status'] == FAIL
    assert any(c['status'] == FAIL for c in round_tripped['checks'])
    assert round_tripped['missing_files'] == ['NVDA_gamma_records_*.csv']


@pytest.mark.unit
def test_report_lists_every_check(tmp_path):
    write_marker(tmp_path, 'vol', vol_payload(output_dir=str(tmp_path)))
    write_vol_csvs(tmp_path)

    result = validate_suite_output('vol', str(tmp_path), ticker='NVDA')
    report = result.report()

    assert report.splitlines()[0].endswith('PASS')
    assert len(report.splitlines()) == 1 + len(result.checks)
