# Dealer Positioning — Master Spec & Work Record (updated 2026-08-11)

> **Status: CURRENT.** This is the consolidated, up-to-date spec for the
> `Vol_Suite/dealer_positioning.py` model and every consumer of its sign/greek
> conventions, covering the work done 2026-08-04 → 2026-08-11: the debates, the
> notes, the implementation history, the backtest evidence, and fresh test
> results. Where older documents in this package disagree with this file, this
> file wins (it is source-verified; see §2).
>
> Companion files in this package:
> - `TEST_RESULTS_20260811.md` — fresh test output (focused dealer battery, full Vol_Suite, Direction package)
> - `docs/` — the complete historical record (briefs, debate notes/reports, plans, specs)
> - `skill_references/` — the authoritative evidence trails maintained in the dealer-positioning-model skill

---

## 1. The canonical model — SINGLE, LOCKED (2026-08-09). Do NOT flip-flop.

Verified against live source on 2026-08-11 (`Vol_Suite/dealer_positioning.py`):

| Contract | Value | Source location |
|---|---|---|
| `CANONICAL_SIGN_MODEL` | `'direction'` (V5 Direction 5-signal) | L294 |
| `VALID_SIGN_MODELS` | `(CANONICAL_SIGN_MODEL,)` — ONE model, no alternates | L295 |
| Per-expiry mode | `DEALER_DIRECTION_PER_EXPIRY = 'sabr_deviation'` | L496 |
| Lookback / accumulation | 150-day accumulation ON (the accumulated signed-OI book drives ALL four greeks: gamma, delta, vanna, charm) | `compute_dealer_positioning` |
| Evidence gate | NO_CALL gate shared (`resolve_direction_call`) | — |
| Model label (single string everywhere) | `"V5 Direction (sabr_deviation, 150d accumulation)"` | L503 |
| Sign source precedence | Per-expiry SABR deviation is PRIMARY; run-level direction/whale bias is the fallback for unreadable fits | `_resolve_sign` / `compute_expiry_sign_map` |
| **The gate** | `_compute_sabr_deviation_for_expiry` returns `0.0` when `fallback_bias == 0` (no whale/direction read → no trade). `DEALER_DIRECTION_GATE=0` disables it — BACKTEST REPRODUCTION ONLY, never live | L531 |

**Deleted (do not re-create):** all alternate sign models (`oi_heuristic`,
`replication`, `vol_surface_replication`, `oi_flow`) and all alternate per-expiry
modes (`uniform`, `gamma_weighted`, `whale_decomposed`) are gone from the active
tree, including their helpers, the `_VALID_PER_EXPIRY_MODES` set, the interactive
sign-model menu, and the `DEALER_SIGN_MODEL` env override (only
`DEALER_DIRECTION_MIN_SCORE` remains forwardable). `archive/dealer-positioning-alternates/`
was deleted (git history only).

**Why the gate is load-bearing (re-learned 2026-08-09 night):** the 5f9833c
change removed the gate ("SABR fires on its own even when the run-level read is
neutral"). Wired into the backtest v5 leg, the ungated variant FLIPPED the v5
sign on the exact decision panel (coef **+0.0010 / t +4.503 / SPY short 0/46**)
vs. the validated gated model (−0.0003 / −1.919 / 39/46). Jason's order:
**"wire in the fucking gate."** A zeroed chart (e.g. TGB) is a whale-read
payload problem, not a gate problem — fix the whale read, never ungate the
decomposition.

## 2. Source-verified state (2026-08-11 greps)

- `grep CANONICAL_SIGN_MODEL` → `'direction'`; `VALID_SIGN_MODELS = (CANONICAL_SIGN_MODEL,)`
- `DEALER_DIRECTION_PER_EXPIRY = 'sabr_deviation'`; gate present at L531
  (`if not fallback_bias and os.environ.get("DEALER_DIRECTION_GATE", "1") != "0": return 0.0`)
- `options_chain_scanner.py`: `compute_vanna_positioning(dealer_result, spot)`
  (L391) renders `dealer_result.vanna_shares_by_strike` verbatim; `scan_chain`
  computes the solver itself when `dealer_result is None` (L488); `scan_all_expiries`
  computes the solver ONCE per ticker (L1021) — the 150d surface is ticker-level.
- `run_dealer_positioning` returns `(files, interp, result, greek_result)` (L1762);
  `volatility_suite.py` threads `greek_result` into `run_chain_scanner(dealer_result=...)`.
- `DealerPositioningResult` carries `vanna_call_shares` / `vanna_put_shares`
  (L137-138), scaled `CONTRACT_MULTIPLIER * VANNA_PP_SCALE`, tracked in the
  single-day loop AND the accumulation rebuild (L946-947).
- `Direction/whale_scanner.py`: `WHALE_THRESHOLD_BPS` + `threshold_bps` param
  (bps of one-contract ATM notional; 0/unset = legacy $25K).

**Caution — stale docs exist on purpose.** The historical docs in this package
carry SUPERSEDED banners and describe deleted models (`oi_flow`,
`vol_surface_replication` default, `DEALER_SIGN_MODEL`). They are the record of
the work, not the current model. `docs/superpowers/reports/2026-08-09-sabr-deviation-default-evidence.md`
is an OUTDATED intermediate report (still names `vol_surface_replication` as the
primary default) — it predates the 08-09 lock; do not read it as current.

## 3. Timeline of the work

| Date | Event | Artifact |
|---|---|---|
| 08-04 | Accuracy spec M1–M6 written after CARL R1; pooled panel (n=428) shows ALL daily-horizon OI/IV proxies null; M1+M2 implemented on the backtest side | `docs/DEALER_METHOD_V3_SPEC.md` |
| 08-05 | Code briefing (architecture, formulas, constraints); five-voice structured debate (Carl unavailable); direction question framed | `docs/dealer_positioning_brief.md`, `docs/dealer_positioning_debate_notes.md`, `docs/PLAN_dealer_v2_to_v3_migration.md` |
| 08-06 | `oi_flow` sign model implemented (ΔOI flow + dollar-gamma magnitude + NO_CALL gate); 356 tests green; vol replication proven untouched; **multi-agent CARL debate** (R1 5-panelist → R2 cross-exam → R3 judge) judged **V5 direction @ min_score=1 (whale-only)** the winner; E1 pooled re-run permitted by Jason (n=494): v5 is the only negative-coef model (−0.0002, block perm p 0.0825); M2 inverted-significant (0.0245); E4 `WHALE_THRESHOLD_BPS` implemented + calibrated (3000 bps recommended) | `docs/dealer_positioning_debate_report.md`, `docs/dealer_positioning_test_results.md`, `docs/CARL_reference_dealer_direction.md`, `docs/PLAN_dealer_direction_v5_winner.md`, `skill_references/dealer-direction-debate-20260806.md` |
| 08-08 | v3 impl plan (SABR all-strikes + 150d accumulation + per-expiry A/B/C); per-expiry backtest: **sabr_deviation wins** (coef −0.0003, t −1.919, full perm p 0.0135, block 0.0680); **8-agent deep debate**: Track A status quo upheld (binary-sign ≈ DDKZ-weighted within 6%, 100% directional agreement); Track B weight decay = expected & correct (calls decay 68% at 30% OTM, puts GROW 14.7×); R1-REF correlation sign error caught by R2 (+0.766 claimed, actual −0.4177); 3 shadow tests specified | `docs/debate-preflight-20260808.md`, `docs/debate-evidence-kit-20260808.md`, `docs/r1-ref-empirical-report-20260808.md`, `docs/r2-cross-examination-20260808.md`, `docs/debate-final-spec-20260808.md`, `docs/per-expiry-direction-backtest-20260808.md` |
| 08-09 02:16 | `9366d1a` — default per-expiry direction → SABR deviation | git |
| 08-09 16:10 | `eb8b5b6` — **lock**: sign model fixed to V5 Direction everywhere; alternates deleted | git |
| 08-09 16:49 | `5f9833c` — SABR deviation is the sign source; accumulated book feeds all greeks — **but removed the fallback_bias gate** (silently shipped an unbacktested model) | git |
| 08-09 evening | Backtest reproducibility investigation: decision runs were made from an UNCOMMITTED tree; committed backtest never threaded per-expiry SABR; live-wired reproduction FLIPPED the sign; Jason: "wire in the fucking gate" | `skill_references/backtest-v5-sabr-reproducibility-20260809.md` |
| 08-09 night | Gate restored; verified reproduction found `DEALER_DIRECTION_MIN_SCORE=1` + `uniform` mode reproduces the recorded decision signature (coef −0.0002, t −1.547, SPY short 35/46); full Vol_Suite 378 passed / 5 skipped | git `89d2b28` |
| 08-09/10 | **Two-vanna divergence** (user: "still showing two different vanna values… who knows what model is running you guys have fucked this up 3 times in a row"): scanner computed its OWN vanna on a single expiry with today's OI while the 4-panel plotted the 150d accumulated book — same label, different data | `skill_references/two-vanna-divergence-20260809.md` |
| 08-10 | **FINAL two-vanna fix**: scanner calls the SAME solver (`compute_dealer_positioning(ticker, target_years, max_days=150)`), `compute_vanna_positioning(dealer_result, spot)` renders engine numbers verbatim; `DealerPositioningResult` gains `vanna_call_shares`/`vanna_put_shares`; parity tests added | git `89d2b28` (with the gate restore) |
| 08-11 | This package + fresh test run | — |

## 4. The debates — condensed record

### 4.1 08-05 five-voice debate (structured fallback — Carl unavailable)
Question: *how should the dealer model determine direction more accurately, without touching the Demeterfi-DDKZ vol replication?* Five voices, stress-tested against the all-null empirical referee (n=428 pooled panel):
- **V1 FLOW-FIRST** (ΔOI is direction, level is noise) — breaks endogeneity at the root; classifier proven (30L/31S) but predictor null.
- **V2 SURFACE-TUNER** (keep rich/cheap, fix mechanics) — **DROPPED**: re-fits the confound; rich-IV→dealer-short is endogenous to the vol regime.
- **V3 ECONOMICS-FIRST** (dollar gamma + strip weights + term structure) — **DROPPED as a direction fix**: M5-style pooling already refuted by the panel; weight-magnitude reuse risks the shared OTM gate.
- **V4 HONESTY-GATE** (no-call layer + re-target research) — **DROPPED standalone**: gating a null signal is hygiene, not accuracy; adopted as the winner's output layer.
- **V5 SYNTHESIS** (flow direction + economic magnitude + honesty gate) — **WON**: the only structure consistent with every empirical fact; claims classification + decision hygiene, explicitly re-targets prediction to the intraday/trade-level track.

Winner's narrative: *Direction is a flow question, not a surface question.*

### 4.2 08-06 multi-agent CARL debate (judged winner → V5 direction)
- R1 five-panelist debate → R2 cross-examination → R3 judgment, weaker-than-unanimous boundary, dissent recorded.
- Corrected facts established: **6 significant cells in the 5×6 matrix, 2 right / 4 wrong** (referee's "4 cells 2-2" was arithmetically wrong); AAPL negative at BOTH independent gate configs vs AMD positive at BOTH → **name-dependence, not noise**; whale premium = volume × option close × 100 (NOT spot — ~20-30× less broken than claimed); both pooled v5 runs to date unusable (dead column / truncation).
- **Judged winner:** `sign_model='direction'` at `DEALER_DIRECTION_MIN_SCORE=1` (whale-only leg) × NO_CALL gate × classification-only labeling, with AMD/SPY per-name suppression (registry). Ranking: v5@1 > v4 > v5@3 > v2.1 > v1/v2.
- **E1 pooled re-run (Jason-permitted, n=494, 5 names, ticker-FE, 2000 perms):** v5 is the ONLY negative-coef model (−0.0002, t −1.13, block perm p **0.0825** — suggestive, NOT <0.05); AAPL survives pooling; M2 is the ONLY significant pooled relation (block p 0.0245) and it is **INVERTED (positive)** — the strongest significant dealer-flow signal points AGAINST the short-gamma→amplify premise. Consequence: classification-only labeling is mandatory.
- **E4 (approved + implemented):** `WHALE_THRESHOLD_BPS` — bps of one-contract ATM notional; calibration sweep: **3000 bps** is the strongest (AAPL perm p 0.0015; SPY noise suppressed 0.072→0.265).

### 4.3 08-08 8-agent deep debate
- **Track A (post-whale improvements): status quo UPHELD.** Decisive R2 emergent finding: binary-sign aggregation reproduces DDKZ-weighted within **6% magnitude and 100% directional agreement** across 4 scenarios. Weighted sign, continuous sign, multi-day accumulation, per-expiry bias: all no-ops or deferred. Confidence 0.87.
- **Track B (weight decay): weight-drop is expected and correct.** Three pillars: anti-correlation is structural (r = −0.4177 corrected; persists on dense chains at −0.787); the 3,018× scaling mismatch is a straw-man; design contract at `dealer_positioning.py:339-343` documents intentional discarding. |w| aggregation would suppress gamma-rich strikes 57-88% — actively harmful.
- **Key math:** DDKZ weights are ASYMMETRIC — call-side decays to 68% at 30% OTM (f''(K)=2/(TK²)→0), put-side GROWS 14.7× (f''(K)→∞ as K→0); BS gamma decay (500-3600×) overwhelms both.
- **Process fix:** R1-REF claimed call-wing r = +0.766; actual −0.4177 — a sign error caught by R2. Rule: R2 MUST independently recompute referee arithmetic in every future debate.
- **Shadow tests (diagnostics only, 30 trading days):** SHADOW-1 weighted-sign deviation monitor (`DEALER_SHADOW_WEIGHTED_SIGN=true`), SHADOW-2 0DTE contamination share, SHADOW-3 illiquid-chain SABR stability (`SABR_STABILITY_WARN=true`). Escalation ESC-1 (0DTE) open/blocking for new tickers; ESC-4 quarterly p=0.0825 stability check.

## 5. Backtest evidence — all key numbers in one place

### 5.1 The empirical referee (08-04/08-05 baseline — daily horizon, all null)
- Single expiry SPY 20261030 90d: v3 (ΔOI flow) balanced 30L/31S (v1 60/1, v2 5/56 degenerate); all models non-significant (best perm p = delta-OI 0.31).
- Pooled panel AMD/NVDA/TSLA/MU n=428, ticker-FE, 2000 perms: v1 +0.0000 (0.987), v2 −0.0000 (0.961), v3 +0.0005 (0.798), M2 0.0000 (0.565). MU v1 "hit" (p 0.019, degenerate 1/112 split) = false positive.
- **Conclusion:** identification problem (endogeneity), not power. Don't re-run the daily panel.

### 5.2 08-08 per-expiry direction comparison (SPY+AAPL, 30d, 5d fwd, 2000 perms, direction + accumulation)
| Mode | coef | t | full perm p | block perm p |
|---|---:|---:|---:|---:|
| uniform (baseline) | −0.0003 | −1.659 | 0.0335 | 0.1114 |
| gamma_weighted | −0.0003 | −1.606 | 0.0350 | 0.1064 |
| whale_decomposed | −0.0003 | −1.733 | 0.0260 | 0.0890 |
| **sabr_deviation (winner)** | **−0.0003** | **−1.919** | **0.0135** | **0.0680** |

Regime counts identical across all 4 modes (SPY 39/46, AAPL 46/46 short) — the decomposition scales magnitude, not sign.

### 5.3 Reproducibility saga (08-09) — what actually reproduced
| Run | v5 coef | t | SPY short | AAPL short |
|---|---:|---:|---:|---:|
| Recorded decision (sabr_deviation, gated) | −0.0003 | −1.919 | 39/46 | 46/46 |
| Ungated live-wired, SPY+AAPL 30d | **+0.0010** | **+4.503** | 0/46 | 4/46 |
| Gated (restored), SPY+AAPL 30d | +0.0009 | +3.472 | 24/46 | 19/46 |
| **Gated + MIN_SCORE=1 + uniform** (2026-08-09 19:39 verified reproduction) | **−0.0002** | **−1.547** | **35/46** | **43/46** |

- The recorded decision runs (08-08 19:58-20:10) were made from an **UNCOMMITTED working tree**; `_compute_sabr_deviation_for_expiry` entered git 6h later (9366d1a, 02:16). The committed backtest at decision time never threaded per-expiry SABR (`git diff f558ecd..HEAD -- Vol_Suite/backtest_stage3.py` is empty).
- The lost knob = `DEALER_DIRECTION_MIN_SCORE=1` (a **Direction-package whale gate** — 1/5 signals needed, whale included — NOT a backtest OI knob). Default min_score=3 fires 22/46 days; ms1 fires 43/46.
- **SABR-decomposition mode with the same knobs gives the WRONG sign** (+0.0011 / SPY 0/46) — decomposition is a magnitude modulator, NOT the sign source.
- Fixes landed: (1) gate restored in `_compute_sabr_deviation_for_expiry`; (2) backtest v5 leg respects `DEALER_DIRECTION_PER_EXPIRY` (uniform = run-level bias = recorded mode; sabr_deviation = live default); (3) corrupted scipy source force-reinstalled (mojibake files, `bad marshal data`).
- **Still not fully closed:** the gated model with default knobs does NOT reproduce the recorded row; the full accumulation seam in the backtest v5 leg doesn't exist yet (backtest uses per-day level OI; live accumulates). Sweep proposal (min_score × bps) not yet run as of 08-11. **Do not claim the model validated or invalidated on backtest rows until that lands.**

## 6. The two-vanna divergence — root cause and FINAL fix

Symptom (user, repeated 5×): scanner "Net Dealer Vanna by Strike" ≠ dealer 4-panel vanna, same model label, same run.

- Root cause (verified from source + UROY run 9b868325): the 08-09 fix only shared the SIGN RESOLVER. The scanner still computed the vanna SERIES itself (`dealer_sign * vanna * oi * 100 * 0.01`, single scanned expiry, TODAY's OI) while the 4-panel plotted the 150d whole-surface accumulated book. Same label, different data → different bars. (Also: the scanner's flat `call=+1/put=−1` path was numerically identical to direction bias=−1 — another reason "direction" could silently equal the flat heuristic.)
- Fix (user's words: "make the same call to the same solver… wiring two places to one module"):
  1. `scan_chain` → `compute_dealer_positioning(ticker, target_years, max_days=150)` (SAME solver + 150d window); attached as `ScanResult.dealer_result`.
  2. `compute_vanna_positioning(dealer_result, spot)` + `plot_scanner_charts` render `dealer_result.vanna_shares_by_strike` / `strike_grid` VERBATIM (raise if missing). No scanner-side vanna math exists anymore.
  3. `run_dealer_positioning` → `(files, interp, result, greek_result)`; `volatility_suite` threads `greek_result` so the suite fetches the 150d surface ONCE; both charts consume the exact same object.
  4. `DealerPositioningResult.vanna_call_shares` / `vanna_put_shares` (same `CONTRACT_MULTIPLIER * VANNA_PP_SCALE` scaling; single-day loop AND accumulation rebuild; split by right == 'C') — insight text uses engine numbers.
  5. `scan_all_expiries` computes the solver ONCE per ticker (per-expiry refetch = N whole-chain fan-outs → proxy saturation).
- Regression tests: `test_scanner_vanna_equals_dealer_engine_vanna` (scanner net == sum(engine series); call+put == net), `test_plot_scanner_charts_uses_dealer_engine_result` (raises without `dealer_result`). Standalone `run_chain_scanner` without `dealer_result` computes the solver itself — same call, same numbers.
- **Invariant:** a change to `dealer_positioning.py` that alters sign model, greek aggregation, or scaling MUST update `options_chain_scanner.py` in the same commit with a cross-file parity test.

## 7. Test results (fresh, 2026-08-11)

See `TEST_RESULTS_20260811.md` for the full verbatim output. Summary:

- Direction package (`Direction/tests`): **75 passed** (incl. whale scanner bps threshold tests).
- Focused dealer battery (`test_dealer_positioning_sign_model.py`, `test_options_chain_v5_wiring.py`, `test_run_modes_smoke.py`): all passed — 5 + 4 + 2.
- `test_dealer_positioning_direction.py`: 36 tests, 35 fast + 1 slow e2e (`test_report_renders_caveat_key_and_read_label`, ~103s — real SABR-fit compute, not a hang).
- Full `Vol_Suite/tests`: **378 passed, 5 skipped in 332.29s** (2026-08-11 fresh run; 5 skips = pre-existing `test_implied_vol` floor cases).
- Historical baseline: 378 passed / 5 skipped (08-09 night, gated + two-vanna fix verified) — identical to today.

## 8. Open items / known limitations

1. **Backtest v5 leg ≠ full live model yet**: live accumulates (150d book); backtest uses per-day level OI. No accumulation seam in the backtest for v5 → even gated+SABR-wired rows understate the live model.
2. **Recorded decision row not fully reproduced**: gated + ms1 + uniform gets closest (−0.0002/−1.547/35-43) but sabr_deviation mode with same knobs flips. Sweep (min_score × bps) still pending.
3. **Daily-horizon prediction null is structural** — the honest claim is classification (which side is the book on) + decision hygiene (NO_CALL), not vol forecasting. Intraday/trade-level track is the only remaining lever.
4. **0DTE contamination (ESC-1)** — open, needs production telemetry before new tickers.
5. **Illiquid chains** — SABR stability monitor (ESC-2); min-strike gate exists.
6. **Shadow tests** (SHADOW-1/2/3) specified 08-08 — deployment status to confirm.
7. **Stale labels** — `_gamma_subtitles` at `dealer_positioning.py:1206` still reads "V5 direction: whale-bias sign on OTM legs" (stale since 5f9833c — primary is per-expiry SABR). Rename pending Jason's approval (per trust rules: SHOW before patching).
8. **Docs drift** — the historical docs in this package describe deleted models; keep the SUPERSEDED banners, do not rewrite history.

## 9. Operational rules (from the hard lessons)

1. Before citing ANY stage-3/pooled v5 number as evidence for the live model: grep `_compute_sabr_deviation_for_expiry` in `backtest_stage3.py` (absent → tests the OLD run-level-bias v5), confirm the leg matches `compute_expiry_sign_map`, confirm the gate is present.
2. Every consumer calls the SAME sign-resolution function — no inline call/put heuristics anywhere (dealer 4-panel, scanner 2-panel, sentiment GEX scanner, CSV/JSON artifacts, backtests).
3. Before flipping any memory/doc about the canonical model: verify `CANONICAL_SIGN_MODEL` in the actual source. Archive filenames and single agent messages are NOT evidence (memory flip-flop failure, 08-09).
4. When file mtimes / git status / reflog disagree across consecutive reads → concurrent writer (other Hermes profile gateway). Pin the state before patching.
5. Test invocation: `env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest` (bare pytest fails collection).
6. `DEALER_DIRECTION_MIN_SCORE` is a Direction-package whale gate, NOT a backtest OI knob.
7. ThetaData proxy: stage pairs (2 concurrent, THETADATA_HIST_CONCURRENCY=6); never 6-way whole-chain fan-outs (502 storms).
8. Stale pyc under concurrent writers → pytest can report phantom test names; delete `__pycache__` and re-run with `-p no:cacheprovider`.
