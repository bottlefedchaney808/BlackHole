# PLAN — Expiry Book Exposure v2 (Post-CARL-Consult Revision)

Status: **SUPERSEDED 2026-08-20.** Expiry-book is the live production engine (`volatility_suite._run_production_dealer_positioning` → `fetch_production_result`). Keep this file as the CARL/v2 design record. Current live contract: `PLAN_vanna_stock_flow_scalars_20260820.md`.
Author: Hermes Agent (default profile), 2026-08-14
Supersedes: `Vol_Suite/docs/PLAN_expiry_book_exposure_20260814.md` (v1)
Based on: CARL consult verdict (Karsan arbiter + 3 adversarial panelists), all recommendations ACCEPTED.
Consult record: `trading_journal/carl_panel_positions_20260814.md`, `trading_journal/carl_consult_prompt_20260814.md`,
skill reference `dealer-positioning-model/references/carl-expiry-book-exposure-20260814.md`.

---

## 0. What this is

A **revision of the proposed spec** for a NEW, SEPARATE, **test-only** "expiry book exposure" model.
It does NOT replace, modify, or re-wire the live dealer-positioning model. Per Jason's standing rule,
no production code changes until he says the candidate is ready. The live model stays frozen.

This v2 folds in the full CARL-consult merged upgrade set (arbiter rulings (a)-(g) + the three
accepted panelist insights), corrected formulas (no charm ×1/DTE bug, no double vanna formula), and
the flow-first output. It is the document the coder implements via subagent-driven development.

**The honest framing (kept from v1):** the model is a CONDITIONAL path gated on a pre-registered
falsifier. It does NOT promise the model predicts anything. The falsifier may return "no."

---

## 1. Grounding — every source read (carried from v1 + consult)

- `trading_journal/expiry_book_exposure_research_20260814.md` — the research (GEX methods, higher-order greeks, sign rigor).
- `trading_journal/carl_consult_packet_20260814.md` — live model + test protocol + suite breakdown.
- `trading_journal/carl_panel_positions_20260814.md` — the 3 adversarial panelists' findings + insights.
- `Vol_Suite/docs/PLAN_expiry_book_exposure_20260814.md` — v1 (the consult subject).
- Live code: `Vol_Suite/dealer_positioning.py`, `vol_surface_reference.py`, `replication_reference.py`,
  `options_chain_scanner.py`, `svi_rp.py`, `backtest_accumulation_falsifier.py`, `vanna_transform_pin.py`.
- House plan references: `financial-development-planning` skill + repo-map / implementing-plan-phases / accumulation-wiring-pattern.

---

## 2. Empirical / context — the real numbers and the honest conclusion

### 2.1 Measured verdicts (2026-08-13, full-power SPY/QQQ n=165) — carried from v1
- **Single-ticker:** SPY snapshot corr **−0.37**, QQQ **+0.35 (OPPOSITE sign)**, accumulated delta
  R² ≈ 0.0000–0.0015 → **REDUNDANT**. The multi-day accumulated book does NOT beat the same-day snapshot.
- **Cross-sectional (12 tickers):** 9 SHORT / 3 LONG, corr(acc,rv) +0.12, t 0.39, perm p 0.69 → INCONCLUSIVE.
- **SVI-magnitude:** corr +0.10, t 0.32, perm p 0.27 → INCONCLUSIVE.

### 2.2 The arbitrated conclusion this v2 is built on (CORRECTED vs v1)
1. **Snapshot vs structural is a false binary (arbiter (a)).** The per-strike snapshot vector is the
   correct *measurement* object; the structural book is **co-primary** *economics* (the carry
   direction). The old falsifier tested the derivative's multi-day average in its weakest habitat
   (single far-dated 20261218 anchor, charm dead at 190d) — it does not prove the structural book
   is worthless. **Rule: snapshot = primary measurement; structural = co-primary, re-tested at the
   REGIME level (multi-expiry, term-structure-weighted), NOT via the old single-anchor accumulation.**
2. **The static per-strike "buy N shares" LEVEL is NOT the deliverable (arbiter (b), all 3 panelists).**
   It's a pre-hedged state, not a flow forecast. **The actionable output is a FLOW.**
3. **GEX is the primary falsified channel** (documented edge: Barbon–Buraschi, Ni et al.). **Vanna is
   real but only event-gated** (dated exogenous ΔIV shock), never a standalone daily lead (reflexive).
4. **The falsifier must test FLOW → forward returns at the clock the flows actually run on**
   (arbiter (f)) — event-window arms + short-DTE anchor, not all-days daily bars with prior-night OI.

---

## 3. Corrected greek ranking & hedging-channel split (arbiter (b))

| Greek | Role | Hedge channel | Output unit |
|---|---|---|---|
| **GEX** (gamma) | **PRIMARY lead channel**, fires continuously at spot | stock/futures | dollar-gamma per 1% (`Γ×OI×100×spot²×0.01`) |
| **VEX** (vanna) | **conditional** event channel, only after exogenous ΔIV shock | stock/futures (needs ΔIV sign) | shares per vol-point (×ΔIV, decimal) |
| **ChaEX** (charm) | OpEx clockwork, structural (multi-day) | stock/futures (needs DTE sign) | shares per day (×(1/365) if annualized) |
| **DEX** (delta) | **carry/inventory descriptor, NOT a forecast** | stock/futures | shares (multiplier pinned) |
| **vega** | portfolio vol-book sensitivity | options/vol | vega-$ → VIX/var-swap term structure |
| **VoEX** (volga) | portfolio vega-convexity | options/vol | vega-$ per vol-point |

- **Options/vol channel must be portfolio-vega / vega-convexity sensitivity to the VIX/variance-swap
  term structure** — NOT "accumulate N vega-$ at these strikes" (vol desks hedge portfolio vega with
  VIX futures/var-swaps, not per-strike option strikes).
- The **stock/futures vs options/vol split is correct**; the ranking by documented-edge + tradeable
  cadence (not spot-hedgeability) is the arbiter's correction.

---

## 4. Global constraints (binding, copy verbatim into every implementer/reviewer brief)

1. **Live dealer-positioning model is UNTOUCHED.** New model = a new standalone module + new tests only.
   No change to `dealer_positioning.py`, `vol_surface_reference.py`, `replication_reference.py`,
   `options_chain_scanner.py`, the `DEALER_*` env toggles, or the existing falsifier.
2. **All six greeks in DEALER-FRAME** (fix the vega/volga option-frame leak). Sign each greek once,
   never stack a `right_dir`.
3. **`rec.vanna = −1 × BS_vanna`** is the vanna destination convention; raw BS vanna is a magnitude
   reference only. SPY −0.956 / QQQ −0.989, |scale|≈1.04.
4. **Real spot, NOT median-strike.** All greeks at current spot/IV/DTE.
5. **`IV_DEADBAND_VOL` must exist** in `resolve_vol_surface_sign` (do not remove).
6. **Charm scaling:** `×(1/DEFAULT_A)` if `CHARM_ANNUALIZED=True` (i.e. ×1/365). NEVER ×(1/DTE).
7. **Vanna flow ΔIV unit: decimal vol**, ONE formula:
   `vanna_flow = Σ signed_vanna × OI × 100 × VANNA_PP_SCALE × (ΔIV/0.01)`.
   A Phase-1 unit test MUST assert the spec's §3.1 form and §3.4 form produce identical values.
8. **GEX pinned to dollar-gamma-per-1%** (`Γ×OI×100×spot²×0.01`) for cross-provider comparability.
9. **DEX multiplier pinned** and contracts-vs-shares stated explicitly (the reported number is
   POST-multiplier shares). Property test: reported shares = Σ signed_Δ × OI × 100 exactly.
10. **Scanner parity must hold** if the new model ever feeds the scanner (`test_scanner_vanna_parity.py`).
11. **Falsifier pre-registered** before any directional run: primary hypothesis, single corr threshold,
    block-perm p<0.05, multiplicity control (Bonferroni/BH q<0.10 over the greek family), and a rule
    that a **SPY/QQQ sign flip on the same construct = INCONCLUSIVE/FAIL, never a pass.**
12. **Environment:** `env -u PYTHONPATH -u VIRTUAL_ENV` before the venv python. Suite tests via
    `cd Vol_Suite && ../.venv/Scripts/python.exe -m pytest tests -q`.

---

## 5. Phased task breakdown (implemented by the coder via subagent-driven development)

### Phase 0 — Measurement Closure Gate (BLOCKING, before any directional test)
Implement an El-Karoui-style Taylor P&L identity test:
`ΔV ≈ Σ[Δ·dS + ½Γ·dS² + ν·dσ + vanna·dS·dσ + charm·dS·dt + ½volga·dσ²]`,
computed **twice** — once with feed-provided greeks, once with **bump-recomputed** greeks
(finite-difference in spot and IV at the repo's vol-point scale, `VANNA_PP_SCALE=0.01`) — on the
offline seed corpus (`Vol_Suite/docs/Dealer posistioning notes/_extracted/handoff_20260812/seed_data/`).
Report per-greek closure error + closure-R². This proves all six greeks are feasible (esp. volga —
if `eod_greeks` lacks it, the bump side diverges) and catches every units/sign/multiplier bug at once.
- **Pre-registered blocking criterion:** closure-R² ≥ 0.90 AND max per-greek absolute error ≤ tolerance,
  on held-out data. Gate order: **Phase 0 → Phase 6 → Jason's go/no-go.**
- **Tests:** synthetic-ticker closure test on the seed corpus (network-free), asserting the identity
  closes to tolerance and that a deliberately-mis-scaled greek (e.g. charm ×1/DTE) breaks closure on
  that term. Per-greek MEASURED/ESTIMATED provenance flag on every output cell.

### Phase 1 — Greeks engine + full sign/units contract
Build a NEW standalone module (e.g. `expiry_book_exposure.py`) producing `net_exposure[expiry][strike][greek]`
as a snapshot vector for all six greeks at real spot/IV/DTE, dealer-frame signs, explicit units.
Reuses existing scaling constants (`CONTRACT_MULTIPLIER=100`, `VANNA_PP_SCALE=0.01`, `DEFAULT_A=365`).
Extend `vanna_transform_pin.py`'s work into a tested, complete dealer-frame sign table covering ALL six
greeks (fix vega/volga option-frame leak; complete charm's empty cells; state charm in Bloomberg-negative
convention).
- **Tests:** network-free with a `_FakeTD` controller. Assert vector shape, multiplier-once, NaN-field
  per-greek skip (not raise), units per §3 table, `rec.vanna=−1×BS` sign/magnitude, "don't stack a
  right_dir" rejection, vanna §3.1≡§3.4, charm ×(1/365), DEX post-multiplier property.

### Phase 2 — Execution-locus futures-flow map
Collapse the per-strike vector to the **zero-gamma level**, nearest **call/put walls** (concentrated
OI), and the **delta-hedge tolerance band** around spot. Model flow firing in **threshold-gated
bursts** (not continuity) when accumulated delta breaches a tolerance band at the zero-gamma/wall,
sized by local GEX slope. Output granularity = levels, not strikes. Carry residual delta.
- **Tests:** synthetic zero-gamma/wall detection on a canned chain; burst gating at the band edge;
  level-vs-strike output shape.

### Phase 3 — Scenario-conditional hedge-flow budget (THE ACTIONABLE OUTPUT)
For each named scenario (ΔS=±1%, Δσ=±1 vol pt, Δt=1 day toward OpEx), report the **forced dealer
hedge flow** = Σ over strikes (sensitivity × OI × multiplier × scenario shock) as one signed number
per channel per scenario. Each scenario-cell weighted by Phase-2 locus activation. DEX appears only
as a carry descriptor, NEVER as a forecast.
- **Tests:** scenario×channel matrix on a canned chain; the DEX example reproduced as a carry
  descriptor only (no "buy N shares" forecast); channel split exhaustive + non-overlapping.

### Phase 4 — Structural/regime arm (co-primary, the "better seed")
A term-structure-weighted, multi-expiry **persistent-carry** object that sets the direction of the
carry — the improved replacement for the old single-anchor replication seed. **Fresh construction,
NO dependency on the live RP seed** (arbiter (a), option A). Event-clocked: OpEx crescendo as an
*accumulated* channel, dated vol shocks, term-structure contango/backwardation flag.
- **Tests:** synthetic multi-expiry regime construction; a persistent-regime arm that does NOT
  single-day-revert; event-clock gating.

### Phase 5 — Overlays
Fixed-strike cheap/rich (SVI-RP via `svi_rp.calibrate_svi`) + term-structure regime flag. Reuses
`svi_rp.py`; does NOT alter `vol_surface_reference.py`.
- **Tests:** synthetic smile with a cheap-strike dip → overlay labels it cheap (dealer long /
  mean-revert-prone); contango-vs-backwardation flag.

### Phase 6 — Pre-registered falsifier (the gate)
Extend the `backtest_accumulation_falsifier` pattern, network-free on the offline seed corpus:
1. **PRIMARY — GEX flow → forward 1-day return sign / realized variance** (NOT level → vol).
   Pre-registered: n_tickers ≥ 12, ≥ 100 usable days/ticker, |corr| ≥ minimum-detectable from a
   power analysis at n, block-perm p < 0.05, Bonferroni/BH (q<0.10) over the greek family, and
   **sign-consistency required across SPY/QQQ** (a flip = INCONCLUSIVE/FAIL).
2. **Vanna lead arm** — ONLY after a dated exogenous ΔIV shock (FOMC/econ release/earnings/vol-supply);
   test `shock → vanna flow(t) → forward return(t→t+k), k≥1`. DELETE the v1 "does vanna flip sign
   into drawdowns" test (auto-passes; not falsifying).
3. **Event-window arms** — post-FOMC afternoon drift, OpEx Thu→Fri, so each channel is falsified at
   the horizon it actually acts on (arbiter (f)).
4. **Short-DTE second anchor** (weekly/2DTE snapshot, no accumulation) + **per-underline handling**:
   index keeps call+/put− + 0DTE exclusion; single names get a low-confidence inventory label with a
   sensitivity range, no exact share counts, 0DTE marked as a scope gap in the charm arm.
5. **Retained accumulated-book overlay re-test** — does it add R² over the snapshot under the new
   vector (v1's test said no; this re-confirms or overturns).
- **Tests:** synthetic-ticker falsifier smoke tests (harness runs, verdict taxonomy honored,
  `FALSIFIER_FORCE=1` bypasses cache). No network.

---

## 6. Scope taxonomy

- **SELECTED (implement in the new standalone module):** Phases 0-6 as above.
- **CONDITIONAL:** promotion of any part toward production — ONLY after Jason reviews the falsifier
  verdicts and says go. Retained accumulated-book overlay — only if it adds R² over the snapshot.
  Real-time/DDOI inventory — depends on data not currently available.
- **PRESERVED (do NOT touch):** live dealer-positioning model + its sign models + default
  `'oi_heuristic'` (direct API) / `'vol_surface_replication'` (suite headless); scanner parity;
  IV_DEADBAND_VOL; rec.vanna = −1×BS pin; all existing `Vol_Suite/tests/` (must stay green).
- **EXCLUDED (v1/v2):** 0DTE intraday modeling (index per-underline), real-time intraday inventory,
  DDOI transaction-level direction, anything needing new paid data, the `Direction/` package.

---

## 7. What pins current behavior (regression guard — carried from v1)
`test_dealer_positioning_sign_model.py`, `test_run_modes_smoke.py`, `test_scanner_vanna_parity.py`,
`test_dealer_positioning_accumulation.py`, `test_replication_reference_accumulation.py`,
`test_svi_rp.py`, `test_svi_robust_fit.py`, `test_vol_surface_reference.py`, the four falsifier test
files, and constants `CONTRACT_MULTIPLIER/VANNA_PP_SCALE/DEFAULT_A/CHARM_ANNUALIZED/IV_DEADBAND_VOL/MIN_HEDGE_OI`.
The new model adds a NEW module + NEW tests; it must not change any of the above.

---

## 8. Risks / pitfalls (carried from v1 + consult)
1. The model may simply not predict anything — acceptable, honest, must be reported.
2. Sign-convention bugs corrupt output silently — mitigated by Phase-1 pin table + tests; rec.vanna
   pin is highest-risk.
3. Units drift — every greek normalized to §3 unit, multiplier once.
4. Horizon-of-flow vs horizon-of-test mismatch — a daily falsifier nulls event-clocked channels;
   fixed by Phase-6 event-window + short-DTE arms (arbiter (f)).
5. Scope creep into 0DTE/real-time/DDOI — keep EXCLUDED.
6. Regression risk to live model — new module must be a true separate file; shared constants verified
   against §7.
7. Scanner parity — if the new model feeds the scanner, same-solver rule must hold.

---

## 9. Acceptance gate ("Jason says ready" — arbiter (e))
Phase-0 closure passes (closure-R² ≥ 0.90, per-greek error ≤ tolerance, held-out) **AND** the Phase-6
primary GEX hypothesis passes at pre-registered thresholds (|corr| ≥ min-detectable, block-perm
p<0.05, sign-consistent across SPY/QQQ, FDR-controlled), with the vanna arm passing separately under
exogenous-shock gating. All-null = honest "not ready," reported not papered over.

---

## Appendix — 30-second version
- New **separate, test-only** model; live dealer-positioning frozen.
- **Flow is the product, not the level** — scenario-conditional hedge-flow budget (Phase 3) weighted
  by execution locus (Phase 2); DEX is a carry descriptor only.
- **Corrected ranking:** GEX (primary lead) → event-gated VEX → DTE-ChaEX → DEX (descriptor) ≫
  vega → VoEX (portfolio vol-book).
- **Phase 0 El-Karoui closure gate is blocking** (proves all six greeks + catches every units/sign bug).
- **Phase 4 structural/regime arm = the "better seed"** (co-primary, fresh, term-structure-weighted).
- **Falsifier pre-registered**: GEX flow→forward returns, event-window + short-DTE arms, SPY/QQQ
  sign-consistency, FDR. All-null = honest "not ready."
- **Live gap to close separately:** only vanna is empirically pinned; charm convention + vega/volga
  measurability are assumptions needing a live ThetaData probe.
