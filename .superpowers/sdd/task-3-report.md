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
  - `9 passed in 0.04s` after review fixes
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

- `compare_common_input` serializes the `CanonicalInput` once, constructs one immutable
  `CanonicalPayload(data, digest, canonical_input)`, and passes that exact payload to both
  adapters. Each adapter must return/record `consumed_input_sha256`; both digests are checked
  against the canonical SHA-256 before any comparison. A transforming/ignoring adapter yields
  an explicit `ComparisonInvalid.invalid_result` with status `INVALID`, reason, and exclusions.
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

## Commit

Review-fix commit hash is reported in the handback.
