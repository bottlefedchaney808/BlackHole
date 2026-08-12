# Dealer Positioning — Winning Approach: MVP Implementation & Test Results

> **SUPERSEDED 2026-08-09** — the `oi_flow` sign model and the
> `vol_surface_replication` headless default described in this record were
> **deleted** on 2026-08-09 (Jason's exclusive choice). Shipped reality:
> `CANONICAL_SIGN_MODEL='direction'` (V5 Direction) is the only sign model,
> `DEALER_SIGN_MODEL` env override does not exist, per-expiry mode is
> `sabr_deviation`, 150-day accumulation. Keep this file as the historical
> record of the 08-06 debate/implementation, not as current behavior.

**Date:** 2026-08-06 · **Task:** t_98d0f1a2 · **Inputs:** `dealer_positioning_debate_notes.md` (winner: Synthesis — ΔOI-flow sign M1/M2 + dollar-gamma magnitude M4-half + NO-CALL gate E), `dealer_positioning_brief.md` (constraints §5)
**Scope guard honored:** vol replication method (Demeterfi-DDKZ 1999 fair-variance strike, `variance_swap_live.compute_fair_variance_strike` L136–L193) **untouched — zero-line diff** (see §4).

---

## 1. What was implemented (code diff summary)

### 1a. `dealer_positioning.py` — the new `oi_flow` sign model (M1/M2)
- `VALID_SIGN_MODELS` extended with `'oi_flow'` (4th model, selectable alongside v1/v2/v2.1).
- `_resolve_sign(right, strike, sign_model, otm_strikes, vol_surface_ref, oi_flow_map)` — new branch:
  `sign = −sign(ΔOI · direction)`, direction = +1 call / −1 put (same convention as `_dealer_sign`).
  Customers adding a leg → dealer takes the other side: call-add → **−1** (dealer short call), put-add → **+1** (dealer long put). Flow out flips the sign. **Zero/missing ΔOI → 0 (no contribution, never a forced fallback to Layer 1b's −1)**. Still gated by the shared OTM replicating-set keys (constraint §5.2 respected: keys only, weight magnitudes never reused).
- `_build_oi_flow_map(hist_oi_rows)` — pure ΔOI reducer: last two distinct dates present in the OI history → per-(strike,right) day-over-day OI change; <2 dates → `{}` (no flow → no contribution).
- `_fetch_oi_flow_map(td, ticker, exp)` — best-effort per-expiry network fetch (failure → `{}` → that expiry contributes no flow, never a crash).
- Magnitude: the flow sign is applied to the **existing dollar-gamma magnitude** (`γ·S·100·OI`, already computed) — M4's dollar-gamma half, under the flow sign (per debate §5 step 4). No change to any other sign model's math.

### 1b. `dealer_positioning.py` — NO-CALL honesty gate (E)
- `NO_CALL_FLOOR_FRAC` (default **0.05**, env `DEALER_NO_CALL_FLOOR_FRAC`) + `resolve_direction_call(result, floor_frac, floor)` → `'AMPLIFY' | 'DAMPEN' | 'NO_CALL'`.
- Dead-band: NO_CALL when `|net dollar gamma| < floor`; floor = absolute `$` (if given) else `floor_frac × gross book size`. **Floor scaled against the UNSIGNED gross book** (new `gross_gamma_exposure` accumulator) — a signed aggregate can collapse to 0 (e.g. no flow → all signs 0) and would silently disable the gate.
- `compute_dealer_positioning` / `run_dealer_positioning` take `no_call_floor` / `no_call_floor_frac` (floor=0 restores the historical bare `net_gamma>0` boundary).
- `DealerPositioningResult` gains `direction_call`, `no_call_floor`, `gross_gamma_exposure`; `print_report` and the heatmap header render NO-CALL instead of a forced AMPLIFY/DAMPEN.

### 1c. Threading (constraint §5.3)
- `volatility_suite.py` interactive prompt adds `(4) OI-Flow ΔOI`; headless `--pack` default **stays `vol_surface_replication`** (no silent behavior change) with explicit `DEALER_SIGN_MODEL=oi_flow` env opt-in for orchestrators.
- `dealer_positioning.py` CLI prompt + both `_SIGN_MODEL_LABELS` dicts updated.

### 1d. `backtest_stage3.py` + `shared/schemas.py` — v4 proxy (before/after gate)
- `DayRecord` gains `net_gamma_v4` / `regime_v4`; new `_net_gamma_v4_oi_flow_live()` — the **live wiring's exact math** (flow-sign × level-OI magnitude, OTM gated) as a 5th proxy, so the PRIMARY regression tests the code path the live charts run (v3 differs: |ΔOI| magnitude).
- `BacktestResult` v4 fields + report column + `backtest_summary.json` keys; `BacktestSummary` TypedDict extended.

**Diff stats (my change only):** `dealer_positioning.py` +255/−~20, `backtest_stage3.py` +95/−~8, `volatility_suite.py` +15/−2 (mine; the file also carries a pre-existing expiry-from-context change from an earlier task), `shared/schemas.py` +22. **`variance_swap_live.py`, `replication_reference.py`, `vol_surface_reference.py`: 0 lines changed.**

---

## 2. Before/after metrics

### 2a. Real-data backtest — SPY 20261030 (90d), 67 usable days, 5d forward window, n=62
Run: `python backtest_stage3.py SPY 90` (ThetaData live, 2026-08-06).

```
                         v1 (oi_heuristic)    v2 (vol_surface_replication)         v3 (oi-change flow)           v4 (live oi_flow)
long-gamma days                         61                               5                          30                          22
short-gamma days                         1                              57                          32                          40
mean vol | long                     0.1285                          0.1266                      0.1251                      0.1332
mean vol | short                    0.0892                          0.1280                      0.1305                      0.1250
short - long                           nan                          0.0014                      0.0054                     -0.0082
t-stat                                 nan                           0.119                       0.408                      -0.574
p-value                                nan                          0.9077                      0.6851                      0.5696

PRIMARY TEST — regress fwd realized vol on continuous net-gamma, controlling for ATM IV + TTE:
                                   v1 coef                         v2 coef                     v3 coef                     v4 coef
coef                                0.0001                         -0.0005                      0.0002                      0.0001
t                                    0.498                          -0.906                       0.268                       1.178
ols p                               0.6206                          0.3689                      0.7895                      0.2437
perm p                              0.5507                          0.3523                      0.7926                      0.2329
delta-OI coef (M2)                  0.0000                          0.2529
```

**Read (matches the winning narrative exactly):**
- **Degeneracy fixed (constraint §5.5):** v1 61/1 and v2 5/57 are untestable splits; **v4 (live oi_flow) 22L/40S = 33%/60% each side** — balanced, same as v3's 30L/32S. The flow sign fixes the classifier.
- **Prediction remains null at the daily horizon (as documented, NOT a failure):** v2 coef −0.0005 (perm p 0.352), v4 coef +0.0001 (perm p 0.233), v3 +0.0002 (0.793), delta-OI ~0.0000 (0.253). This replicates the spec's pooled-panel null on live single-expiry data — the identification problem is unchanged; the winning approach claims *classification + decision hygiene* now and re-targets *prediction* to the intraday/trade-level track (§6).
- v4's perm p 0.233 is the best of the flow models but nowhere near significant — consistent with "do not re-run the panel; the honest levers are trade-level/intraday."

### 2b. Live snapshot comparison — SPY, 15 expiries (≤60d), 3,562 records, spot $769.77
`python tests/_live_direction_comparison.py SPY 60` (same snapshot through both sign models + gate on/off):

| metric | BEFORE (vol_surface_replication) | AFTER (oi_flow) | AFTER, gate off |
|---|---|---|---|
| direction call | AMPLIFY | **AMPLIFY** | AMPLIFY |
| net dollar gamma | −$2.689B | −$265.2M | −$265.5M |
| net gamma | −3.49e4 | −3.45e3 | −3.45e3 |
| gross book ($, unsigned) | $4.002B | $4.002B | $4.002B |
| NO-CALL floor ($, 5% gross) | $200.1M | $200.1M | 0 (disabled) |
| gamma-flip level | $774.67 | $768.77 | $768.77 |

**Read:**
- Both models agree SPY is dealer-short (AMPLIFY) today — no direction conflict on this snapshot.
- The flow sign **narrows the net exposure ~10×** (only legs with actual day-over-day flow count), and pushes the gamma-flip level from $774.67 to $768.77 — *into* the current spot range, i.e. the flow book is near-balanced. `|net DG|` ($265M) sits only ~1.3× the NO-CALL floor ($200M): a modest flow shift flips this snapshot to NO_CALL — exactly the flip-flop suppression the gate exists for.
- Gate-off on identical evidence would still print AMPLIFY; gate-on prints AMPLIFY only because $265M > $200M. (On the zero-evidence case — see §5 — gate-off prints AMPLIFY on a 0 net while gate-on correctly prints NO_CALL.)

---

## 3. Vol-replication preservation — evidence

1. **Zero-line diff** on the entire replication chain: `variance_swap_live.py`, `replication_reference.py`, `vol_surface_reference.py` are untouched (`git diff` empty). The fair-variance formula, the OTM mid-curve fetch, `_build_weights`/`_otm_leg_weights` — byte-for-byte identical. The change is confined to dealer-direction logic; by construction (one-directional dependency, briefing §5.1) it cannot alter `compute_fair_variance_strike` output.
2. **Snapshot invariance test** (`tests/test_dealer_positioning_oi_flow.py::test_fair_variance_strike_snapshot_unchanged`): `compute_fair_variance_strike` on a fixed deterministic chain must equal the **pre-change** values captured 2026-08-05 from the untouched implementation (`tests/_capture_fairvar_snapshot.py`): fair var 0.1407233064, fair vol 37.5131%, ATM IV 22.0%, convexity premium 15.5131%, 21 strikes. If anyone ever edits the DDKZ path, this test fails.
3. **Import-graph test**: `variance_swap_live` does not import any dealer module.
4. **Paper-validation suite untouched & green**: `tests/test_variance_swap_replication.py` (Demeterfi Fig. 3) passes unchanged.
5. **Full suite green**: **356 passed, 5 skipped** (baseline 337 passed + 19 new; 0 regressions).

---

## 4. Data limitation discovered & fixed (best-possible-validation clause)

The debate's Phase-1 spec said "ΔOI fetch: `option_bulk_hist_oi` (yesterday vs. prior day, per expiry)". **Live probing on 2026-08-06 showed `option_bulk_hist_oi` cannot do that**: it wraps the per-contract `hist/option/open_interest` route, which returns a **start-date snapshot** — every contract's rows were stamped with the range's first date (`20260723`), so day-over-day change is unmeasurable (and the fetch cost ~300–600 requests/expiry for nothing). First live `oi_flow` run therefore produced an empty flow map → net gamma 0 → honest NO_CALL (the gate correctly refused to call on zero evidence; gate-off would have printed AMPLIFY on nothing).

**Fix:** `_fetch_oi_flow_map` now uses **`option_bulk_hist_oi_by_day`** — whole-chain per calendar day, the same route the stage-3 backtest runs on (verified working: 117 weekdays fetched) — ~7 requests/expiry, and returns each weekday's full chain so ΔOI = last two trading days' difference. After the fix, all 15 SPY expiries produced 160–386 ΔOI legs each and the live direction resolved. Documented in `_fetch_oi_flow_map`'s docstring.

---

## 5. Honesty-gate limitation (documented per task clause)

The debate proposed the NO-CALL floor as "0.5× the 90-day rolling median of net dollar gamma". A single live snapshot has no 90-day history, so the MVP implements the floor as a **fraction of unsigned gross book size** (default 5%, configurable) — the same hygiene computed from data available in one snapshot: a book whose net is small relative to its gross has no reliable direction. The rolling-median form remains available in the backtest domain (multi-day series) and is a drop-in parameter (`floor=` absolute `$`, or `floor_frac=`); switching to a rolling-median floor for the live path would require a historical net-DG series (Phase-3-adjacent work). A floor of 0 disables the gate and restores the historical boundary.

---

## 6. Follow-up (explicitly separated, NOT this task's scope)

Per the winning narrative: forward-vol **prediction** at the daily horizon is empirically null for all OI-derived proxies; the remaining honest lever is **trade-level flow / intraday horizon** (debate §5 Phase 3). The spec forbids re-running the daily pooled panel. This MVP delivers classification + decision hygiene only; prediction is the separated research track.

---

## 7. Reproduction

```bash
# tests (network-free, ~5s)
cd /home/bottl/Financial_Development/Vol_Suite
/home/bottl/Financial_Development/Financial_Dev_Env/bin/python -m pytest tests/test_dealer_positioning_oi_flow.py -v
/home/bottl/Financial_Development/Financial_Dev_Env/bin/python -m pytest tests/ -q          # full suite

# real-data before/after backtest (ThetaData, ~6-8 min)
python backtest_stage3.py SPY 90

# live snapshot before/after direction comparison (ThetaData, ~1-2 min)
python tests/_live_direction_comparison.py SPY 60 outputs/t98d0f1a2_test/spy_live_comparison.json
```

Artifacts: `outputs/t98d0f1a2_test/spy_backtest_90d.log`, `outputs/t98d0f1a2_test/spy_live_comparison.json`.
