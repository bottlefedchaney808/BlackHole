# KARSAN HANDOFF — Dealer Positioning / SVI / Cross-Sectional Testing (2026-08-13)

**Author:** Hermes Agent (default profile, Windows-native FinancialDevelopment), 2026-08-13
**Purpose:** Self-contained context for continuing the dealer-positioning/SVI/cross-sectional
work on another system or session WITHOUT re-deriving the findings or re-deciding canon.
**Critical:** everything described here that is uncommitted must be committed before switching
systems, or the continuation must read from THIS same working tree/filesystem. Losing work
again is the exact failure this whole investigation exists to prevent.

---

## 0. Provenance — do NOT conflate the three threads

1. **`20260813` Claude Code handoff** (the folder this file lives in): authored by Claude Code
   (Sonnet 5) on the Windows repo. It audited the live dealer wiring (CARL Mode B) + ran a
   5-person debate (seed-vs-flow, testing methodology), found the "V5 direction / sabr_deviation /
   150d accumulation" model was NEVER merged into this repo and the two-vanna bug was still live,
   then implemented real accumulation wiring + the two-vanna fix + `backtest_accumulation_falsifier.py`
   + `seed_data_loader/maker`. **That is Claude's work.**
2. **`20260811_011529_029dc3` Dealer Hedge Brainstorm** (Hermes, WSL): the run BEFORE the handoff,
   on the WSL tree. Produced the vanna convention pin, the M2 joint battery, `svi_rp.py`,
   `seed_flip_compare.py`, `recompute_vanna_from_eod.py`, `vanna_transform_pin.py`, the
   `VOL_SURFACE_FITTER=svi|sabr|quadratic` toggle, and the 08-11 WSL `vol_surface_reference.py`
   (SVI-based sign resolver). Its work lives in the extracted `_extracted/handoff_20260812/` package.
   **This is the session whose work was brought in for testing (not canonized).**
3. **This Hermes session (default profile, Windows):** brought the 08-11 work in as testable arms,
   fixed the SVI flat-smile, and is wiring the fitter toggle (in progress at time of writing).

The handoff was made by Claude; session `20260811` is the run prior to the one that made it.

---

## 1. Canon decision status (2026-08-13) — NOTHING CANONICAL YET

Jason's explicit directive (2026-08-13): **"bring these in for testing don't make anything canon,
lets figure out what we have and make something good."** And on SVI: **"Its B but I want to make
the changes to SVI you mentioned earlier first... wire it in test it, always test against previous
versions of the model to see growth."**

So: **Option B** (flip the sign-source fitter default to SVI), but ONLY AFTER fixing the SVI
flat-smile first, then wire it in and test against the SABR version to measure growth. Since we
are starting from scratch, the improved-SVI test results will NOT match the WSL +464,578 exactly
— that is expected and fine.

The OLD canonical claim (`CANONICAL_SIGN_MODEL='direction'`, `VALID_SIGN_MODELS=('direction',)`)
was NEVER true of THIS repo — the live `dealer_positioning.py:266` still has
`VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')`. Do not trust the
skill's canonical claim for this tree; verify against source.

---

## 2. Cross-sectional falsifier — the real-data verdicts

`Vol_Suite/backtest_accumulation_falsifier.py` (CLI: `--cross`, `--svimag`, `--pooled`).

### Sign axis = BASE (Jason's call, 2026-08-13) — BUT this was a SABR-sign-source artifact
Under the OLD SABR sign source: real 12-ticker cached data gave **every ticker accumulates
SHORT (-1.0)** → sign axis degenerate → labeled **`BASE`** (resting regime, not signal).

**Under the NEW SVI sign source (2026-08-13, re-run by coder): the sign axis is NO LONGER
degenerate.** Accumulated signs = 8 SHORT / 4 LONG (GOOGL, NFLX, SPY, TSLA flip LONG under SVI).
So the "all-short BASE" was a SABR-reference-curve artifact, not a property of the accumulated
book. The cross-sectional test now runs on real between-ticker variance — but is still
**REDUNDANT / not significant**: corr(acc, rv) −0.16 (t −0.51, p 0.62), mean rv SHORT 0.368 vs
LONG 0.321 (in the *expected* dealer-short→higher-vol direction, but not significant). Takeaway:
the sign source changes WHICH tickers read short/long, so any cross-sectional/regime claim must be
re-checked under the active fitter. The result-cache now persists these (outputs/falsifier_cache/).

### SVI-magnitude axis = INCONCLUSIVE (Jason's SVI-magnitude idea, tested)
Because sign is degenerate, use SVI cheap/rich marking as a MAGNITUDE signal.
`_run_svi_magnitude_cross_sectional_from_histories` / `--svimag`: per-ticker OI-weighted
|IV − ref| (smile distortion) + SVI net seed, vs each ticker's realized-vol level.
Real result: **INCONCLUSIVE** — corr +0.10, t-stat 0.32, perm p 0.27. SVI magnitude does NOT
significantly sort realized vol on this sample. Tests: `tests/test_svi_magnitude_falsifier.py` (5).

### Day-level pooled = REDUNDANT (design artifact)
The pooled ticker-FE test mechanically zeroes a within-ticker-constant accumulated series.
`PooledFalsifierResult.constant_signal_tickers` surfaces this. Corroborates the "book settles
into one persistent regime" narrative from a third angle.

---

## 3. Data density — the `eod_greeks` route and the "find the right expiry" lesson (2026-08-13)

**Why SPY/QQQ were thin:** the cached `seed_data_*.json` for SPY/QQQ covered only ~20-60 of
150 days because the option-chain history for the far-dated `20261120` expiry only extends as far
back as that expiry was LISTED. The greeks route (`all_greeks`) is sparse (2 dates/40d), so we
compute greeks ourselves. But there is an **untapped dense route that returns greeks directly**:

- **`bulk_hist/option/eod_greeks/{root}/{exp}`** — whole-chain EOD **OHLC + IV + full greeks
  (gamma, vanna, everything)** honoring the date range, ~1 request per expiry, 17:15 ET close.
  Verified working against the proxy (200, dense rows, includes `implied_vol`/`gamma`/`vanna`).
  NOT yet wrapped in `shared/thetadata.py` — probe-verified via `_get_with_retry` directly.
  This REPLACES the local-IV-inversion + sparse-greeks machinery for dense historical greeks.
- **The real constraint is EXPIRY LISTING AGE, not the greek computation.** The route cannot
  manufacture history for an expiry that didn't exist yet. To get full 150d data, pick an expiry
  that has been listed ≥150 trading days (a far-dated/LEAPS expiry), NOT the nearest 0.25-year one.
  `seed_data_maker.py` currently resolves `expiry_selector(..., 0.25)` → picks a ~3-month-out
  expiry that was only just listed → thin. **Fix: resolve to the furthest-listed / longest-history
  expiry for the lookback window.**

**Re-pull result (2026-08-13):** SPY via compute-greeks re-pull → **63 usable days** (was 55-62);
QQQ stuck at 22 (20261120 expiry genuinely only ~23 days old). To get QQQ/SPY to 150d, re-pull with
a far-dated expiry using the `eod_greeks` route.

### Root cause (measured on real SPY, date 20260810, spot 773.03, T 0.279)
The exact 3-observable SSVI construction (`svi_rp.calibrate_ssvi`) is **structurally
over-constrained on a steep equity put skew**:
- Measured ATM skew `psi_t = -0.394`, put-wing `p_t = -0.485`.
- SSVI needs `rho·phi = 4·psi_t/σ_atm = -10.2`, but the butterfly-arbitrage clamp caps
  `phi ≤ 4/(1+|rho|) = 2.0`.
- Contradiction → `rho` saturates at `-0.999`, `phi` pins at `2.0`, fit collapses to the FLATTEST
  arbitrage-free curve → badly underfits the steep market put wing.

| K | mktIV | old (flat) SSVI | robust SVI |
|---|---|---|---|
| 300 | 0.651 | 0.264 | **0.642** |
| 500 | 0.451 | 0.213 | **0.401** |
| 700 | 0.220 | 0.170 | **0.223** |
| 773 (ATM) | 0.152 | 0.156 | 0.188 |

The robust fit tracks the wing; note it runs slightly HIGH at ATM (0.188 vs 0.152) because the
OI-weighted least-squares is dominated by the huge far-OTM put IVs — acceptable for a sign source
that must track skew shape.

### The fix (implemented, GREEN)
`svi_rp.py` gained `calibrate_svi` — a full 5-param SVI (`a,b,rho,m,sigma`) least-squares fit to
the chain's total implied variance, OI-weighted, with a **butterfly-arbitrage penalty**
(second-finite-difference of total variance must be ≥ 0), falling back to `calibrate_ssvi` on
non-convergence. `SviRpReference` gained an optional `svi_params` field; when present,
`sigma_ref`/`sigma_ref_batch` use the full-SVI curve (`svi_w`). Tests:
`tests/test_svi_robust_fit.py` (3: steep-wing tracking, sane shape, mark consistency).

## 4. The fitter toggle — IN PROGRESS at time of writing

Goal (Option B): add `VOL_SURFACE_FITTER=svi|sabr|quadratic` to `vol_surface_reference.py` with
**SVI default** (reconciling the sign-source discrepancy).

### Sign-source discrepancy (the reason this matters)
Ported `DEALER_VANNA_FLOW` gives SPY **−128,331 SHORT** in this tree vs the WSL-recorded
**+464,578 LONG**, because:
- This tree's `vol_surface_reference.py` = **SABR-only** (no fitter toggle, no SVI refs).
- The 08-11 WSL tree used `VOL_SURFACE_FITTER=svi`.
- Both use the SAME `resolve_vol_surface_sign` (`-1 if dev>0 else +1` where
  `dev = IV_market − IV_reference`) — the ONLY difference is the reference curve (SABR-Hagan vs
  SSVI). So "sabr_deviation" is literally the sign source in this tree; "svi deviation" in the WSL.

### What's DONE
- `vol_surface_reference.py`: added `_VALID_FITTERS`, `_fitter()` (default 'svi'),
  `fit_svi_reference(chain_iv, forward, T)` using robust `svi_rp.calibrate_svi`,
  and **rewired `compute_vol_surface_reference` to dispatch on `VOL_SURFACE_FITTER`**
  (svi → sabr → quadratic fall-through). Committed `f98dfb4`.
- `svi_rp.py`: `calibrate_svi` + `SviRpReference.svi_params`.
- Tests: `test_default_fitter_is_svi` (pins Option B), `test_svi_falls_back_to_sabr`,
  SABR-path tests pin `VOL_SURFACE_FITTER=sabr` explicitly.

### What's DONE (fitter toggle fully wired)
The fitter-selection rewrite (previously the one remaining piece) is **complete and
committed** (`f98dfb4`). `compute_vol_surface_reference` now:
1. `fitter = _fitter()` (default 'svi') when `forward/T` given.
2. If `fitter == 'svi'`: `params = fit_svi_reference(...)`; if `params['_ref']` present,
   builds the reference via `ref_obj.sigma_ref(k)` (fitter='svi'), else falls through.
3. If SVI didn't produce and fitter in ('svi','sabr'): `params = fit_sabr_reference(...)`,
   builds via `sabr_vol_hagan` (fitter='sabr').
4. Else falls through to the existing quadratic path.

vol_surface_reference + SVI focused: 25 passed.

### Scanner wired to SVI (2026-08-13, `2ea6f70`) — scanner and 4-panel now agree
`options_chain_scanner.fit_svi_smile` (robust `svi_rp.calibrate_svi`) replaced the
quadratic smile fit in `scan_chain`, keeping the SAME `fit_iv`/`iv_residual_pts`/
`is_edge`/`edge_kind` contract; quadratic is the fallback. `ScanResult` gained
`svi_params`. Demonstrated real effect: on a steep-skew chain with one deliberately
cheapened strike, the SVI fit correctly isolates the cheap strike (−8.8 pts) AND
surfaces a rich band (83-88) the quadratic flattened over entirely. Full suite
now 464 passed / 5 skipped.

---

## 5. Commits already landed (all safe, full suite green)

| Commit | What |
|---|---|
| `a4cee72` | 08-13 Claude handoff work: accumulation wired into live path, two-vanna fix, falsifier harness, seed_data loader/maker, dossier |
| `dd98e1f` | Cross-sectional falsifier (sign axis) + 6 tests |
| `cf795d2` | SVI-RP reusable module (`svi_rp.py`) + 6 known-answer tests |
| `9fb220c` | 08-11 arms brought in as testable: vanna/seed modes, vannaflow, seed_flip_compare(+offline), recompute_vanna_from_eod, vanna_transform_pin, runners. **Nothing canonical.** |
| `9099163` | Handoff doc recording BASE + SVI-magnitude INCONCLUSIVE + sign-source discrepancy |

Full Vol_Suite suite before the SVI-fit work: **457 passed / 5 skipped**. After adding the robust
SVI fit + toggle (uncommitted at writing): SVI tests 9 passed (6 known-answer + 3 robust).

---

## 6. Environment / run conventions (Windows-native)

- **Repo:** `C:\Users\bottl\FinancialDevelopment` (the ONLY editable tree; the MIGRATED tree is
  experimental). WSL trees under `C:\Users\bottl\WSL-Ubuntu-backup\` are recovery-reference only.
- **Venv python:** `.venv\Scripts\python.exe`. **ALWAYS** `env -u PYTHONPATH -u VIRTUAL_ENV` before
  invoking it (Hermes-venv leak → `pydantic_core` / PIL `_imaging` breakage).
- **Suite tests:** `cd Vol_Suite && env -u PYTHONPATH -u VIRTUAL_ENV ../.venv/Scripts/python.exe -m pytest tests -q`
  (bare root `pytest`/`hermes verify` FAILS collection — cross-suite collision on `config.py`).
- **Cached data:** `Vol_Suite/docs/Dealer posistioning notes/_extracted/handoff_20260812/seed_data/`
  (12 tickers × 150d, offline — no ThetaData proxy needed for falsifier/seed-compare work).
- **Proxy rules (ThetaData/PotatoHedge):** `option_bulk_hist_oi_by_day` is MOST fragile — sequential,
  3× retry, never 2 concurrent tickers. EOD route handles staged pairs at `THETADATA_HIST_CONCURRENCY=4-6`;
  4×4 opens the breaker (~60s cooldown). `oi=0` after genuine errors = garbage.

---

## 7. Operational rules carried forward

1. No result computed in an environment other than this repo's git tree gets called "the winner"
   until reproduced in-tree with a commit hash. (This caused the original loss.)
2. Every chart/report stating a sign model must source its label from
   `dealer_positioning.sign_model_render_label(result)` — never a hardcoded string.
3. Any change to sign conventions (dealer OI sign, vanna sign, `VALID_SIGN_MODELS`, the fitter
   default) needs explicit review — this codebase has flipped a sign convention at least 3×.
4. Test against previous model versions to measure growth (Jason's standing expectation).
5. Never 2+ concurrent whole-chain ThetaData fan-outs.
6. `env -u PYTHONPATH` before the venv python (Hermes-venv leak).

---

## 8. Next steps (in priority order)

1. **Finish the fitter toggle** in `compute_vol_surface_reference` (the mid-edit block above), then
   `python -m pytest tests/test_vol_surface_reference.py` + full suite.
2. **Re-run vannaflow under SVI** (`VOL_SURFACE_FITTER=svi`, default) vs `=sabr` on the 12-ticker
   cached data via `seed_flip_compare_offline.py` — reconcile against WSL +464,578 (SPY) and measure
   growth/delta from SABR. This is the "test against previous versions" step.
3. **Re-run `--cross` and `--svimag`** under the SVI sign source to see if the cross-sectional /
   SVI-magnitude verdicts change (they were measured under the SABR sign source).
4. **Commit** the SVI fit + fitter toggle + any test additions.
5. Update the `dealer-positioning-model` skill to reflect: canon is UNsettled in this tree, the
   flat-smile fix, and the fitter toggle.
