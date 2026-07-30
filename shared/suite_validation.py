"""shared/suite_validation.py -- explicit, per-suite output validation.

The orchestrator used to treat "child exited 0" as "child produced usable
output". That is not the same claim. A suite can exit 0 after swallowing every
one of its own exceptions (Vol_Suite's ``run_focus_workflow`` catches per-step
failures and prints them), and it can write a result file that parses as JSON
but carries none of the fields the next stage reads. Both cases used to flow
downstream silently and surfaced later as an unrelated KeyError inside whichever
suite happened to touch the missing field first.

This module makes the claim explicit. Each suite must leave a **marker file** --
a named result JSON in the run's ``output_dir`` -- proving it finished, and that
marker is checked against three things:

1. **Presence.** The marker exists, is non-empty, and parses as JSON.
2. **Schema.** It passes the matching validator in ``shared.schemas`` (or, for
   Vol_Suite, the local one below since Vol_Suite has no shared validator), and
   its ``schema_version`` equals the pinned value where the artifact carries one.
3. **Side artifacts.** For suites whose real deliverable is files rather than
   JSON -- Vol_Suite -- the required CSV globs each match at least one file.

Vol_Suite used to be the awkward one: ``volatility_suite.py`` had no
``--context-out`` writer at all, so the orchestrator *materialized*
``vol_result.json`` by listing ``VS_OUTPUT_DIR`` just to give it a marker. It
now writes its own, via ``volatility_suite.run_context_mode``, against
``shared.schemas.validate_vol_result`` -- which is what the local
:func:`validate_vol_result` below delegates to. The one thing it adds on top is
the ``produced_files`` check: the schema proves the analysis blocks are
well-formed, this proves the run also left artifacts on disk, which together
catch the failure this module exists for -- a suite exiting 0 having produced
nothing but a stack trace on stdout.

Strictness of the glob checks is tunable, because "which CSVs Vol_Suite emits"
depends on the workflow branch taken (the correlation CSVs only appear when the
basket step runs). ``SUITE_VALIDATION_STRICT=0`` in the environment downgrades a
missing glob from a failure to a warning; the marker and schema checks are never
downgraded.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from shared.schemas import (
    VOL_RESULT_SCHEMA_VERSION,
    _require,
    validate_options_result,
    validate_sentiment_context,
    validate_var_result,
)
from shared.schemas import validate_vol_result as _validate_vol_result_schema

__all__ = [
    'SuiteValidationError',
    'CheckResult',
    'ValidationResult',
    'SuiteRequirement',
    'SUITE_REQUIREMENTS',
    'canonical_suite_name',
    'marker_filename',
    'validate_vol_result',
    'validate_suite_output',
    'require_suite_output',
]

PASS = 'PASS'
FAIL = 'FAIL'
WARN = 'WARN'


class SuiteValidationError(Exception):
    """Raised by :func:`require_suite_output` when a suite's output is invalid.

    Carries the full :class:`ValidationResult` on ``.result`` so a caller that
    wants the per-check breakdown does not have to re-run validation.
    """

    def __init__(self, message: str, result: 'ValidationResult'):
        super().__init__(message)
        self.result = result


# --------------------------------------------------------------------------
# name normalization
# --------------------------------------------------------------------------

# The orchestrator speaks short keys ('vol'); humans, CLI flags and the task
# spec speak directory names ('Vol_Suite', 'sentiment-scanner', 'VaR_Tools').
# Both resolve to the same canonical key so no caller has to care.
_ALIASES: Dict[str, str] = {
    'sentiment': 'sentiment',
    'sentiment_scanner': 'sentiment',
    'sentimentscanner': 'sentiment',
    'scanner': 'sentiment',
    'vol': 'vol',
    'vol_suite': 'vol',
    'volatility': 'vol',
    'volatility_suite': 'vol',
    'options': 'options',
    'options_suite': 'options',
    'var': 'var',
    'var_tools': 'var',
    'var_tools_simulations': 'var',
    'vartools': 'var',
}


def canonical_suite_name(suite_name: str) -> str:
    """Map any accepted spelling of a suite name onto its canonical short key.

    Accepts 'Vol_Suite', 'vol-suite', 'vol', 'VaR_Tools', 'sentiment-scanner',
    etc. Raises KeyError for anything unrecognized rather than guessing, so a
    typo in a call site fails at the call site instead of silently validating
    nothing.
    """
    key = str(suite_name or '').strip().lower().replace('-', '_').replace(' ', '_')
    while '__' in key:
        key = key.replace('__', '_')
    if key not in _ALIASES:
        raise KeyError(
            f"Unknown suite {suite_name!r}; expected one of "
            f"{sorted(set(_ALIASES.values()))} (or a directory-name alias)")
    return _ALIASES[key]


# --------------------------------------------------------------------------
# Vol_Suite marker validator
# --------------------------------------------------------------------------

def validate_vol_result(data: Dict[str, Any]) -> None:
    """Validate the ``vol_result.json`` marker.

    The payload contract itself lives in
    ``shared.schemas.validate_vol_result`` -- the vol surface, the dealer
    positioning block and the gamma ladder Vol_Suite now writes for itself --
    and is checked first, so there is exactly one definition of that shape and
    it is owned by the module the producer also validates against.

    Two orchestration-level assertions are layered on top, because they are
    about the *run* rather than the payload and only make sense from the
    consumer's side:

    * ``output_dir`` must be recorded on success, since the required-glob checks
      below are meaningless without knowing where the suite wrote.
    * ``produced_files`` must be non-empty on success. A Vol_Suite run that
      passed schema validation but left no files on disk is the silent-failure
      case this whole module exists to catch.
    """
    _validate_vol_result_schema(data)

    if data.get('status') == 'ok':
        _require(isinstance(data.get('output_dir'), str) and data['output_dir'].strip(),
                 "output_dir must be a non-empty string for status='ok'")
        _require(isinstance(data.get('produced_files'), list),
                 "produced_files must be a list for status='ok'")
        _require(len(data['produced_files']) > 0,
                 "produced_files is empty -- Vol_Suite exited cleanly but wrote "
                 "no analysis artifacts")


# --------------------------------------------------------------------------
# requirement table
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SuiteRequirement:
    """What one suite must leave behind for its stage to count as complete."""

    suite: str
    marker: str
    validator: Optional[Callable[[Dict[str, Any]], None]] = None
    # Pinned schema_version, or None when the artifact does not carry one.
    schema_version: Optional[int] = None
    # Glob patterns (matched against basenames in output_dir). Each pattern must
    # match at least one file. '{ticker}' is substituted with the focus ticker
    # when one is known, and left as a '*' wildcard when it is not.
    required_globs: Tuple[str, ...] = ()
    description: str = ''


SUITE_REQUIREMENTS: Dict[str, SuiteRequirement] = {
    'sentiment': SuiteRequirement(
        suite='sentiment',
        marker='sentiment_result.json',
        validator=validate_sentiment_context,
        schema_version=2,
        required_globs=(),
        description='sentiment-scanner --export-context payload (schema_version 2)',
    ),
    'vol': SuiteRequirement(
        suite='vol',
        marker='vol_result.json',
        validator=validate_vol_result,
        schema_version=VOL_RESULT_SCHEMA_VERSION,
        # Focus-workflow deliverables: dealer-positioning gamma records (written
        # with save_csv=True) and the correlation-engine basket CSVs.
        required_globs=(
            '{ticker}_gamma_records_*.csv',
            'correlation_matrix_*.csv',
            'correlation_pairs_*.csv',
        ),
        description='Vol_Suite run_context_mode result + dealer/correlation CSVs',
    ),
    'options': SuiteRequirement(
        suite='options',
        marker='options_result.json',
        validator=validate_options_result,
        schema_version=None,
        required_globs=(),
        description='Options_Suite run_context_mode result',
    ),
    'var': SuiteRequirement(
        suite='var',
        marker='var_result.json',
        validator=validate_var_result,
        schema_version=None,
        required_globs=(),
        description='VaR_Tools_Simulations run_context_mode result',
    ),
}


def marker_filename(suite_name: str) -> str:
    """The required marker filename for *suite_name* (accepts any alias)."""
    return SUITE_REQUIREMENTS[canonical_suite_name(suite_name)].marker


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------

@dataclass
class CheckResult:
    """One named assertion and how it went."""

    name: str
    status: str          # PASS / FAIL / WARN
    detail: str = ''

    def to_dict(self) -> Dict[str, Any]:
        return {'check': self.name, 'status': self.status, 'detail': self.detail}

    def __str__(self) -> str:
        return f"{self.status:4s} {self.name}" + (f" -- {self.detail}" if self.detail else '')


@dataclass
class ValidationResult:
    """Outcome of validating one suite's output directory.

    ``status`` is the single PASS/FAIL verdict; ``checks`` is the per-assertion
    breakdown that gets logged so a failure is diagnosable from the audit trail
    alone, without re-running anything.
    """

    suite: str
    status: str
    output_dir: str
    marker_path: str
    checks: List[CheckResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    matched_files: Dict[str, List[str]] = field(default_factory=dict)
    validated_at: str = ''
    strict: bool = True

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def __bool__(self) -> bool:
        return self.passed

    def summary(self) -> str:
        """One-line verdict suitable for a console log."""
        if self.passed:
            extra = f" ({len(self.warnings)} warning(s))" if self.warnings else ''
            return f"[validate] {self.suite}: {PASS}{extra}"
        return f"[validate] {self.suite}: {FAIL} -- " + '; '.join(self.errors)

    def report(self) -> str:
        """Multi-line PASS/FAIL breakdown of every check."""
        lines = [self.summary()]
        for check in self.checks:
            lines.append(f"    {check}")
        return '\n'.join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'suite': self.suite,
            'status': self.status,
            'output_dir': self.output_dir,
            'marker_path': self.marker_path,
            'checks': [c.to_dict() for c in self.checks],
            'errors': list(self.errors),
            'warnings': list(self.warnings),
            'missing_files': list(self.missing_files),
            'matched_files': {k: list(v) for k, v in self.matched_files.items()},
            'validated_at': self.validated_at,
            'strict': self.strict,
        }


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _strict_default() -> bool:
    """Glob strictness, overridable per-box without editing code.

    Only the *side-artifact* checks honour this. Marker presence, JSON parse and
    schema validation are never downgraded -- a suite that wrote no marker has
    not proven completion under any setting.
    """
    raw = os.environ.get('SUITE_VALIDATION_STRICT', '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def _expand_globs(patterns: Sequence[str], ticker: Optional[str]) -> List[str]:
    token = (str(ticker).strip().upper() if ticker else '*')
    return [p.replace('{ticker}', token) for p in patterns]


def _list_dir(output_dir: str) -> List[str]:
    try:
        return sorted(name for name in os.listdir(output_dir)
                      if os.path.isfile(os.path.join(output_dir, name)))
    except OSError:
        return []


def validate_suite_output(suite_name: str,
                          output_dir: str,
                          *,
                          payload: Optional[Dict[str, Any]] = None,
                          ticker: Optional[str] = None,
                          strict: Optional[bool] = None) -> ValidationResult:
    """Validate that *suite_name* left usable output in *output_dir*.

    Never raises for a validation failure -- it returns a
    :class:`ValidationResult` whose ``status`` is ``'FAIL'`` and whose ``errors``
    say why. That keeps it usable both as a gate (check ``.passed``) and as a
    reporter (log ``.report()`` and continue), which is exactly the split the
    orchestrator needs between its default best-effort mode and
    ``--fail-on-suite-error``. Only an unknown *suite_name* raises, because that
    is a programming error rather than a run outcome.

    Parameters
    ----------
    payload:
        Already-parsed marker contents. When given, the on-disk marker is still
        required to exist (the marker file *is* the proof of completion) but the
        supplied dict is what gets schema-checked. Lets the orchestrator
        validate the payload it just handed downstream rather than a re-read
        that could differ.
    ticker:
        Focus ticker, substituted into ``{ticker}`` glob patterns. Falls back to
        the payload's own ``ticker`` field, then to a bare wildcard.
    strict:
        Whether a missing required glob is a failure (True) or a warning
        (False). Defaults to ``SUITE_VALIDATION_STRICT`` in the environment.
    """
    suite = canonical_suite_name(suite_name)
    requirement = SUITE_REQUIREMENTS[suite]
    strict = _strict_default() if strict is None else bool(strict)

    output_dir = os.path.abspath(output_dir or '')
    marker_path = os.path.join(output_dir, requirement.marker)

    result = ValidationResult(
        suite=suite,
        status=PASS,
        output_dir=output_dir,
        marker_path=marker_path,
        validated_at=_iso_utc_now(),
        strict=strict,
    )

    def record(name: str, ok: bool, detail: str = '', downgradable: bool = False) -> bool:
        if ok:
            result.checks.append(CheckResult(name, PASS, detail))
            return True
        if downgradable and not strict:
            result.checks.append(CheckResult(name, WARN, detail))
            result.warnings.append(f"{name}: {detail}")
            return False
        result.checks.append(CheckResult(name, FAIL, detail))
        result.errors.append(f"{name}: {detail}")
        result.status = FAIL
        return False

    # ---- 1. output directory ----
    if not record('output_dir_exists', os.path.isdir(output_dir),
                  f"not a directory: {output_dir}"):
        return result

    # ---- 2. marker file present and non-empty ----
    if not os.path.isfile(marker_path):
        record('marker_present', False,
               f"required marker {requirement.marker!r} missing from {output_dir}")
        result.missing_files.append(requirement.marker)
        return result
    record('marker_present', True, marker_path)

    try:
        marker_size = os.path.getsize(marker_path)
    except OSError as e:                                    # pragma: no cover
        record('marker_non_empty', False, f"could not stat marker: {e}")
        return result
    if not record('marker_non_empty', marker_size > 0,
                  f"marker is zero bytes: {marker_path}"):
        return result

    # ---- 3. marker parses as JSON ----
    data = payload
    if data is None:
        try:
            # utf-8-sig: children launched under PYTHONUTF8 on Windows still
            # occasionally emit a BOM; plain utf-8 chokes on it.
            with open(marker_path, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception as e:
            record('marker_parses', False, f"{type(e).__name__}: {e}")
            return result
        record('marker_parses', True, f"{marker_size} bytes")
    else:
        record('marker_parses', True, 'payload supplied by caller')

    if not record('marker_is_object', isinstance(data, dict),
                  f"marker must be a JSON object, got {type(data).__name__}"):
        return result

    # ---- 4. schema_version, where the artifact pins one ----
    if requirement.schema_version is not None:
        actual = data.get('schema_version')
        record('schema_version',
               actual == requirement.schema_version,
               f"expected schema_version={requirement.schema_version}, got {actual!r}")

    # ---- 5. schema validation ----
    if requirement.validator is not None:
        try:
            requirement.validator(data)
            record('schema_valid', True, requirement.validator.__name__)
        except Exception as e:
            record('schema_valid', False, f"{type(e).__name__}: {e}")

    # ---- 6. declared status inside the marker ----
    marker_status = data.get('status')
    if marker_status is not None:
        record('reported_status', marker_status != 'error',
               f"suite reported status={marker_status!r}: "
               f"{data.get('error') or 'no error detail'}")

    # ---- 7. required side artifacts ----
    if requirement.required_globs:
        resolved_ticker = ticker or data.get('ticker')
        patterns = _expand_globs(requirement.required_globs, resolved_ticker)
        present = _list_dir(output_dir)
        for pattern in patterns:
            matches = fnmatch.filter(present, pattern)
            if matches:
                result.matched_files[pattern] = matches
                record(f'required_file[{pattern}]', True,
                       f"{len(matches)} match(es), e.g. {matches[0]}")
            else:
                result.missing_files.append(pattern)
                record(f'required_file[{pattern}]', False,
                       f"no file in {output_dir} matches {pattern!r}",
                       downgradable=True)

    return result


def require_suite_output(suite_name: str,
                         output_dir: str,
                         **kwargs: Any) -> ValidationResult:
    """:func:`validate_suite_output`, but raise :class:`SuiteValidationError`
    on FAIL.

    The raising variant exists for callers that want the dependency edge to be
    a hard precondition -- ``--fail-on-suite-error`` in the orchestrator, and
    any future code path that must not run against unvalidated upstream output.
    """
    result = validate_suite_output(suite_name, output_dir, **kwargs)
    if not result.passed:
        raise SuiteValidationError(
            f"{result.suite} output validation FAILED: " + '; '.join(result.errors),
            result)
    return result
