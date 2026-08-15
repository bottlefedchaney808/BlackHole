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
  - Both runners receive the same immutable canonical object; byte identity is checked.
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
  - Four network-free tests covering byte-identical inputs, level-vs-flow separation,
    identity/accumulation fallback invalidation, coverage fail-closed behavior, and deadband
    zero handling.

## Verification commands and output

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_common_input_live_vs_expiry_book.py -q`
  - `4 passed in 0.04s`
- `py -3.12 -m py_compile Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
  - passed
- `py -3.12 -m ruff check Vol_Suite/run_live_vs_expiry_book_common_input.py Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
  - `All checks passed!`
- `git diff --check`
  - passed

A direct default-adapter offline smoke invocation was attempted. It was blocked before the
live call by the checkout's missing optional `matplotlib` dependency while importing
`dealer_positioning`; injected-engine tests remain fully offline and pass. No network or
acquisition endpoint was called.

## Scope / concerns

- `dealer_positioning.py`, live configuration, master, and all pre-existing untracked corpora
  were not modified.
- The default adapter uses a local seed controller and should be exercised in an environment
  with the repository's normal runtime dependencies installed; the contract tests inject
  lightweight engine results and therefore do not require matplotlib or ThetaData.
- This task intentionally does not acquire data, make causal claims, or promote the expiry-book
  model. It produces comparison-invalid status on missing accumulation or common-input coverage.
- Existing historical comparison artifacts remain historical and are not overwritten.

## Commit

`feat(vol): add common-input live expiry comparison` (final commit hash reported in handback)
