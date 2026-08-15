# Task 3 — Common-input live-vs-expiry-book harness

## Status

**IMPLEMENTED — network-free contract harness and focused tests pass.** The historical
`Vol_Suite/run_compare_live_vs_new.py` and `_expiry_falsifier_cache/compare_live_vs_new_result.md`
were inspected and left untouched. This implementation does not use their historical pooled
results as evidence.

## Files created

- `Vol_Suite/run_live_vs_expiry_book_common_input.py`
  - Immutable canonical input record with ticker, calendar day, expiry, DTE, spot, IV source
    timestamp, sorted per-strike/right IV/OI rows, chain source, and source hashes.
  - Canonical JSON bytes and SHA-256 input identity.
  - Explicit live call uses `sign_model="vol_surface_replication"` and `accumulate=True`.
  - Returned live identity and actual `result.accumulate` are asserted; fallback is
    `ComparisonInvalid`.
  - New engine uses the same spot/expiry/rows and `T=dte/365`, with `expiry_book_exposure`
    deriving `rec.vanna=-1xBS`.
  - Per-strike/right live vanna levels are extracted from live records and compared only to
    new net-vanna levels. No `vanna_flow` is called or compared.
  - Emits exact coverage, signed pairs, live source/new convention labels, same/opposite/
    zero-vs-nonzero classes, units, Pearson/Spearman correlations, absolute error, and
    deadband-aware weighted `D_conv`.
  - Deterministic JSON artifact writer.
- `Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
  - Network-free tests covering the adapter contract, fallback invalidation, exact canonical
    coverage, duplicate/extra/wrong-expiry rejection, shared-subset rejection, and deadband.

## Verification commands and output

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_common_input_live_vs_expiry_book.py -q`
  - `4 passed in 0.04s` on the original implementation
- `env PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_common_input_live_vs_expiry_book.py -q`
  - `12 passed in 0.04s` after review fixes
- `py -3.12 -m py_compile Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
  - passed
- `py -3.12 -m ruff check Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
  - passed after formatting fixes
- `git diff --check`
  - passed

A direct default-adapter offline smoke invocation was previously blocked before the live call by
the checkout's missing optional `matplotlib` dependency while importing `dealer_positioning`.
The dependency-independent injected adapter contract test is now the documented smoke path; no
network or acquisition endpoint was called.

## Review-fix details

- `compare_common_input` serializes the `CanonicalInput` once and gives each adapter an
  immutable `CanonicalPayload` wrapper over those exact bytes. The wrapper records access and
  derives the consumption digest internally; returned result fields are never trusted as
  attestation. An adapter that ignores the payload (even while echoing its digest) yields an
  explicit `ComparisonInvalid.invalid_result` with status `INVALID`.
- Default adapters call `payload.consume()` before constructing or normalizing engine input, so
  attestation is tied to actual input consumption rather than stamped after computation.
- New-engine rows are runtime-checked against an independently computed Black-Scholes invariant:
  returned `rec.vanna` must equal `-1xBS` before its level enters the comparison. Canonical and
  returned rights are exact `C`/`P`; `PUT`, `CALL`, lowercase, and other ambiguous tokens are
  rejected rather than normalized.
- Deterministic JSON artifact writing remains sorted, fixed-format, and newline-stable.

Previous review-fix coverage was expanded to 12 focused tests, including ignored/echoed payload,
malformed rights, and a falsified per-record vanna regression.
- Each engine's `(strike, right)` rows is checked against the canonical key set exactly, with
  expiry checked on each row (or the new engine's result-level expiry). Missing, extra, duplicate,
  and wrong-expiry rows are recorded as exclusions and invalidate the comparison. No dict
  overwrite or shared incomplete subset can pass.
- Live sign model/actual accumulation, level-vs-level scope, `rec.vanna=-1xBS`, deterministic
  artifact behavior, and no-network scope remain unchanged.

## Scope / concerns

- `dealer_positioning.py`, live configuration, master, and all pre-existing untracked corpora
  were not modified.
- This task intentionally does not acquire data, make causal claims, or promote the expiry-book
  model. Invalid comparisons remain invalid.
- Existing historical comparison artifacts remain historical and are not overwritten.

## Final review-blocker fixes

- `CanonicalPayload` is now opaque: it has no public `canonical_input`, `data`, `digest`, or `consume` API. Adapters have one controlled `read()` operation; the harness records the exact returned bytes and computes the SHA-256 itself. Adapter-returned attestation fields are ignored. A bypass that ignores the read API or uses an alternate payload is invalidated before comparison.
- This boundary enforces the adapter protocol and byte identity, not arbitrary malicious code that deliberately lies or bypasses the process outside the supplied adapter boundary. The report makes no stronger claim.
- Strict causal/provenance validation now requires timezone-qualified timestamps, `iv_source_ts < breach_window_start_prov`, non-null `delta_iv_pre_window`, semantic SHA-256 source/payload hashes, PASS status, complete all-unit coverage, and no mixed same-day family provenance. Incomplete evidence returns `COMPARISON_INVALID` / `CAUSAL_BLOCKED`; no causal comparison is emitted. Same-day clusters preserve nested ticker records and explicitly prohibit pre-fit averaging.
- Malformed strike/right output rows are captured as structured exclusions rather than escaping as `AttributeError`. Live sign/accumulation, levels-only comparison, `rec.vanna=-1xBS`, deterministic artifacts, and network-free scope remain preserved.

## Final verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_common_input_live_vs_expiry_book.py -q` — `16 passed`
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest tests/test_dealer_exposure_acquisition.py -q` — `29 passed`
- `py -3.12 -m py_compile Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `py -3.12 -m ruff check ...` — passed after the final slot-order fix
- `git diff --check` — passed

## Latest critical-finding fixes (2026-08-15)

- Removed public `CanonicalPayload.read_bytes` and `read_sha256` audit APIs. The only adapter-facing operation is `read()`; the harness keeps read-ledger bytes and digest in local closure state, so adapters cannot manually mark consumption through a public method or read harness attestation state. The documented boundary is a Python protocol, not a malicious-code sandbox; reflection/process mutation are outside the trust model.
- `validate_causal_eligibility` now rejects syntax-only or `raw_payload_hash` fallback evidence. Each unit requires `artifact_hash`, `raw_payload_hash`, and `source_hashes` bound to a supplied artifact registry entry; the registry payload is hashed and must match `raw_payload_hash`, and source hashes must match the registry.
- Both causal timestamps must be aware ISO-8601 values, accept all numeric offsets supported by `datetime.fromisoformat`, fall on `calendar_day` in `declared_timezone`, and satisfy strict source-before-breach ordering.
- `compare_common_input` forwards the registry to the strict validator and therefore raises `COMPARISON_INVALID` with `CAUSAL_BLOCKED` for missing binding, forged hashes, or wrong-day timestamps.
- Added regressions for public bypass attempts, missing/forged registry binding, wrong-day timestamps, valid registered provenance, and comparison causal blocking.

## Latest verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_common_input_live_vs_expiry_book.py -q` — `20 passed`
- `py -3.12 -m py_compile Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py` — passed
- `py -3.12 -m ruff check Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py` — `All checks passed!`
- `git diff --check` — passed

## Review follow-up fixes (2026-08-15)

- Added shared `Vol_Suite/provenance_contract.py` so acquisition and comparison use one source-hash validator and canonical JSON serializer.
- Every acquired unit now persists endpoint, request parameters, declared timezone, spot timestamp, chain timestamp, source/breach timestamps, hashes, and a canonical artifact manifest. Artifact hashes are recomputed from that manifest; malformed causal PASS/PRE_WINDOW units are rejected by the census, so manually supplied PASS records cannot bypass acquisition provenance.
- Calendar-day checks now use the declared `ZoneInfo` consistently for source, breach, spot, and chain timestamps. Source/breach/spot/chain timestamps must be timezone-qualified; UTC-boundary regression coverage confirms local-day matching.
- `CanonicalInput` rejects non-finite spot/strike/IV/OI and canonical JSON serialization uses `allow_nan=False`; malformed artifacts return structured causal invalidity reasons.
- Existing opaque payload/attestation, exact-key coverage, live accumulation, levels-only comparison, same-day clustering, no-network, no-acquisition, and no live source/master/secrets boundaries remain unchanged.

## Verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — `56 passed`
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check ...` — `All checks passed!`
- `git diff --check` — passed

## Latest Task 3 provenance-boundary review fixes (2026-08-15)

- `validate_causal_eligibility` now requires typed, complete acquisition provenance on every causal unit: candidate key/status, endpoint, `request_parameters`, declared timezone, spot/chain/source/breach timestamps, source hashes, raw payload hash, registry-bound artifact hash, and same-day cluster metadata. The registered manifest is checked field-by-field against the supplied unit; candidate key, status, raw payload hash, source hashes, and artifact binding cannot be forged independently.
- Causal `delta_iv_pre_window` now requires two persisted timestamped observations (`iv_before_ts`/`iv_before_value` and `iv_source_ts`/`iv_source_value`), strict `iv_before_ts < iv_source_ts < breach_window_start_prov` ordering, same-day timestamps, aggregation identifier/version `iv_source_minus_iv_before`/`1`, and exact arithmetic equality. Scalar-only, mislabeled day-level, incorrect, and wrong-day derivations are blocked.
- Acquisition artifacts persist the observation and aggregation fields, canonical `request_parameters`, and explicit same-day cluster metadata before recomputing the artifact manifest/hash. Opaque payload, exact keys, live gates, levels-only scope, no acquisition/live source/master/secrets boundaries remain unchanged.
- Added regressions for missing acquisition fields/timestamps, forged manifest cross-fields, scalar-only/incorrect/wrong-day IV evidence, missing clustering metadata, and complete valid registered provenance.

## Latest verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — `70 passed`
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — `All checks passed!`
- `git diff --check` — passed

## Commit

Final commit hash is reported in the handback.

## Final fail-closed review follow-up (2026-08-15)

- Hardened `validate_causal_eligibility` and every nested provenance validator against missing `KeyError`, `IndexError`, and `AttributeError` paths. Missing `delta_iv_pre_window` and nested acquisition/cluster fields now return structured `COMPARISON_INVALID` / `CAUSAL_BLOCKED` reasons; no `BaseException` catch was added.
- Engine output validation now rejects non-finite live vanna, OI, applied signs, new `rec.vanna`, new levels, and all emitted diagnostic numeric fields. Invalid rows become structured exclusions and cannot select or describe a model.
- `write_deterministic_artifact` now serializes with `allow_nan=False`, validates `VALID` status, refuses causal-blocked/invalid payloads before opening the destination, and has deterministic regression coverage for NaN and invalid status payloads.
- Same-day cluster validation now requires `cluster_id == calendar_day`, unique ticker membership, consistent `n_tickers`, unit-ticker membership, and the registered aggregation rule. Acquisition persists `n_tickers` in cluster metadata; regressions cover mismatched IDs and membership counts.

## Latest verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **79 passed**
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **All checks passed**
- `git diff --check` — passed

No acquisition, live-model/master, or secrets files were changed.

## Latest review follow-up (2026-08-15)

- `build_provenance_census` now delegates every unit to the same strict
  `validate_causal_eligibility` contract used by `compare_common_input`, with a
  registry-bound manifest/payload, endpoint/request/spot/chain provenance,
  timestamped two-observation PRE_WINDOW derivation, hashes, and cluster metadata.
  A forged minimal PASS returns `gate_pass: false`, `CAUSAL_BLOCKED`, and
  `COMPARISON_INVALID` reasons instead of being approved.
- Causal validation now checks same-day cluster membership across the complete
  unit set: one day-derived cluster ID, identical unique ticker membership and
  `n_tickers`, required units, and registered aggregation rule. Contradictory
  per-unit clusters block eligibility.
- Runner exceptions and malformed `gamma_records`/`rows` containers are converted
  to structured `ComparisonInvalid` results with `COMPARISON_INVALID`; no
  `BaseException` catch was introduced.
- Added regressions for forged census units, contradictory same-day clusters,
  missing result containers, and runner exceptions. Existing acquisition and
  comparison gates remain network-free and fail closed.

## Latest verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **82 passed**
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **All checks passed**
- `git diff --check` — passed

No acquisition, live-model/master, or secrets files were changed.

## Critical/Important provenance-integrity closure (2026-08-15)

- `compare_common_input` now binds every supplied provenance unit to the exact canonical ticker, calendar day, expiry, DTE, canonical input SHA-256, and declared same-day cluster identity before either adapter runs; detached identity returns structured `COMPARISON_INVALID` / `CAUSAL_BLOCKED`.
- Registry-bound provenance now includes `expiry`, `dte`, `imputed`, and `no_imputation` in `_PROVENANCE_FIELDS` and the canonical artifact manifest. Manifest/unit equality and artifact hashing reject post-registration expiry/DTE mutation.
- Causal eligibility is explicitly no-imputation: `imputed` must be exactly `False` and `no_imputation` exactly `True`; missing or contradictory flags block eligibility.
- Duplicate `candidate_key` units and duplicate same-day cluster memberships are rejected before coverage/census can satisfy `intended_units`.
- Added regressions for wrong ticker/day/expiry/DTE/input hash, expiry/DTE registry mutation, imputation flags, duplicate units, and a valid fully-bound unit.

## Latest verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **93 passed**
- `python -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `uvx ruff check Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `git diff --check` — passed

No acquisition, live-model/master, or secrets files were changed.

## Critical/Important integrity closure (2026-08-15 follow-up)

- Added `ticker`, `calendar_day`, and `canonical_input_hash` to the strict provenance field set, acquisition manifests, and persisted registry entries. Comparison identity validation binds the supplied unit and registry manifest to the exact canonical input identity; post-registration mutations and cross-input use block causally.
- `execute_sequential_acquisition` now returns and writes the complete `artifact_registry`; registry entries retain manifest, artifact/raw hashes, source hashes, payload, and identity fields. JSON persistence round-trips the same registry.
- New-engine result and every returned row must carry the canonical expiry and `T == dte / 365` under explicit `1e-12` relative / `1e-15` absolute tolerance. Violations become structured comparison-invalid exclusions.
- Causal comparison now requires explicit `intended_units` or an intended corpus manifest. It never derives the coverage denominator from supplied units; duplicate units and exact intended candidate identities are checked, so partial corpora are `CAUSAL_BLOCKED` / `COMPARISON_INVALID`.
- Acquisition and comparison now use shared strict `canonical_json_bytes`/`canonical_sha256`; no `default=str` fallback remains in the touched boundary code. Representation/hash mutation regressions were added.

## Follow-up verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **101 passed**
- `python -m py_compile Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — passed
- `uvx ruff check Vol_Suite/provenance_contract.py Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py tests/test_dealer_exposure_acquisition.py` — **All checks passed**
- `git diff --check` — passed

No network, acquisition endpoint, live model, master, or secrets path was invoked.
