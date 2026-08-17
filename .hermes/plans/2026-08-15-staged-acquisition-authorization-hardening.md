# Staged Acquisition Authorization Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make staged dealer-exposure acquisition executable only under a frozen, hash-bound, cost-bounded authorization whose probes, PRE_WINDOW evidence, registry identity, and executor behavior are independently fail-closed.

**Architecture:** Add a small authorization-contract layer beside the existing Task 1–6 acquisition/expansion modules. Admission is ordered and non-skippable: **calendar resolution → calendar-enriched candidate manifest → probes → registry closure → authorization → restricted executor**. The caller first resolves and freezes the OpEx calendar binding, then creates a canonical candidate manifest and authorization object; a separate admission function validates exact hashes, scope, quotas, probe bindings, provenance, and budget before handing an immutable admitted-unit list to a restricted executor. Existing Task 1/2/3/5/6 validators remain the evidence gates, but no boolean approval flag, pre-calendar manifest, or caller-supplied executor may substitute for the authorization object.

**Tech Stack:** Python 3.12, existing `Vol_Suite` acquisition modules, strict canonical JSON/SHA-256 from `Vol_Suite/provenance_contract.py`, pytest with plugin autoload disabled, Ruff where available.

## Global Constraints

- Live `Vol_Suite/dealer_positioning.py`, live configuration, `master`, secrets, and scheduler paths remain unchanged.
- No network acquisition is authorized by this plan; implementation and tests must use injected fakes/captured fixtures only.
- The model remains descriptive/conditional. Authorization admits data collection only; it never authorizes model promotion, a headline comparison, or Cem approval.
- Historical acquisition is sequential and `THETADATA_HIST_CONCURRENCY=1`; any other value blocks before a request.
- SPY/QQQ remain held reference families and cannot be expansion candidates; held ticker×calendar-day pairs cannot reappear.
- DTE strata remain exactly 1–3, 4–7, and 8–10; zero-DTE, imputed-zero, missing, and fabricated values are hard stops.
- Event/control target remains 1/3 event habitat and 2/3 controls, with no arm imbalance greater than 2:1; same-day ticker rows are preserved but one calendar day is one independent unit.
- Causal eligibility requires 100% intended-unit coverage, registry-bound artifact/source hashes, explicit no-imputation, and exactly two ordered timestamped PRE_WINDOW observations per admitted unit.
- The PM-reviewed OpEx calendar plan/contract is a hard prerequisite: calendar resolution and its identity must be frozen before candidate selection, manifest hashing, probes, or authorization construction; no implementation may substitute a private calendar interpretation.
- Every candidate unit must carry mandatory `calendar_hash`, `calendar_policy_version`, and `resolver_code_hash`, plus observed expiry, session, settlement, and window binding; missing, conflicting, or unknown values are a hard gap.
- Admission order is fixed and non-skippable: calendar resolution → calendar-enriched candidate manifest → probes → registry closure → authorization admission → restricted executor.
- Calendar enrichment after authorization is prohibited. A changed calendar snapshot, policy, resolver, observed expiry/session/settlement/window binding, or binding hash invalidates the manifest and authorization and requires a new admission.
- Only an explicitly admitted unit list may reach the executor; executor output is not evidence of authorization.

## Mandatory admission ordering and cross-plan dependency

The authorization implementation is blocked until the PM-reviewed OpEx calendar plan (`.hermes/plans/2026-08-15-opex-calendar-module-pm-review.md`) has a frozen contract output covering snapshot, policy, resolver, observed-session semantics, and binding-hash construction. The authorization schema must consume that output; it may not substitute a private calendar interpretation. If implementation proceeds before the OpEx plan is frozen, it must use an explicitly versioned calendar-enriched manifest schema that rejects missing calendar fields and enforces the OpEx contract version—never silently enriching a pre-calendar manifest.

The single admission path must enforce this exact order with no shortcut: **calendar resolution → candidate manifest → probes → registry closure → authorization → restricted executor**. Calendar resolution produces the immutable binding first; the candidate manifest is then created from calendar-enriched units; probes and registry closure bind to that same manifest; authorization validates the closed evidence; and only the resulting immutable admitted-unit tuple reaches the restricted executor. A pre-calendar manifest cannot later be enriched, reprojected, or authorized under its original identity; enrichment requires a new manifest version, digest, and authorization.

## Current Evidence and Bypasses to Close

- `Vol_Suite/_staged_expansion_20260815/audit_report.json` records `CAUSAL_BLOCKED`, `0/6` PRE_WINDOW coverage, `PASS=0`, `HARD_GAP=6`, and `heavy_fetch_occurred=false`; the first stop is XLE on 2026-07-06 with `HTTPStatusError`.
- `probe_records.json` shows the fail-fast behavior: only the first XLE probe was invoked; XLF and later dates were retained as `not_run_after_prior_hard_gap`. This must remain auditable and must not be mistaken for successful negative evidence.
- The current dry-run manifest has six eligible XLE/XLF units after two invalid-DTE exclusions. Its full-file canonical SHA-256 is `dc103a0fbe96d191e7b5e383132b96f8f10ec7a42fe432e874d114b1bcbbabb4`; the proposed authorization identity projection (defined below) hashes to `8e9137283850a300d345ccb7c1d11bda91d6d3ad12dcb7e73ec1fcdfe7af226b`. These are audit references for the observed packet, not authorization to rerun it.
- The next hypothesized AAPL/META late-July cohort is not present in an approved authorization object and must not be inferred from seed files, scratch manifests, or prior outcomes.
- Current `approve_network`/`approval` booleans are not an authorization object: they have no actor, expiry, scope hash, cost ceiling, nonce, or immutable candidate-manifest binding.
- `build_expansion_manifest()` and `execute_sequential_acquisition()` do not bind execution to a candidate-manifest digest or enforce a request/cost ceiling.
- `_validate_probe_contract()` checks candidate key membership but its request comparison currently covers only ticker/day/expiry/DTE; habitat, sector, and candidate source must also bind exactly. A PASS with detached quota/provenance metadata must be rejected.
- The direct Task 2 `select_primary_schedule()` path accepts a mapping-derived PASS set and is weaker than Task 5’s complete `ProbeResult` validation unless all callers are routed through one admission function; duplicate/unknown probe identities must be rejected before any mapping.
- The injected executor is intentionally testable but currently unrestricted: there is no endpoint allowlist, per-unit call budget, total-call ceiling, wall-clock/byte ceiling, or proof that it received only authorization-admitted units.
- `_validate_pre_window_observations()` accepts at least two rows and computes from first/last. The authorization contract must require exactly two selected observations, preserve both source identities/hashes, and bind their derived delta to the registry; extra observations require an explicit bounded re-authorization rather than silent selection.
- The current CLI `--approve-network` path deliberately raises because it has no reviewed universe/executor. Preserve that safe behavior; do not turn a CLI flag into implicit authorization.

## Authorization Object (Normative Schema)

Implement a typed, canonical JSON-serializable `AcquisitionAuthorization` with these required fields. Unknown fields are rejected unless explicitly versioned; booleans must be actual JSON booleans, not truthy strings/integers.

```json
{
  "schema_version": 1,
  "authorization_id": "uuid-or-operator-supplied-unique-id",
  "issued_at": "timezone-qualified ISO-8601",
  "expires_at": "timezone-qualified ISO-8601",
  "issued_by": "non-empty operator/PM identity",
  "purpose": "staged-dealer-exposure-acquisition",
  "environment": "offline-test|staged-network",
  "candidate_manifest_sha256": "64 lowercase hex chars",
  "candidate_manifest_projection": "candidate_manifest_calendar_enriched_v2",
  "calendar": {
    "calendar_hash": "64 lowercase hex chars",
    "calendar_policy_version": "non-empty frozen policy identifier",
    "resolver_code_version": "non-empty frozen resolver identifier",
    "resolver_code_hash": "64 lowercase hex chars",
    "observed_session_id": "non-empty point-in-time session identity",
    "calendar_binding_hash": "64 lowercase hex chars"
  },
  "scope": {
    "candidate_keys": ["exact sorted candidate keys"],
    "calendar_hash": "same frozen calendar hash as every manifest unit",
    "calendar_policy_version": "same frozen calendar policy version as every manifest unit",
    "resolver_code_hash": "same frozen resolver code hash as every manifest unit",
    "observed_bindings": [
      {
        "candidate_key": "exact candidate key",
        "observed_expiry_date": "YYYY-MM-DD",
        "session_id": "exact point-in-time session identity",
        "session_status": "OPEN|HOLIDAY_CLOSED|UNKNOWN",
        "settlement_style": "PM_CLOSE|AM_SETTLEMENT|UNKNOWN",
        "settlement_timestamp": "timezone-qualified ISO-8601 or null",
        "event_window_id": "exact window identity",
        "window_start": "timezone-qualified ISO-8601",
        "window_end": "timezone-qualified ISO-8601",
        "calendar_binding_hash": "64 lowercase hex chars"
      }
    ],
    "ticker_day_pairs": [["AAPL", "2026-07-XX"]],
    "dte_strata": [[1, 3], [4, 7], [8, 10]],
    "event_habitats": ["FOMC", "EARNINGS", "OPEX"],
    "event_fraction": 0.3333333333333333,
    "control_fraction": 0.6666666666666666,
    "max_arm_ratio": 2.0,
    "held_pair_policy": "reject",
    "no_imputation": true,
    "same_day_aggregation": "preserve_ticker_values_v1",
    "calendar_hash": "same value as calendar.calendar_hash",
    "calendar_policy_version": "same value as calendar.calendar_policy_version",
    "resolver_code_version": "same value as calendar.resolver_code_version",
    "resolver_code_hash": "same value as calendar.resolver_code_hash",
    "observed_session_id": "same value as calendar.observed_session_id",
    "calendar_binding_hash": "same value as calendar.calendar_binding_hash"
  },
  "probe_policy": {
    "required_status": "PASS",
    "required_validated": true,
    "required_invoked": true,
    "required_checks": ["chain", "spot_ohlc", "same_expiry_grid_oi_iv", "strike_sides_moneyness", "timestamp_granularity", "expiry_dte", "post_window_returns"],
    "pre_window_observations_exact": 2,
    "probe_code_hash": "64 lowercase hex chars"
  },
  "cost_ceiling": {
    "max_units": 6,
    "max_probe_calls": 12,
    "max_heavy_calls": 24,
    "max_total_endpoint_calls": 36,
    "max_payload_bytes": 52428800,
    "max_wall_seconds": 900,
    "concurrency": 1,
    "on_exceed": "stop_and_hard_gap"
  },
  "executor_policy": {
    "allowed_executor_id": "staged_historical_adapter_v1",
    "allowed_executor_entrypoint": "injected staged historical adapter v1; implementation/code hash recorded in authorization audit",
    "allowed_endpoint_families": ["hist/option/all_greeks", "hist/option/open_interest", "hist/stock/ohlc", "hist/stock/eod"],
    "allowed_endpoint_paths": ["/hist/option/all_greeks", "/hist/option/open_interest", "/hist/stock/ohlc", "/hist/stock/eod"],
    "allowed_request_methods": ["GET"],
    "scope_binding": "candidate_keys + ticker_day_pairs + observed expiry/session/settlement/window bindings; no wildcard or dynamic expansion",
    "network_fetch_allowed": true,
    "allow_new_candidate_keys": false,
    "allow_held_pairs": false,
    "allow_live_model_calls": false,
    "allow_scheduler_calls": false,
    "allow_writes_outside_artifact_root": false
  },
  "stop_conditions": ["enumerated below"],
  "authorization_sha256": "computed from all fields except this field"
}
```

The exact ceiling is deliberately bounded to the proposed six-unit staged batch: at most two lightweight probe calls per unit and four heavy endpoint-family calls per unit, 36 total endpoint calls, 50 MiB aggregate payload, 15 minutes wall time, and serial execution. A larger AAPL/META cohort requires a new authorization object and new hash; it may not reuse or edit this one. If the provider’s actual request decomposition needs more calls, stop and ask for a revised authorization rather than raising limits in code or at runtime.

### Candidate manifest hash

Create a canonical candidate-manifest projection containing only immutable admission inputs: `schema_version`, explicit `manifest_schema` declaring calendar enrichment, sorted complete calendar-enriched unit objects, sorted exclusions, `selection_provenance`, `quota`, held-pair evidence hash, probe policy, executor/cost policy, and the complete calendar binding projection. Every unit’s projection must explicitly include mandatory `calendar_hash`, `calendar_policy_version`, `resolver_code_hash`, `observed_expiry_date`, `session_id`, `session_status`, regular open/close and early-close facts, `settlement_style`, `settlement_timestamp`, `event_window_id`, `window_start`, `window_end`, and `calendar_binding_hash` (with `resolver_code_version` and nominal date retained where required by the frozen OpEx contract). Exclude generated timestamps, output paths, mutable results, raw payloads, and audit status. Serialize with the shared strict canonical JSON routine (`sort_keys=True`, compact separators, `allow_nan=False`) and compute lowercase SHA-256. The authorization’s `candidate_manifest_sha256` must equal the recomputed projection digest byte-for-byte, and the calendar fields in authorization scope—including every observed expiry/session/settlement/window binding—must equal the manifest projection and every resolved unit. Candidate keys alone are insufficient because habitat, sector, source, expiry, DTE, held-pair decisions, and observed session semantics are scope. A pre-calendar manifest cannot later be silently enriched, reprojected, or authorized under the same identity; enrichment requires a new versioned manifest, digest, and authorization.

### PASS binding and PRE_WINDOW gate

A probe is admissible only when its exact `candidate_key`, ticker, calendar day, expiry, DTE, habitat, sector, candidate source, `calendar_hash`, `calendar_policy_version`, `resolver_code_version`, `resolver_code_hash`, `observed_session_id`, and `calendar_binding_hash` equal the frozen calendar-enriched manifest and authorization scope; its complete Task 1 checks/evidence validate; `status == PASS`, `validated is True`, `invoked is True`; its `request_parameters` equal the complete request identity; and its `probe_code_hash` equals the authorization. Exactly one probe per candidate key is required. Any duplicate, unknown, missing, contradictory, pre-calendar, or post-manifest probe blocks the whole batch.

Each admitted unit must have exactly two mappings with `role=PRE_WINDOW`, distinct timezone-qualified timestamps in increasing order, both strictly before `breach_window_start_prov`, both on the unit’s local calendar day, finite IV values, non-empty source identities, valid SHA-256 source hashes, and source hashes present in the registry. Require `iv_before_ts < iv_source_ts < breach_window_start_prov`; require `delta_iv_aggregation == iv_source_minus_iv_before`, version `1`, and exact `delta_iv_pre_window = iv_source_value - iv_before_value`. No day-level fallback, contemporaneous value, imputed timestamp/value, or “latest two” selection from an unbounded response is permitted.

### Registry identity

The registry is keyed by `artifact_hash`. Every entry must duplicate and agree with the artifact manifest and unit on `candidate_key`, ticker, calendar day, expiry, DTE, status, canonical input hash, raw payload hash, source hashes, `imputed`, `no_imputation`, endpoint/request parameters, cluster identity, and the two PRE_WINDOW observations. Recompute the artifact hash from the canonical manifest and recompute the raw payload hash from exact captured payload bytes. Registry entries must be immutable after authorization admission; mutation, missing key, cross-unit reuse, or JSON round-trip mismatch is `CAUSAL_BLOCKED`.

## Bounded SDD Tasks

### Task 1: Add the authorization contract and canonical manifest identity

**Files:**
- Create: `Vol_Suite/dealer_exposure_authorization.py`
- Modify: `Vol_Suite/dealer_exposure_expansion.py` only to call the contract (no live-model changes)
- Test: `tests/test_dealer_exposure_authorization.py`

- [ ] Write failing tests for schema typing, required fields, expiry, exact scope, mandatory calendar snapshot/policy/resolver/session/binding fields, unknown fields, canonical projection, known current-packet digest, and authorization self-hash.
- [ ] Implement `AcquisitionAuthorization.from_mapping()`, `candidate_manifest_projection()`, `candidate_manifest_sha256()`, and `authorization_sha256()` using `canonical_json_bytes()`/`sha256_bytes()`; require the frozen OpEx calendar contract/version before constructing an authorization.
- [ ] Reject expired/future-invalid authorizations, duplicate or unsorted candidate keys, pre-calendar manifests, missing/mismatched calendar scope, scope keys not present in the manifest, arm ratios over 2:1, non-serializable/non-finite values, and any attempt to authorize live-model/scheduler writes.
- [ ] Verify the observed dry-run identity projection reproduces `8e9137283850a300d345ccb7c1d11bda91d6d3ad12dcb7e73ec1fcdfe7af226b`; do not authorize or acquire it.
- [ ] Run the focused authorization tests with plugin autoload disabled.

### Task 2: Replace boolean approval with atomic admission

**Files:**
- Modify: `Vol_Suite/dealer_exposure_expansion.py`
- Modify: `Vol_Suite/dealer_exposure_acquisition.py`
- Test: `tests/test_dealer_exposure_expansion.py`
- Test: `tests/test_dealer_exposure_acquisition.py`

- [ ] Write failing tests proving `approve_network=True` without a valid authorization, matching manifest hash, unexpired scope, and exact evidence cannot invoke a probe or executor.
- [ ] Add `admit_acquisition(authorization, manifest, probes, evidence, registry)` returning an immutable admitted-unit tuple plus an audit record; route all execution entry points through it and enforce calendar resolution → manifest → probes → registry closure → authorization → executor ordering.
- [ ] Bind full probe request identity (including habitat, sector, candidate source, calendar snapshot/policy/resolver/session/binding hashes, and probe code hash) and reject duplicate/unknown/missing/pre-calendar probes before building lookup maps.
- [ ] Make the authorization’s `candidate_manifest_sha256`, exact candidate set, quota, concurrency, and cost ceiling mandatory inputs to admission; do not derive denominators from supplied evidence.
- [ ] Preserve dry-run/probe-only as network-free and preserve current fail-fast audit records.
- [ ] Run the affected Task 1/2/5 suites.

### Task 3: Enforce the exact two-observation PRE_WINDOW gate and registry closure

**Files:**
- Modify: `Vol_Suite/dealer_exposure_expansion.py`
- Modify: `Vol_Suite/dealer_exposure_acquisition.py`
- Modify: `Vol_Suite/provenance_contract.py` only if a shared primitive is required
- Test: `tests/test_dealer_exposure_acquisition.py`
- Test: `tests/test_dealer_exposure_expansion.py`
- Test: `Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`

- [ ] Write failing tests for three observations, reordered observations, duplicate timestamps, wrong-day timestamps, post-breach timestamps, invalid hashes, registry cross-unit reuse, payload mutation, and exact arithmetic mismatch.
- [ ] Require exactly two observations at admission and preserve their endpoint/source hash bindings through Task 6 captured mapping.
- [ ] Recompute payload and artifact hashes from canonical bytes; compare every duplicated registry identity field and reject mutation after registration.
- [ ] Ensure malformed mapping, missing timezone, raw `breach_eligible != True`, HARD_GAP decision, and conflicting `record.l2` provenance remain structured HARD_GAP/COMPARISON_INVALID, never exceptions that can be interpreted as success.
- [ ] Run the full affected provenance/comparison suite and verify no network path is called.

### Task 4: Add restricted executor and hard cost accounting

**Files:**
- Create: `Vol_Suite/dealer_exposure_executor.py`
- Modify: `Vol_Suite/dealer_exposure_expansion.py`
- Test: `tests/test_dealer_exposure_executor.py`
- Test: `tests/test_dealer_exposure_expansion.py`

- [ ] Write failing tests for wrong executor ID, unallowed endpoint, new candidate key, held pair, concurrency >1, probe/heavy/total call overrun, payload-byte overrun, wall-time overrun, and executor exception.
- [ ] Implement an executor wrapper that receives only admitted immutable units, checks each request against the authorization scope, counts calls/bytes/time, records endpoint/request/payload hashes, and stops atomically on the first limit breach.
- [ ] Permit only the named injected adapter/entrypoint and exact enumerated endpoint families, paths/templates, and methods; reject prefix/wildcard/dynamic endpoint expansion; prohibit live model calls, schedulers, arbitrary filesystem writes, and any request outside the authorization scope.
- [ ] Return `FAILED_EXECUTION`/`HARD_GAP` with `network_fetch_allowed=false` after any failure or ceiling breach; never continue with later units after a hard stop.
- [ ] Run executor tests and affected acquisition/expansion tests with fake adapters only.

### Task 5: Produce complete authorization/audit artifacts

**Files:**
- Modify: `Vol_Suite/dealer_exposure_expansion.py`
- Modify: `Vol_Suite/dealer_exposure_acquisition.py`
- Test: `tests/test_dealer_exposure_authorization.py`
- Test: `tests/test_dealer_exposure_expansion.py`

- [ ] Write failing round-trip tests for authorization, manifest projection/hash, probe census, execution audit, registry, stop reason, counters, and final status.
- [ ] Persist under the approved artifact root, before any endpoint call: `authorization.json`, `candidate_manifest.json`, `authorization_audit.json`, and `SHA256SUMS.txt`; after each call append deterministic `probe_records.jsonl`/`acquisition_records.jsonl` or an equivalent atomic JSON structure.
- [ ] Audit fields must include authorization ID/hash, manifest hash, issued/expiry times, operator identity, exact scope, cost ceiling and usage, executor ID, concurrency, probe code hash, per-unit candidate key, request parameters, endpoint, response status, payload/artifact/source hashes, PRE_WINDOW observations, registry key, invoked/not-invoked state, stop classification/reason, and final `CAUSAL_BLOCKED`/`COMPARISON_INVALID`/success status.
- [ ] Ensure blocked runs write audit artifacts without writing partial “successful” records and without overwriting existing artifacts.
- [ ] Run deterministic serialization/hash tests twice and compare bytes.

### Task 6: Add integration and negative-path gates

**Files:**
- Modify: `Vol_Suite/run_universe_causal_comparison.py` if needed to consume the admission result
- Test: integration test covering `Vol_Suite/run_universe_causal_comparison.py`
- Test: `tests/test_dealer_exposure_authorization.py`

- [ ] Write failing integration tests for the current XLE/XLF failure packet, missing PRE_WINDOW, forged PASS, detached probe, candidate-manifest mutation, registry mutation, and AAPL/META scope expansion without a new authorization.
- [ ] Verify the current packet remains blocked with no heavy call and a first-stop `HTTPStatusError` audit; verify a later unit is not silently retried or promoted.
- [ ] Verify a complete synthetic six-unit packet can pass admission only with exact authorization, probe, registry, two-observation, balance, and cost evidence; use a fake executor and assert the exact call list.
- [ ] Verify the runner’s CLI/nonzero status remains fail-closed for `COMPARISON_INVALID`, `CAUSAL_BLOCKED`, `INDETERMINATE`, `HARD_GAP`, and `FAILED_EXECUTION`; only a completed decision may return zero, and even that is not model promotion.
- [ ] Run all affected no-plugin tests, compile checks, Ruff if installed, and `git diff --check`.

## Stop Conditions (Runtime-Normative)

Stop immediately and emit a structured hard-gap audit if any of these occurs: authorization missing/expired/hash-mismatched; manifest or candidate key changes; any held pair/reference family; duplicate/unknown/missing probe or non-PASS probe; probe request mismatch; any required Task 1 check absent; event/control arms absent or ratio >2:1; DTE/expiry mismatch, zero-DTE, non-finite or imputed value; fewer or more than two valid PRE_WINDOW observations; wrong-day/equal/later timestamp; registry or payload hash mismatch; source hash not registered; executor identity/endpoint/scope mismatch; concurrency not equal to 1; any call, byte, or wall-time ceiling reached; executor exception/failure marker; artifact write outside the approved root; live-model/master/secrets/scheduler access; or any attempt to continue after a hard stop. No “best effort” partial corpus may be labeled causal PASS.

## Verification and Handoff

Implementation is not complete until the focused and affected tests execute successfully in the repository Python 3.12 environment with plugin autoload disabled, compile checks pass, Ruff passes where installed, and `git diff --check` is clean. The current worktree artifacts must remain unchanged except for the implementation files selected by PM. Before any real acquisition, PM must review and approve a newly generated authorization object whose manifest hash, exact AAPL/META candidate list, cost ceiling, and expiry are recorded in the audit; this plan itself grants no network authority.

---

**Plan status:** read-only design complete; no acquisition, live-model change, or commit is authorized by this document.
