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

## Commit

Final commit hash is reported in the handback.
