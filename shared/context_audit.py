"""shared/context_audit.py -- before/after audit trail for context mutations.

The unified orchestrator run has exactly one point where the shared
``suite_context`` object is mutated after it has been built and validated: the
sentiment-scanner stage is a *producer*, and its ``--export-context`` payload is
folded back into ``context['sentiment']`` so every downstream suite reads the
manifest this run actually produced rather than the default path
``build_context`` guessed at.

That fold used to be four unguarded assignments. It is the highest-leverage
line in the whole chain -- every later stage reads the object it edits -- and it
had no record of what changed, no check that the result still satisfied the
schema, and no way back if it did not. A producer that wrote a half-formed block
could overwrite a working manifest path, and the damage surfaced two stages
later as an unrelated failure inside whichever suite touched the field first.

This module turns that fold into an audited transaction:

1. **Baseline.** Deep-copy the context and hash it before anything is touched.
2. **Pre-check.** The baseline must satisfy ``validate_suite_context`` -- there
   is no point diffing against a starting state that was already broken, and
   attributing a pre-existing violation to the mutation would be a lie.
3. **Source check.** The producer payload must satisfy
   ``validate_sentiment_context`` (the schema_version=2 export contract) before
   any of its fields are trusted enough to copy.
4. **Mutate.** Fold the sentiment block in, recording every leaf that moved.
5. **Post-check.** The mutated context must still satisfy
   ``validate_suite_context`` *and* the sentiment-block schema, and every
   recorded mutation must live under ``sentiment.`` -- a changed leaf anywhere
   else means the fold reached somewhere it had no business reaching.
6. **Rollback.** A failure restores the baseline in place (so every existing
   reference to the context dict sees the restored state) and the audit comes
   back FAIL, which the orchestrator turns into a failed run rather than
   feeding a suspect context downstream.

The verdict answers one question -- *did this mutation damage the context?* --
and it FAILs only when the answer is yes: a mutation was applied and rejected.
Two other things can go wrong here, and neither is this mutation's fault, so
both are recorded as WARN with full detail and leave the run to continue:

* **The baseline was already invalid.** Blaming the fold for a violation that
  predates it would send the next reader hunting the wrong line, and the
  post-check therefore compares against the baseline's *own* errors -- only
  violations the mutation introduced count against it. This also keeps the fold
  running: refusing to mutate a context that was already imperfect would
  silently strip the fresh manifest path out of the whole downstream chain,
  which is a bigger failure than the one being guarded against.
* **The producer payload was untrustworthy.** No mutation is attempted, so the
  context is exactly as safe as it was a moment earlier. That is a producer
  failure, and `shared.suite_validation` already fails the sentiment stage for
  it independently.

The resulting :class:`ContextMutationAudit` serializes to the JSON that lands in
``orchestrator_runs.results_json``: before/after hashes and snapshots, the list
of changed keys, the per-check PASS/FAIL breakdown, and whether a rollback
happened. Snapshots are deliberately *summaries* -- hash, key list, leaf count,
and length-capped previews of the sentiment block -- not full copies of the
context, so the audit row stays readable and the swap_activity payload (up to 50
rows) does not get duplicated into the database on every run.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.schemas import (
    SENTIMENT_BLOCK_KEYS,
    validate_sentiment_block,
    validate_sentiment_context,
    validate_suite_context,
)
from shared.suite_validation import FAIL, PASS, WARN, CheckResult

__all__ = [
    'AUDIT_VERSION',
    'ContextSnapshot',
    'Mutation',
    'ContextMutationAudit',
    'snapshot_context',
    'flatten',
    'diff_contexts',
    'fold_sentiment_block',
    'restore_context',
    'audit_sentiment_mutation',
]

#: Bumped when the shape of the logged audit entry changes, so a reader of an
#: old ``orchestrator_runs`` row knows which fields to expect.
AUDIT_VERSION = 1

#: The only prefix the sentiment fold is allowed to touch. Anything else that
#: moved during the mutation window is an unexpected mutation and fails the audit.
SENTIMENT_PREFIX = 'sentiment.'

#: Length cap for a single value rendered into the diff. Long enough to tell two
#: manifest paths apart by their tail, short enough to keep a console line and a
#: logged row readable.
PREVIEW_CHARS = 96

#: Cap on diff lines printed to the console. The full list is always in the
#: logged audit entry; this only bounds the concise console rendering.
MAX_DIFF_LINES = 12


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _canonical_json(obj: Any) -> str:
    """Stable JSON rendering: sorted keys, no incidental whitespace.

    ``default=str`` because a context can carry values (dates, Decimals) that
    are not natively JSON-serializable; the hash only needs to be stable and
    collision-resistant for *change detection*, not to round-trip.
    """
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


def hash_obj(obj: Any) -> str:
    """SHA-256 of the canonical JSON rendering of *obj*."""
    return hashlib.sha256(_canonical_json(obj).encode('utf-8')).hexdigest()


def preview(value: Any) -> str:
    """One-line, length-capped rendering of a leaf value.

    ASCII-only on purpose: the orchestrator prints these on a Windows console
    that is frequently still cp1252, and an audit line is not worth a
    UnicodeEncodeError.
    """
    if value is None:
        return 'null'
    text = value if isinstance(value, str) else _canonical_json(value)
    text = ' '.join(str(text).split())
    if len(text) > PREVIEW_CHARS:
        text = text[:PREVIEW_CHARS - 3] + '...'
    return text


def flatten(obj: Any, prefix: str = '') -> Dict[str, Any]:
    """Flatten a JSON-ish object to a ``{dotted.path: leaf}`` map.

    Lists get index suffixes (``basket.tickers[0]``) so an element that changes
    in place is reported at the position it changed, not as a wholesale
    replacement of the list. Empty containers are themselves leaves, otherwise
    ``{}`` -> ``{'a': 1}`` would register as an addition with no corresponding
    record that the container existed before.
    """
    flat: Dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj:
            flat[prefix or '<root>'] = {}
        for key, value in obj.items():
            child = f'{prefix}.{key}' if prefix else str(key)
            flat.update(flatten(value, child))
    elif isinstance(obj, list):
        if not obj:
            flat[prefix or '<root>'] = []
        for index, value in enumerate(obj):
            flat.update(flatten(value, f'{prefix}[{index}]'))
    else:
        flat[prefix or '<root>'] = obj
    return flat


# --------------------------------------------------------------------------
# snapshots and diffs
# --------------------------------------------------------------------------

@dataclass
class ContextSnapshot:
    """A hash plus a human-readable summary of a context at one instant.

    Not a copy of the context: the whole point is that this can be written to
    the audit trail on every run without duplicating the payload. ``sha256``
    answers "did anything at all change", ``sentiment_sha256`` answers "did the
    block we were allowed to touch change", and the previews answer "to what".
    """

    label: str
    captured_at: str
    sha256: str
    sentiment_sha256: str
    top_level_keys: List[str] = field(default_factory=list)
    leaf_count: int = 0
    sentiment: Dict[str, str] = field(default_factory=dict)
    swap_activity_rows: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            'label': self.label,
            'captured_at': self.captured_at,
            'sha256': self.sha256,
            'sentiment_sha256': self.sentiment_sha256,
            'top_level_keys': list(self.top_level_keys),
            'leaf_count': self.leaf_count,
            'sentiment': dict(self.sentiment),
            'swap_activity_rows': self.swap_activity_rows,
        }

    @property
    def short_sha(self) -> str:
        return self.sha256[:12]


def snapshot_context(context: Dict[str, Any], label: str) -> ContextSnapshot:
    """Summarize *context* for the audit trail without copying it."""
    context = context if isinstance(context, dict) else {}
    sentiment = context.get('sentiment')
    sentiment = sentiment if isinstance(sentiment, dict) else {}
    return ContextSnapshot(
        label=label,
        captured_at=_iso_utc_now(),
        sha256=hash_obj(context),
        sentiment_sha256=hash_obj(sentiment),
        top_level_keys=sorted(str(k) for k in context.keys()),
        leaf_count=len(flatten(context)),
        sentiment={key: preview(sentiment.get(key)) for key in SENTIMENT_BLOCK_KEYS},
        swap_activity_rows=len(context.get('swap_activity') or []),
    )


@dataclass
class Mutation:
    """One leaf that differs between two contexts."""

    key: str
    change: str           # added / removed / modified
    before: Optional[str] = None
    after: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {'key': self.key, 'change': self.change,
                'before': self.before, 'after': self.after}

    def __str__(self) -> str:
        return (f"{self.change:8s} {self.key}: "
                f"{self.before if self.before is not None else '-'} -> "
                f"{self.after if self.after is not None else '-'}")


def diff_contexts(before: Dict[str, Any], after: Dict[str, Any]) -> List[Mutation]:
    """Every leaf that differs between *before* and *after*, sorted by key."""
    flat_before = flatten(before)
    flat_after = flatten(after)
    mutations: List[Mutation] = []
    for key in sorted(set(flat_before) | set(flat_after)):
        if key not in flat_after:
            mutations.append(Mutation(key, 'removed',
                                      before=preview(flat_before[key])))
        elif key not in flat_before:
            mutations.append(Mutation(key, 'added',
                                      after=preview(flat_after[key])))
        elif flat_before[key] != flat_after[key]:
            mutations.append(Mutation(key, 'modified',
                                      before=preview(flat_before[key]),
                                      after=preview(flat_after[key])))
    return mutations


# --------------------------------------------------------------------------
# the mutation itself
# --------------------------------------------------------------------------

def fold_sentiment_block(context: Dict[str, Any],
                         payload: Dict[str, Any]) -> List[str]:
    """Fold a producer payload's sentiment block into *context*, in place.

    Only truthy values overwrite, which is the pre-existing semantic and is
    load-bearing: the producer emits ``''`` / ``[]`` for anything it did not
    determine this run, and those must not clobber the defaults
    ``build_context`` established. Returns the dotted keys actually changed --
    a value equal to what was already there is not a mutation.
    """
    block = (payload or {}).get('sentiment') or {}
    target = context.setdefault('sentiment', {})
    changed: List[str] = []
    for key in SENTIMENT_BLOCK_KEYS:
        value = block.get(key)
        if not value:
            continue
        if target.get(key) != value:
            target[key] = value
            changed.append(SENTIMENT_PREFIX + key)
    return changed


def restore_context(context: Dict[str, Any], baseline: Dict[str, Any]) -> None:
    """Restore *context* to *baseline* in place.

    In place, via clear/update rather than rebinding, because the orchestrator
    hands the same dict object to every stage -- rebinding a local name would
    roll back nothing that anyone else can see.
    """
    context.clear()
    context.update(copy.deepcopy(baseline))


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

@dataclass
class ContextMutationAudit:
    """Outcome of one audited context mutation.

    ``to_dict`` is the JSON written to ``orchestrator_runs.results_json``.
    """

    stage: str
    status: str                                   # PASS / FAIL
    run_id: Optional[str] = None
    before: Optional[ContextSnapshot] = None
    after: Optional[ContextSnapshot] = None
    rejected: Optional[ContextSnapshot] = None
    mutations: List[Mutation] = field(default_factory=list)
    checks: List[CheckResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # Anomalies that are real but are not this mutation's doing -- a baseline
    # that was already invalid, a producer payload that could not be trusted.
    # Recorded in full, but they do not fail the audit or the run.
    warnings: List[str] = field(default_factory=list)
    rolled_back: bool = False
    mutation_applied: bool = False
    skipped_reason: str = ''
    started_at: str = ''
    completed_at: str = ''
    audit_version: int = AUDIT_VERSION

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def __bool__(self) -> bool:
        return self.passed

    @property
    def mutated_keys(self) -> List[str]:
        return [m.key for m in self.mutations]

    def summary(self) -> str:
        """One-line verdict for the console."""
        head = (f"[audit] {self.stage}: {self.status} "
                f"({len(self.mutations)} mutation(s))")
        if self.rolled_back:
            head += ' ROLLED BACK'
        if self.skipped_reason:
            head += f' -- {self.skipped_reason}'
        if self.errors:
            head += ' -- ' + '; '.join(self.errors)
        if self.warnings:
            head += ' -- ' + str(len(self.warnings)) + ' warning(s)'
        return head

    def diff_report(self, max_lines: int = MAX_DIFF_LINES) -> str:
        """Concise before/after diff -- changed leaves only, never full JSON."""
        before_sha = self.before.short_sha if self.before else '?'
        after_sha = self.after.short_sha if self.after else '?'
        if self.rolled_back:
            # Without this the header reads "context X -> X" directly above a
            # list of changes, which is exactly backwards: those changes are
            # what was rejected, and X -> X is the proof the rollback worked.
            rejected_sha = self.rejected.short_sha if self.rejected else '?'
            lines = [f"    context {before_sha} -> {rejected_sha} REJECTED, "
                     f"rolled back to {after_sha}; attempted changes:"]
        else:
            lines = [f"    context {before_sha} -> {after_sha}"]
        if not self.mutations:
            lines.append("    (no leaf changed)")
            return '\n'.join(lines)
        for mutation in self.mutations[:max_lines]:
            lines.append(f"    {mutation}")
        remaining = len(self.mutations) - max_lines
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (full list in the audit row)")
        return '\n'.join(lines)

    def report(self) -> str:
        """Verdict, per-check breakdown, and the concise diff."""
        lines = [self.summary()]
        for check in self.checks:
            lines.append(f"    {check}")
        lines.append(self.diff_report())
        return '\n'.join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'audit_version': self.audit_version,
            'stage': self.stage,
            'run_id': self.run_id,
            'validation_status': self.status,
            'before_context': self.before.to_dict() if self.before else None,
            'after_context': self.after.to_dict() if self.after else None,
            # Present only on a rolled-back run: the state that was rejected,
            # so the audit row still says what the producer tried to write.
            'rejected_context': self.rejected.to_dict() if self.rejected else None,
            'mutations_detected': self.mutated_keys,
            'mutation_details': [m.to_dict() for m in self.mutations],
            'mutation_applied': self.mutation_applied,
            'rolled_back': self.rolled_back,
            'checks': [c.to_dict() for c in self.checks],
            'validation_errors': list(self.errors),
            'validation_warnings': list(self.warnings),
            'skipped_reason': self.skipped_reason,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
        }


def _validate_context(context: Dict[str, Any]) -> List[str]:
    """Full-schema + sentiment-block check. Returns error strings, [] if clean."""
    errors: List[str] = []
    try:
        validate_suite_context(context)
    except Exception as e:
        errors.append(f"suite_context: {type(e).__name__}: {e}")
    try:
        # Redundant with the above by construction, but explicit: the sentiment
        # block is what this audit exists to guard, so it gets named separately
        # in the check list rather than hiding inside a whole-context verdict.
        validate_sentiment_block(context.get('sentiment'), strict=False)
    except Exception as e:
        errors.append(f"sentiment block: {type(e).__name__}: {e}")
    return errors


def audit_sentiment_mutation(context: Dict[str, Any],
                             sentiment_payload: Optional[Dict[str, Any]],
                             stage: str = 'sentiment_context_mutation'
                             ) -> ContextMutationAudit:
    """Validate, fold, re-validate and (on failure) roll back the sentiment block.

    *context* is mutated in place on success and restored in place on failure,
    so the caller's object is always left in a state that satisfies
    ``validate_suite_context`` -- either the folded one or the baseline.

    A *sentiment_payload* carrying an ``error`` key means the producer stage
    itself failed; no mutation is attempted and the audit passes with zero
    mutations, because the context is untouched and still valid. That is a
    different condition from a mutation failure, and conflating the two would
    make an unrelated scanner crash look like context corruption.

    Never raises for a validation failure -- the FAIL verdict is the return
    value, in the same shape and with the same PASS/FAIL/WARN vocabulary
    ``shared.suite_validation`` uses.
    """
    started_at = _iso_utc_now()
    baseline = copy.deepcopy(context) if isinstance(context, dict) else {}

    audit = ContextMutationAudit(
        stage=stage,
        status=PASS,
        run_id=(context or {}).get('run_id'),
        before=snapshot_context(baseline, 'before'),
        started_at=started_at,
    )

    def record(name: str, ok: bool, detail: str = '',
               ok_detail: str = '', blames_mutation: bool = True) -> bool:
        """Record one check.

        *detail* describes the failure and *ok_detail* the success, because a
        PASS line carrying the text of the violation it did not find reads as
        the opposite of what happened. ``blames_mutation=False`` downgrades a
        failure to a WARN, for conditions this mutation cannot be responsible
        for.
        """
        if ok:
            audit.checks.append(CheckResult(name, PASS, ok_detail))
            return True
        if not blames_mutation:
            audit.checks.append(CheckResult(name, WARN, detail))
            audit.warnings.append(f"{name}: {detail}")
            return False
        audit.checks.append(CheckResult(name, FAIL, detail))
        audit.errors.append(f"{name}: {detail}")
        audit.status = FAIL
        return False

    def finish() -> ContextMutationAudit:
        audit.after = snapshot_context(context, 'after')
        audit.completed_at = _iso_utc_now()
        return audit

    # ---- 1. baseline check: was the context valid before we touched it? ----
    # A pre-existing violation is recorded and carried into step 5, where it is
    # subtracted from the post-mutation errors so the fold is judged on the
    # damage it did rather than the state it inherited.
    baseline_errors = _validate_context(baseline)
    record('baseline_schema', not baseline_errors,
           detail=('pre-existing violation, not caused by this mutation: '
                   + '; '.join(baseline_errors)),
           ok_detail='context valid before mutation',
           blames_mutation=False)

    # ---- 2. did the producer stage even run? ----
    payload = sentiment_payload or {}
    if 'error' in payload:
        audit.skipped_reason = ('producer stage failed; context left at baseline '
                                '(no mutation attempted)')
        audit.checks.append(CheckResult('source_payload', PASS, audit.skipped_reason))
        return finish()

    # ---- 3. source check: is the producer payload trustworthy? ----
    try:
        validate_sentiment_context(payload)
        record('source_payload', True,
               ok_detail='schema_version=2 sentiment context')
    except Exception as e:
        # Nothing is folded in, so the context is exactly as safe as it was.
        # The producer's own stage validation is what fails this run.
        audit.skipped_reason = ('producer payload rejected; context left at '
                                'baseline (no mutation attempted)')
        record('source_payload', False,
               f"producer payload rejected: {type(e).__name__}: {e}",
               blames_mutation=False)
        return finish()

    # ---- 4. mutate ----
    applied = fold_sentiment_block(context, payload)
    audit.mutation_applied = bool(applied)
    audit.mutations = diff_contexts(baseline, context)

    # ---- 5. post-mutation checks ----
    introduced = [e for e in _validate_context(context) if e not in baseline_errors]
    schema_ok = record('post_mutation_schema', not introduced,
                       detail=('violations introduced by the mutation: '
                               + '; '.join(introduced)),
                       ok_detail='context still valid after mutation')

    stray = [key for key in audit.mutated_keys
             if not key.startswith(SENTIMENT_PREFIX)]
    scope_ok = record('mutation_scope', not stray,
                      detail=('mutations outside the sentiment block: '
                              + ', '.join(stray)),
                      ok_detail=f'{len(audit.mutations)} change(s), all under '
                                f'{SENTIMENT_PREFIX}')

    if schema_ok and scope_ok:
        return finish()

    # ---- 6. rollback ----
    audit.rejected = snapshot_context(context, 'rejected')
    restore_context(context, baseline)
    audit.rolled_back = True
    return finish()
