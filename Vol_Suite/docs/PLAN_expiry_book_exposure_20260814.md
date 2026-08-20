# PLAN — Expiry Book Exposure: A Per-Strike, Per-Expiry, Per-Greek Net-Exposure Vector With an Explicit Hedging Expectation

Status: **SUPERSEDED 2026-08-20.** Expiry-book is live. Historical v1 plan. Current: `PLAN_vanna_stock_flow_scalars_20260820.md`.
Author: Hermes Agent (coder, Vol_Suite dealer-positioning build) — 2026-08-14
Deliverable: `Vol_Suite/docs/PLAN_expiry_book_exposure_20260814.md`

---

## 0. Why this plan exists, and what it is NOT

This is a **proposal for a NEW, SEPARATE model** — "expiry book exposure" — built for
**TESTING only**. It does **not** replace, modify, or re-wire the live dealer-positioning model.
Per Jason's standing rule, no production code changes until he says the candidate is ready.
The live model stays untouched; the new model is developed alongside it, exercised against the
reference, and (only if the falsifier gates pass) presented for a go/no-go.

The plan implements the researcher's reframe and Section-5 build order: the correct object is a
**per-strike, per-expiry, per-greek snapshot vector** (DEX=delta, GEX=gamma, VEX=vanna, ChaEX=charm,
plus vega and VoEX=volga), **NOT a fancier scalar GEX**. Each greek carries an explicit hedging
expectation split into two channels:

- **DEX / GEX / VEX / ChaEX → stock/futures hedging expectation** ("net DEX −2444 → buy ~2444
  shares to become delta-neutral").
- **vega / VoEX → options/vol hedging expectation** ("net vega at these strikes → accumulate N
  vega-$ there").

The plan is **honest about evidence**: the falsifier says the same-day **snapshot** is the
informative object and **multi-day accumulation adds nothing** (SPY snapshot corr −0.37, QQQ +0.35,
accumulated-delta R² ≈ 0.0000–0.0015 → **REDUNDANT**). This plan therefore makes the snapshot the
primary signal and frames the whole model as a **conditional path gated on pre-registered
cross-sectional falsification** — it does NOT promise the model "works."

---

## 1. Grounding — every source read

### 1.1 Context docs (read fully)
- **`trading_journal/expiry_book_exposure_research_20260814.md`** (read fully) — the research report
  this plan implements. Supplies: the reframe (dealer positioning → expiry book exposure), the
  canonical GEX formulas, the 8 failure modes, the higher-order-greeks hedging-forcing channels
  (§2), the sign/measurement-rigor traps (§4), and the Section-5 build order (per-strike vector,
  ΔIV-signed vanna flow, DTE-signed charm, accumulation-vs-snapshot clarity, SVI-RP overlay,
  cross-sectional falsification).
- **`trading_journal/carl_consult_packet_20260814.md`** (read fully) — the CARL consult packet for
  the LIVE model + falsifier protocol. Supplies: the current sign models and live default, the
  surface reference/fitter toggle, the dead-band, the accumulation+vannaflow arms, scanner parity,
  the measured falsifier verdicts, and the Gate-0 pins (rec.vanna = −1×BS; real-spot-not-median;
  dead-band must exist; scanner parity must hold).

### 1.2 House plan-format references (read fully)
- `financial-development-planning` skill `SKILL.md` + `references/repo-map.md`,
  `references/implementing-plan-phases.md`, `references/accumulation-wiring-pattern.md` — the house
  `PLAN_*.md` skeleton (Status header, grounding, empirical-first, phased `- Tests:` lines, scope
  taxonomy, risks/open questions), the repo map of what pins what, the network-free fake-controller
  test pattern, and the accumulation-wiring facts this plan deliberately de-prioritizes.

### 1.3 Live code (grep + read, anchored in reality)
- **`Vol_Suite/dealer_positioning.py`** (read fully) — sign models, greek aggregation, scaling,
  result dataclass, hedge requirement. **Confirmed live default of `compute_dealer_positioning` is
  `sign_model='oi_heuristic'` (line 351)**; `VALID_SIGN_MODELS` line 266; `_dealer_sign` call+1/put−1
  line 235; `_resolve_sign` line 292; scaling constants `CONTRACT_MULTIPLIER=100`,
  `VANNA_PP_SCALE=0.01`, `CHARM_ANNUALIZED=True`/`DEFAULT_A=365` lines 39–61; aggregation lines
  560–767.
- **`Vol_Suite/vol_surface_reference.py`** (read fully) — `VOL_SURFACE_FITTER` default `'svi'`
  (robust full-SVI via `svi_rp.calibrate_svi`), `IV_DEADBAND_VOL` and `resolve_vol_surface_sign`
  (lines 517–532), deviation = IV_market − IV_reference (rich→−1, cheap→+1, dead-band→0→fallback −1).
- **`Vol_Suite/replication_reference.py`** (grep + read) — accumulation seed modes
  {`replication`, `vanna`, `svi_rp`, `oi_heuristic`}, `DEALER_SEED_SIGN` (default −1),
  `DEALER_VANNA_FLOW` (default 1, weights daily ΔOI flow by rec.vanna×sign), `MIN_HEDGE_OI=50`,
  `_otm_leg_weights`. The accumulated book applies to the **anchor/primary expiry only**.
- **`Vol_Suite/options_chain_scanner.py`** (grep + read) — **scanner parity**: `compute_vanna_positioning`
  calls the SAME solver `compute_dealer_positioning(150d)` and renders `vanna_shares_by_strike`
  verbatim; no scanner-side vanna math.
- **`Vol_Suite/svi_rp.py`** (grep + read) — `calibrate_svi(otm, forward, T)` (5-param full SVI,
  OI-weighted, butterfly-arbitrage penalty, flat-smile fix), `calibrate_ssvi`, `SviRpReference.sigma_ref(k)`,
  `sigma_ref_batch`.
- **`Vol_Suite/backtest_accumulation_falsifier.py`** (grep + read) — verdict taxonomy, the
  measured verdicts, `_build_day_records` snapshot arm, block-permutation logic, `FALSIFIER_FORCE`.
- **`Vol_Suite/vanna_transform_pin.py`** (confirmed present) — rec.vanna = −1×BS magnitude ~1.04×,
  SPY −0.956 / QQQ −0.989.
- **`Vol_Suite/volatility_suite.py`** (line 1351) — suite path maps '3' → `vol_surface_replication`;
  **the headless/suite default is `vol_surface_replication`, distinct from the direct-API default
  `oi_heuristic`** — both are pinned by tests.
- **Tests that pin current behavior** — `Vol_Suite/tests/test_run_modes_smoke.py` (suite default
  `== "vol_surface_replication"` at ~144/169/198/223), `test_dealer_positioning_sign_model.py`
  (`test_oi_heuristic_is_default_and_runs`, `test_invalid_sign_model_raises`), plus
  `test_dealer_positioning_accumulation.py`, `test_replication_reference_accumulation.py`,
  `test_scanner_vanna_parity.py`, `test_svi_rp.py`, `test_svi_robust_fit.py`,
  `test_vol_surface_reference.py`, `test_backtest_accumulation_falsifier.py`,
  `test_cross_sectional_falsifier.py`, `test_svi_magnitude_falsifier.py`,
  `test_pooled_accumulation_falsifier.py`.

---

## 2. Empirical / context — the real numbers and the honest conclusion

### 2.1 What the falsifier actually measured (2026-08-13, full-power SPY/QQQ at n=165)
- **Single-ticker:** SPY snapshot corr **−0.37**, accumulated adds nothing (delta R² **+0.0015**,
  t 0.53) → **REDUNDANT**. QQQ snapshot corr **+0.35 (OPPOSITE sign)**, accumulated flat (delta R²
  **0.0000**) → **REDUNDANT**. → **The multi-day accumulated book does NOT beat the same-day
  snapshot at full power.** Lead-lag: SPY best k=2 (weak), QQQ all 0.
- **Cross-sectional (12 tickers):** sign axis 9 SHORT / 3 LONG (SPY/QQQ SHORT, GOOGL/NFLX/TSLA
  LONG), corr(acc, rv) **+0.12**, t **+0.39**, perm p **0.69** → **INCONCLUSIVE**; fragile to which
  window/expiry feeds SPY/QQQ.
- **SVI-magnitude (`--svimag`):** corr **+0.10**, t **0.32**, perm p **0.27** → **INCONCLUSIVE**.
- **Day-level pooled:** **REDUNDANT** (a within-ticker-constant accumulated series is mechanically
  zeroed by the ticker-FE).
- Verdict taxonomy in the harness: `REDUNDANT` / `ACCUMULATION_ADDS_SIGNAL` / `BASE` /
  `INCONCLUSIVE` / `ACCUMULATION_CROSS_SECTIONAL_SIGNAL`. A candidate is `ACCUMULATION_ADDS_SIGNAL`
  only if accumulated adds R² **and** is independent of the snapshot (|corr(acc,snap)|<0.5) **and**
  block-perm p<threshold. Every practitioner source defines GEX/VEX/ChaEX as **point-in-time sums
  over current OI**.

### 2.2 The honest conclusion this plan is built on
1. **Accumulation is REDUNDANT vs the snapshot.** The snapshot is the informative object. This plan
   makes the **snapshot vector the primary signal**, and treats any retained accumulated book as a
   slow-regime overlay to be re-tested against the snapshot (never the core read).
2. **The right object is a vector, not a fancier scalar.** Net DEX/GEX/VEX/ChaEX/vega/volga
   **per strike per expiry**, with pinned dealer-frame signs, real spot/IV/DTE, explicit units, and
   a multiplier applied **once**.
3. **The model is CONDITIONAL, not promised.** It ships only the measurement rigour and the falsifier
   harness. Whether it "works" is a question the pre-registered tests answer — and they may answer
   "no" (as the live accumulation arm already did for the accumulation question).

---

## 3. Design — the proposed model

### 3.1 Object & output
Compute `net_exposure[expiry][strike][greek]` for the six greeks, as a per-strike snapshot vector.
For each strike the output is a hedging implication, not a bare number:

| Greek | Meaning | Primary output unit | Hedging channel |
|---|---|---|---|
| **DEX** (Δ) | net signed delta × OI × 100 | shares | stock/futures |
| **GEX** (Γ) | net signed gamma × OI × 100 × spot-scale | shares per $1 (or $ per 1%) | stock/futures |
| **VEX** (vanna) | net signed vanna × OI × 100 × 0.01 × **ΔIV** | shares per vol-point (× ΔIV) | stock/futures (needs ΔIV sign) |
| **ChaEX** (charm) | net signed charm × OI × 100 × (1/DTE) | shares per day (× DTE/decay) | stock/futures (needs DTE sign) |
| **vega** | net signed vega × OI × 100 | vega-$ | options/vol |
| **VoEX** (volga) | net signed volga × OI × 100 | vega-$ per vol-point | options/vol |

### 3.2 The hedging-expectation split (the actionable output)
- **DEX/GEX/VEX/ChaEX → stock/futures expectation.** Concretely: `net_DEX = −2444 → buy ≈ 2444
  shares (×100 if per contract)`; `net_GEX` short → dealers chase price (buy rips, sell dips);
  ΔIV-signed `VEX` → buy/sell N shares on the day's IV move; DTE-signed `ChaEX` → persistent bid
  into OpEx. The output column is the number of shares/futures, direction-coded.
- **vega/VoEX → options/vol expectation.** Concretely: `net_vega` at strikes where dealers must buy
  options/vol products (VIX futures, variance swaps) to be vega-neutral → "accumulate N vega-$ at
  these strikes." VoEX says how that vega hedge must be re-sized as IV moves.

### 3.3 Pinned measurement-rigor rules (do not corrupt your own signal)
These are LOCKED, non-negotiable encoding of Section 4 of the research + the Gate-0 pins:

1. **Pinned sign-convention table (per greek, dealer-hedge frame)** — sign each greek **once**,
   never stack a `right_dir`, never double-flip. **`rec.vanna = −1 × BS_vanna`** (SPY −0.956, QQQ
   −0.989, |scale|≈1.04×). Raw BS vanna is the **same sign for call & put at the same OTM strike**
   (negative when d₂<0) and is used only as a magnitude reference — the **directional object is the
   dealer-frame vanna**. Full table in §5.
2. **Use real spot, NOT median-strike.** All greeks evaluated at current spot/IV/DTE. Median-strike
   fabricates a false right-dependent sign.
3. **Units: shares vs $ vs points are NOT interchangeable.** Normalize every greek to the
   hedge-actionable unit in the table above. The spot²×0.01 term is the per-$1↔per-1% conversion;
   the vol-point conversion is vanna×0.01.
4. **Apply the contract multiplier (100) exactly once, consistently.**
5. **Dealer-frame vanna destination convention** is pinned; raw BS is a magnitude reference only.

### 3.4 The snapshot is primary; signed-flow increments sit on top
- **Snapshot vector** = the core read (per falsifier, this is where the information is).
- **ΔIV-signed vanna flow** = `Σ signed_vanna × OI × 100 × ΔIV_day`. This is the single highest-value
  change: it lets VEX flip negative into drawdowns instead of being a constant-sign arm.
- **DTE-signed charm flow** = `Σ signed_charm × OI × 100 × (1/DTE)` — the OpEx clockwork arm.
- Retained accumulated book, if any, is a **slow-regime overlay**, always re-tested against the
  snapshot.

### 3.5 Overlays
- **Fixed-strike cheap/rich (SVI-RP):** `rich(strike) = IV_actual − IV_svi_reference > 0 → dealers
  short there → likely to decay/mean-revert`. This is Karsan's fixed-strike-vol insight mapped onto
  the existing `svi_rp.py` reference (`SviRpReference.sigma_ref`).
- **Term-structure regime flag:** contango = carry-positive; backwardation = flip risk.

### 3.6 Build order (implements research §5, 5.1→5.6)
Phases 1–6 below. Each is a self-contained, network-free-testable increment; none touches live code.

---

## 4. Phased task breakdown

### Phase 1 — Per-strike, per-expiry, per-greek net-exposure vector (the greeks engine)
Build a NEW, standalone module (e.g. `expiry_book_exposure.py`) that, given a ThetaData-style chain
(real spot, per-expiry IV, per-strike OI, delta/gamma/vanna/charm/vega/volga), produces
`net_exposure[expiry][strike][greek]` as a snapshot vector. Reuses existing scaling constants
(`CONTRACT_MULTIPLIER=100`, `VANNA_PP_SCALE=0.01`, `DEFAULT_A=365`) and existing greek extraction
(`_extract_greek_field`-style) rather than re-deriving them. This is a NEW module; the live
`dealer_positioning.py` aggregation is untouched.

- **Tests:** network-free with a `_FakeTD` controller (canned chain: fixed spot, 2–3 expiries,
  known OI, hand-computed greeks). Assert the vector shape, that multiplier is applied once, that
  units match the §3.1 table, and that a NaN greek field degrades to a per-greek skip (matching the
  live NaN-tolerance pattern) rather than raising.

### Phase 2 — Pinned sign-convention table (measurement rigor)
Extend the `vanna_transform_pin.py` work into a documented, tested pin table covering ALL six greeks
in the dealer-hedge frame (§5 below). This is the codification of the "do not corrupt your own
signal" rules. No production sign model changes; the pin table is a pure mapping used by the new
module.

- **Tests:** pure-function unit tests: `rec.vanna = −1×BS` sign/magnitude reproduction; the
  "don't stack a right_dir" invariant (feeding the dealer-frame value + a right_dir must be
  rejected); call/put-gamma-as-dealer-long-gamma rule (the OPPOSITE of the naive call+/put− rule for
  puts); per-greek sign table correctness on OTM/ITM/ATM rows.

### Phase 3 — Snapshot vector output + two-channel hedging expectation
Emit `net_exposure` + the hedging implication column per strike: "buy/sell N shares" for
DEX/GEX/VEX/ChaEX; "accumulate N vega-$ at these strikes" for vega/VoEX. This is the directly
actionable output and the object the falsifier implies is informative.

- **Tests:** with a canned chain, assert the DEX example reproduces ("net DEX −2444 → buy ≈ 2444
  shares"), the GEX short-vs-long directional coding, the vega accumulation target per strike, and
  the channel split (stock/futures vs options/vol) is exhaustive and non-overlapping.

### Phase 4 — Signed flow increments (ΔIV-signed VEX, DTE-signed ChaEX)
Add `Σ signed_vanna × OI × 100 × ΔIV_day` and `Σ signed_charm × OI × 100 × (1/DTE)` on top of the
snapshot. The ΔIV-signed vanna flow must be able to flip sign into drawdowns (the fix the research
flags as #1). DTE-signed charm must cresendo into OpEx (larger at low DTE).

- **Tests:** on a synthetic two-day chain with a vol-down then vol-up sequence, assert the vanna
  flow signs + then −; on strikes at 2-DTE vs 30-DTE, assert the charm flow scales ~10×; assert a
  day lacking ΔIV data falls back to snapshot-only (never multiplies by 0.0 — mirror the live
  vannaflow safety).

### Phase 5 — Overlays: fixed-strike cheap/rich (SVI-RP) + term-structure flag
Wire the SVI-RP fixed-strike deviation (`IV_actual − IV_svi_reference`) as a cheap/rich overlay per
strike, and a term-structure regime flag (contango vs backwardation) across expiries. Reuses
`svi_rp.calibrate_svi`; does NOT alter `vol_surface_reference.py`.

- **Tests:** on a synthetic smile with a deliberate cheap-strike dip (like the
  `vol_surface_reference` self-test), assert the overlay labels that strike cheap (dealer long /
  mean-revert-prone); a contango vs backwardation flag test with two crafted expiries.

### Phase 6 — Pre-registered cross-sectional falsification (the gate)
Add a falsifier harness (extending the `backtest_accumulation_falsifier` pattern, network-free on
the offline seed corpus) with pre-registered tests:
1. **Does the snapshot net-GEX predict forward realized vol / returns** (cross-sectional, gated on
   n tickers and a non-degenerate sign axis)?
2. **Does the ΔIV-signed vanna flow flip sign into drawdowns** (the fix's core claim)?
3. **Does a retained accumulated book add R² over the snapshot** (the existing test says no — this
   re-confirms under the new vector, or overturns it)?

This is the gate that turns the model from "proposed" to "Jason decides." Verdicts reuse the house
taxonomy (`REDUNDANT` / `ADDS_SIGNAL` / `BASE` / `INCONCLUSIVE`).

- **Tests:** synthetic-ticker falsifier smoke tests on the offline seed corpus asserting the
  harness runs, the verdict taxonomy is honored, and `FALSIFIER_FORCE=1` bypasses the cache
  (mirroring the live harness). No network.

---

## 5. Pinned sign-convention table (dealer-hedge frame — the measurement-rigor contract)

| Greek | Long call | Short call | Long put | Short put | Dealer-hedge directional read |
|---|---|---|---|---|---|
| **Delta (DEX)** | dealer short underlying (−) | dealer long underlying (+) | dealer long underlying (+) | dealer short underlying (−) | `net_DEX = Σ signed Δ × OI × 100` → "buy/sell N shares" |
| **Gamma (GEX)** | long gamma (stabilizing) | short gamma (amplifying) | long gamma (stabilizing) | short gamma (amplifying) | sign via long/short-option, NOT call+/put− (that rule mis-signs puts) |
| **Vanna (VEX)** | + | − | − | + | dealer-frame = `rec.vanna = −1×BS`; OTM call + / OTM put −; magnitude ≈1.04× BS |
| **Charm (ChaEX)** | decays + toward +1 (time-decay sign per moneyness) | | | OTM decays toward 0 → dealer unwinds short-stock hedge → buy | DTE-signed; cresendo into OpEx |
| **Vega** | + | − | + | − | options/vol hedge; largest ATM & long-dated |
| **Volga (VoEX)** | + convexity (longer vega as vol rises) | − | + | − | re-size the vega hedge as IV moves |

Rules pinned in code:
- **Never stack a `right_dir`** on top of a dealer-frame value — sign each greek once, in one frame.
- **`rec.vanna = −1 × BS_vanna`** is the vanna destination; raw BS vanna (same sign for both rights
  when OTM, negative when d₂<0) is a **magnitude reference only**.
- **Real spot, not median-strike**; **multiplier applied once**; **units explicitly shares/$/points**
  and normalized to the §3.1 table.

---

## 6. Scope taxonomy

- **SELECTED (implement now, in the new standalone module):**
  - Per-strike, per-expiry, per-greek net-exposure **snapshot vector** (DEX/GEX/VEX/ChaEX/vega/VoEX)
    at real spot/IV/DTE, dealer-frame signs, shares/$ units, multiplier once (Phases 1–3).
  - Pinned sign-convention table extended from `vanna_transform_pin.py` to all greeks (Phase 2).
  - Two-channel hedging expectation output (stock/futures vs options/vol) (Phase 3).
  - ΔIV-signed VEX flow and DTE-signed ChaEX flow as signed-flow increments on the snapshot
    (Phase 4).
  - Fixed-strike cheap/rich (SVI-RP) overlay + term-structure regime flag (Phase 5).
  - Pre-registered cross-sectional falsifier harness (Phase 6) — the gate.

- **CONDITIONAL (only if a gate passes / a prerequisite verifies):**
  - Promotion of any part of the new model toward production — ONLY after Jason reviews the falsifier
    verdicts and says go. Nothing here auto-flips a live default.
  - Retained accumulated-book overlay — kept only if it demonstrably adds R² over the snapshot under
    the new vector (the live falsifier says no; this must overturn that to justify keeping it).
  - Real-time/intraday inventory or DDOI-style transaction-level direction — depends on data access
    that is **not** currently available (see EXCLUDED).

- **PRESERVED (do NOT touch — downstream contract):**
  - **The live dealer-positioning model in its entirety** — `dealer_positioning.py`, its sign models,
    its default `'oi_heuristic'` (direct API) and the suite headless default `'vol_surface_replication'`,
    the accumulation wiring, the falsifier — until Jason explicitly approves a change.
  - **Scanner parity** — `options_chain_scanner.compute_vanna_positioning` must keep calling the SAME
    solver and rendering `vanna_shares_by_strike` verbatim (pinned by `test_scanner_vanna_parity.py`).
  - **The dead-band** — `IV_DEADBAND_VOL` must exist in `resolve_vol_surface_sign` (restored
    2026-08-13); the SPY live+vannaflow end-book ≈ **+254,031 LONG** reference number and the
    MIGRATED tree ground truth.
  - **`rec.vanna = −1 × BS` pin** — Gate-0, not relitigable.
  - All existing tests in `Vol_Suite/tests/` must stay green (full suite bar, e.g. 388 passed /
    5 skipped per `implementing-plan-phases.md`).

- **EXCLUDED (explicitly out of scope for v1, note as future):**
  - **0DTE modeling** — Cboe data shows 0DTE is ~59% of SPX volume but MM net gamma is de minimis
    (~0.04–0.17% of daily futures liquidity); modeling 0DTE-as-net-dealer-gamma would overstate it.
    Out of v1.
  - **Real-time intraday inventory** (SpotGamma TRACE-style / static-OI replacement) — needs paid /
    proprietary data not available.
  - **DDOI transaction-level buy/sell direction** — needs transaction-level data (SqueezeMetrics
    DDOI), not available in v1; the call+/put− convention is retained as a documented modeling
    assumption.
  - **Anything needing new paid data** — the plan reuses the existing ThetaData routes
    (`eod_greeks`, OI-by-day) and the offline seed corpus only.
  - The **`Direction/` package** (Jason's 5-signal direction method) — a separate concern, not part
    of the greek book.

---

## 7. What pins current behavior (regression guard)

If ANY future step (this plan is spec-only, but Phase-6 downstream work must honor these):
- `test_dealer_positioning_sign_model.py` — asserts direct-API default `== "oi_heuristic"` and the
  invalid-sign-model ValueError guard. A default-flip to `vol_surface_replication` in the direct
  API breaks this.
- `test_run_modes_smoke.py` — asserts the suite/headless default `== "vol_surface_replication"`
  (lines ~144/169/198/223) and maps '3' to it. The two defaults are DIFFERENT and both pinned.
- `test_scanner_vanna_parity.py` — scanner vanna must call the SAME solver; cross-file parity.
- `test_dealer_positioning_accumulation.py`, `test_replication_reference_accumulation.py` — the
  accumulation arm and seed/vannaflow behavior.
- `test_svi_rp.py`, `test_svi_robust_fit.py`, `test_vol_surface_reference.py` — SVI fitter and
  `resolve_vol_surface_sign` + dead-band behavior.
- `test_backtest_accumulation_falsifier.py`, `test_cross_sectional_falsifier.py`,
  `test_svi_magnitude_falsifier.py`, `test_pooled_accumulation_falsifier.py` — the falsifier
  harness and verdict taxonomy.
- Constants: `CONTRACT_MULTIPLIER=100`, `VANNA_PP_SCALE=0.01`, `DEFAULT_A=365`, `CHARM_ANNUALIZED`,
  `IV_DEADBAND_VOL`, `MIN_HEDGE_OI=50` — every consumer relies on these.
- Call sites: `compute_dealer_positioning`, `run_dealer_positioning`, `volatility_suite.py:1351`,
  `options_chain_scanner.compute_vanna_positioning`, `sign_model_render_label` (the ONE
  render-time source of the model label), and the `DEALER_ACCUMULATION` / `DEALER_VANNA_FLOW` /
  `DEALER_SEED_SIGN` env toggles.

The new model adds a NEW module and NEW tests; it must not change any of the above.

---

## 8. Risks / pitfalls

1. **The model may simply not predict anything** — GEX-family is structurally positive and a weak
   standalone direction signal (research §1.3.8). The falsifier may return REDUNDANT / INCONCLUSIVE
   for the snapshot-to-forward-realized-vol question too. That is an acceptable, honest outcome and
   must be reported, not papered over.
2. **Sign-convention bugs corrupt the output silently** — a stacked `right_dir` or a
   BS-sign-as-dealer-flow misread flips the hedging expectation. Mitigated by the Phase-2 pin table
   + tests; the rec.vanna = −1×BS pin is the highest-risk item.
3. **Units drift** — mixing per-$1 and per-1% (or shares and vega-$) makes the hedging expectation
   meaningless. Every greek is normalized to the §3.1 unit and the multiplier applied once.
4. **Accumulation temptation** — do NOT slip an accumulated book back in as the primary read. The
   falsifier proved it redundant; any retained overlay must re-earn its place via Phase-6 test 3.
5. **Scope creep into 0DTE / real-time / DDOI** — each needs data v1 does not have. Keep them in
   EXCLUDED; a "one more data source" spiral is how this goes wrong.
6. **Regression risk to live model** — the new module must be a true separate file; any shared
   constant touched must be verified against the pin list in §7. The full suite must stay green.
7. **Scanner parity** — if the new model ever feeds the scanner, the same-solver rule must hold, or
   `test_scanner_vanna_parity.py` breaks.

---

## 9. Open questions (for Jason / CARL panel to adjudicate — see consult brief)

1. Is the **call+/put− inventory convention** acceptable for v1 (no DDOI)? The research is clear it
   is a convention, not a fact — but for an index book it is roughly right. Should v1 expose it as a
   toggle or lock it?
2. **Which greeks genuinely force a tradeable hedge** for the index/optioned-stock universe at hand?
   The ranking (DEX > GEX > VEX > ChaEX > vega > VoEX) is synthesis; the panel should pressure-test
   the spot-hedgeable split.
3. **What is the falsifier's minimum power to call the snapshot net-GEX predictive?** Pre-registered
   threshold (corr magnitude, t-stat, perm p, n tickers) must be set BEFORE running so we don't
   tune to the sample.
4. Should the **ΔIV-signed vanna flow** be the primary predictive channel, or GEX? The research calls
   ΔIV-vanna the #1 actionable fix; the panel should weigh it against GEX's stronger academic
   grounding (Barbon–Buraschi).
5. **Is 0DTE truly excluded for v1?** Given 0DTE is ~59% of volume but de minimis net dealer gamma,
   this plan excludes it. If the panel/Jason wants intraday v1 coverage, that is a data-scope change.
6. What is the **acceptance bar** for "Jason says ready"? A threshold on the Phase-6 verdicts (e.g.
   block-perm p < 0.05 AND a directionally-correct sign on the net-GEX→forward-vol channel), or a
   different gate?

---

## Appendix — 30-second version for Jason
- New **separate, test-only** model; live dealer-positioning stays untouched until you say ready.
- The object is a **per-strike, per-expiry, per-greek snapshot vector** — DEX/GEX/VEX/ChaEX/vega/VoEX
  — with a per-strike hedging expectation split into **stock/futures** (DEX/GEX/VEX/ChaEX) and
  **options/vol** (vega/VoEX) channels.
- **Snapshot is primary**; accumulation is REDUNDANT (your falsifier) and stays a re-earned overlay
  at most.
- Highest-value change: **ΔIV-signed vanna flow** so VEX flips negative into drawdowns.
- Measurement rigour pinned: **rec.vanna = −1×BS**, **no right_dir stacking**, **real spot not
  median-strike**, **multiplier once**, **units explicit**.
- Gated: nothing "works" until the **pre-registered cross-sectional falsifier** passes — and it may
  honestly say no.
