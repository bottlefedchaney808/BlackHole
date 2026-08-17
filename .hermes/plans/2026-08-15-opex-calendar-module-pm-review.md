# PLAN — Reusable Point-in-Time OpEx Calendar Module (PM Review)

Status: **plan-only; NO production code, data acquisition, corpus, SDD progress file, or commit changed**  
Author: Hermes Agent (Dealer-Exposure-Dev worktree, 2026-08-15)  
Approval gate: PM approval is required before implementation or calendar/event acquisition.

## Goal

Create a reusable, deterministic calendar module for US listed equity/ETF options that resolves nominal standard monthly OpEx dates into point-in-time session, settlement, and event-window records. The module will provide one hash-bound calendar contract to Task 1 probes and Task 4 strata without changing the live dealer model, current expiry book, or causal estimand.

## Architecture and non-goals

The implementation is a pure resolver over an explicitly supplied, immutable calendar snapshot. It has no network side effects and no implicit `datetime.now()`, current exchange schedule, current vendor listing, or “latest” fallback. A later, separately approved adapter may materialize local snapshots, but adapters remain outside the resolver.

The module distinguishes:

- nominal third-Friday rule output;
- observed listed expiration/expiry date;
- last tradable session and actual regular/early close;
- settlement style/timestamp; and
- analysis event day/window.

It does **not** infer event surprises, select a historical universe, backfill missing holidays, choose earnings expectations, or alter dealer Greek/sign conventions.

## Grounding from actual repository artifacts

Read-only inspection covered the following:

- `Vol_Suite/expiry_selector.py`: `third_friday()` is arithmetic-only, `DEFAULT_A=365`, and `resolve_expiration()` can use `datetime.now()` and silently fall back to a different ticker expiry. It has no point-in-time source snapshot, holiday/early-close, settlement, or event-window contract.
- `Vol_Suite/dealer_exposure_universe.py`: Task 1 owns `Candidate`, `ProbeResult`, `ManifestUnit`, and `UniverseManifest`; requires `selection_date`, real `source_list`, point-in-time sector, exact expiry/DTE probe matching, locked DTE strata `(1–3),(4–7),(8–10)`, event habitats `FOMC/EARNINGS/OPEX`, and `PASS/INELIGIBLE/HARD_GAP` with no imputed zeros. Held ticker×day extraction is already an explicit boundary.
- `Vol_Suite/dealer_exposure_acquisition.py`: schedules are deterministic over `(calendar_day,ticker,expiry,dte,habitat,sector,candidate_source)`, sorted and deduplicated, held pairs remain visible, and acquisition is sequential/fail-closed. The calendar binding must be added to this request/evidence identity rather than independently re-derived.
- `Vol_Suite/provenance_contract.py`: canonical JSON is `sort_keys=True`, compact separators, UTF-8, `allow_nan=False`; SHA-256 helpers are the repository-wide provenance primitives.
- `Vol_Suite/run_task4_evaluation.py` and `.superpowers/sdd/task-4-report.md`: Task 4 clusters same-day tickers into one unique calendar-day observation, retains no-firing days, reports event/control/pooled strata, and requires strict registry/artifact/source hashes. Its primary causal target and fixed clocks remain unchanged.
- `.superpowers/sdd/task-1-brief.md` / `task-1-report.md`: candidate selection must be point-in-time and non-survivorship-biased; event/control mix is approximately 1/3 versus 2/3; missing operational surprise is descriptive only; no zero-DTE or imputation.
- `.superpowers/sdd/task-4-brief.md` / `task-4-report.md` and `integration-report.md`: primary unit is unique day, target power is approximately `n=257`, registry identity is fail-closed, and no live/master/model promotion is permitted.
- Existing `.hermes/plans/2026-08-15-opex-calendar-module.md` was read as a related untracked draft; this file is a separate PM-review artifact and does not overwrite it.

### Current gaps

1. `third_friday()` does not resolve holiday shifts, early closes, or settlement convention.
2. `expiry_selector.py` has wall-clock dependence and nearest-expiry fallback that can change across reruns as listings evolve.
3. `OPEX` is currently a habitat string, not a typed event with session ID, source identity, or window boundaries.
4. FOMC/earnings labels are not yet a shared, hash-bound event contract and must not be conflated with surprises.
5. Task 1 and Task 4 need the same calendar identity to prove that date/session/window semantics were resolved under the same point-in-time rules.

## Global constraints

- Planning is read-only: no acquisition, no corpus/manifests edits, no live/master/config/model edits, no SDD progress update, and no commit.
- Initial scope is US listed equity/ETF options in `America/New_York`; other venues/products require a new approval.
- Every snapshot has an explicit knowledge cutoff, source-selection provenance, schema/policy/resolver versions, and source content hashes.
- Facts are usable only when available by the declared cutoff. Unknown or conflicting facts become `HARD_GAP`/`INELIGIBLE`, never a guessed date or zero.
- Calendar dates are local venue dates; serialized instants are timezone-qualified ISO-8601 and normalized to UTC for comparison.
- Task 1 remains eligibility/probe owner; Task 4 remains unit, strata, outcome, and causal-gate owner.
- Calendar membership is descriptive habitat; it cannot create operational surprise or causal eligibility.

## Proposed files and schema

### Files to create after PM approval

- `Vol_Suite/opex_calendar.py`: immutable records, validation, standard-monthly arithmetic, snapshot resolver, windows, canonical serialization and hashes.
- `Vol_Suite/tests/test_opex_calendar.py`: network-free unit and fixture replay tests.
- `Vol_Suite/tests/fixtures/opex_calendar/`: minimal reviewed historical fixtures; no runtime downloads.
- `.superpowers/sdd/opex-calendar-integration.md`: approved SDD handoff and ownership record.

### Core records

Use frozen dataclasses or equivalent immutable mappings with explicit schema versions:

```python
CalendarSnapshot(
    schema_version: int,
    calendar_policy_version: str,
    resolver_code_version: str,
    knowledge_cutoff: str,        # aware ISO-8601 instant
    timezone: str,                # America/New_York in v1
    venue_scope: str,             # US_OPTIONS_REGULAR
    source_records: tuple[SourceRecord, ...],
    holidays: tuple[HolidayRecord, ...],
    sessions: tuple[SessionRecord, ...],
    monthly_rules: tuple[MonthlyRule, ...],
    event_records: tuple[EventRecord, ...],
    snapshot_hash: str,
)

SourceRecord(
    source_id: str, source_kind: str, publisher: str, source_version: str,
    effective_start: str | None, effective_end: str | None,
    retrieved_at: str | None, available_at: str,
    content_sha256: str, parser_version: str, selection_reason: str,
)

OpExRecord(
    product_family: str,
    nominal_date: str,
    observed_expiry_date: str,
    expiry_class: str,             # STANDARD_MONTHLY|SHIFTED_MONTHLY|NONSTANDARD
    session_id: str,
    session_status: str,            # OPEN|HOLIDAY_CLOSED|UNKNOWN
    regular_open: str | None,
    regular_close: str | None,
    early_close: bool,
    close_reason: str | None,
    settlement_style: str,          # PM_CLOSE|AM_SETTLEMENT|UNKNOWN
    settlement_timestamp: str | None,
    listing_source_ref: str | None,
    calendar_hash: str,
)

EventWindow(
    event_id: str, event_type: str, event_day: str,
    window_start: str, window_end: str, timezone: str,
    anchor: str, source_ref: str, surprise_status: str,
    causal_surprise_eligible: bool, calendar_hash: str,
)
```

`EventWindow.event_type` is `OPEX`, `FOMC`, or `EARNINGS`. `surprise_status` is `NOT_APPLICABLE`, `OPERATIONAL`, or `DESCRIPTIVE-HABITAT`; calendar resolution may only carry an already-attested status and may not manufacture one.

### Pure API

```python
load_snapshot(payload: Mapping[str, Any]) -> CalendarSnapshot
canonical_calendar_bytes(snapshot: CalendarSnapshot) -> bytes
calendar_hash(snapshot: CalendarSnapshot) -> str
standard_monthly_candidate(year: int, month: int) -> date
resolve_opex(snapshot: CalendarSnapshot, *, product_family: str,
             nominal_or_observed: str, as_of: str) -> OpExRecord
resolve_event_window(snapshot: CalendarSnapshot, *, event_type: str,
                     event_day: str, window_policy: str, as_of: str) -> EventWindow
calendar_for_probe(snapshot: CalendarSnapshot, *, ticker: str,
                   calendar_day: str, expiry: str, dte: int) -> Mapping[str, Any]
```

`standard_monthly_candidate()` must never claim session validity. `resolve_opex()` rejects an `as_of` later than the snapshot cutoff, refuses unavailable source facts, and never silently snaps to a neighboring listed expiry.

## Point-in-time sources and selection provenance

Define an allowlisted source registry with four source tiers:

1. venue/exchange rule source for expiration and settlement semantics;
2. historical venue session source for holiday, regular, and early-close dates;
3. point-in-time listed-expiration evidence proving the contract existed;
4. supplied FOMC/earnings event snapshots, used only as integration inputs.

For every field, persist the ordered source contribution, `source_id`, publisher, effective interval, `available_at`, retrieval time, parser/schema version, content SHA-256, and selection rationale. A lower tier may fill only a field absent from a higher tier and must state why. Conflicting authoritative facts produce `HARD_GAP` with both source identities; “latest wins” is forbidden.

For analysis day `D`, only facts with `available_at <= knowledge_cutoff` are admissible. Preserve original scheduled event timestamps separately from later revisions. A historical rerun is a pure function of `(snapshot_hash, product_family, requested_date, policy_version, as_of)`.

Calendar snapshots must not be generated from today’s surviving symbols, current vendor pages, or post-event revised metadata when a historical knowledge cutoff is requested. Candidate universe provenance remains Task 1’s `selection_date`/`source_list` contract.

## Timezone, sessions, and OpEx semantics

- Interpret session dates in `America/New_York`; reject naive timestamps and timestamps that convert to a different local `calendar_day`.
- Keep regular open, regular close, early close, holiday closed, and settlement timestamp as separate facts.
- Compute third Friday, then consult the point-in-time session snapshot. An open date becomes `STANDARD_MONTHLY` only when venue rule and listing evidence agree.
- A holiday-closed nominal date may shift only according to the product-specific sourced rule, becoming `SHIFTED_MONTHLY`; retain nominal date, observed date, holiday, and rule source. Do not assume all products shift identically.
- An early close retains the same date with actual close and reason; it is not a holiday shift.
- AM settlement, PM close, last tradable session, and analysis anchor are distinct. Unknown settlement is a visible gap.
- If listing evidence disagrees with rule-derived date, emit `NONSTANDARD`/`HARD_GAP` for explicit review rather than selecting one silently.
- DTE remains exact local-date arithmetic from Task 1’s study day to resolved observed expiry. Settlement-time DTE would be a new policy version.

Versioned initial window policies should include `PRE_OPEX_SESSION` (prior valid session close through OpEx close), `OPEX_DAY` (local open through actual close), and `POST_OPEX_RESPONSE` (actual close through Task 4’s existing response clock). The calendar module provides boundaries; Task 4 owns return clocks and breach semantics. Equality at the PRE_WINDOW/breach boundary remains invalid under the existing strict-before rule.

## FOMC and earnings boundaries

- **FOMC:** normalize an approved scheduled timestamp and timezone into an event window. Do not fetch statements, infer rate surprises, or compute actual-minus-expected. `run_causal_arm_v2` remains the owner of operational surprise eligibility.
- **Earnings:** carry only an approved ticker-specific release window and stated anchor (`BMO`, `AMC`, or `DURING_SESSION`). Do not choose from a current page, infer expectations, or calculate surprise. Expectations/actuals require a separate hash-bound adapter.
- **Overlap:** preserve a sorted tuple of all applicable events and derive only a presentation label. Task 4 must be able to report OPEX-only, FOMC-only, earnings-only, overlap, control, and descriptive-habitat slices without changing the unique-day primary unit.

## Task 1 integration

Before a schedule can be admitted, Task 1 supplies ticker, local calendar day, requested expiry, DTE, snapshot hash, and `as_of`. The calendar service returns observed expiry, session ID/status, open/close/settlement, timezone, event IDs/windows, calendar hash, and policy version.

Extend the deterministic schedule/probe identity with calendar binding identity, while preserving legacy held-pair extraction keys. PASS requires exact agreement among candidate day/expiry/DTE, observed expiry/session, `calendar_hash`, source hashes, and binding hash. Missing, conflicting, unknown, wrong-day, or wrong-timezone facts become `HARD_GAP`/`INELIGIBLE`. Probe evidence records nominal/observed dates, session timestamps, resolver version, snapshot/source hashes, and window ID. Dry-run/probe-only paths remain network-free.

## Task 4 integration and strata

Task 4 consumes calendar-enriched Task 3 records; it does not call the resolver. Add to immutable provenance/context: `calendar_hash`, `calendar_policy_version`, `event_ids`, sorted `event_types`, `event_window_id`, `nominal_opex_date`, `observed_expiry_date`, `session_status`, `early_close`, `settlement_style`, timezone, and binding hash.

Same-day ticker collapse remains deterministic and preserves nested per-ticker calendar metadata. Aggregate events by sorted union, with explicit overlap label; never average calendar fields. Keep all-eligible primary, event-only, control-only, pooled, and descriptive OPEX/FOMC/earnings/overlap slices. No-firing days remain in the denominator. Early-close and shifted-OpEx rows remain reportable sensitivity labels, not post-hoc exclusions.

Bind calendar snapshot/binding hashes into the Task 3 artifact manifest. Any source, policy, resolver, or field change must invalidate the old artifact and force a new comparison. Calendar membership cannot by itself drive `BETTER`, `WORSE`, or causal acceptance; existing Task 4 registry, power, CI, placebo, reverse-lag, and event/control gates remain authoritative.

## Hashes, versions, and deterministic output

Use `provenance_contract.py` exclusively:

- `calendar_schema_version`: serialized-field/meaning changes;
- `calendar_policy_version`: shift/session/settlement/window rule changes;
- `resolver_code_version` and `resolver_code_hash`;
- per-source `content_sha256`;
- `snapshot_hash`: canonical snapshot payload excluding self-hash;
- `calendar_binding_hash`: canonical `(snapshot_hash, product_family, nominal date, observed date, session, settlement, windows, policy, as_of)`;
- existing `candidate_key`, `raw_payload_hash`, `artifact_hash`, and registry identity remain owned by Task 1/3 acquisition contracts.

Serialize UTF-8 canonical JSON with sorted keys, compact separators, `allow_nan=False`; sort all records by explicit stable keys. Do not include wall-clock `generated_at` in deterministic identity; if runtime metadata is emitted, inject it and exclude it from canonical hashing. Identical fixtures with permuted input order must produce byte-identical output and hash.

## Staged path into the approved SDD workflow

### Stage 0 — PM contract freeze

Approve product/venue scope, allowlisted local sources, knowledge-cutoff semantics, shift/settlement rules, primary window policy, and whether early-close/shifted rows are primary or sensitivity labels. Record decisions in `.superpowers/sdd/opex-calendar-integration.md`; acquire nothing.

### Stage 1 — Pure schema and arithmetic (TDD, network-free)

Write failing tests, then implement snapshot loading, validation, canonical serialization, hashes, third-Friday arithmetic, and the resolver state machine. Cover ordinary, holiday-closed, non-shifting, early-close, AM/PM settlement, unknown/conflict, `as_of`, DST, UTC-boundary, and deterministic-order cases. Do not modify `expiry_selector.py` yet.

### Stage 2 — Fixture-backed Task 1 binding

Extend schedule/probe contracts with calendar binding and exact identity. Test shifted expiry, missing listing, wrong date/DTE/timezone, duplicate binding, held pair, locked strata, zero-DTE, no-imputation, and descriptive event status. Verify no fetcher is invoked by dry-run/probe-only.

### Stage 3 — Fixture-backed Task 4 consumption

Extend Task 3 registry and Task 4 context/strata with calendar fields and overlap union. Test same-day collapse, all event/control/pooled slices, no-firing denominator, early-close/shifted labels, and hash mismatch invalidation. Preserve the existing estimand and gates.

### Stage 4 — Historical fixture replay gate

Add a reviewed fixture matrix for ordinary OpEx, holiday shift, early close, AM/PM settlement, FOMC scheduled event, BMO/AMC earnings, overlap, and unavailable surprise. Include a later source revision that must be excluded under an earlier cutoff. Run with network disabled and compare canonical bytes/hashes.

### Stage 5 — Conditional adapters

Only after Stage 4 and separate PM approval, add adapters that archive raw inputs and request/response identity, parse into the pure schema, and emit source hashes/cutoff. A source failure remains `HARD_GAP`; it cannot fall back to a current source. Run sequentially under existing acquisition approval/concurrency rules.

### Stage 6 — SDD execution and integration

Create an approved task brief with exact file ownership. Execute task-by-task using repository SDD conventions: failing test, minimal implementation, focused no-plugin Python 3.12 verification, review, then integration. Run calendar, Task 1, Task 4, and relevant Vol Suite tests; verify no network, `git diff --check`, status, and artifact hashes. Only then consider an opt-in compatibility wrapper for `expiry_selector.py`, after auditing every caller/test; do not silently replace its public fallback.

## Test matrix

### Network-free unit/fixture tests

- leap/non-leap and month-boundary third Fridays;
- sourced holiday shift versus explicitly non-shifting product;
- early close with actual close/reason, not holiday classification;
- AM settlement distinct from PM close;
- unknown/conflicting facts fail closed;
- cutoff excludes later source revision;
- DST, UTC/local-date boundary, naive timestamp, wrong-day timestamp;
- permutation-invariant bytes/hashes and changed-source/policy hashes;
- no endpoint import/call in fixture mode.

### Task 1 tests

- PASS requires exact calendar binding and source/hash evidence;
- missing/ambiguous calendar facts are visible exclusions;
- deterministic request/manifest bytes;
- held ticker×day, candidate provenance, sectors/tickers, DTE strata, zero-DTE, no-imputation, and descriptive-surprise rules remain intact.

### Task 4 tests

- deterministic unique-day collapse and event union;
- OPEX/FOMC/earnings/overlap/control slices without replacing primary;
- no-firing retention and fixed clocks;
- missing/forged/detached calendar registry binding invalidates before fitting;
- changed source/policy/resolver hash invalidates artifact;
- early-close/shifted labels remain present and cannot be outcome-selected away.

## Scope taxonomy

### SELECTED — implement after PM approval

- Pure snapshot-backed US equity/ETF OpEx/session/settlement resolver.
- Versioned event-window contract for OpEx plus supplied FOMC/earnings schedule metadata.
- Shared calendar/binding hashes and deterministic serialization.
- Fixture-backed Task 1 probe binding and Task 4 provenance/strata consumption.
- Network-free and historical replay tests.

### CONDITIONAL — separate gate required

- Any network source adapter or new calendar/event acquisition.
- SPX/index, futures, non-US venues, quarterly-only products, or different settlement conventions.
- Earnings/FOMC expectations, actuals, surprises, or causal joins.
- Replacing `expiry_selector.py` behavior or removing nearest-expiry fallback.
- Treating descriptive event habitat as causal evidence.

### PRESERVED — do not touch here

- `Vol_Suite/dealer_positioning.py`, live configuration, current expiry book, and Greek/sign conventions.
- Task 1 held-pair/no-survivorship boundary, candidate provenance, probe statuses, locked DTE strata, and no-imputation rules.
- Task 4 unique-day unit, fixed clocks, no-firing denominator, power/CI/falsifier gates, and strict artifact registry.
- Existing acquisition/scratch corpora, master branch, secrets, and SDD progress state.

### EXCLUDED — future work

- Automatic current-calendar scraping or resolver-time downloads.
- Imputation of missing holidays, expiries, settlements, event times, surprises, or returns.
- Post-hoc event reclassification, model selection, trading signals, or dealer-model promotion.

## PM decisions required

1. Confirm US equity/ETF options and `America/New_York` as v1 scope.
2. Approve local point-in-time source registry and authoritative knowledge-cutoff field.
3. Confirm date-to-date DTE versus a future settlement-time policy.
4. Select the primary OpEx window and early-close/shifted-session treatment.
5. Decide whether `expiry_selector.py` receives an opt-in wrapper only after caller audit.

---

This artifact is intentionally untracked and plan-only. No data was acquired, no existing file was modified, and no commit was created while drafting it.
