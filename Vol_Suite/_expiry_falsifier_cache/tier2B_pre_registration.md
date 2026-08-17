# Tier 2B — Pre-registration (locked BEFORE inspecting any multi-window results)

**Date:** 2026-08-14
**Study:** Multi-window short-DTE confirmation of the Tier 2 burst→forward-return effect
(pooled corr −0.20, n=110, ONE 2–7 DTE window per ticker).
**Protocol source:** Task brief + `Vol_Suite/docs/PLAN_expiry_book_exposure_v2_20260814.md`
(Phase 6 short-DTE arm, falsifier pre-registration). The standalone
`PLAN_expiry_tier2_multiwindow_20260814.md` referenced in the brief does NOT exist in the repo;
the protocol below is the locked interpretation of the brief.

## Window definition
A **window** = one listed option expiry observed over its genuine 2–7 calendar-day-to-expiry
trading days (real observation dates; end-of-window DTE ∈ [2,7]). Each ticker×window is an
independent cluster. The existing `20260817`/`20260821` pull (as-of 2026-08-14) is reused as the
**most-recent** window — NOT re-pulled, NOT relabeled. Prior windows are pulled from ThetaData
with the SAME pull pattern as `pull_short_dte.py` (nearest 2–7 DTE expiry at a chosen as-of,
14-day greeks/OI/spot lookback), with as-of dates spaced so 14-day lookbacks do NOT overlap.

## Data rules (non-negotiable)
1. Real ThetaData only (`shared.thetadata.ThetaDataController` via `expiry_selector`/`implied_vol`
   as in the reference puller). No fabrication, simulation, date-shift, or far-DTE-relabeled-as-short.
2. Every window recorded: ticker / expiry / as-of / DTE range / row counts / SHA-256.
3. Reused most-recent window is NOT re-pulled (its hashes are recorded as-is).
4. Any window/ticker that cannot be pulled genuinely is recorded **BLOCKED** with the exact error.

## Protocol amendments (recorded BEFORE analysis)
- **Discovery method (2026-08-14, after connectivity probe):** ThetaData `list_expirations`
  returns only *currently-listed* (unexpired) expiries, so filtering it against a historical as_of
  is structurally empty. **Expired contracts ARE queryable by EXPLICIT expiry.** Therefore historical
  windows are pulled by deterministic explicit expiry: probe each weekday in `[as_of+2 .. as_of+7]`
  via explicit-expiry greeks; any nonempty result proves a genuine listed short expiry at that as_of.
  Per (as_of, ticker) select the valid candidate with smallest DTE ≥ 2 (ties → Friday preferred),
  mirroring the reused window's dte=3 choice. Log the full candidate set per window.
- **"2–7 DTE window" definition:** end-of-window DTE ∈ [2,7] at as_of (matches Tier 2's dte=3/dte=7
  choice); observation days span the 14-day lookback (DTE ~14→end). Kept identical to Tier 2 for
  comparability; n is NOT restricted to DTE≤7 only.
- **Lookback overlap:** as-of dates are 14 days apart with 14-day lookbacks, so consecutive windows
  share exactly the boundary date. Reported honestly, not claimed as non-overlapping.
- **Reuse window does NOT count toward the "prior windows" target.**
- **Per-(as_of,ticker) selection rule (locked BEFORE analysis):** select the valid candidate with the
  smallest DTE ≥ 2 (ties → Friday preferred), mirroring the reused window's dte=3 choice. The full
  weekday-candidate set is logged per window; acceptance requires nonempty greeks AND OI>0 AND
  spot-date coverage, proving the expiry is real. SPY/QQQ have near-daily expiries; a non-Friday
  selected candidate is still genuine and is reported as such.
- **"2–7 DTE window" / observation days (locked):** window = (as_of, expiry) with end-of-window
  DTE ∈ [2,7]. Observation days span the 14-day lookback (DTE ~14 → end), identical to Tier 2;
  Tier-2B's primary pooled estimate uses ALL lookback days de-meaned per cluster (comparable to
  Tier 2's n=110); a DTE≤7-only sensitivity arm is reported side-by-side, NOT as the primary.
- **Effective-N reporting:** nominal obs, # independent ticker×window clusters, and effective-sample
  assumption reported SEPARATELY. Multiplicity (BH q<0.10) applied over the window family AND the
  per-ticker family.

## Verdict ladder (pre-registered, applied to the family of primary tests)
- **SUPPORTED (CONFIRMED):** pooled meta-analytic estimate of the burst→forward-return corr is
  negative with cluster-aware (ticker×window) CI excluding 0, sign-consistent across the majority
  of windows AND tickers, survives multiplicity (BH q<0.10 over the window family) AND leave-one-
  window-out AND leave-one-ticker-out AND the placebo/control checks, AND is outside the placebo
  distribution. No single ticker/window dominates.
- **FRAGILE / INCONCLUSIVE:** pooled estimate supported but substantial sign heterogeneity across
  windows/tickers, OR too few independent windows (<3/ticker, <~15 total), OR instability under
  leave-one-out / placebo, OR significance depends on one window/ticker.
- **RULED_OUT:** cluster-aware CI excludes the target effect magnitude (i.e. CI does not contain a
  meaningful negative effect) with adequate independent windows; or placebo indistinguishable.
- **BLOCKED:** fewer than 3 genuine windows/ticker, driver/analysis failure, protocol violation,
  or ThetaData inaccessible for prior windows.

Primary estimand: pooled (meta-analytic) correlation between the corrected burst object
(`hedge_flow_at` at zero-gamma/wall, gamma lagged one day) and the continuous forward return,
within genuine 2–7 DTE days, de-meaned per ticker×window cluster.

## Analysis plan (locked)
- Per-ticker AND per-window pooled corr; per-ticker×window cluster bootstrap CI.
- Random-effects (DerSimonian-Laird / permutation) meta-analytic pooling of per-window estimates.
- Report nominal observations vs # ticker×window clusters vs effective-n SEPARATELY.
- Multiplicity: BH q<0.10 over the window family and over per-ticker family.
- Leave-one-window-out, leave-one-ticker-out.
- Placebo: (a) non-breach days only, (b) shifted forward-return horizon.
- DTE buckets 2–3 / 4–5 / 6–7. Index ETFs (SPY/QQQ) reported separately from single names.
- Vanna-shock arm: marked UNTESTABLE/UNDERPOWERED unless ≥ genuine shock obs exist.
- SPY/QQQ sign flip on the same construct = FRAGILE/FAIL signal (per v2 plan §4.11).

## Integrity
- Hash the corrected driver `run_expiry_tier1.py` before and after the run (unchanged).
- `git status --short` before and after.
- No live model file or existing driver modified; new analysis is a NEW file
  `run_expiry_tier2_multi.py` + report files.
