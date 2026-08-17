# Universe Expansion and Locked-Live vs Expiry-Book Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the evaluation beyond exhausted SPY/QQQ and run a provenance-clean, apples-to-apples comparison of the locked live dealer model against `expiry_book_exposure.py`, followed by a pre-registered better/worse/indeterminate decision without auto-promotion.

**Architecture:** Keep `Vol_Suite/dealer_positioning.py` and the live configuration untouched. Add a read-only acquisition/eligibility layer for genuinely new ticker×calendar-day clusters, a common-input dual-model comparison harness, and a causal-arm adapter that feeds one deduplicated calendar-day decision table into the existing `run_causal_arm_v2.py` locks. Persist raw inputs, manifests, per-strike outputs, provenance, diagnostics, hashes, and final verdicts as immutable run artifacts.

**Tech Stack:** Existing Python/NumPy/Pandas code, JSON/Markdown artifacts, pytest network-free tests, existing ThetaData seed/probe conventions, `run_causal_arm_v2.py`, `run_compare_live_vs_new.py`, and prior dual-pipeline/falsifier harness patterns.

## Global Constraints

- Planning approval precedes implementation; no acquisition or production edit starts from this plan alone.
- The live model remains exactly `vol_surface_replication` + SVI + `IV_DEADBAND=0.01` + `DEALER_VANNA_FLOW=1` + accumulation ON; `rec.vanna=-1×BS`; real spot.
- `expiry_book_exposure.py` remains descriptive/conditional and test-only; a positive result is re-admission-to-evidence only.
- No imputation. Missing pre-window IV, spot, chain, OHLC, or firing support yields an explicit exclusion/data-gap record.
- One independent unit is one calendar day. Same-day tickers are clustered/merged, never counted as separate causal units.
- No billed/network-heavy work occurs until the user approves universe scope, cost, and held-date policy and a read-only design audit clears the implementation.

---

## 1. Baseline findings that the implementation must preserve

- `Vol_Suite/_intraday_cache/causal_arm_v2_prewindow_acquisition_RESULT.md` reports only 1/7 PRE_WINDOW units; the other six are associational-only, so the current causal corpus is not usable for the causal claim.
- `Vol_Suite/_causal_acquisition_20260815/acquisition_manifest.json` contains 62 unique SPY/QQQ days, but the ledger records that the 62-day packet powers the correlational arm only; the orthogonalized β has power 0.3277 and `n_for_80=257`.
- `run_causal_arm_v2.py` already provides fail-closed pre-window cutoffs, unique-day deduplication, Vanna⊥, rank/condition/VIF diagnostics, locked horizons, two clocks, placebo/lead-lag hooks, and provenance labeling. The new implementation must feed it true PRE_WINDOW ΔIV and must not weaken those gates.
- No `run_compare_live_vs_new.py` exists in this worktree. Existing `run_dual_pipeline_gate*.py` files are reference patterns, not a ready harness. The implementation must create a new common-input comparison harness and make level-vs-flow, EOD-vs-intraday, aggregation, and convention-map identities explicit and machine-checked.
- Existing R5–R9 artifacts establish that pooled ticker rows and same-day SPY/QQQ are not independent, that `D_conv` is a distance/descriptive measure, and that the earlier sign/concordance evidence is not promotion evidence.

## 2. Files and artifacts to create/modify during implementation

**Create:**
- `Vol_Suite/universe_expansion_manifest.py` — deterministic candidate universe, held-cluster exclusion, sector/capacity quotas, eligibility/probe result schema, and stable candidate ordering.
- `Vol_Suite/acquire_universe_prewindow.py` — sequential, approval-gated acquisition only; one session per ticker×day; writes raw source payloads and fail-loud provenance records.
- `Vol_Suite/run_live_vs_expiry_book_common_input.py` — common-input adapter; runs both models on identical ticker/day/expiry/spot/chain/OI/IV rows and emits per-strike + aggregate comparison records.
- `Vol_Suite/run_universe_causal_comparison.py` — merges units by calendar day, validates 100% PRE_WINDOW coverage, calls `run_causal_arm_v2`, and emits the single decision table and verdict packet.
- `Vol_Suite/tests/test_universe_expansion_plan_contract.py`
- `Vol_Suite/tests/test_common_input_live_vs_expiry_book.py`
- `Vol_Suite/tests/test_universe_causal_comparison.py`

**Create per run, outside tracked source:**
- `Vol_Suite/_universe_expansion_<run_id>/candidate_manifest.json`
- `probe_results.json`, `acquisition_manifest.json`, `raw/`, `records/`, `per_strike_comparison.json`, `day_records_merged.json`, `provenance_census.json`, `decision_table.json`, `RESULT.md`, and `SHA256SUMS.txt`.

**Do not modify:** `Vol_Suite/dealer_positioning.py`, its locked environment defaults, or the existing locked live path. If an adapter needs a production helper, wrap/import it without changing its source.

## 3. Universe-selection and eligibility procedure

### Task 1 — Freeze the candidate universe rule before probing

- [ ] Start from a point-in-time, non-survivorship-biased candidate list available in repo artifacts or a user-approved static file; do not select from today’s winners. Include SPY/QQQ only as reference families, not as the expansion pool.
- [ ] Use liquid, continuously listed US ETFs/equities with listed options and stable root symbols. Record the selection date, source list, and every exclusion.
- [ ] Exclude every ticker×calendar-day cluster already present in the held SPY/QQQ corpus, `_scratch_tier2`, `_scratch_tier2b`, `_causal_acquisition_20260815`, `_prewindow_acquisition_20260815`, and existing acquisition manifests. A ticker may be new on a date only if that exact pair is absent; a calendar day remains one final unit after clustering.
- [ ] Apply explicit eligibility: listed option chain for the target expiry; valid real spot and OHLC covering the pre-window, firing/response window, and both return clocks; non-empty OI and IV rows on the same expiry/grid; enough strikes across both sides and the locked moneyness band; strictly timestamped PRE_WINDOW snapshot before breach; forward-return support at the locked horizons; no zero-DTE records.
- [ ] Apply data-availability probes before acquisition and record `PASS`, `INELIGIBLE`, or `HARD_GAP` with reason. A probe must test chain listing, historical greeks/IV, OI, spot/OHLC, timestamp granularity, expiry/DTE, and post-window return support. Probe failure never becomes an imputed zero.
- [ ] Avoid sector concentration using point-in-time sector labels: cap any sector at 20% of intended unique days and any single ticker at 10% of intended units unless the user approves an exception. Stratify the candidate schedule across broad sectors and ETF/large-cap single-name families; do not treat correlated names on one date as extra independent days.
- [ ] Pre-specify event/control mix: target approximately 1/3 event-habitat days (FOMC, earnings, OpEx only when the surprise is operationally available) and 2/3 non-event controls, with event and control days distributed across sectors and calendar periods. If event surprise is unavailable, label `DESCRIPTIVE-HABITAT` and do not make a causal-surprise claim.
- [ ] Pre-specify DTE strata 1–3, 4–7, and 8–10, with no post-hoc DTE selection. Report achieved counts and exclusions by stratum.

### Task 2 — Probe and schedule without acquisition

- [ ] Generate a deterministic candidate table keyed by `(calendar_day, ticker, expiry, dte, habitat, sector, candidate_source)` and sort it before any requests.
- [ ] Compare candidate keys to all held manifests and seed discovery directories; emit `held_pair_exclusion` and `held_day_reference` fields.
- [ ] Run only the lightweight availability probes after explicit user approval. Store request parameters, response status/counts, source timestamps, and probe code version/hash.
- [ ] Select the primary balanced schedule only from `PASS` probes; retain all failed probes in the census. Do not replace missing candidates after seeing outcome values; replacement may use only the pre-specified candidate order and probe status.

## 4. True PRE_WINDOW ΔIV and provenance census

### Task 3 — Acquire one pre-window snapshot per ticker×day, then cluster

- [ ] Lock the breach/response window and one horizon before acquisition: from-breach `h=1` ten-minute bucket and daily close-to-close `h=1` trading day, matching `run_causal_arm_v2.py`.
- [ ] Define `iv_source_ts` as the timestamp of the actual chain/IV snapshot and require `iv_source_ts < breach_window_start_prov` strictly. Persist source timestamp, timezone, request endpoint/parameters, expiry, spot timestamp, chain timestamp, and raw payload hash.
- [ ] Compute `delta_iv_pre_window` only from two pre-window IV observations available before the cutoff, using decimal-vol units and the pre-registered aggregation. If either observation or its timestamp is absent/invalid, set the field to null and mark the ticker-day `ASSOCIATIONAL-ONLY`; never substitute day-level/contemporaneous ΔIV.
- [ ] Persist `pre_event_vanna_exposure` as the level `Σ dealer_frame_vanna × OI × 100 × VANNA_PP_SCALE`, with no ΔIV multiplier and no post-cutoff rows. Use real spot, expiry-specific T/DTE, and the new engine’s `rec.vanna=-1×BS` only for the new-model descriptive field.
- [ ] Require 100% PRE_WINDOW provenance for the final causal corpus: every included ticker record and every merged calendar-day unit must have valid pre-window timestamps and non-null pre-window ΔIV. Mixed PRE_WINDOW/associational same-day family data is not causal-eligible; retain it only in an associational appendix.
- [ ] Merge same-day tickers into one day unit while preserving nested per-ticker fields, event/control metadata, DTE strata, and provenance. Do not average away family disagreement; emit family-specific values and a day-level aggregation rule before fitting.
- [ ] Produce a census with intended units, probed PASS units, acquired units, hard gaps, associational exclusions, PRE_WINDOW n/N, and reasons. Gate causal execution on `PRE_WINDOW n/N == 100%` and no imputation.

## 5. Sample, power, and estimands

### Task 4 — Lock the unit and power plan

- [ ] Primary unit: unique calendar day. Same-day ticker records are clustered and contribute one observation to β, confidence intervals, power, both-clock counts, and all acceptance gates.
- [ ] Primary causal estimand: the coefficient on `Vanna⊥ × ΔIV_PRE_WINDOW`, with `Vanna⊥` residualized against per-family pre-vanna levels and the ΔIV main effect, then controlling for gamma/burst, ΔS, market/common shock, A6 reflexivity, event indicator, and cross-family spillover as implemented by `run_causal_arm_v2.py`.
- [ ] Keep the existing two-clock hypothesis: from-breach forward return is confirmatory and expected positive; daily close-to-close is diagnostic and expected negative. No best-lag selection; reverse lag and placebo are pre-registered falsifiers.
- [ ] Use all eligible units for the primary analysis, subject only to the frozen eligibility rule. Use a balanced panel as a sensitivity analysis, not as a replacement: cap ticker/sector contributions and report equal-day and family-balanced estimates separately.
- [ ] Require a pre-specified event/control mix and report event-only, control-only, and pooled results without changing the primary gate. No-firing days remain in the denominator; they must not be dropped merely because the model signal is zero.
- [ ] Set the β-specific target to approximately `n=257` unique eligible calendar days for one-sided 80% power after Vanna⊥, based on the current `n_for_80=257`; recompute power from the final variance/SE and report `n_for_80`, rather than claiming that `n≈257` automatically gives 80%.
- [ ] Keep `n≈29`/`md≤0.5` explicitly labeled as the correlational-arm target only. It is not causal β power. If the expanded universe cannot reach 257, report the actual β power and stop causal acceptance claims.

## 6. Apples-to-apples live-vs-new comparison

### Task 5 — Build the common-input harness

- [ ] Define a canonical input record containing `(ticker, calendar_day, expiry, DTE/T, real spot, IV source timestamp, per-strike strike/right/IV/OI, chain source identifiers, raw payload hashes)`. Validate that both engines consume the exact same record and strike/right set.
- [ ] No comparison harness currently exists; create one using the existing `run_dual_pipeline_gate*.py` patterns and `_SeedTD`/frozen-as-of approach.
- [ ] Call the live engine with explicit `sign_model="vol_surface_replication"` and `accumulate=True`; never rely on `compute_dealer_positioning`'s function defaults (`oi_heuristic`/`False`) and never silently accept its accumulation fallback. Verify from the returned object that `sign_model`, `accumulate`, spot, selected expiry, and record counts match the manifest. If accumulation history is unavailable, mark the comparison `COMPARISON INVALID`, not `WORSE` or a same-day substitute.
- [ ] Verify the SVI/deadband route from source before each run: `vol_surface_replication`, `IV_DEADBAND=0.01`, `DEALER_VANNA_FLOW=1`, and accumulation ON. Record the resolved per-strike production sign provenance (rich/cheap/deadband/fallback) and the exact source/config hash.
- [ ] Run the new engine on the same spot, expiry, IV, OI, strike grid, and DTE. Assert `rec.vanna=-1×BS`, real spot, single dealer-frame sign, no `right_dir` stacking, and no use of contemporaneous ΔIV for the level comparison.
- [ ] Compare only like-for-like levels: live vanna level (`vanna_call_shares + vanna_put_shares`) versus new per-expiry net vanna level; live/new GEX only after both are converted to dollar-gamma-per-1% (`Gamma×OI×100×spot²×0.01`); DEX only in post-multiplier shares. Never compare live level to new `vanna_flow`.
- [ ] Emit per-strike pairs with exact key matching, signed values, units, OI, IV, spot, T, live sign/source category, new sign, and distance class: same, zero-vs-nonzero, opposite. Emit aggregate level, absolute error, signed correlation, rank correlation, sign agreement, and weighted `D_conv` with conservative deadband handling.
- [ ] Require 100% common-input coverage for any headline comparison; list unmatched rows/expiries as exclusions. Do not pool SPY/QQQ or unrelated tickers without day/family clustering and a declared estimand.

### Task 6 — Pre-register outcome/falsifier tests

- [ ] Primary temporal test: compare each model’s pre-window level and signed exposure against the locked from-breach forward return at `h=1`; separately test the daily clock at `h=1`. Fit the residualized causal arm only on the 100% PRE_WINDOW corpus.
- [ ] Include A6 `corr(ΔIV_PRE_WINDOW or declared exposure clock, forward return)` as an in-model/control baseline, plus gamma/burst, ΔS, market/common shock, and cross-family spillover. If β disappears after these controls, classify the result as reflexivity/gamma, not dealer-vanna evidence.
- [ ] Include reverse-causal lead/lag tests (prior return predicting signal), a deterministic placebo/permutation signal test, and a no-firing/control-day test. No post-hoc lag or event window may replace the locked horizon.
- [ ] Include event-window results by habitat, with surprise-based results only when actual-minus-expected is operationally observed before the outcome. Otherwise label descriptive habitat.
- [ ] Include the opposite convention `+1×BS` sensitivity and report whether magnitude/sign changes are convention-bound. It is a falsifier/sensitivity, not a license to choose the favorable convention.
- [ ] Report descriptive agreement (levels, signs, distance, rank) separately from predictive evidence (lead/lag, controls, placebo, two-clock outcomes). No exposure agreement alone can validate the mechanism.

## 7. Acceptance ladder and better/worse decision

### Task 7 — Implement fail-closed verdict logic

- [ ] **Gate 0 — integrity:** raw payload hashes, manifests, source timestamps, exact common inputs, live config identity, no imputation, and 100% provenance census. Any failure = `BLOCKED/INVALID`.
- [ ] **Gate 1 — mechanical equivalence:** identical ticker/day/expiry/spot/chain/OI/IV inputs, valid units, per-strike key coverage, and live accumulation actually ON. Failure = `COMPARISON INVALID`, not worse/better.
- [ ] **Gate 2 — descriptive comparison:** publish exposure level/sign agreement and `D_conv` with day/family-balanced and deadband sensitivities. This gate can classify `DESCRIPTIVELY CLOSER`, `DESCRIPTIVELY DIFFERENT`, or `INDETERMINATE`; it cannot promote.
- [ ] **Gate 3 — temporal/falsifier evidence:** from-breach sign/horizon, daily diagnostic, A6/gamma/reflexivity controls, reverse-causal, placebo, no-firing, event/control, and opposite-convention results satisfy the pre-registered joint rule. A positive raw correlation without these controls is not a pass.
- [ ] **Gate 4 — power/independence:** β-specific unique-day n reaches the computed target (nominal target ≈257), actual one-sided power ≥0.80, no unresolved VIF/identifiability issue, and family interaction does not produce opposite signs. `n≈29` only passes the descriptive correlational arm.
- [ ] **Decision:** `BETTER` only if the new model clears Gates 0–4 and improves the pre-registered temporal/mechanistic criteria without worsening falsifiers; `WORSE` if common-input integrity passes but it is less predictive, less stable, or fails required controls relative to live; `NOT IDENTIFIABLE/INDETERMINATE` if underpowered, provenance-incomplete, or comparison-invalid. A descriptive distance is neither better nor worse for the mechanism.
- [ ] **Disposition:** no auto-promotion under any result. A positive residualized β or favorable common-input comparison only re-admits the expiry-book model to the evidence record and permits a fresh R1→R2→R3 Cem review. Until Cem approval, the model remains descriptive/conditional and the locked live model remains live.

## 8. Execution phases and safety gates

### Phase A — design freeze (no network)
- [ ] Add contract tests for candidate deduplication, held-cluster exclusion, sector/ticker caps, probe schema, DTE/event quotas, and deterministic ordering.
- [ ] Add tests proving PRE_WINDOW strict inequality, null/no-imputation behavior, mixed-family provenance veto, one-day clustering, and exact 100% census gating.
- [ ] Add common-input tests proving both engines receive byte-equivalent canonical rows, live flags are asserted, accumulation fallback fails closed, and live source files are not modified.
- [ ] Add tests for level-vs-flow separation, units, per-strike sign-distance classes, opposite convention, no-firing inclusion, placebo determinism, lead/lag direction, and β not-identifiable behavior.
- [ ] Run only network-free targeted tests and inspect the diff. Obtain read-only R1/R2 design and mechanism audit before any acquisition.

### Phase B — probe and user approval gate
- [ ] Present the user with candidate count, expected unique-day yield, sector/DTE/event mix, probe cost, acquisition cost, and projected β power.
- [ ] Require explicit approval of universe scope/cost and whether held dates may be re-designated with freshly acquired PRE_WINDOW snapshots. Default is **do not re-designate held dates**; if approved, mark them as a separate re-designated cohort and never mix silently with genuinely new clusters.

### Phase C — sequential acquisition
- [ ] Run one ticker×day session at a time with `THETADATA_HIST_CONCURRENCY=1`; persist raw responses immediately; never overwrite existing artifacts.
- [ ] After each batch, update the provenance census and stop if the pre-registered balance, cost ceiling, or source integrity fails. Do not adapt selection based on returns or model outputs.

### Phase D — common-input comparison and causal packet
- [ ] Run the common-input harness, verify 100% match, then run the descriptive comparison.
- [ ] Build one row per unique calendar day, preserve nested family fields, enforce PRE_WINDOW-only causal eligibility, run the orthogonalized causal driver, and emit the single decision table.
- [ ] Run the full targeted suite and a deterministic offline smoke test before any conclusion.

### Phase E — artifacts, hashes, and review
- [ ] Write `RESULT.md` with intended/acquired/excluded counts, provenance census, input/config identity, level-vs-flow distinction, descriptive tables, all falsifiers, β power, and calibrated better/worse/indeterminate verdict.
- [ ] Write `SHA256SUMS.txt` for raw payloads, merged records, comparison JSON, decision table, source snapshots/config manifest, and result report. Record Python version, package lock/hash, git commit, and dirty/untracked status.
- [ ] Require fresh read-only R1 design/mechanism review, R2 source cross-exam, then—only if the joint gate is met—narrow R3 Cem re-admission review. Never dispatch Cem as an approval/promotion request from a merely descriptive or underpowered result.

## 9. Open questions requiring user approval

1. **Universe scope and cost:** approve the candidate universe size, permitted asset types (ETFs only vs ETFs plus single names), date range, sector caps, probe budget, and acquisition budget. The default recommendation is broad liquid ETFs plus a capped, sector-balanced large-cap sample rather than an unconstrained ticker list.
2. **Held-date policy:** default is no re-designation of held SPY/QQQ dates. Approve separately whether to acquire fresh PRE_WINDOW snapshots on held dates as a labeled sensitivity/re-designated cohort; such dates must not be counted as genuinely new ticker×calendar-day clusters.
3. **Power target:** approve the β-specific target of approximately 257 **unique eligible calendar days**, with final power recomputed from the orthogonalized model; confirm that a lower target may produce only an exploratory/indeterminate result.
4. **Event definition:** approve the listed FOMC/earnings/OpEx habitats and the rule that unavailable surprise data yields descriptive-habitat, not a causal surprise claim.
5. **Family scope:** approve whether same-day expanded tickers are all clustered into one day unit (recommended) and whether a family-balanced panel is a sensitivity only (recommended), rather than the primary estimand.

## Completion criteria for implementation

- [ ] No production live source/config changed; `git diff` confirms this.
- [ ] Network-free tests and smoke run pass; no billed acquisition occurs before all approval gates.
- [ ] Final packet has 100% PRE_WINDOW provenance for any causal claim, exact common-input evidence for any model comparison, unique-day clustering, β-specific power, falsifiers, hashes, and an explicit `BETTER`, `WORSE`, or `INDETERMINATE/NOT IDENTIFIABLE` disposition.
- [ ] Any favorable result is labeled re-admission-to-evidence only; Cem re-admission is the sole next decision and no automatic promotion is permitted.

---

**Plan self-review:** All requested requirements are covered: new ticker×day universe rule and probes; no-survivorship/sector controls; strict PRE_WINDOW ΔIV and 100% provenance; one-day clustering; all-vs-balanced panel and event/control/DTE/power policy; identical live/new inputs with locked live flags and sign/spot conventions; descriptive versus causal separation; two clocks, A6/gamma/reflexivity, placebo/lead-lag, no-firing, event, and opposite-convention tests; acceptance ladder; no auto-promotion/Cem-only re-admission; execution phases, tests, artifacts, hashes, safety gates; and user-approval questions for universe cost and held-date re-designation.

**Execution handoff:** After user approval, implement Phase A first and request the read-only R1/R2 audit. Do not begin acquisition directly from this plan.
