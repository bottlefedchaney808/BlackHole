# PLAN — Dealer-Positioning Sign Model: v2 (`vol_surface_replication`) → surface-backed / v3 migration

Status: DRAFT 2026-08-05 (plan-only; NO production code changed)
Author: Hermes Agent (delegated plan task)
Grounding: `Vol_Suite/DEALER_METHOD_V3_SPEC.md` (read fully), `Vol_Suite/dealer_positioning.py`
(read fully), pooled-panel backtest results (`Vol_Suite/pooled_panel_backtest.py`), and the new
`Options_Suite/PLAN_vol_surface.md` surface work.

---

## 0. Empirical context up front — do NOT promise v3 "works"

The honest state of the dealer-hedge channel, per the spec + pooled backtest:

- **Signal degeneracy fixed, but the effect is not detectable.** v1 (oi_heuristic) gives an
  unbalanced ~60/1 short/long split and v2 (vol_surface_replication) ~5/56 — both untestable as
  binary classifiers. The **v3 OI-change flow** fixes that as a classifier (balanced ≈30L/31S), so
  v3 is a real improvement at the *classification* step.
- **But every model is null on the regression.** Pooled panel (120d window, 5344 records) of fwd
  realized vol on continuous net-gamma with ATM-IV + TTE controls + ticker FE, permutation nulls:
  - **v3 (OI-change flow): perm p ≈ 0.90** — flat null.
  - **M2 (net delta-OI): most predictive of the set but POSITIVE sign** — positive coef means
    "more dealer-short → LOWER fwd vol", i.e. **contradicts** the short-gamma→amplification
    hypothesis the whole model rests on. That is not green; it is evidence the proxy is
    mis-specified or endogenous.
  - v1/v2 also null (single-expiry 90d run re-confirms: best perm p 0.31 delta-OI, v3 0.90).
- **Conclusion the spec draws, which this plan adopts:** this is an **identification problem**
  (OI × IV-residual × gamma measures are endogenous to the vol level / too noisy at daily OI
  granularity), **not a power problem**. Pooling did not rescue it.
- **Therefore the migration is framed as:** *build the surface first (PLAN_vol_surface.md), get
  dealer net-gamma from a proper multi-expiry surface reference, re-run the pooled backtest against
  that surface-based net-gamma, and only flip the default if that channel survives the controls AND
  the permutation null with the correct NEGATIVE sign, stable across σ and expiries. Until then the
  shipped default stays v2-style (surface-backed), and v3/M2 remain experimental flags.*

---

## 1. What the code does today (full read)

- `dealer_positioning.py`
  - `VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')`.
  - `_resolve_sign(right, strike, sign_model, otm_strikes, vol_surface_ref)`:
    - `'oi_heuristic'` (v1): flat `_dealer_sign(right)` every strike.
    - `'replication'` (v2 Layer 1b): `0` off the OTM replicating set, `-1` on it.
    - `'vol_surface_replication'` (v2 Layers 1a+1b, **current default**): gate by OTM set, then
      per-strike flip via `vol_surface_reference.resolve_vol_surface_sign(ref, strike, right)`
      (rich→-1 / cheap→+1 / dead-band→0), falling back to Layer 1b `-1` when `ref is None`.
  - `compute_dealer_positioning(..., sign_model='vol_surface_replication')` — **single-day snapshot**
    against one expiry window; per-expiry it builds `chain_iv`, `otm_strikes`, and a `vol_surface_ref`
    from `compute_vol_surface_reference(ticker, chain_iv, spot, forward=expiry_forward, T=tte)`.
  - `run_dealer_positioning(..., sign_model='vol_surface_replication')` (programmatic runner).
  - `plot_heatmap` + `plot_greek_exposure_comparison` key `_SIGN_MODEL_LABELS` and per-sign-model
    subtitles off `result.sign_model`.
  - interactive `main()` defaults to 'oi_heuristic' via prompt (unused by the suite path).
- `volatility_suite.py`: `_prompt_sign_model_and_options_chain()` maps choice `'3'` →
  `'vol_surface_replication'`; the headless pack path hardcodes
  `sign_model = "vol_surface_replication"` (line 1105).
- `sentiment-scanner/scanner/gex_scanner.py`: `scan_gex(..., sign_model="oi_heuristic")` default but
  accepts and forwards any `sign_model` to `compute_dealer_positioning`.
- `pooled_panel_backtest.py`: regresses fwd realized vol on `net_gamma_v1/v2/v3` and `net_delta_oi`
  (M2) — this is where the empirical null lives and where a surface-based column gets added.

---

## 2. Change design — what migrates and what stays

**Guiding rule: keep the choosing logic (`sign_model` param + VALID_SIGN_MODELS + per-model
`_resolve_sign` branches) intact.** We add new branch values and re-point the reference source of
the default, but never replace the switch with a single hardcoded path.

### 2.1 New sign_model values

| value | meaning | default? | where |
|---|---|---|---|
| `vol_surface_replication` (existing) | v2 Layers 1a+1b via per-expiry `vol_surface_reference` | **YES (shipped)** | snapshot compute |
| `vol_surface` (**NEW**) | v2 Layers 1a+1b but reference drawn from the **multi-expiry `MarketVolSurface` slice** instead of `vol_surface_reference` | candidate default once ready | snapshot compute |
| `oi_flow` (**NEW, experimental**) | v3 OI-change flow (`sign = −sign(ΔOI·direction)`) | **NO** | backtest harness only |
| `delta_oi` (**NEW, experimental**) | M2 `net_delta_oi = Σ _dealer_sign(right)·|delta|·OI` | **NO** | backtest harness only |

### 2.2 The key architectural point: snapshot vs. flow

- **v2 family (incl. the new `vol_surface`)** is a **single-day snapshot** — all inputs come from
  `option_bulk_greeks` + `option_bulk_oi` at an as-of time. It fits inside `compute_dealer_positioning`.
- **v3 (`oi_flow`)** needs **historical OI**: `option_bulk_hist_oi` / `option_bulk_oi_by_day` to form
  ΔOI across days. It **cannot** be produced by the single-day snapshot `compute_dealer_positioning`.
- **M2 (`delta_oi`)** is a pure-data measure but is **meaningful as a time-series** (day labels,
  panel rows), not as one snapshot number.
- **Consequence:** v3/M2 belong in the **backtest harness** (`pooled_panel_backtest.py`), not in the
  shipped snapshot default. Flipping the shipped default to v3 would break snapshot consumers
  (gex_scanner, heatmap, pack runs) for no reason and is not possible without a historical-OI fetch
  path that doesn't exist in the snapshot compute. So: **the migration's real lever is
  surface-backing the snapshot default (v2→`vol_surface`), not flipping to v3.**

### 2.3 Exactly what changes in `dealer_positioning.py`

1. `VALID_SIGN_MODELS` ← add `'vol_surface'`, `'oi_flow'`, `'delta_oi'`.
2. `_resolve_sign`:
   - Add a `'vol_surface'` branch: reads the OTM-gated per-strike deviation from the **surface slice**
     (`MarketVolSurface.slice(expiry)` → `deviation_by_strike`), applies the same
     `IV_DEADBAND_VOL` dead-band → rich −1 / cheap +1 / dead-band 0, fallback to Layer 1b `-1`.
     Reuse `vol_surface_reference.resolve_vol_surface_sign`'s mapping on the slice's dict (keeps one
     sign convention in one place).
   - Add `'oi_flow'` / `'delta_oi'` branches that **raise NotImplementedError with a clear message**
     ("requires historical OI; run via pooled_panel_backtest") rather than silently proxy — so nobody
     can accidentally set a snapshot default to a flow model.
3. `compute_dealer_positioning`: when `sign_model == 'vol_surface'`, accept an optional
   `vol_surface=None` param; if a surface is provided, use `surface.slice(expiry)` per expiry;
   **if not provided, build a one-expiry surface from this run's chain** (which reduces to today's
   `vol_surface_reference` behavior — i.e. graceful degrade, default unchanged).
4. `run_dealer_positioning` and `volatility_suite._prompt_sign_model_and_options_chain` + pack path:
   **default mapping unchanged** (`'3'`/pack → `'vol_surface_replication'`). Add an optional
   experimental prompt only in the interactive path (never in the headless pack default).
5. `_SIGN_MODEL_LABELS` + chart subtitles: add entries for `vol_surface` / `oi_flow` / `delta_oi`.

---

## 3. Enumerated call sites / tests that pin the current default — what a v3-oriented flip breaks

| Site | Pins today | Breaks if default → v3 |
|---|---|---|
| `dealer_positioning.compute_dealer_positioning(..., sign_model='vol_surface_replication')` | snapshot default | v3 needs hist-OI fetch → snapshot path can't produce it → compute fails |
| `dealer_positioning.run_dealer_positioning(...)` default | v2 | pack/orchestrator run would throw |
| `volatility_suite.py:1105` headless pack `sign_model = "vol_surface_replication"` | v2 | every pack run breaks with no interactive fix |
| `volatility_suite._prompt_sign_model_and_options_chain` (`'3'` → v2) | v2 | interactive default silently changes behavior |
| `tests/test_run_modes_smoke.py:143,168,197` (assert default == "vol_surface_replication") | v2 | asserts fail |
| `tests/test_dealer_positioning_sign_model.py` (`test_vol_surface_replication_is_default_and_runs` line 78-82; `test_invalid_sign_model_raises` line 121) | v2 default + invalid-model guard | default assert fails; `'nonsense'` still must raise |
| `sentiment-scanner/scanner/gex_scanner.py` `scan_gex(..., sign_model="oi_heuristic")` | passes through | its default is v1, but forwards anything — a flipped upstream default changes GEX output semantics silently |
| `tests/test_tools_batch2.py:284` (sign_model round-trip) | list of valid models | a new default must stay in the accepted set |
| `pooled_panel_backtest.py` (`net_gamma_v1/v2/v3`, `net_delta_oi`) | model inventory | **intended target** — add `net_gamma_surface` |
| `PROJECT_SPEC.md` §: "keep `vol_surface_replication` as the default (least-wrong of the three)" | documented contract | docs must be updated in lock-step if flipped |

**What a v3-oriented default concretely breaks (summary):** the entire single-day snapshot
stack (compute, heatmap, greek-exposure chart, gex_scanner, pack/orchestrator runs) cannot produce
a flow-based signal; run_modes smoke asserts; the sign-model wiring test's default assert; and the
PROJECT_SPEC documented contract. **Therefore flipping the shipped default to v3 is rejected in
this plan.** The migration is: (1) surface-back the default, (2) keep v3/M2 experimental in the
backtest harness, (3) re-run the panel, (4) decide.

---

## 4. Phased, testable plan

### Phase 1 — Surface-backed sign source (SELECTED; ties PLAN_vol_surface.md Phase 4)
- [ ] New `'vol_surface'` branch in `_resolve_sign` (slice → dead-band sign, Layer 1b fallback).
- [ ] `compute_dealer_positioning(..., vol_surface=None)`; when absent, degrade to today's
      `vol_surface_reference` (built from this run's chain) — **default unchanged**.
- [ ] VALID_SIGN_MODELS + labels updated; v3/M2 branches raise NotImplementedError in snapshot path.
- Tests: synthetic surface + synthetic chain → per-strike sign equals `resolve_vol_surface_sign` on
  the same deviation; surface absent → behavior identical to current default (regression: existing
  `test_vol_surface_replication_is_default_and_runs` still green); `'oI_flow'`/`'delta_oi'` snapshot
  call raises NotImplementedError (no silent proxy); `'nonsense'` still raises ValueError.

### Phase 2 — Wire into suite entry points, keep default (SELECTED)
- [ ] `_prompt_sign_model_and_options_chain` + pack path: default still `'vol_surface_replication'`;
      interactive path may offer `'vol_surface'` as an alternate; **pack default untouched**.
- [ ] `_SIGN_MODEL_LABELS` + heatmap/greek-chart subtitles for the new values.
- Tests: `test_run_modes_smoke` still asserts v2 default (unchanged); a run with
  `sign_model='vol_surface'` + injected surface produces `result.sign_model == 'vol_surface'` and
  renders both charts without exception; gex_scanner forwards it unchanged.

### Phase 3 — Re-run pooled backtest with surface-based net-gamma (SELECTED; the decision gate)
- [ ] Add `net_gamma_surface` column to `pooled_panel_backtest.py` (net-gamma where the repo day's
      per-leg sign comes from the surface slice), alongside existing v1/v2/v3/M2 columns.
- [ ] Run the existing pooled panel (120d, 5344-record frame) with the new column.
- Tests: pooled runner produces the surface column on a synthetic panel with known sign;
  comparison table unchanged for the existing four columns (no accidental regression while adding).

### Phase 4 — CONDITIONAL: flip the default (only on evidence)
Gate (all must hold, then flip):
- surface-based `net_gamma_surface` coef is **negative** (short→higher vol, the hypothesis), AND
- permutation p < 0.05 under the strict within-ticker block null, AND
- stable across σ sweep (M3) and across the front 2–3 expiries (M5).
If the gate fails → **document the null and keep v2-style default**. If it passes:
- [ ] flip default to `'vol_surface'` (surface-backed) in `compute_dealer_positioning`,
      `run_dealer_positioning`, `volatility_suite` pack path, `_prompt_sign_model...`;
- [ ] update `test_run_modes_smoke` default asserts + `test_dealer_positioning_sign_model` default
      assert + PROJECT_SPEC default statement in lock-step;
- [ ] update gex_scanner doc + orchestrator/bridge expectations.

### Phase 5 — CONDITIONAL/EXCLUDED-by-default: v3 OI-flow as shipped default
- Only pursue if Phase 4's surface path is decisive AND a separate trade-level/intraday or
  historical-OI flow proves the channel at the right horizon (the spec's own conclusion — the real
  levers are trade-level flow + intraday, not more daily-OI regression). Otherwise **EXCLUDED**:
  v3/M2 remain backtest-only experimental flags; the documented finding (all proxies null, M2
  positive sign) is kept on the record.

---

## 5. Explicit scope

**SELECTED**
- `'vol_surface'` branch (slice + dead-band sign, Layer 1b fallback), optional `vol_surface` param,
  degrade-to-current-default when absent
- `'oi_flow'`/`'delta_oi'` as backtest-harness-only experimental flags (snapshot path raises)
- `net_gamma_surface` column in `pooled_panel_backtest.py` + re-run (the decision gate)
- Labels/subtitles for new models; pack + interactive defaults **unchanged** in Phases 1–3
- Tests per phase (regression on current default, new-model wiring, panel column, NotImplementedError)

**CONDITIONAL**
- Phase 4 default flip — gated on negative sign + block perm p<0.05 + σ/expiry stability
- Syncing PROJECT_SPEC / gex_scanner / orchestrator only when the flip actually lands

**PRESERVED**
- `sign_model` param + `VALID_SIGN_MODELS` switching logic + per-model `_resolve_sign` branches
- Shipped default behavior (v2-style) until the Phase 4 gate passes
- `vol_surface_reference.py` + its tests (the surface slice reuses, not replaces, its dead-band sign)
- Dealer snapshot/heatmap/greek-chart/gex_scanner contract and run_modes smoke asserts (unchanged in Phases 1–3)
- The documented empirical null (v3 0.90, M2 positive sign) — kept on the record, not papered over

**EXCLUDED**
- No flipping the default to v3/M2 in this effort (rejected: not a snapshot model, and empirically null)
- No re-litigating the OI×IV-residual identification problem with more daily-OI regression
- No changes to `vol_surface_reference` internals; no new SABR implementation
- No removal of the `sign_model` choosing logic or its test coverage
