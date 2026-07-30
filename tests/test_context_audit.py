"""Tests for the context mutation audit trail.

The subject is the one place the shared suite_context is mutated after it has
been built and validated: the sentiment fold in `run_unified`. Every test below
is a variation on "the producer wrote something into the context -- what
actually changed, did it survive validation, and if not is the object the rest
of the chain holds a reference to back the way it started".
"""

import copy

import pytest

from shared.context_audit import (
    AUDIT_VERSION,
    audit_sentiment_mutation,
    diff_contexts,
    flatten,
    fold_sentiment_block,
    hash_obj,
    preview,
    restore_context,
    snapshot_context,
)
from shared.schemas import validate_sentiment_block
from shared.suite_validation import FAIL, PASS

TS = '2026-07-29T12:00:00Z'
DEFAULT_MANIFEST = '/repo/sentiment-scanner/data/exports/latest_manifest.json'
NEW_MANIFEST = '/repo/orchestrator_output/20260729T120000Z/latest_manifest.json'


# ── fixtures ─────────────────────────────────────────────────────────────

def make_context(**over):
    """A minimal but schema-valid suite_context, as build_context leaves it."""
    context = {
        'schema_version': 1,
        'run_id': '20260729T120000Z',
        'created_at_utc': TS,
        'output_dir': '/repo/orchestrator_output/20260729T120000Z',
        'focus': {
            'ticker': 'NVDA',
            'option_type': 'call',
            'strike': None,
            'target_years': 0.25,
            'expiration_date': '2026-10-16',
        },
        'basket': {
            'index_ticker': 'SPY',
            'tickers': ['NVDA'],
            'weights': [1.0],
        },
        'sentiment': {
            'manifest_path': DEFAULT_MANIFEST,
            'pack_json_path': None,
            'group_id': None,
            'ranked_tickers': [],
        },
        'var': {'horizon_days': 1, 'confidence': 0.99, 'positions': None},
        'controls': {'run_options_suite': True, 'run_var_suite': True,
                     'compile_pdf': False},
        'paths': {
            'options_suite_root': '/repo/Options_Suite',
            'var_suite_root': '/repo/VaR_Tools_Simulations',
            'sentiment_suite_root': '/repo/sentiment-scanner',
        },
        'swap_activity': [
            {'product': 'EQ:NVDA', 'total_notional': 1.0e9, 'trade_count': 12,
             'effective_date': '2026-07-28'},
        ],
    }
    context.update(over)
    return context


def make_payload(**over):
    """A schema_version=2 sentiment context export, as the producer writes it."""
    payload = {
        'schema_version': 2,
        'run_id': '20260729T120000Z',
        'created_at_utc': TS,
        'suite': 'sentiment',
        'sentiment': {
            'manifest_path': NEW_MANIFEST,
            'pack_json_path': '/repo/packs/cns-alerts-20260729.json',
            'group_id': 'cns-threshold-alerts-20260729',
            'ranked_tickers': ['NVDA', 'GME'],
        },
    }
    payload.update(over)
    return payload


# ── flatten / diff primitives ────────────────────────────────────────────

def test_flatten_indexes_list_elements():
    flat = flatten({'a': {'b': [1, 2]}, 'c': 'x'})
    assert flat == {'a.b[0]': 1, 'a.b[1]': 2, 'c': 'x'}


def test_flatten_treats_empty_containers_as_leaves():
    # Otherwise [] -> ['NVDA'] would look like a pure addition with no record
    # that the key existed before the mutation.
    assert flatten({'a': [], 'b': {}}) == {'a': [], 'b': {}}


def test_diff_reports_modified_added_and_removed():
    before = {'keep': 1, 'change': 'old', 'drop': True}
    after = {'keep': 1, 'change': 'new', 'gain': 5}
    changes = {m.key: m.change for m in diff_contexts(before, after)}
    assert changes == {'change': 'modified', 'drop': 'removed', 'gain': 'added'}


def test_diff_is_empty_for_identical_contexts():
    context = make_context()
    assert diff_contexts(context, copy.deepcopy(context)) == []


def test_diff_pinpoints_the_changed_list_element():
    before = {'sentiment': {'ranked_tickers': ['NVDA', 'GME']}}
    after = {'sentiment': {'ranked_tickers': ['NVDA', 'AMC']}}
    mutations = diff_contexts(before, after)
    assert [m.key for m in mutations] == ['sentiment.ranked_tickers[1]']
    assert mutations[0].before == 'GME' and mutations[0].after == 'AMC'


def test_preview_is_length_capped_ascii():
    long_value = 'x' * 500
    rendered = preview(long_value)
    assert len(rendered) < 120 and rendered.endswith('...')
    assert rendered.isascii()


def test_hash_is_stable_across_key_order():
    assert hash_obj({'a': 1, 'b': 2}) == hash_obj({'b': 2, 'a': 1})


def test_hash_changes_when_a_leaf_changes():
    assert hash_obj({'a': 1}) != hash_obj({'a': 2})


# ── snapshots ────────────────────────────────────────────────────────────

def test_snapshot_summarizes_without_copying_the_context():
    snap = snapshot_context(make_context(), 'before').to_dict()
    assert snap['label'] == 'before'
    assert len(snap['sha256']) == 64
    assert 'sentiment' in snap['top_level_keys']
    assert snap['swap_activity_rows'] == 1
    # The sentiment block is previewed, not embedded whole, and the swap
    # activity payload is counted rather than duplicated into the audit row.
    assert snap['sentiment']['manifest_path'] == DEFAULT_MANIFEST
    assert 'swap_activity' not in snap


def test_snapshot_sentiment_hash_isolates_the_mutable_block():
    before = make_context()
    after = make_context()
    after['swap_activity'] = []
    a, b = snapshot_context(before, 'a'), snapshot_context(after, 'b')
    assert a.sha256 != b.sha256          # something changed
    assert a.sentiment_sha256 == b.sentiment_sha256   # but not the block we guard


# ── the fold itself ──────────────────────────────────────────────────────

def test_fold_reports_only_the_keys_that_actually_moved():
    context = make_context()
    payload = make_payload()
    payload['sentiment']['manifest_path'] = DEFAULT_MANIFEST   # unchanged
    changed = fold_sentiment_block(context, payload)
    assert 'sentiment.manifest_path' not in changed
    assert set(changed) == {'sentiment.pack_json_path', 'sentiment.group_id',
                            'sentiment.ranked_tickers'}


def test_fold_does_not_let_empty_producer_values_clobber_defaults():
    context = make_context()
    payload = make_payload()
    payload['sentiment']['manifest_path'] = ''
    payload['sentiment']['ranked_tickers'] = []
    fold_sentiment_block(context, payload)
    assert context['sentiment']['manifest_path'] == DEFAULT_MANIFEST
    assert context['sentiment']['ranked_tickers'] == []


def test_restore_context_mutates_in_place():
    context = make_context()
    alias = context                      # what the downstream stages hold
    baseline = copy.deepcopy(context)
    context['sentiment']['group_id'] = 'corrupted'
    restore_context(context, baseline)
    assert alias['sentiment']['group_id'] is None
    assert alias is context


# ── audit: the happy path ────────────────────────────────────────────────

def test_audit_passes_and_records_the_mutations():
    context = make_context()
    audit = audit_sentiment_mutation(context, make_payload())

    assert audit.status == PASS and audit.passed
    assert audit.mutation_applied is True
    assert audit.rolled_back is False
    assert set(audit.mutated_keys) == {
        'sentiment.manifest_path',
        'sentiment.pack_json_path',
        'sentiment.group_id',
        # The empty list was itself a leaf; filling it removes that leaf and
        # adds one per element, which is what "[] -> ['NVDA', 'GME']" is.
        'sentiment.ranked_tickers',
        'sentiment.ranked_tickers[0]',
        'sentiment.ranked_tickers[1]',
    }
    assert context['sentiment']['manifest_path'] == NEW_MANIFEST


def test_audit_fails_only_when_the_mutation_itself_is_at_fault(monkeypatch):
    # The verdict answers "did this mutation damage the context", so a FAIL
    # always comes with a rollback and vice versa.
    assert audit_sentiment_mutation(make_context(), make_payload()).status == PASS
    _install_bad_fold(monkeypatch, _corrupt_sentiment)
    bad = audit_sentiment_mutation(make_context(), make_payload())
    assert bad.status == FAIL and bad.rolled_back is True


def test_audit_entry_carries_before_after_hashes_and_status():
    context = make_context()
    entry = audit_sentiment_mutation(context, make_payload()).to_dict()

    assert entry['audit_version'] == AUDIT_VERSION
    assert entry['validation_status'] == PASS
    assert entry['before_context']['sha256'] != entry['after_context']['sha256']
    assert entry['before_context']['sentiment']['manifest_path'] == DEFAULT_MANIFEST
    assert entry['after_context']['sentiment']['manifest_path'] == NEW_MANIFEST
    assert entry['mutations_detected']
    assert {c['check'] for c in entry['checks']} >= {
        'baseline_schema', 'source_payload', 'post_mutation_schema',
        'mutation_scope'}


def test_audit_is_json_serializable():
    import json
    entry = audit_sentiment_mutation(make_context(), make_payload()).to_dict()
    assert json.loads(json.dumps(entry))['validation_status'] == PASS


def test_audit_of_an_unchanged_context_is_a_pass_with_no_mutations():
    context = make_context()
    payload = make_payload()
    payload['sentiment'] = copy.deepcopy(context['sentiment'])
    payload['sentiment']['pack_json_path'] = '/repo/packs/p.json'
    payload['sentiment']['group_id'] = 'g'
    context['sentiment']['pack_json_path'] = '/repo/packs/p.json'
    context['sentiment']['group_id'] = 'g'

    audit = audit_sentiment_mutation(context, payload)
    assert audit.status == PASS
    assert audit.mutations == []
    assert audit.mutation_applied is False
    assert audit.before.sha256 == audit.after.sha256


# ── audit: producer stage failed ─────────────────────────────────────────

def test_failed_producer_is_a_pass_with_no_mutation():
    # A scanner crash is not context corruption. The context is untouched and
    # still valid, so the audit must not claim otherwise -- the suite's own
    # error dict is what reports the failure.
    context = make_context()
    audit = audit_sentiment_mutation(context, {'suite': 'sentiment',
                                               'error': 'exited with returncode 1'})
    assert audit.status == PASS
    assert audit.mutations == []
    assert audit.skipped_reason
    assert context['sentiment']['manifest_path'] == DEFAULT_MANIFEST


# ── audit: failures and rollback ─────────────────────────────────────────

def test_invalid_baseline_warns_but_still_folds():
    # Refusing to fold into an already-imperfect context would silently strip
    # the fresh manifest path out of every downstream stage -- a worse failure
    # than the one being guarded against. The violation is recorded as a
    # warning, attributed to the baseline, and the mutation proceeds.
    context = make_context()
    del context['basket']                      # broken before we touch it
    audit = audit_sentiment_mutation(context, make_payload())

    assert audit.status == PASS
    assert audit.rolled_back is False
    assert audit.errors == []
    assert any('baseline_schema' in w for w in audit.warnings)
    assert audit.mutation_applied is True
    assert context['sentiment']['manifest_path'] == NEW_MANIFEST


def test_baseline_violation_is_not_charged_to_the_mutation():
    # The same violation shows up in the post-mutation check. It must be
    # subtracted, or every fold into a slightly-off context would roll back.
    context = make_context()
    del context['controls']
    audit = audit_sentiment_mutation(context, make_payload())

    assert audit.status == PASS
    assert not any('post_mutation_schema' in e for e in audit.errors)
    assert audit.rolled_back is False


def test_untrustworthy_producer_payload_is_not_folded_in():
    context = make_context()
    payload = make_payload()
    payload['schema_version'] = 1              # not the v2 export contract
    audit = audit_sentiment_mutation(context, payload)

    # No mutation was attempted, so the context is exactly as safe as it was:
    # a producer failure, which the sentiment stage's own validation reports.
    assert audit.status == PASS
    assert audit.mutation_applied is False
    assert any('source_payload' in w for w in audit.warnings)
    assert audit.skipped_reason
    assert context['sentiment']['manifest_path'] == DEFAULT_MANIFEST


def test_malformed_producer_block_never_reaches_the_context():
    # A producer that emits the wrong type for a field the schema pins is
    # rejected at the source, before the fold -- the cheapest place to stop it.
    context = make_context()
    payload = make_payload()
    payload['sentiment']['ranked_tickers'] = {'NVDA': 1}

    audit = audit_sentiment_mutation(context, payload)

    assert audit.mutation_applied is False
    assert audit.mutations == []
    assert any('source_payload' in w for w in audit.warnings)
    assert context['sentiment']['ranked_tickers'] == []


def _install_bad_fold(monkeypatch, mutate):
    """Replace the fold with one that mutates *however mutate says*.

    The source check makes a schema-violating context unreachable through the
    real fold, which is the point of having it. Getting at the post-mutation
    check and the rollback therefore means breaking the fold itself -- i.e.
    testing the defence that exists for the day someone edits it.
    """
    import shared.context_audit as ca

    def fold(ctx, payload):
        return mutate(ctx)

    monkeypatch.setattr(ca, 'fold_sentiment_block', fold)


def _corrupt_sentiment(ctx):
    ctx['sentiment']['manifest_path'] = NEW_MANIFEST
    ctx['sentiment']['ranked_tickers'] = {'NVDA': 1}     # schema says list
    return ['sentiment.manifest_path', 'sentiment.ranked_tickers']


def test_post_mutation_schema_violation_rolls_back(monkeypatch):
    context = make_context()
    baseline_manifest = context['sentiment']['manifest_path']
    _install_bad_fold(monkeypatch, _corrupt_sentiment)

    audit = audit_sentiment_mutation(context, make_payload())

    assert audit.status == FAIL
    assert any('post_mutation_schema' in e for e in audit.errors)
    assert audit.rolled_back is True
    assert context['sentiment'] == {
        'manifest_path': baseline_manifest,
        'pack_json_path': None,
        'group_id': None,
        'ranked_tickers': [],
    }
    # The rejected state is still described in the audit row, so the rollback
    # does not erase the evidence of what the mutation tried to write.
    entry = audit.to_dict()
    assert entry['rejected_context']['sentiment']['manifest_path'] == NEW_MANIFEST
    assert entry['after_context']['sha256'] == entry['before_context']['sha256']
    assert entry['mutations_detected']          # what it tried is still recorded


def test_rollback_restores_every_key_not_just_the_sentiment_block(monkeypatch):
    context = make_context()
    baseline_hash = hash_obj(context)

    def wreck(ctx):
        ctx['sentiment']['ranked_tickers'] = {'NVDA': 1}   # forces a rollback
        ctx['swap_activity'] = []
        ctx['focus']['strike'] = 180.0
        return ['sentiment.ranked_tickers']

    _install_bad_fold(monkeypatch, wreck)
    audit_sentiment_mutation(context, make_payload())

    assert hash_obj(context) == baseline_hash


def test_mutation_outside_the_sentiment_block_fails_the_scope_check(monkeypatch):
    context = make_context()

    def stray(ctx):
        # Still schema-valid, so only the scope check can catch it.
        ctx['focus']['ticker'] = 'TSLA'
        return ['focus.ticker']

    _install_bad_fold(monkeypatch, stray)
    audit = audit_sentiment_mutation(context, make_payload())

    assert audit.status == FAIL
    assert any('mutation_scope' in e for e in audit.errors)
    assert audit.rolled_back is True
    assert context['focus']['ticker'] == 'NVDA'


# ── reporting ────────────────────────────────────────────────────────────

def test_diff_report_is_concise_and_not_full_json():
    context = make_context()
    audit = audit_sentiment_mutation(context, make_payload())
    report = audit.diff_report()

    assert len(report.splitlines()) <= 2 + len(audit.mutations)
    assert 'sentiment.manifest_path' in report
    assert '->' in report
    # A concise diff, not a dump of the context.
    assert 'swap_activity' not in report
    assert 'options_suite_root' not in report


def test_report_flags_a_rollback_on_the_verdict_line(monkeypatch):
    _install_bad_fold(monkeypatch, _corrupt_sentiment)
    audit = audit_sentiment_mutation(make_context(), make_payload())
    assert 'ROLLED BACK' in audit.summary()
    assert FAIL in audit.summary()


# ── the shared sentiment-block validator ─────────────────────────────────

def test_sentiment_block_lenient_allows_unfilled_pack_fields():
    validate_sentiment_block(make_context()['sentiment'], strict=False)


def test_sentiment_block_strict_rejects_unfilled_pack_fields():
    with pytest.raises(ValueError, match='pack_json_path'):
        validate_sentiment_block(make_context()['sentiment'], strict=True)


def test_sentiment_block_error_message_uses_the_given_path():
    with pytest.raises(ValueError, match=r'ctx\.sentiment\.ranked_tickers'):
        validate_sentiment_block(
            {'manifest_path': 'm', 'pack_json_path': None, 'group_id': None,
             'ranked_tickers': 'NVDA'},
            path='ctx.sentiment')
