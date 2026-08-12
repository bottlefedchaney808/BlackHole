# Dealer Positioning Direction — Final Debate Report

> **SUPERSEDED 2026-08-09** — the recommendation below (keep
> `vol_surface_replication` headless default, opt-in `DEALER_SIGN_MODEL=oi_flow`)
> is **obsolete**: both `oi_flow` and `vol_surface_replication` were deleted on
> 2026-08-09 (Jason's exclusive choice). Shipped reality: `sign_model='direction'`
> (V5 Direction, the 08-08 backtest winner) is the ONLY sign model —
> `CANONICAL_SIGN_MODEL='direction'`, `VALID_SIGN_MODELS=('direction',)`,
> per-expiry `DEALER_DIRECTION_PER_EXPIRY='sabr_deviation'`, 150-day
> accumulation, NO_CALL gate shared. This file is the historical record of the
> 08-06 debate; do not read the recommendation line as current behavior.

**Date:** 2026-08-06 · **Task:** t_a15f4639
**Supersedes/consolidates:** `dealer_positioning_debate_notes.md` (the debate, t_8ea05d81) and `dealer_positioning_test_results.md` (implementation + testing, t_98d0f1a2)
**Audience:** anyone needing to understand the decision without prior context. This report is self-contained; the two source documents above provide line-level detail.

---

## 0. Executive summary

The Vol Suite computes two independent things: **vol replication** (a Demeterfi-Derman-Kamal-Zou 1999 fair-variance strike — "fair vol") and **dealer positioning** (a net dealer-gamma sign that maps to an AMPLIFY / DAMPEN direction call on the market). The question was: *how should the dealer positioning model determine its direction more accurately, without touching the vol replication method?*

A five-voice, chair-moderated debate was held against an empirical referee that had already shown **every daily-horizon OI/IV proxy is null** (pooled panel, n=428). The winner was the **synthesis position**: take direction from **ΔOI flow** (the day-over-day change in open interest — endogeneity-free by construction), weight it **economically** (dollar gamma under the flow sign), and gate the output with a **NO-CALL honesty dead-band** instead of forcing an AMPLIFY/DAMPEN call on weak evidence. Forward-vol *prediction* at the daily horizon is explicitly re-targeted to an intraday/trade-level research track, because the empirical record shows it cannot be done with daily OI data.

The winning approach was implemented as a new `oi_flow` sign model, tested (356 tests passing, 0 regressions, real-data SPY backtest + live comparison), and shipped opt-in. **The vol replication method (Demeterfi fair-variance strike) remains byte-for-byte in place** — see §9 for the evidence.

**Recommendation in one line:** keep the shipped headless default (`vol_surface_replication`) for continuity, but use `DEALER_SIGN_MODEL=oi_flow` for the accuracy-recommended direction path, with the NO-CALL gate on.

---

## 1. Background — the two engines and why the question exists

The suite's orchestrator (`volatility_suite.py`) drives two independent engines:

| Engine | Module | What it computes |
|---|---|---|
| **Vol replication** | `variance_swap_live.py` | Demeterfi-DDKZ (1999) discrete fair-variance strike ("fair vol") from the OTM option mid-curve + forward. Pure function of option prices; reads no OI, no gamma, no dealer sign. |
| **Dealer positioning** | `dealer_positioning.py` | Per-strike dealer gamma sign × OI → net gamma / net dollar gamma → **AMPLIFY** (dealers short, hedging amplifies moves) or **DAMPEN** (dealers long, hedging dampens), plus gamma-flip level and a heatmap. |
| Shared OTM-strip math | `replication_reference.py` | Demeterfi Appendix-A strip weights; used by *both* engines (see constraint 2). |

The dealer direction call has historically come from three selectable "sign models":

- **v1 `oi_heuristic`** — flat call=+1 / put=−1 (GEX-style convention; a modeling assumption, not a measurement).
- **v2 `replication`** — dealer is short whatever the Demeterfi strip is long (OTM legs −1, ITM 0).
- **v2.1 `vol_surface_replication`** (the live and headless default) — same OTM gate, but per-strike sign from IV richness/cheapness: rich → short, cheap → long, in-dead-band → 0 → fall back to −1.

**Why a debate was needed:** the accuracy record for these signs is poor. The backtest harness (`backtest_stage3.py`) regresses forward realized vol on continuous net-gamma with ATM-IV and TTE controls plus a permutation null. Results (SPY 20261030, 90d; and a pooled panel of AMD/NVDA/TSLA/MU, n=428):

- v1 gives a degenerate 60L/1S split (untestable); the one standalone "hit" (MU v1, perm p 0.019) collapsed to null when pooled → documented false positive.
- v2 gives a degenerate 5L/56S split and its direction wobbles run-to-run.
- v3 (a backtest-only ΔOI-flow variant) gives a balanced 30L/31S split — good *classification* — but **all four models are statistically null** at the daily horizon (best perm p = delta-OI 0.31; pooled panel all p ≥ 0.56).

The spec's verdict: this is an **identification** problem (OI×IV-residual proxies are endogenous to the vol level / too noisy at daily OI granularity), **not a power** problem. The remaining honest levers are trade-level flow data and an intraday horizon — not more regressions on daily OI-derived signals.

### The scope guard (what the debate could not touch)

The dependency is one-directional: `dealer_positioning` → `replication_reference`/`vol_surface_reference`, and `variance_swap_live.compute_fair_variance_strike` imports **neither**. Any change confined to dealer-direction logic cannot alter the fair-variance output *by construction*. Five hard constraints framed every position:

1. **One-directional dependency** — direction edits must not touch `compute_fair_variance_strike`.
2. **Shared OTM gate** — `_otm_leg_weights`/`_build_weights` feed both the replication diagnostics and the dealer OTM gate; changing OTM classification changes both engines (validated against Demeterfi paper Fig. 3).
3. **Sign-model threading** — a new sign model must be added to `VALID_SIGN_MODELS`, `_resolve_sign`, the CLI prompts, `_SIGN_MODEL_LABELS`, and the README.
4. **Accuracy gate** — a sign must survive ATM-IV+TTE controls AND the permutation null, stable across σ/expiries.
5. **Degeneracy** — binary sign models collapse day-splits (v1 60/1, v2 5/56); a testable split needs magnitude or flow-based signs.

---

## 2. The debate — what was debated

Five positions were argued, chair-moderated, and stress-tested against the empirical referee (the all-null record above) and the hard constraints. (Note: the debate ran as a structured multi-voice fallback because the originally intended "Carl" command was unavailable in this environment — five explicit voices with pros/cons and code-level stress tests, equivalent coverage.)

### Voice 1 — FLOW-FIRST: ΔOI is the direction; the level is noise
- **Thesis:** direction must come from *flow*, not *level*. "Rich IV → dealer short" is mechanically tied to the vol regime — the endogeneity that killed every IV-residual proxy. Replace the per-strike sign with the ΔOI flow signal: `sign = −sign(ΔOI·direction)` (spec M1), plus M2 (net delta-OI across the chain) as a pure-data reference needing no IV fit at all.
- **Pros:** breaks the endogeneity loop at the root; already proven as a *classifier* (v3's balanced 30L/31S split); the math already exists on the backtest side and in `replication_reference.compute_accumulated_position`; data is retrievable from an existing ThetaData route.
- **Cons / stress-test:** ΔOI ≠ executed flow (same-day open+close never touches OI); the referee doesn't forgive it — v3's pooled perm p is 0.798, i.e. flow fixes the *method's* identification but not the *target's* daily-horizon predictability; live wiring needs a historical OI fetch per expiry (latency + retry discipline).

### Voice 2 — SURFACE-TUNER: keep rich/cheap, fix the mechanics
- **Thesis:** don't discard the IV-surface insight; improve it by porting continuous tanh weighting live, grounding the scale in data (M3), and tuning the dead-band/fitter window.
- **Pros:** minimal change, maximal continuity with the method identity (`vol_surface_replication`); no new data dependency; cheap to test.
- **Cons / stress-test:** the referee kills it first — the spec's central finding is that "rich IV → dealer short" is *structurally* endogenous to the vol regime. Tuning the dead-band is **re-fitting the confound**, not removing it. Every historical IV-residual "hit" inverted once ATM-IV+TTE controls were added. Smoothing magnitude doesn't stabilize the *sign*. Widening the dead-band re-injects the Layer-1b −1 bias via fallback. **Dropped as a standalone** (see §4.1).

### Voice 3 — ECONOMICS-FIRST: dollar gamma + strip weights + term structure
- **Thesis:** the sign isn't the problem — the *aggregation* is. Use dollar gamma (M4: γ·S·OI) so deep-OTM high-OI legs don't dominate, weight legs by Demeterfi replication weights, and aggregate the front expiries weighted by inverse TTE (M5).
- **Pros:** uses data already fetched; economically grounded ("short variance" dealers are short the weighted strip, not every OTM leg equally); multi-expiry aggregation defuses single-expiry TTE sensitivity.
- **Cons / stress-test:** magnitudes don't fix a wrong sign (presupposes a sound sign layer); reusing strip weight *magnitudes* trips constraint 2 (shared OTM gate — the briefing explicitly notes weight magnitudes are deliberately NOT reused today); **M5 is empirically pre-refuted** — the pooled panel *is* M5-style pooling and it is all-null. **Dropped as a standalone direction fix** (§4.2); its dollar-gamma half was adopted under the flow sign.

### Voice 4 — HONESTY-GATE: no-call layer + re-target the research
- **Thesis:** the record says daily-horizon OI/IV proxies cannot separate dealer flow from vol clustering. The most accurate *output* today is a **no-call** when evidence is weak: a dead-band / minimum |net dollar gamma| before emitting AMPLIFY/DAMPEN, plus shifting the research budget to the only honest levers (intraday horizon, trade-level flow).
- **Pros:** zero risk to vol replication (pure output gating — the cheapest possible change); immediately implementable, no data dependency; improves decision precision over recall; suppresses flip-flops near the zero crossing; aligns with the spec's own conclusion.
- **Cons / stress-test:** gating is *hygiene, not accuracy* — a threshold on a null signal just hides the null; |net_gamma| magnitude is itself vol-regime-correlated and can reintroduce the confound; the intraday/trade-level path is a real research program, not a patch. **Dropped as a standalone** (§4.3); adopted as the output layer of the winner.

### Voice 5 — SYNTHESIS: flow direction + economic magnitude + honesty gate
- **Thesis:** layer three fixes, each solving one proven failure:
  1. **Direction source = flow (M1/M2)** — ΔOI sign is the only endogeneity-free, non-degenerate sign available from retrievable data.
  2. **Magnitude = economics (M4)** — dollar gamma (+ replication-strip weights *under the flow sign*) — principled weighting because the sign is now principled.
  3. **Output = honesty gate (E)** — dead-band on final net gamma with a documented NO-CALL state.
  4. **Vol replication untouched** by construction (constraint 1).
- **Stress-test against the referee:** this is the only voice that *accepts* the pooled null as its premise. It claims direction **classification** accuracy (which side is the book on) plus **decision hygiene**, explicitly does **not** claim daily-horizon predictive power for forward vol, and re-targets that claim to the intraday/trade-level track. That honesty is what lets it survive the gate.

### Cross-examination summary

| Stress test | V1 Flow-first | V2 Surface-tuner | V3 Economics-first | V4 Honesty-gate | V5 Synthesis |
|---|---|---|---|---|---|
| Vol replication intact (constraint 1) | ✅ | ✅ | ✅ (w/ test guard) | ✅ | ✅ |
| Survives controls + perm gate (constraint 4) | ⚠️ classifier only, predictor null | ❌ proxy is the confound | ❌ pre-refuted by pooled panel | ⚠️ doesn't claim signal | ⚠️ claims classification + hygiene, re-targets prediction |
| Fixes degeneracy (constraint 5) | ✅ (30L/31S) | ⚠️ continuous helps, sign wobbles | ❌ sign still binary | ❌ | ✅ (flow sign + continuous magnitude) |
| New data needed | `hist_oi` (existing route) | none | none | none | `hist_oi` only |
| Risk to shared OTM gate (constraint 2) | none | none | **high** (weight reuse) | none | low (weights reused only under flow sign, keys-only today) |
| Live-implementable this sprint | ✅ | ✅ | ✅ | ✅ | ✅ (staged) |

---

## 3. What was discussed (key discussion points)

- **Classification vs prediction.** The single most important distinction: a sign model can be a good *classifier* (balanced day-splits, correct book side) while having zero *predictive* regression effect at the daily horizon. The debate settled on claiming the former and explicitly separating the latter.
- **Endogeneity of IV-richness.** Every IV-residual-based sign inverts under ATM-IV+TTE controls — the classic vol-clustering confound. This is why surface tuning cannot pass the accuracy gate, and why the winning sign must come from a source orthogonal to today's vol level (ΔOI).
- **Degeneracy mechanics.** Binary ±1 signs collapse day-level splits (60/1, 5/56), making the models untestable; flow-based signs naturally produce balanced splits.
- **The shared OTM gate tripwire.** Reusing `_build_weights` *magnitudes* (not just keys) would change what the shared gate feeds into both engines. Resolution: keys-only gating stays as-is; magnitude reuse is deferred behind a regression guard.
- **What "accuracy" can honestly mean today.** Given the all-null referee, the deliverable is (a) a balanced, non-degenerate direction model, (b) fewer confident-but-wrong calls via the no-call gate, and (c) a concrete research path (intraday/trade-level) for actual prediction.
- **Data realities.** ΔOI needs historical OI per expiry; the live path must tolerate fetch failure gracefully (empty flow → no contribution, never a crash or a forced fallback).

---

## 4. What was dropped and why

1. **Surface-tuning alone (V2) — DROPPED.** It re-fits the confound the spec already identified: IV-richness→dealer-short is mechanically tied to the vol regime, and every historical IV-residual "hit" inverted under controls. Tuning `IV_DEADBAND_VOL`/fitter parameters cannot survive constraint 4 because the proxy *is* the endogeneity. Kept only as the selectable legacy default for continuity, with a documented accuracy caveat.
2. **Economics-first alone (V3) — DROPPED as a direction fix.** M4's strip-weight reuse risks the shared OTM gate (constraint 2), and M5-style pooling is *already refuted* by the n=428 panel (all null). Magnitudes are adopted **under the flow sign** (V5), never as a standalone sign source.
3. **Honesty-gate alone (V4) — DROPPED as a standalone.** A no-call threshold on a null signal is silence, not accuracy; |net_gamma| gating risks re-correlating with the vol regime. Adopted as the output layer of V5, where it does real work (flip-flop suppression on a *plausible* signal).
4. **More OI-regression research — DROPPED explicitly.** The spec forbids re-running the panel; the identification problem is not a power problem.
5. **v1 `oi_heuristic` — DROPPED as a direction candidate.** Degenerate 60/1 splits, unmeasurable, and the MU v1 "hit" was a documented false positive.

---

## 5. How the final choice emerged

The synthesis (V5) won because it is the only structure consistent with *every* empirical fact:

- All daily proxies are null → don't predict vol from OI regressions (kills V3-as-predictor and more regression research).
- v3's balanced split → flow fixes the classifier (kills V2's claim that tuning is needed).
- Pooled panel null → don't promise term-structure magic (kills M5 as a direction fix).
- Degenerate splits → the sign must carry magnitude or flow (kills v1 and bare binary signs).
- Vol replication must stay untouched → the change must be confined to dealer-direction logic and output gating (kills anything touching `_build_weights` magnitudes or the fair-variance path).

**Winning narrative:** *Direction is a flow question, not a surface question. Determine which side the book is on with ΔOI flow (endogeneity-free, non-degenerate), weight it economically (dollar gamma × replication-strip notional), gate the output honestly (no-call below a minimum evidence threshold), and keep the Demeterfi fair-variance computation byte-for-byte untouched. Claim classification + decision accuracy now; re-target forward-vol prediction to the intraday/trade-level horizon as the explicitly separated research track.*

The agreed implementation was staged in three phases: **Phase 1** live `oi_flow` sign model + dollar-gamma magnitude (M4 half) + NO-CALL gate (E) — this sprint; **Phase 2** the full testing battery; **Phase 3** the separated intraday/trade-level prediction research track (do NOT re-run the daily pooled panel).

---

## 6. What was implemented (the winning approach, Phase 1)

1. **New `oi_flow` sign model (M1/M2)** in `dealer_positioning.py`: `sign = −sign(ΔOI·direction)` per (strike, right), direction = +1 call / −1 put. Customer adds a leg → dealer takes the other side (call-add → −1, put-add → +1); flow out flips the sign. **Zero/missing ΔOI → 0 (no contribution, never a forced fallback to the Layer-1b −1).** Still gated by the shared OTM replicating-set keys (keys only — weight magnitudes never reused, constraint 2 respected).
2. **ΔOI plumbing**: `_build_oi_flow_map` (pure day-over-day reducer over the last two distinct dates in the OI history; <2 dates → no flow) and `_fetch_oi_flow_map` (best-effort per-expiry network fetch; failure → `{}` → that expiry contributes nothing, never a crash).
3. **Dollar-gamma magnitude (M4 half)**: the flow sign is applied to the existing `γ·S·100·OI` magnitude. No other sign model's math changed.
4. **NO-CALL honesty gate (E)**: `resolve_direction_call` emits `AMPLIFY | DAMPEN | NO_CALL`. Dead-band: NO-CALL when `|net dollar gamma| < floor`, where floor = absolute `$` if given, else `floor_frac ×` the **unsigned gross book size** (default 5%, env `DEALER_NO_CALL_FLOOR_FRAC`; floor=0 restores the historical bare `net_gamma>0` boundary). `DealerPositioningResult` gains `direction_call`, `no_call_floor`, `gross_gamma_exposure`; the report/heatmap render NO-CALL instead of a forced call.
5. **Threading (constraint 3)**: interactive prompt gains `(4) OI-Flow ΔOI`; the headless `--pack` default **stays `vol_surface_replication`** (no silent behavior change) with `DEALER_SIGN_MODEL=oi_flow` env opt-in for orchestrators; `_SIGN_MODEL_LABELS` and CLI prompts updated.
6. **Backtest wiring**: `backtest_stage3.py` + `shared/schemas.py` add a **v4 proxy** (`_net_gamma_v4_oi_flow_live`) that mirrors the live wiring's exact math (flow-sign × level-OI magnitude, OTM gated) — so the primary regression tests the code path the live charts actually run.

**Diff footprint:** `dealer_positioning.py` +255/−~20, `backtest_stage3.py` +95/−~8, `volatility_suite.py` +15/−2, `shared/schemas.py` +22. **`variance_swap_live.py`, `replication_reference.py`, `vol_surface_reference.py`: 0 lines changed.**

---

## 7. Evidence from test results

### 7a. Real-data backtest — SPY 20261030 (90d), 67 usable days, 5d forward window, n=62

```
                         v1 (oi_heuristic)    v2 (vol_surface_replication)         v3 (oi-change flow)           v4 (live oi_flow)
long-gamma days                         61                               5                          30                          22
short-gamma days                         1                              57                          32                          40
short - long                           nan                          0.0014                      0.0054                     -0.0082
perm p                              0.5507                          0.3523                      0.7926                      0.2329
delta-OI coef (M2)                                                                     0.0000 (perm p 0.2529)
```

- **Degeneracy fixed (constraint 5):** v1 61/1 and v2 5/57 are untestable; **v4 (live oi_flow) 22L/40S — 33%/60% each side, balanced** (same as v3's 30/32).
- **Prediction remains null at the daily horizon — as documented, NOT a failure:** v4 coef +0.0001 (perm p 0.233) is the best of the flow models but nowhere near significant; v2 −0.0005 (0.352), v3 +0.0002 (0.793), delta-OI ~0.0000 (0.253). This **replicates the spec's pooled-panel null on live single-expiry data** — the identification problem is unchanged. The winning approach claims classification + decision hygiene now, and re-targets prediction to the intraday/trade-level track (§10).
- Net: the classifier is fixed and testable; no false prediction claim is being made.

### 7b. Live snapshot comparison — SPY, 15 expiries (≤60d), 3,562 records, spot $769.77

| metric | BEFORE (vol_surface_replication) | AFTER (oi_flow) | AFTER, gate off |
|---|---|---|---|
| direction call | AMPLIFY | **AMPLIFY** | AMPLIFY |
| net dollar gamma | −$2.689B | −$265.2M | −$265.5M |
| net gamma | −3.49e4 | −3.45e3 | −3.45e3 |
| gross book ($, unsigned) | $4.002B | $4.002B | $4.002B |
| NO-CALL floor ($, 5% gross) | $200.1M | $200.1M | 0 (disabled) |
| gamma-flip level | $774.67 | $768.77 | $768.77 |

- Both models agree SPY is dealer-short (AMPLIFY) on this snapshot — no direction conflict.
- The flow sign **narrows the net exposure ~10×** (only legs with actual day-over-day flow count) and pushes the gamma-flip level from $774.67 to $768.77 — into the current spot range, i.e. the flow book is near-balanced.
- `|net DG|` ($265M) sits ~1.3× the NO-CALL floor ($200M): a modest flow shift flips this snapshot to NO_CALL — exactly the flip-flop suppression the gate exists for.
- Gate-off on identical evidence would still print AMPLIFY; gate-on prints AMPLIFY only because $265M > $200M. On the zero-evidence case, gate-off prints AMPLIFY on a 0 net while gate-on correctly prints NO_CALL.

### 7c. Test suite

**356 passed, 5 skipped (baseline 337 + 19 new; 0 regressions).** New file `tests/test_dealer_positioning_oi_flow.py` covers: ΔOI→sign mapping recovery (network-free), NO-CALL gate firing below floor and silence at exactly 0, OTM-gate key respect, fair-variance snapshot invariance, and more.

---

## 8. Data limitation discovered & fixed during implementation

The debate's plan said "ΔOI fetch: `option_bulk_hist_oi`". **Live probing showed that route cannot do day-over-day ΔOI**: it wraps the per-contract open-interest route, which returns a **start-date snapshot** (every contract's rows stamped with the range's first date — ~300–600 requests/expiry for nothing). The first live `oi_flow` run therefore produced an empty flow map → net gamma 0 → **honest NO_CALL** (the gate correctly refused to call on zero evidence; gate-off would have printed AMPLIFY on nothing — a nice real-world demonstration of the gate).

**Fix:** `_fetch_oi_flow_map` now uses **`option_bulk_hist_oi_by_day`** — whole-chain per calendar day, the same route the stage-3 backtest runs on (~7 requests/expiry, 117 weekdays verified). After the fix all 15 SPY expiries produced 160–386 ΔOI legs each and the live direction resolved.

---

## 9. Vol replication remains in place — evidence

The Demeterfi-DDKZ 1999 fair-variance method is **unchanged**, by construction and by verification:

1. **Zero-line diff** on the entire replication chain: `variance_swap_live.py`, `replication_reference.py`, `vol_surface_reference.py` untouched. The fair-variance formula, the OTM mid-curve fetch, and `_build_weights`/`_otm_leg_weights` are byte-for-byte identical. The change is confined to dealer-direction logic; the one-directional dependency (constraint 1) makes it impossible for this change to alter `compute_fair_variance_strike` output.
2. **Snapshot invariance test**: `compute_fair_variance_strike` on a fixed deterministic chain must equal the pre-change values captured 2026-08-05 from the untouched implementation (fair var 0.1407233064, fair vol 37.5131%, ATM IV 22.0%, convexity premium 15.5131%, 21 strikes). If anyone ever edits the DDKZ path, this test fails.
3. **Import-graph test**: `variance_swap_live` does not import any dealer module (one-directional dependency enforced).
4. **Paper-validation suite untouched & green**: `tests/test_variance_swap_replication.py` (Demeterfi Fig. 3) passes unchanged.
5. Full suite green: 356 passed, 0 regressions.

**Bottom line: the vol replication method stays exactly as it was. This change is purely a dealer-direction improvement.**

---

## 10. Caveats and limitations (documented honestly)

1. **Daily-horizon prediction is null for all OI-derived proxies — this is not fixed.** The winning approach fixes *classification* (balanced, non-degenerate book-side calls) and *decision hygiene* (no confident calls on weak evidence), not forward-vol prediction at the daily horizon. Anyone reading a `oi_flow` AMPLIFY/DAMPEN call should treat it as "where the dealer book is and how confident the evidence is," not as a daily vol forecast.
2. **ΔOI ≠ executed flow.** OI change is the net of opens/closes/rolls at daily granularity; same-day open+close never touches OI. This is the same "too noisy at daily OI granularity" caveat from the spec.
3. **NO-CALL floor is a gross-fraction approximation.** The debate proposed "0.5× 90-day rolling median of net dollar gamma"; a single live snapshot has no 90-day history, so the MVP uses 5% of the unsigned gross book (configurable). The rolling-median form is a drop-in parameter in the backtest domain but needs a historical net-DG series for the live path (Phase-3-adjacent work). A floor of 0 disables the gate and restores the historical boundary.
4. **Headless default unchanged on purpose.** `--pack` still ships `vol_surface_replication` (continuity, no silent behavior change); `oi_flow` is the accuracy-recommended option via `DEALER_SIGN_MODEL=oi_flow`. A future release may flip the default, with a release note.
5. **Single-expiry focus.** The live path still aggregates one near-dated expiry; M5 (multi-expiry inverse-TTE aggregation) and M3 (data-grounded weight scale) remain optional robustness work behind the Phase-2 gates.
6. **Latency/network dependency.** The live flow fetch adds a per-expiry historical OI request; failures degrade to "no flow → no contribution," never a crash, but a persistent data outage would make `oi_flow` silent (and the gate would then produce NO_CALL).
7. **The referee's verdict is replicated, not overturned.** v4's perm p 0.233 is the best of the flow models but far from significant — consistent with the spec's "do not re-run the panel; the honest levers are trade-level/intraday."

---

## 11. Recommendation

1. **Adopt `oi_flow` as the accuracy-recommended dealer-direction model** (`DEALER_SIGN_MODEL=oi_flow`), layered as: ΔOI-flow sign (M1/M2) → dollar-gamma magnitude (M4 half) → NO-CALL gate (E). Keep `vol_surface_replication` as the shipped headless default until a release note flips it.
2. **Keep the NO-CALL gate on** (default 5% of unsigned gross book). It is cheap, zero-risk to vol replication, and demonstrably prevents calls on zero evidence (the first empty-flow run correctly produced NO_CALL while gate-off would have printed AMPLIFY on nothing).
3. **Do not re-run the daily pooled panel.** Forward-vol prediction is a Phase-3 research track: trade-level flow / intraday horizon — the only levers the empirical record leaves open.
4. **Leave the Demeterfi fair-variance vol replication method exactly in place**, as it is now, and rely on the snapshot-invariance + import-graph tests to keep it that way.

---

## 12. Source documents

- `dealer_positioning_brief.md` — architecture, formulas, constraints §5, candidate modification points (t_6ad06963).
- `dealer_positioning_debate_notes.md` — full five-voice debate, cross-examination, drops, winning narrative (t_8ea05d81).
- `dealer_positioning_test_results.md` — implementation diff summary, before/after metrics, vol-replication evidence, reproduction (t_98d0f1a2).
- `DEALER_METHOD_V3_SPEC.md` — the accuracy spec (M1–M6), empirical referee results, sequencing.
- Test artifacts: `outputs/t98d0f1a2_test/spy_backtest_90d.log`, `outputs/t98d0f1a2_test/spy_live_comparison.json`.

---

*Chair's note (from the debate): the all-null empirical record is why every voice had to state its predictive claim precisely — the synthesis wins precisely because it is the only structure whose claims match the evidence.*
