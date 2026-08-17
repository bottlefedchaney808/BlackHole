# PLAN — Reusable Point-in-Time OpEx Calendar Module

Status: **plan-only; NO production code, live model, expiry-book, acquisition corpus, or network data changed**  
Author: Hermes Agent (Dealer-Exposure-Dev worktree, 2026-08-15)  
Approval gate: PM approval is required before implementation or any calendar-data acquisition.

## Goal

Add a reusable, deterministic calendar service that resolves standard monthly OpEx dates and their actual market-session semantics as-of a declared historical knowledge cutoff, then supplies the same dated event/session contract to Task 1 probes and Task 4 strata without changing the live dealer model or the current expiry-book.

## Architecture

Create a pure calendar core plus immutable, hash-addressed source snapshots. The core accepts only a supplied snapshot/fixture and an explicit `as_of`/knowledge cutoff; it never silently consults the current network, current exchange schedule, or today's event calendar. A thin adapter may later materialize approved source snapshots, but source selection and normalization remain separate from date arithmetic so historical reruns are reproducible.

The module should distinguish **nominal expiry date**, **observed settlement/expiration session**, and **analysis event day/window**. A standard third-Friday rule is only a candidate generator. Holiday shifts, exchange session status, early close, settlement convention, and vendor-listed expiry must be explicit fields and must never be inferred from a filename or a current listing lookup.

## Grounding from the current repository

Read-only inspection of the current worktree found:

- `Vol_Suite/expiry_selector.py` is the existing shared expiry picker. It uses `DEFAULT_A = 365`, calculates `third_friday`, classifies a Friday as `monthly`, and chooses the nearest listed weekly/monthly/overall expiry. It has no point-in-time source snapshot, holiday calendar, settlement rule, early-close field, event-window contract, or historical `as_of`; `resolve_expiration` can silently fall back to a different listed expiry when the requested one is absent.
- `Vol_Suite/dealer_exposure_universe.py` already locks DTE strata `(1, 3)`, `(4, 7)`, `(8, 10)`, the habitat vocabulary `FOMC`, `EARNINGS`, `OPEX`, and `DESCRIPTIVE-HABITAT`, and strict no-zero-DTE/no-imputation probe validation. Task 1 currently requires an exact `(ticker, day, expiry, DTE)` probe match but does not own the calendar semantics that produce that expiry/day.
- `Vol_Suite/dealer_exposure_acquisition.py` creates deterministic ticker×calendar-day schedules, excludes held ticker×day pairs, records `candidate_key`, and requires sequential concurrency `1`. Its probe request currently carries only calendar/expiry/DTE/habitat/sector/source fields; it needs a calendar identity and session-window identity, not an independently re-derived date.
- `Vol_Suite/run_task4_evaluation.py` collapses same-day tickers into one unique calendar-day unit, reports event/control/pooled strata, preserves no-firing days, and requires strict hashes/registry provenance. It currently consumes an event indicator/label rather than a typed OpEx event object with session semantics.
- `Vol_Suite/run_causal_arm_v2.py` already enforces strict pre-window-before-breach timestamps, fixed clocks, and labels missing operational surprise as `DESCRIPTIVE-HABITAT`; calendar integration must provide context only and must not turn event membership into a surprise or a causal claim.
- `Vol_Suite/provenance_contract.py` defines the repository's canonical JSON (`sort_keys`, compact separators, `allow_nan=False`) and SHA-256 primitives. The calendar identity must use these primitives rather than inventing another hash convention.
- `.superpowers/sdd/task-1-brief.md` and `.superpowers/sdd/task-4-brief.md` freeze the current study unit, event/control mix, DTE strata, and unique-day/power rules. `.superpowers/sdd/task-4-report.md` records the fail-closed Task 4 registry/hash boundary and the requirement that event-only, control-only, and pooled outputs remain separate.
- `.superpowers/sdd/task-1-report.md` records the verified Task 1 contract and its remaining boundary: it intentionally does not invent candidate lists or event labels; approved static point-in-time inputs and real probes are a later concern.
- The handoff `Vol_Suite/docs/Dealer posistioning notes/HANDOFF_dealer_exposure_dev_20260814.md` explicitly preserves the live model and says no approval should be inferred from descriptive event results. This plan therefore leaves `dealer_positioning.py`, live configuration, and expiry-book calculations untouched.

### Current calendar gaps to close

1. `third_friday` is a nominal rule, not a historical exchange-session resolver. It does not answer what happens when the date is a holiday, when trading closes early, or whether settlement is AM/PM/closing value.
2. `expiry_selector` depends on `datetime.now()` when `today` is omitted and can fallback to a neighboring listed expiry. That is unsuitable for point-in-time replay and can change the selected contract as listings evolve.
3. `OPEX` is currently only a habitat string. There is no event date/window, session ID, event-source identity, or explicit distinction between nominal monthly expiry and the actual tradable/settlement session.
4. FOMC and earnings are handled at the analysis-record boundary, not by a reusable calendar contract. The module must not fetch or manufacture releases, expectations, or surprises.
5. Task 1's exact probe binding and Task 4's strict registry binding need a shared calendar hash/version to prove that a date/expiry/window was resolved under the same rules.

## Global constraints

- **Read-only now:** do not acquire network data, alter existing corpora/manifests, modify the live model or expiry-book, commit, or edit the approved SDD implementation files during planning.
- **Point-in-time only:** every calendar artifact requires `knowledge_cutoff` and source-selection provenance; no `latest`, wall-clock, or implicit current-date default is allowed.
- **Fail closed:** unknown holiday/session/settlement/event status is `HARD_GAP` or `INELIGIBLE`, never a guessed date, zero, or imputed control.
- **Deterministic:** canonical sort order, canonical JSON, explicit timezone, explicit clock rules, stable candidate keys, and SHA-256 identities are mandatory.
- **No survivorship:** calendar data must not be selected from today's listed winners, today's surviving symbols, or post-event revised metadata when a historical snapshot is required.
- **Preserve existing contracts:** Task 1 remains the eligibility owner, Task 4 remains the evaluation/strata owner, and the live model/expiry-book remain unchanged.

## Proposed module and contracts

### Files to create

- `Vol_Suite/opex_calendar.py` — pure calendar types, resolver, event-window construction, canonical serialization, validation, and hash/version functions.
- `Vol_Suite/tests/test_opex_calendar.py` — network-free unit/fixture tests.
- `Vol_Suite/tests/fixtures/opex_calendar/` — small checked-in historical fixtures covering ordinary months, holiday shifts, early close, and source revisions. Fixtures contain only synthetic/minimal calendar rows or already-approved local snapshots; no acquisition is performed by this plan.
- `.superpowers/sdd/opex-calendar-integration.md` — implementation handoff/SDD gate record, created only after PM approval (listed here for the later implementation stage, not to be created now).

### Core typed records

Use frozen dataclasses or equivalent immutable mappings with explicit schema versions:

```python
CalendarSnapshot(
    schema_version: int,
    calendar_version: str,
    knowledge_cutoff: str,        # aware ISO-8601 instant
    timezone: str,                # IANA, initially America/New_York for US equity options
    venue_scope: str,             # e.g. US_OPTIONS_REGULAR
    source_records: tuple[SourceRecord, ...],
    holidays: tuple[HolidayRecord, ...],
    sessions: tuple[SessionRecord, ...],
    monthly_rules: tuple[MonthlyRule, ...],
    event_records: tuple[EventRecord, ...],
    snapshot_hash: str,
)

SourceRecord(
    source_id: str,
    source_kind: str,             # venue_rule, exchange_calendar, vendor_listing, event_feed
    publisher: str,
    source_version: str,
    retrieved_at: str | None,     # provenance only; not a runtime default
    effective_start: str | None,
    effective_end: str | None,
    content_sha256: str,
    selection_reason: str,
)

OpExRecord(
    product_family: str,
    nominal_date: str,             # third-Friday candidate / rule output
    observed_expiry_date: str,    # actual date represented by the listed contract
    expiry_class: str,             # STANDARD_MONTHLY, SHIFTED_MONTHLY, NONSTANDARD
    session_id: str,
    session_status: str,          # OPEN, HOLIDAY_CLOSED, UNKNOWN
    regular_open: str | None,
    regular_close: str | None,
    early_close: bool,
    close_reason: str | None,
    settlement_style: str,        # PM_CLOSE, AM_SETTLEMENT, UNKNOWN
    settlement_timestamp: str | None,
    listed_contract_evidence: SourceRef | None,
    calendar_hash: str,
)

EventWindow(
    event_id: str,
    event_type: str,               # OPEX, FOMC, EARNINGS
    event_day: str,
    window_start: str,
    window_end: str,
    timezone: str,
    anchor: str,                   # expiry_session_close, release_timestamp, etc.
    source_ref: SourceRef,
    surprise_status: str,          # NOT_APPLICABLE, OPERATIONAL, DESCRIPTIVE-HABITAT
    causal_surprise_eligible: bool,
    calendar_hash: str,
)
```

The exact field names may be adapted to repository style, but the implementation must preserve these distinctions and identity fields. `event_id` must include the event type, event date, venue/product scope, and calendar hash; two records with the same date but different source version must not collide.

### Public API

Implement a small API with no network side effects:

```python
def load_snapshot(payload: Mapping[str, Any]) -> CalendarSnapshot: ...
def canonical_calendar_bytes(snapshot: CalendarSnapshot) -> bytes: ...
def calendar_hash(snapshot: CalendarSnapshot) -> str: ...
def standard_monthly_candidate(year: int, month: int) -> date: ...
def resolve_opex(snapshot: CalendarSnapshot, *, product_family: str,
                nominal_or_observed: str, as_of: str) -> OpExRecord: ...
def event_window(snapshot: CalendarSnapshot, *, event_type: str,
                 event_day: str, window_policy: str, as_of: str) -> EventWindow: ...
def calendar_for_probe(snapshot: CalendarSnapshot, *, ticker: str,
                       calendar_day: str, expiry: str, dte: int) -> Mapping[str, Any]: ...
```

`resolve_opex` must reject a request whose `as_of` is later than the snapshot cutoff or whose requested source evidence was not available by the cutoff. It must return an explicit non-standard/unknown result rather than silently snapping. `standard_monthly_candidate` is arithmetic only and must never claim that a candidate was a valid settlement/expiry session.

## Point-in-time source policy and selection provenance

### Source tiers

The implementation phase should define an allowlisted source registry, not ad hoc URLs:

1. **Venue/exchange rule source:** authoritative contract specification for standard monthly expiration and settlement style for the product family.
2. **Historical trading-session source:** exchange/venue holiday and regular/early-close schedule effective for the requested year.
3. **Historical listing evidence:** point-in-time listed expirations from an approved local snapshot or vendor artifact. This is evidence that a contract existed, not a replacement for venue rules.
4. **Event source adapters:** FOMC schedule and earnings release metadata only when supplied as point-in-time snapshots. These are integration inputs, not calendar truth for OpEx.

A lower-tier source may fill a field only when the higher-tier source does not define it and the record says why. Conflicts must produce `HARD_GAP` with both source identities; do not choose by “latest” or by lexical order alone.

Each snapshot must record: `source_id`, publisher, source version/effective interval, selection rationale, retrieval time, declared knowledge cutoff, content hash, parser/schema version, and the ordered field-level contribution. “Current exchange calendar” and “today's listed expirations” are invalid provenance for a historical date unless the artifact itself is explicitly a historical snapshot.

### Historical/as-of rule

For a requested analysis day `D`, use only facts whose `available_at <= knowledge_cutoff`. If the study uses an event scheduled in advance, retain the originally published schedule and separately retain later outcome/revision metadata if available. Never overwrite the original scheduled timestamp with a revised release timestamp. Calendar resolution must be a pure function of `(snapshot_hash, product_family, requested_date, policy_version, as_of)`.

## Timezone and session rules

- Initial scope is US listed equity/ETF options and US market events in `America/New_York`; all serialized instants are timezone-qualified ISO-8601 and normalize to UTC for comparison, while session dates are interpreted in the declared IANA timezone.
- A `calendar_day` is the local venue date, not UTC date. A timestamp is eligible for that day only when its conversion to the declared timezone lands on that date.
- Define `regular_open`, `regular_close`, `early_close`, `holiday_closed`, and `settlement_timestamp` independently. A closed/early session is not converted to a normal close by padding or weekend logic.
- The OpEx analysis window must be policy-named and versioned. Recommended initial policies: `PRE_OPEX_SESSION` (prior valid regular session close through OpEx session close), `OPEX_DAY` (local session open through observed close), and `POST_OPEX_RESPONSE` (observed close through the locked Task 4 response clock). The module supplies boundaries; Task 4 owns outcome clocks.
- `PRE_WINDOW` observations remain subject to the existing strict-before-breach rule. If a boundary equals the breach timestamp, it is invalid, not “close enough.”
- Early-close sessions must carry the actual close and a reason. Whether the event window includes the post-close interval is a policy decision encoded in `window_policy`, never an inferred extra hour.

## OpEx date semantics

1. Compute the nominal third Friday for the Gregorian month.
2. Consult the point-in-time session snapshot for that local date.
3. If the date is open, classify it `STANDARD_MONTHLY` only if the venue/product rule and listed-contract evidence agree.
4. If it is holiday-closed, apply the venue-approved shift rule (for example, prior valid session where applicable) and classify `SHIFTED_MONTHLY`; record both nominal and observed dates, the holiday, and the rule source. Never assume every product shifts identically.
5. If the date is an early close, retain the same date but set `early_close=true`, actual close, reason, and settlement style. Do not treat early close as a holiday shift.
6. If settlement is AM or otherwise not equal to the regular/early close, record settlement separately. A contract's last tradable session, settlement observation, and analysis event anchor must not be conflated.
7. If vendor listing evidence disagrees with the rule-derived date, preserve the disagreement as `HARD_GAP`/`NONSTANDARD` and require explicit product-family review; do not silently pick the vendor date.
8. Standard monthly OpEx is not the same as “nearest monthly listed expiry” for a ticker. `expiry_selector` integration must consume an explicit resolved calendar record or an explicit listed-contract record, not re-run nearest-date fallback.

## Event windows and integration boundaries

### OPEX

The calendar module owns the typed OpEx event/window and the session/settlement anchor. Task 1 uses it to generate/validate the candidate `expiry`, `calendar_day`, `dte`, `event_habitat`, and `window_id`; Task 4 receives the resulting event label and window provenance. OpEx membership alone is descriptive habitat and does not imply an operational surprise.

### FOMC

The calendar module may normalize an approved FOMC scheduled event timestamp into a `FOMC` `EventWindow`, including timezone and source hash. It must not fetch statements, infer rate surprises, or calculate `actual - expected`. `run_causal_arm_v2.surprise_status` remains the owner of operational surprise eligibility; absent surprise remains `DESCRIPTIVE-HABITAT` and cannot become causal through calendar membership.

### Earnings

The calendar module may carry an approved earnings release window and `BMO`, `AMC`, or `DURING_SESSION` anchor when the source states it. It must not choose a release timestamp from a current vendor page, backfill missing expectations, or treat an earnings label as a surprise. Ticker-specific earnings source and expectation/actual data remain outside this module and must be joined by a separate, hash-bound event adapter.

### Overlapping events

Represent all applicable events as a sorted tuple, not one overwritten habitat. Derive a deterministic primary display label only as a presentation field. Task 4 must be able to report OPEX-only, FOMC-only, earnings-only, overlap, control, and descriptive-habitat rows without changing the primary unit or dropping no-firing days.

## No-survivorship and candidate handling

- The calendar module must not construct an expansion universe from present-day symbols or contracts. It accepts a Task 1 candidate manifest with `selection_date`, `source_list`, point-in-time sector, and asset type.
- Do not infer that a historical symbol was eligible because it exists today. Historical candidate-list membership and sector labels remain upstream provenance fields.
- `held_pairs_from_paths` and existing held manifests remain the authoritative exclusion boundary for ticker×calendar-day pairs. The calendar layer may annotate a candidate with `held_pair_exclusion`, but must not replace or silently drop the exclusion reason.
- Preserve all schedule outcomes (`PASS`, `INELIGIBLE`, `HARD_GAP`) and source references. A missing historical holiday, listing, or event record is a visible exclusion, never an imputed control.
- Calendar deduplication key: `(product_family, nominal_date, observed_expiry_date, session_id, calendar_hash)`. Task 1 candidate key remains the study unit identity and must include the calendar hash/version when materialized.

## Task 1 probe integration

Add a calendar resolution step before `run_availability_probes` admits a primary schedule:

1. Task 1 supplies `ticker`, `calendar_day`, requested `expiry`, DTE, and the snapshot hash/as-of.
2. The calendar service returns a validated calendar binding containing observed expiry date, local session date, session ID, close/settlement, event windows, `calendar_hash`, and `calendar_policy_version`.
3. The probe request includes this binding identity. Probe evidence must confirm the same expiry/session/date and must not re-resolve from `datetime.now()` or a live listing endpoint.
4. Task 1 eligibility adds checks for `calendar_binding`, `session_status`, `observed_expiry`, `settlement_style`, `timezone`, and `calendar_hash`; unknown/contradictory values are `HARD_GAP`.
5. `ProbeResult.evidence` records the calendar snapshot hash, source hashes, resolver version, nominal/observed date pair, and session timestamps. A PASS probe is valid only when its calendar binding matches the schedule and artifact registry.
6. DTE remains exact calendar-day arithmetic between the study day and the resolved observed expiry. If the research policy wants DTE to be measured to settlement rather than date, that is a new explicit policy/version, not a hidden conversion.

Network-free Task 1 tests must use fixture snapshots and assert that identical schedule inputs yield byte-identical requests and manifests, while missing/conflicting calendar facts become explicit exclusions.

## Task 4 strata integration

Task 4 should consume immutable calendar-enriched records, not call the resolver itself:

- Add `calendar_hash`, `calendar_version`, `event_ids`, `event_types`, `event_window_id`, `nominal_opex_date`, `observed_expiry_date`, `session_status`, `early_close`, and `settlement_style` to each record's provenance/context.
- Preserve the existing primary unique-calendar-day clustering and same-day ticker ordering. Event labels must be aggregated deterministically: union/sorted event types and explicit overlap label; no averaging of calendar metadata.
- Keep `event-only`, `control-only`, and `pooled` strata, plus OPEX/FOMC/earnings/overlap descriptive slices. Do not let a richer slice replace the primary all-eligible estimate, and do not drop no-firing days.
- Event/control acceptance gates remain those already implemented in Task 4; calendar completeness can invalidate a comparison, but calendar membership cannot by itself drive `WORSE`, `BETTER`, or causal acceptance.
- The Task 4 artifact registry must bind calendar snapshot hash and canonical calendar binding into the artifact manifest. Any changed calendar source/version must change the artifact hash and force a new comparison artifact.
- Early-close and shifted-OpEx rows should be reportable strata/sensitivity labels. They must not be post-hoc excluded merely because their results look different.

## Hashes, versions, and deterministic outputs

Use `Vol_Suite/provenance_contract.py` for all canonical JSON and SHA-256 operations.

Required identities:

- `calendar_schema_version`: changes when serialized fields/meaning change.
- `calendar_policy_version`: changes when date-shift, session, settlement, or window rules change.
- `resolver_code_version` and `resolver_code_hash`: identify the implementation that normalized the snapshot.
- `source_content_sha256` per source record.
- `snapshot_hash`: SHA-256 of canonical snapshot payload excluding the self-referential hash field.
- `calendar_binding_hash`: SHA-256 of canonical `(snapshot_hash, product_family, nominal date, observed date, session, settlement, event windows, policy version, as_of)`.
- `candidate_key`: existing Task 1 key extended with `calendar_binding_hash` only at the calendar-enriched artifact boundary; do not silently change legacy held-pair extraction keys.
- `artifact_hash`/`raw_payload_hash`: continue to be owned by the acquisition/provenance registry and bind the calendar hashes as manifest fields.

Serialization requirements: sort keys, compact separators, UTF-8, `allow_nan=False`; sort source records, holidays, sessions, events, and output rows by explicit stable keys; never serialize sets or wall-clock-generated `generated_at` in a supposedly deterministic fixture. Runtime manifests may carry an injected `generated_at`, but it must not participate in the canonical calendar identity.

## Staged implementation path into the approved SDD workflow

This is a plan for a later, PM-approved implementation; no stage below is executed now.

### Stage 0 — PM approval and contract freeze

- Review this plan against the existing Task 1/Task 4 contracts and decide the initial product/venue scope (US equity/ETF options, `America/New_York`, standard monthly only versus index products).
- Approve source registry, historical cutoff semantics, early-close/settlement policy, and event-window policies.
- Record the decision in `.superpowers/sdd/opex-calendar-integration.md`; do not acquire data yet.

### Stage 1 — Pure arithmetic and schema (network-free)

- Add `Vol_Suite/opex_calendar.py` types, canonical serializer, hash functions, third-Friday candidate generator, explicit `as_of` validation, and resolver state machine.
- Write fixtures/tests first. Verify ordinary month, nominal holiday-closed month, early-close month, unknown settlement, conflicting source, deterministic ordering, and hash changes.
- Acceptance: focused tests pass with no network adapter imported or invoked; existing expiry selector and live model are unchanged.

### Stage 2 — Fixture-backed Task 1 binding

- Extend Task 1 schedule/probe contracts to require the calendar binding and exact snapshot/policy hash.
- Add fixture-backed tests for shifted monthly expiry, early close, missing listing, wrong timezone/day, wrong expiry/DTE, duplicate binding, held pair, and no-imputation behavior.
- Acceptance: a PASS probe cannot be produced from an unresolved/unknown calendar; dry-run paths remain network-free and deterministic.

### Stage 3 — Fixture-backed Task 4 consumption

- Extend Task 3/Task 4 provenance manifests and the Task 4 strata context with calendar fields and event overlap handling.
- Add tests asserting same-day collapse, event/control/pooled strata, no-firing denominator, calendar hash binding, early-close/shifted labels, and changed-source invalidation.
- Acceptance: Task 4 remains descriptive/conditional and all existing strict registry/hash gates remain fail-closed.

### Stage 4 — Historical fixture corpus and replay gate

- Add a small, reviewed fixture matrix keyed by historical knowledge cutoff: ordinary OpEx, holiday shift, early close, AM/PM settlement distinction, FOMC schedule, BMO/AMC earnings, overlapping events, and unavailable surprise.
- Run a network-disabled replay that regenerates canonical calendar artifacts byte-for-byte and compares expected hashes. Include a fixture deliberately containing a later revision to prove the earlier `as_of` excludes it.
- Acceptance: historical replay is deterministic and no fixture requires endpoint access or current wall-clock time.

### Stage 5 — Conditional source adapters (separate approval)

- Only after Stage 4 passes and PM approves acquisition, implement source adapters that materialize local snapshots into the pure schema. Adapters run outside the resolver and emit source content hashes, parser version, request parameters, response identity, and cutoff.
- Run sequentially under the existing acquisition approval/concurrency boundary; a source failure creates `HARD_GAP`, never a fallback to a different current source.
- Acceptance: live acquisition is not considered complete until the resulting snapshot passes the same fixture/schema/hash validators and is reproducible from its archived raw inputs.

### Stage 6 — SDD integration and final review

- Add an SDD task brief with exact file ownership and test commands, then execute task-by-task using the repository's approved SDD workflow: failing tests, minimal implementation, focused verification, review, and only then integration.
- Update `.superpowers/sdd/progress.md` only with PM-approved status and fresh command output. Do not merge calendar semantics into `dealer_positioning.py` or the expiry-book unless a separate approved model change exists.
- Run focused calendar, Task 1, Task 4, and relevant Vol Suite tests; run `git diff --check`; verify no network calls in network-free mode; inspect `git status` and artifact hashes before any claim of completion.

## Test matrix (network-free and historical fixtures)

### Pure unit tests

- Third Friday for leap/non-leap years and month boundaries.
- Holiday-closed nominal date → explicit shifted date under a fixture rule; no shift when the fixture says the product is non-shifting.
- Early close retains date and records actual close/reason; it is not classified as holiday closed.
- AM settlement differs from PM close and remains separately represented.
- Unknown/conflicting session or settlement facts return structured `HARD_GAP`.
- `as_of` before source availability rejects the record; later revisions do not enter an earlier snapshot.
- DST transition, UTC/local-date boundary, malformed naive timestamp, and wrong-day timestamp fail closed.
- Canonical output ordering and hashes are identical across input permutations; a source/policy/field change changes the expected hash.

### Task 1 contract tests

- Calendar binding is required for PASS, exact candidate expiry/DTE/session identity is enforced, and source/hash identity is stored in probe evidence.
- Dry-run/probe-only never invokes a fetcher; missing adapter data remains `INELIGIBLE`/`HARD_GAP`.
- Held ticker×day exclusion, duplicate candidate identity, sector/ticker caps, locked DTE strata, zero-DTE, and no-imputation rules remain unchanged.
- FOMC/earnings labels without operational surprise become `DESCRIPTIVE-HABITAT`, not causal event evidence.

### Task 4 contract tests

- Same-day multi-ticker rows collapse once with deterministic event union and no-firing retention.
- OPEX/FOMC/earnings/overlap/control strata report independently without changing the primary estimate.
- Calendar hash mismatch, missing event window, forged binding, changed source version, or missing registry entry invalidates the comparison before fitting.
- Early-close and shifted-OpEx labels are retained; they cannot be removed by post-hoc outcome selection.
- Existing CI, power, placebo/reverse-lag, event/control balance, and `n_for_80` gates remain authoritative.

## Scope taxonomy

### SELECTED — implement after PM approval

- Pure, snapshot-backed OpEx/session/settlement calendar core for the initially approved US equity/ETF options scope.
- Versioned event-window contract and deterministic calendar/binding hashes.
- Fixture-backed Task 1 probe binding and Task 4 provenance/strata consumption.
- Network-free and historical replay tests, including holiday, early-close, settlement, timezone, and source-revision cases.

### CONDITIONAL — require a separate gate

- Any network/source adapter or acquisition of new calendar/event data.
- Product families with different expiration/settlement rules (index options, futures options, non-US venues, quarterly-only products).
- Operational FOMC/earnings surprise integration, expectations, or release-result joins.
- Replacing `expiry_selector`'s public behavior or removing its fallback; first prove all callers and tests can consume an explicit calendar binding.

### PRESERVED — do not touch in this module

- `Vol_Suite/dealer_positioning.py` live model and configuration.
- Current expiry-book implementation and its descriptive/conditional status.
- Task 4's unique-calendar-day unit, fixed clocks, no-firing denominator, power gates, and fail-closed registry contract.
- Task 1's no-survivorship candidate boundary, held-pair extraction, DTE strata, probe statuses, and no-imputation rule.
- Existing untracked acquisition corpora/manifests and the master branch.

### EXCLUDED — future work

- Automatic current-calendar downloading or vendor scraping from the resolver.
- Model selection, causal acceptance, trading signals, or changing dealer sign/Greek conventions based on OpEx labels.
- Imputing missing holidays, expiries, settlements, event timestamps, surprises, or returns.
- Reclassifying historical event days after seeing Task 4 outcomes.

## Open questions for PM approval

1. Is the initial product scope US equity/ETF options only, or must SPX/index options and their distinct settlement conventions be included in v1?
2. Which approved local point-in-time source snapshots may be used for exchange holidays, listed expirations, FOMC, and earnings, and what knowledge-cutoff field is authoritative?
3. Should DTE remain date-to-date as Task 1 currently specifies, or should a future policy measure time-to-settlement? The latter must be a new versioned policy.
4. Which OpEx window policy is primary for the study, and are early-close/shifted sessions primary eligible units or labeled sensitivities?
5. Should `expiry_selector.py` gain a compatibility wrapper around the new resolver, or should integration remain opt-in until all callers are audited?

---

**Plan deliverable:** this document is intentionally untracked and plan-only. No data acquisition, production edit, live-model change, or commit was performed while drafting it.
