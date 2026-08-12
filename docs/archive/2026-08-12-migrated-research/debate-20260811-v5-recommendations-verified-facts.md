# Driver-Verified Facts — V5 Recommendations Debate (2026-08-11)

Driver's independent source verification, done BEFORE R2 dispatch. Panelist
self-reports must be checked against this table. Claims marked CONFIRMED /
CORRECTED / STALE were verified against the live repo at 2026-08-11.

## Headline numbers (per-expiry backtest, docs/superpowers/specs/per-expiry-direction-backtest-20260808.md)
- Line 19: sabr_deviation row coef -0.0003, t -1.919, p 0.0587, block-perm **0.0680** — CONFIRMED (doc's "p=0.068" claim correct)
- Line 24: "Lowest block perm p: 0.0680 (closest to 0.05)" — CONFIRMED
- Line 32-33: Baseline block p = 0.0850; sabr_deviation block p = 0.0710 (two presentations of the same cell; 0.0680 vs 0.0710 differ — presentation inconsistency, not necessarily error)
- Line 36: "All runs: block p < 0.02" (v1/oi_heuristic) — CONFIRMED as documented claim; the doc's R-T1-2 "v1 was consistently significant" is accurate to the artifact
- Line 41: "AAPL: n=46, v5 short-gamma=46 (100%)" — CONFIRMED (doc's "46/46" correct)
- Line 59: 2 tickers, 30-day lookback, API rate limits — CONFIRMED
- Only 2 tickers in this test: SPY, AAPL (line 4) — the doc's "SPY+AAPL" framing correct

## Gate history (git)
- 5f9833c (2026-08-09): "fix(vol): SABR deviation is the sign source; accumulated book feeds all greeks" — REMOVED the `if not fallback_bias: return 0.0` gate (diff shows `-    if not fallback_bias:\n-        return 0.0`). CONFIRMED: the doc's "gate was removed once (5f9833c)" is true. Commit message claims removal was intentional ("SABR deviation IS the primary sign source; direction bias is only a fallback").
- 89d2b28 (2026-08-10): "fix(vol): scanner vanna consumes shared dealer engine; restore SABR gate" — RESTORED the gate AND added regression tests. CONFIRMED.
- Current tree state (dealer_positioning.py ~lines 515-523): gate is ON; docstring says "fallback_bias 0 -> return 0.0 (no trade)... DEALER_DIRECTION_GATE=0 disables the gate — BACKTEST REPRODUCTION ONLY."
- **STALE CLAIM (doc R-T2-5):** doc says "No existing test is a *permanent* regression guard against this specific regression — it was re-verified reactively, not prevented." CORRECTED: `Vol_Suite/tests/test_dealer_positioning_direction.py:209` `test_sabr_deviation_zero_fallback_stays_silent` asserts `fallback_bias=0.0 -> 0.0` and `:219` `test_sabr_deviation_zero_fallback_silent_only_when_unreadable`; both added in 89d2b28 (2026-08-10). The permanent regression guard ALREADY EXISTS in this tree. R-T2-5 is at least half-implemented.
- Doc's "replay 5f9833c's diff and confirm the test fails red" — the test exists; replay verification is the only missing part.

## Whale scanner (Direction/whale_scanner.py)
- Line 80: `expiry = future[0] if future else exps[-1]` — single nearest expiry selection. CONFIRMED (doc R-T2-2 correct).
- Lines 114-119: `if call_premium > put_premium * 1.2` / `elif put_premium > call_premium * 1.2` — the 1.2 CALL/PUT ratio exists as an INLINE LITERAL, not a named `_CALL_PUT_RATIO` constant. CONFIRMED with correction: doc R-T2-3's "`_CALL_PUT_RATIO = 1.2` (hardcoded)" is substantively right (1.2 is hardcoded inline) but the named constant does not exist — there is NO knob to sweep without extracting the literal. Grep for `_CALL_PUT_RATIO` across repo: zero hits.
- classify_whale_bias: volume × close × 100 per side, larger side wins; no aggressor-side classification. CONFIRMED (doc R-T3-5 correct).
- WHALE_THRESHOLD_BPS env at line 25. CONFIRMED.

## Backtest vs live accumulation (R-T3-1)
- dealer_positioning.py:862 `_acc = replication_reference.get_accumulated_position(...)` — live path accumulates 150d book. CONFIRMED.
- backtest_stage3.py:407 `_net_gamma_v5_direction(...)` applies `_resolve_sign('direction')`-equivalent to **LEVEL OI** (`_net_gamma_v5_direction(gamma_map, ...)` — the gamma_map is built from day-d level OI). CONFIRMED: backtest v5 leg uses per-day level OI, NOT the accumulated book. R-T3-1's structural claim is correct.
- backtest_stage3.py:46-60: the backtest explicitly mirrors live with a NO-LOOKAHEAD proxy `_build_direction_bias_by_date` (EOD volume + closes up to day d + day-d OI). NOTE: this is a documented deliberate design choice (no-lookahead), not an oversight — R-T3-1's "wire accumulation into the backtest" must be evaluated against the no-lookahead constraint: accumulating a book from history up to day d is causal (no lookahead), so the seam is feasible, but it changes the input structure as the doc claims.

## This tree vs doc §0 (Windows-tree risk)
- dealer_positioning.py:294-295: `CANONICAL_SIGN_MODEL = 'direction'`, `VALID_SIGN_MODELS = (CANONICAL_SIGN_MODEL,)` — this canonical WSL tree is ALREADY V5 single-model. Doc §0's "live risk" (Windows tree on vol_surface_replication) describes the stale /mnt/c/Users/bottl/FinancialDevelopment snapshot, NOT this tree. R-T1-5's startup-assertion idea is still sound insurance but the doc's §0 framing does not apply to the tree panelists read.

## SHADOW tests (R-T3-4)
- grep for DEALER_SHADOW / SHADOW-1/2/3 flags in dealer_positioning.py and whale_scanner.py: ZERO hits. CONFIRMED: specified but absent from code (matches doc's "vaporware" claim).

## Other
- Package zip `dealer_positioning_package_20260811.zip`: NOT present in this tree (doc quotes it as review input; evidence = doc + specs + source).
- docs/superpowers/reports/2026-08-09-sabr-deviation-default-evidence.md: OUTDATED intermediate (says vol_surface_replication default); superseded by direction-everywhere decision 2026-08-09.
- 5f9833c and 89d2b28 both reachable on this branch — git verification possible.
- The doc's claim (R-T3-1) "independently proposed by both, unprompted" — the doc itself documents both lenses proposing it; can't independently verify 'unprompted' but the two-lens corroboration is in the doc's own text.
