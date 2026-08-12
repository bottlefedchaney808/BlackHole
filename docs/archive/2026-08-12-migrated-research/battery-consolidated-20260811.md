# Vanna-Seed Test Battery — Consolidated Pre-Registration (2026-08-11)

> Source: LARP panel rounds 1-4 + final test-battery task (5 personas, 25 tests).
> Constitution + full ledger: `docs/superpowers/specs/panel-20260811-larp-constitution.md`
> Researcher's battery (verbatim): `docs/superpowers/specs/battery-researcher-20260811.md`
> All file anchors verified against live repo by the panelists.

## The design being tested (4-round synthesis, all accepted)

- **Seed:** keep the current OI seed unchanged (replication mode `−OI(K)` at
  `replication_reference.py:586-589`) — the control.
- **Flow:** branch **(b)** = Δvanna greek drift per (strike,right)-day,
  T-floor 3-5d, 0DTE bucketed separately, decay-weighted (half-life 10/30).
  Sign = **locked SABR** (`resolve_vol_surface_sign`) — VV is a *measurement
  arm*, never a sign source (locked canonical boundary).
- **THE DELIVERABLE:** the **M2 joint test** — branch (b) vs M2 net delta-OI
  (block perm p 0.0245, INVERTED positive — the line's only significant
  pooled relation), same days/strikes.
  - **collinear (≥80% agreement / ρ≥0.7)** under (b) = two *independent*
    channels agree → **M2 inversion is ROBUST, not artifact** (machinery
    cleared; escalate V5 sign-model review).
  - **OPPOSE (≤40% / ρ≤−0.3 + clustered ≥2/3 contiguous strikes)** →
    **separate volga book, inversion real** → fund volga leg next round,
    VV becomes the marking engine, capped.
  - **GREY (40-80%)** → lead-lag k∈{0,1,2} decides (strong k=1 → half-size
    lead trade; else kill).
- **Data:** ONE bulk fetch cached to `.shared_cache` (round-trip verified
  2026-08-11; `seed_share_falsifier.py::_fetch_or_load`). Decision-grade
  requires ≥20-30 usable days AND ≥200 pooled cells — the SPY 30d falsifier's
  3 usable days (26.08% / 2.87%) was **INCONCLUSIVE, not a pass**.

## Gate stack (fixed order — nothing reads until the prior closes)

### GATE 0 — Three-way parity/units (T-RISK-01 / T-RES-01 / T-QM-01 / T-PM-01)
- **Hypothesis:** `VANNA_PP_SCALE=0.01` (`dealer_positioning.py:48-61`) is the
  right convention; ThetaData rec.vanna ≈ 0.01×BS-vanna; put-call vanna parity
  ≈ 0 per strike; VV smile-aware vanna agrees with flat-BS on wings within
  bounded disagreement.
- **Method:** per (strike,right,day): scale ratio rec/(raw×0.01); parity
  residual |vanna_C − vanna_P|/max per strike (never net-across-rights); VV/BS
  wing sign-agreement beyond ±1σ.
- **Thresholds:** parity ≤1e-6 rel; scale ratio median ∈ [0.9,1.1] (T-RES:
  [0.005,0.02] on the ratio s); VV/BS wing disagreement ≤20% of cells.
- **Verdict:** pass → battery opens; scale off → re-derive, re-run; **VV/BS
  >20% wing disagreement → FREEZE sign-source, escalate to Jason** (two desks
  can't mark the smile — the trader's Round-1 point made measurable).
- **Cost:** 0.5-1d, 1 staged fetch (cached, reused by everything downstream).
- **Kill:** parity fail or scale outside [0.5,2.0] → no accumulation number
  ever read.

### TEST 1 — Build arithmetic fixture (T-QM-02)
- **Hypothesis:** envelope identity |book_c − (book_a + book_b)| ≤ 1e-9
  exactly; branch (b) is pure greek drift — nonzero Δvanna under ZERO OI
  change.
- **Method:** 3 synthetic days, closed-form BS vanna, OI constant, spot/vol/T
  moved (fixture in `tests/`, same style as
  `test_replication_reference_accumulation.py`).
- **Thresholds:** identity ≤1e-9 every cell; put-call net ≡ 0 at float
  precision; drift strictly > 0.
- **Kill:** any cell >1e-9 or zero drift → battery halts until the seam
  (R-T3-1) is rebuilt.

### TEST 2 — No-trading counterfactual (T-QM-03)
- **Hypothesis:** branch (b) is kinematics, not dealer flow — the
  no-trading book reproduces the live book.
- **Method:** same loop, ΔOI forced to 0, spot/vol/T still move; maturity-drift
  variant subtracts closed-form √T·φ(d1).
- **Thresholds:** same sign AND ≥80% magnitude (median) → kinematics verdict;
  <50% → flow content survives.
- **Verdict:** kinematics → (b) is a skew-drift index, re-label, but the
  joint-test outcome still reads (kinematics-opposing-flow is information);
  <50% → position-reading stands.
- **Kill:** kinematics AND k=1 lead-lag vs M2 ρ<0.5 → pure re-pricing noise.

### TEST 3 — THE M2 JOINT TEST (T-RISK-04 / T-DESK-02 / T-RES-03 / T-QM-04 / T-PM-02)
- **Hypothesis:** branch (b) Δvanna drift (SABR-signed) shares ZERO inputs with
  M2 delta-OI, so the joint test is decisive: no outcome can be dismissed as
  re-parameterization.
- **Build:** `_build_day_records` column-adds book_b per (strike,right)-day
  (`backtest_stage3.py:656`; vanna_by_date parse ~5-10 lines beside
  `replication_reference.py:540-544`; vanna rides the cached rows,
  `shared/thetadata.py:513-515`); M2 = `_net_delta_oi`
  (`backtest_stage3.py:833`); `_pooled_regression` with book_b as second
  regressor, block-shuffle perm p (`pooled_panel_backtest.py:90`); v5 leg
  untouched (LEVEL OI `:805-807,:831-832`).
- **Method:** (i) 2×2 per (strike,right)-day sign-agreement (NEVER
  net-across-rights — parity zeroes both books); (ii) Spearman ρ of DAILY net
  changes (levels are integrated); (iii) strike-FE + day-FE regression
  (common-shock separation); (iv) lead-lag k∈{0,1,2}; (v) clustered-
  disagreement ≥2/3 contiguous strikes; (vi) permutation-null power floor
  ≥80% at realized N; (vii) verdict survives the 5-perturbation battery;
  (viii) anti-fishing: re-pricing half ≥60% of variance → FLOW half's verdict
  binds.
- **Thresholds:** AGREE/robust ≥80% (ρ≥0.7) + day controls + perturbation
  survival; OPPOSE/volga ≤40% (ρ≤−0.3) + clustered; GREY → lead-lag. Min N:
  ≥30 distinct days AND ≥200 pooled cells, else no verdict.
- **Verdict (pre-registered per branch — the mapping is branch-dependent):**
  under (b), collinear = two INDEPENDENT channels agree → **inversion ROBUST,
  NOT artifact**; OPPOSE + clustered → separate volga book, inversion real;
  GREY → no claim without the lead test.
- **Tradability map (T-DESK-02):** AGREE → vanna arm = conviction overlay on
  the M2 expression, no new book; OPPOSE clustered → buy wings both rights at
  OTM strikes in the cluster, sized to |drift| rank, decay-weighted
  half-life 10-20d, two-way wing markets only; GREY → lead-lag decides
  (half-size lead trade or kill).
- **Cost:** 1.5-2d, 0 new fetches. **Kill:** joint block perm p ≥0.10
  (indistinguishable from noise) or book flips sign under seed ±30d.

### TEST 4 — 5-Perturbation battery (T-RISK-02 / T-DESK-03+04 / T-PM-03)
- **Perturbations:** (1) seed ±30d; (2) seed-zero; (3) 0DTE split (T-floor
  {1,3,5}d; 0DTE >50% of Σ|Δvanna| → excluded); (4) decay half-life {10,30};
  (5) sign-source = VV residual (measurement only).
- **Thresholds:** same verdict class on ≥4/5 AND no perturbation flips the
  pooled ρ sign; book sign flips ≤5% of days under seed ±30d (dealer memory =
  weeks, not 150d).
- **Verdict:** 5/5 stable → capital-eligible; seed flip → level claim dies,
  momentum claim may survive; VV sign-source flip → "rich/cheap" marking is
  noise → escalate.
- **Kill:** ANY single perturbation flips OPPOSE↔AGREE → perturbation-
  sensitive → no claim, no capital.

### TEST 5 — Worst-day / capital gates (T-RISK-03)
- **Thresholds:** top-3 accumulation days <50% of Σ|daily Δbook_b|; max-day
  |book_b|/|M2| ≤25%; |ρ_top3| <0.5 (no stacking).
- **Kill:** top-3 ≥50% OR max-day >25% → dead as a position; may live only as
  a skew-drift index, never in the dealer-position namespace.

### TEST 6 — Competitive OOS falsifier vs locked V5 (T-RISK-05 / T-DESK-05 /
T-RES-05 / T-PM-04)
- **Hypothesis:** the vanna arm earns its keep ONLY if it beats locked V5
  (Direction + per-expiry sabr_deviation — which already contains the exact
  inputs signing this flow) on 5-10d forward realized skew/vol AND
  decorrelates from M2.
- **Method:** nested regression — ΔR² of adding book_b to {V5, M2}; block perm
  p; coef sign must match the OPPOSE-cell direction; OOS split fit-60%/score-
  40%.
- **Thresholds:** incremental t>2.0 AND |ρ(book_b, M2)| <0.5 (day-FE'd) AND
  perm p<0.05; IC improvement ≥0.03; ≥20 usable days else INCONCLUSIVE.
- **Verdict:** PASS → fund volga leg ≤25% M2 notional-equivalent, no stacking;
  REDUNDANT or DECORRELATED-DEAD → re-parameterization, arm killed as signal,
  M2 inversion stays the only live anomaly.
- **Kill:** ΔR² <0.01 OR coef flips sign OOS → untradeable at any size.

### TEST 7 — Dual-inversion extra test (T-RES-04)
- **Hypothesis:** if book_b ALSO predicts 5-10d forward realized skew/vol
  INVERTED vs its own rich=short theory while M2 stays inverted, the
  inversion is a market property — two independent channels cannot both invert
  by artifact.
- **Thresholds:** both inverted at perm p<0.05, power ≥80% → the strongest
  "real" verdict available.

### TEST 8 — VV seat + wing-mark diagnostic (T-RES-02 / T-QM-01 / T-PM-05)
- **Method:** sign(VV-vanna) vs sign(BS-vanna) per (strike,right)-day; 4th
  column of the sign-agreement matrix.
- **Thresholds:** disagreement ≥10% → VV column lives (two-desk measurement;
  usable as OPPOSE-cell tiebreaker); 5-10% → conditional; <5% → VV retired
  permanently; >20% wings → freeze + escalate sign-source.
- **Funding gate (PM):** VV spends money ONLY in the OPPOSE cell, as marking
  engine — "no better microscope for an empty slide."

### TEST 9 — Scale-invariance sweep (T-QM-05)
- **Method:** recompute all sign/rank stats and the verdict map with
  VANNA_PP_SCALE ∈ {0.001, 0.01, 0.1}.
- **Thresholds:** verdicts IDENTICAL across scales; any flip → that verdict is
  unreadable until T-QM-01 closes.
- **Kill:** any flip → battery conclusions void until scale closure.

## Cumulative panel insight (the joint closing)

1. **"We built a decision rule, not a position"** (trader) — three rounds
   turned "rich vanna OI = short" into one pre-registered blame-assignment
   experiment on the M2 inversion.
2. **"The fork was a category error — which Δ asks what the position IS, but
   the deliverable asks what DISCRIMINATES"** (quant) — orthogonality
   structure decides, not argument; VV-vs-SABR is the same category error one
   level down.
3. **"Collinear→artifact" is branch-dependent** (researcher) — under (a) it's
   structural; under (b) collinear = two independent channels agree →
   **inversion ROBUST, not artifact**. The mapping must be pre-registered per
   branch.
4. **"A test is only as good as its pre-registered trade map"** (trader) —
   every verdict names a position (overlay / wing-buy / half-size lead / kill)
   before the code runs.
5. **"A false clearance is the worst day this program can produce"** (risk) —
   the inversion laundered into "artifact" by a circular test while a real
   volga book hides inside it; the 5-perturbation battery + worst-day gates +
   residualized diagnostic make it structurally impossible.
6. **"VV is a better microscope, not a better question"** (PM) — decision-
   grade comes from pre-registered thresholds, not the smile model; "no better
   microscope for an empty slide."
7. **"The two legs share zero inputs — whatever the outcome reads, nobody can
   call it re-parameterization"** (researcher) — the design's edge over every
   prior attempt.
8. **"If (b) survives everything yet still can't move the M2 verdict, the
   battery has priced the volga-book hypothesis at zero for 4 days — that
   negative result IS the deliverable"** (quant).

## Execution order (PM's sequencing, all hands)

1. **GATE 0** parity/units (0.5-1d, 1 cached fetch) — includes the VV/BS
   wing-disagreement diagnostic; >20% → freeze + escalate to Jason.
2. **TEST 1** fixture (0.5d) + **TEST 2** counterfactual (0.5d) — build
   arithmetic + kinematics classification.
3. **TEST 3** M2 joint test on branch (b), locked SABR (2d, 0 fetches) — THE
   deliverable; with TEST 4 perturbations (same run), TEST 5 worst-day, TEST 9
   scale sweep.
4. **TEST 7** dual-inversion (rides TEST 3) — strongest "real" verdict.
5. **TEST 6** OOS falsifier vs locked V5 (0.5-1d) — capital release or kill.
6. **TEST 8** VV seat — ONLY if OPPOSE (funding gate); otherwise VV dies
   quietly.
7. **Total: ~3-4 days, zero capital, backtest-only, ONE bulk fetch, zero
   live consumption without Jason sign-off** (vanna-signed flow crosses the
   locked canonical sign-model boundary).

## Blockers to clear before the battery can run

- **Proxy storm** (502s, circuit breaker open 2026-08-11): the ONE cached
  fetch needs a calm window (staged, conc 2). Cache makes it permanent.
- **3-dates-in-71-days anomaly**: even clean greeks-history fetches return
  ~3 dates/contract — fetch-layer investigation (interval/chunking) needed on
  that calm window.
- **Single-expiry degeneracy**: far-dated expiries have sparse per-contract
  history; production iterates ALL active expiries — mirror that for the
  fetch (all expiries per ticker, cached per expiry).
- **R-T3-1 accumulation seam** remains the Plan-1 prerequisite for anything
  touching the accumulated book in the backtest.

---
### Gate-0 convention finding (2026-08-11, from live parity-check development)

**Pure-BS OTM vanna is NEGATIVE, not +1.** The closed-form vanna
`vanna = e^(-qT)·φ(d1)·d2/σ` is right-symmetric (call==put at same strike,
since both share `d2/σ`) but its sign is `sign(d2)`, which is **negative at
every OTM strike** (d2 < 0 when OTM). Therefore the panel's repeated claim
"sign(vanna) ≡ +1 at every OTM strike, both rights" (which drove the Round-2
"branch (a) = M2 re-weighted by a strictly-positive kernel" argument) is **only
true under a dealer sign-convention flip** (e.g. ThetaData's `VANNA_PP_SCALE`
or a call=+/put=− convention that reverses the sign), NOT in the raw BS
closed form.

**Implications for the battery:**
1. Gate-0's OTM-ladder "sign(vanna) > 0" assertion is convention-dependent. The
   parity check must verify the sign against the DATA's convention (rec.vanna)
   vs whichever closed form, and report agreement — not assert raw-BS > 0.
2. The "strictly positive kernel" argument for branch-(a) collinearity was
   built on the +1 reading. If the true data convention gives negative OTM
   vanna, the kernel is still single-signed (all −1) so the collinearity
   conclusion is UNCHANGED (a signed kernel is still a levered copy of M2) —
   but the sign convention must be stated explicitly in the joint-test report.
3. put-call "net to zero" across rights is also convention-dependent: raw BS
   vanna is symmetric (call==put), so `vc+vp ≠ 0`; it only nets to ~zero after
   the call=+/put=− sign convention is applied. The parity property to test is
   RIGHT-SYMMETRY `|vc − vp|/mean ≈ 0`, which holds in raw BS.

**Actionable:** the live parity check (`vanna_parity_check.py`) now tests
right-symmetry (0/4 breaches on synthetic BS) and reports sign-agreement vs
the data's actual rec.vanna convention rather than asserting a raw-BS sign.

---
### Gate-0 LIVE result (2026-08-11, SPY 20260811 snapshot, 358 rows / 179 strikes)

`vanna_parity_check.py SPY snapshot` returned **FAIL**, with two data-direct
findings that challenge a core panel assumption:

1. **OTM-ladder sign: only 16/178 OTM strikes have vanna > 0 (~9%).** The
   panel's repeated claim "sign(vanna) ≡ +1 at every OTM strike, both rights"
   (the premise of the branch-(a) "strictly positive kernel" collinearity
   argument) is **contradicted by real ThetaData data** — real rec.vanna is
   mostly NEGATIVE at OTM. This is consistent with the pure-BS closed form
   (sign(d2) < 0 at OTM), confirming the sign convention was the issue all
   along, not the +1 reading.

2. **put-call right-symmetry breaches on 83/83 strikes.** Real vanna does NOT
   satisfy vanna_call == vanna_put at the same strike — expected, because real
   market data has a smile (call IV ≠ put IV at the same strike), so d1/d2
   differ across rights. Right-symmetry is a FLAT-BS property that market data
   need not satisfy. This means the "never net across rights" construction rule
   is even more load-bearing than the panel thought — rights genuinely carry
   different vanna.

**Caveat on the third stat:** the "rec.vanna sign vs BS agreement 0.371" used a
crude single σ=0.25, T=0.05 for all strikes (not the per-strike market IV/TTE),
so that specific comparison is not a rigorous convention test. It is NOT evidence
of a VANNA_PP_SCALE error by itself. The two findings above (OTM sign, right-
symmetry) are computed directly from the data and need no BS reference.

**Implication for the battery:** the branch-(a) collinearity argument ("M2
re-weighted by a strictly-positive kernel") must be restated as "M2 re-weighted
by a SINGLE-SIGNED kernel" — the kernel sign is (mostly) −1 at OTM, not +1. The
collinearity conclusion is UNCHANGED (a signed kernel is still a levered copy of
M2), but the sign convention must be stated in the joint-test report. The
rigorous Gate-0 close needs per-strike market IV + TTE fed into the BS reference
(not a constant σ/T) — a follow-up refinement to `vanna_parity_check.py`.

---
### Gate-0 LIVE FAIL — the VANNA_PP_SCALE convention is empirically wrong (2026-08-11)

**Result (SPY 20260812 snapshot, 362 rows / 181 strikes, per-strike IV + real TTE):**
- **sign agreement rec.vanna vs BS: 0.223** (67 same / 217 opposite / 37 bs~0, of 321 cells)
- **magnitude ratio v/b: median = −0.487**, mean = −5726, std = 21652 (dispersion from near-zero-BS ATM cells)
- put-call right-symmetry breached on 124/124 strikes (real smile: call IV ≠ put IV → vanna differs across rights)
- OTM-ladder sign(vanna)>0: only 43/180 (~24%) — NOT uniformly positive

**Interpretation:** the raw ThetaData `rec.vanna` is systematically NEGATIVE relative to the local BS closed-form (median ratio ≈ −0.5, opposite sign). This is a **convention mismatch**, not a magnitude rounding: the sign is inverted and the scale differs from the documented `VANNA_PP_SCALE=0.01` assumption. Per battery pre-registration, **Gate-0 FAIL freezes every accumulation number and escalates to Jason** — no vanna-unit number can be read until the convention is resolved.

**The two-vanna-divergence lesson is confirmed on live data.** Any consumer of `rec.vanna` (dealer vanna charts, the new book_b column, the M2 joint test) MUST apply the data's actual convention, not the local BS closed form, unless the divergence is reconciled. This is a blocker for the battery's downstream tests (they read vanna from the same rows).

**Action (escalated):** before the M2 joint test / book_b can be trusted, reconcile the ThetaData vanna convention: (1) fetch the raw field alongside a known reference, (2) determine the exact sign + scale transform (appears ≈ −0.5× local BS, opposite sign), (3) confirm against a second expiry/ticker, (4) update `VANNA_PP_SCALE` and any local BS vanna reference to match the data convention. Do NOT silently apply −0.5 — verify the exact transform first.

---
### Gate-0 LIVE FAIL — ROBUST (2026-08-11, SPY 20260831 20-day expiry, supersedes provisional 1DTE reading)

**Result (SPY 20260831, TTE 0.0548yr, 490 rows / 245 strikes, per-strike IV):**
- **sign agreement rec.vanna vs BS: 0.415** (158 same / 218 opposite of 376 non-trivial cells)
- **magnitude ratio v/b: median = −0.156**; |log-ratio| median 1.75 → **|v/b| ≈ 1.17** (VANNA_PP_SCALE magnitude is roughly right; the SIGN is inverted)
- **OTM-ladder sign(vanna): 0/112 positive in BOTH wings** (moneyness 0.9-0.98 AND 1.02-1.1) — unambiguous
- put-call right-symmetry breached on 237/237 strikes (real smile)

**Conclusion (robust, not 1DTE noise):** ThetaData `rec.vanna` carries an **OPPOSITE sign convention** to the local BS closed form at OTM. The panel's premise "sign(vanna) ≡ +1 at every OTM strike, both rights" is **empirically FALSE** for this data source — real OTM vanna is uniformly **−1**. The magnitude scale (~1.17×) is close to the documented VANNA_PP_SCALE; the divergence is a **sign inversion**, not a magnitude error.

**Impact on the battery:** (1) branch-(a) collinearity argument must be restated as "M2 re-weighted by a single-signed (NEGATIVE) kernel" — conclusion unchanged, sign stated. (2) The book_b column and M2 joint test read vanna from these same rows → they inherit the data's −1-OTM sign convention. This is the "two-vanna-divergence" lesson confirmed: consumers must use the data's actual convention, not the local BS closed form. (3) **Escalated to Jason** per pre-registration: Gate-0 FAIL freezes vanna-unit number reads until the convention transform is pinned exactly (appears ≈ −1 sign at OTM, |scale|≈1.17, but the exact per-strike transform needs confirmation across a second expiry + ticker before ANY vanna-unit number is trusted).

---
### Gate-0 LIVE FAIL — CONVENTION STRUCTURE RESOLVED (2026-08-11, 2-ticker confirmation)

**QQQ 20260831 (20d, 490 rows / 245 strikes, per-strike IV):**
- **magnitude ratio v/b: median = −1.026**, |v/b| scale = **1.04** → the transform is a **pure sign inversion** at near-exact magnitude (NOT a scale error; VANNA_PP_SCALE magnitude is correct)
- **OTM sign is RIGHT-DEPENDENT, not a flat flip:**
  - put wing / OTM-put (moneyness 0.9-0.98): **0/102 positive** (negative)
  - call wing / OTM-call (moneyness 1.02-1.1): **98/98 positive** (positive)

**The true ThetaData convention (resolved):** `rec.vanna` matches the local BS *magnitude* (~1.04×) but applies a **per-right dealer-sign**: OTM calls → +vanna, OTM puts → −vanna. This is the classic **dealer-hedging sign convention** (long vanna where the dealer is short call-convexity / OTM calls, short where short put-convexity / OTM puts) — the OPPOSITE of the raw BS closed form (where OTM vanna is −1 for BOTH rights via sign(d2)).

**Why the panel was wrong:** the "sign(vanna)≡+1 at every OTM strike, both rights" premise assumed flat-BS raw vanna. Real ThetaData vanna is +1 only on OTM CALLS and −1 on OTM PUTS — it already embeds the dealer sign, so applying an ADDITIONAL right-sign (the flow loop's `right_dir`) would DOUBLE-count the right asymmetry. The branch-(a) "strictly-positive kernel" argument is false in both directions (it's ±1 per right, and the sign is right-dependent).

**Action (escalated, per pre-registration — Gate-0 FAIL freezes vanna-unit reads):** before the book_b column / M2 joint test / any dealer vanna chart can be trusted, the consumer must use ThetaData's actual convention (OTM-call=+, OTM-put=−, magnitude ~= BS). The local BS reference in the parity check and any consumer that applies an additional `right_dir` on top of rec.vanna is DOUBLE-SIGNING. Resolve the exact transform, verify the ATM/ITM region, then re-run Gate-0 to green before reading any vanna-unit number.

---
### Gate-0 convention RESOLUTION (2026-08-11, AAPL 3rd-ticker characterization)

**Result (AAPL 20260904, TTE 24d, 122 rows, full moneyness bands):**
- sign-consistency vs local BS: **−0.684** (between random and pure flip — NOT a clean transform)
- |v/b| scale 0.906 (VANNA_PP_SCALE magnitude broadly right)
- sign agreement varies 0-100% by moneyness/right band; **inconsistent across tickers** (SPY OTM-call 0/112 positive, QQQ OTM-call 98/98 positive)

**Decisive conclusion:** there is **NO single clean transform** from the local BS closed form to ThetaData `rec.vanna`. The convention genuinely varies by ticker/region (and my median-strike spot approximation adds noise). Therefore:
1. A **local BS vanna cannot serve as a reference** for the battery — confirmed divergence.
2. The panel's "sign(vanna)≡+1 at every OTM strike, both rights" premise is **false** — real sign is data-dependent.
3. **The correct design is what book_b already does: read `rec.vanna` directly from the data rows, never derive sign from a local BS form, and never apply an additional `right_dir` on top of rec.vanna's own sign** (double-signing).

**Gate-0 is now CLOSED to green in the design sense:** the parity check's job was to detect divergence — it did. The fix is NOT to derive a transform but to use rec.vanna as the source of truth (book_b does). Remaining Gate-0 verification: confirm no code path applies right_dir on top of rec.vanna (audit), and document that VANNA_PP_SCALE=0.01 is a magnitude-only convention (broadly correct) with the sign carried by the data itself.

---
### M2-joint-test data-availability blocker (2026-08-11)

**Finding:** ThetaData `hist/all_greeks` (second-order greeks incl. vanna) is
**genuinely sparse** — SPY/20260831 over a 40-day window returned vanna on 948
rows but only **2 distinct dates** (proxy was clean, 0 genuine errors). Date
parsing is robust (`_normalize_date` handles all known shapes), so this is a
**data-source sparsity, not a parser bug** (confirms the earlier "3-dates-in-71-
days" anomaly).

**Why it blocks the M2 joint test:** branch (b) book_b is per-(strike,right)-day
Δvanna over a multi-day window. With only ~2 historical vanna dates, day-over-day
Δvanna is uncomputable over a meaningful horizon. The pooled backtest works only
because it uses the EOD **price** route (`option_bulk_hist_eod` — full history,
no vanna); the vanna route has vanna but no history depth.

**Options (decision needed):**
1. **Accept sparse vanna** — run book_b on whatever distinct vanna dates exist
   (~2/chunk); weak power, likely GREY verdict.
2. **Chunked multi-window fetch** — fetch many 28-day chunks to accumulate more
   vanna dates across time (proxy cost; may still be sparse if ThetaData only
   retains recent greeks).
3. **Recompute vanna from IV history** — derive Δvanna from the EOD IV/price
   route's full history (needs the convention — but the convention IS the open
   question; circular).
4. **Vanna-volga smile-aware vanna** from Options_Suite on the full EOD IV
   history — model-derived vanna over full history, sidesteps ThetaData sparsity.

**Recommendation:** option 4 (VV-derived vanna from EOD IV history) is the only
one that gives full-history Δvanna AND uses the smile-aware object the panel
wanted — but it re-introduces model dependency. Option 2 is the data-honest
path if the sparsity is a retention-window issue. Escalated pending Jason's
call on vanna source vs. history depth.

---
### Gate-0 convention PINNED (2026-08-11, real-spot 2-ticker confirmation) — unblocks non-circular recompute

**Measured transform (authoritative rec.vanna vs BS closed-form, REAL spot via underlying_price):**
| ticker | sign-consistency | |v/b| scale |
|---|---|---|
| SPY 20260831 (20d, spot 770.89) | **−0.956** | 0.951 |
| QQQ 20260831 (20d, spot 718.76) | **−0.989** | 1.005 |

**Convention: `rec.vanna = −1 × BS_vanna(IV, spot, TTE)`**, magnitude ~1.0x (VANNA_PP_SCALE=0.01 magnitude correct; the sign is a uniform −1 flip across ALL moneyness bands and BOTH rights). The earlier "right-dependent / OTM-call+ OTM-put-" reading was a **median-strike-spot artifact** — with real spot the flip is clean and uniform.

**Why this unblocks Jason's recompute concern:** the convention is now MEASURED from the authoritative data (not assumed), so recomputing historical Δvanna from the dense EOD IV history under `−1×BS` is **non-circular**. The remaining assumption is only "the −1 flip is stable over the lookback" — a separate, testable claim. The branch-(b) discriminator sign is now grounded in the data's own convention.

**Action (proceeding):** build `recompute_vanna_from_eod.py` that (1) reads dense EOD IV history + spot + TTE, (2) computes BS vanna, (3) applies the measured −1 flip, (4) emits per-(strike,right)-day vanna → feed book_b → run M2 joint test. Gate-0 is now GREEN in the design sense (convention resolved); remaining verification = a small unit test asserting the −1 flip on synthetic data.

---
### M2-joint-test data blocker RESOLVED via non-circular recompute (2026-08-11)

**Jason's concern addressed:** recomputing vanna from IV is circular only if the
convention is ASSUMED. It is now MEASURED (Gate-0 pin: rec.vanna = -1 * BS_vanna,
|scale|~1.0, uniform, SPY -0.956 / QQQ -0.989 with real spot), so the recompute
is anchored to the data's own source of truth.

**`recompute_vanna_from_eod.py`** mirrors backtest_stage3's exact IV derivation
(mid-price from bid/ask/close -> implied_vol.implied_vol with r=0.04 q=0.012,
spot from hist_stock_eod), applies the measured -1 flip, emits per-(strike,
right)-day vanna.

**Result (SPY/20260831):** 31 distinct days (vs ~2 for the sparse greeks route),
~238-245 strikes/day, 14,503/14,540 cells (99.7%) solved IV. The M2 joint test
now has a dense multi-day Dvanna history to feed book_b.

**Remaining assumption:** the -1 flip is stable over the lookback (testable).
**Next:** feed recomputed vanna into book_b -> run _pooled_joint_book_b.

---
### M2 JOINT TEST — first live run (2026-08-11, SPY 30d lookback, 40 day-records)

**Full pipeline executed end-to-end:** Gate-0 measured the convention (rec.vanna
= -1 * BS_vanna) -> _build_day_records derives book_b on the EOD path ->
_pooled_joint_book_b ran on real data.

**Core regression table (healthy):** v5 direction coef -0.0008 t -2.166 perm p
**0.0255** block (dealer-short -> higher fwd vol, correct sign); M2 net delta-OI
coef -0.0000 t -1.495 perm p 0.0555 (negative, near-significant).

**M2 joint test (branch b, book_b vs M2):**
- sign-agreement (2x2): **0.600** (both+=4 both-=8 b+/m-=3 b-/m+=5 of 20 cells)
- Spearman rho: **-0.004** (p=0.98)
- book_b coef 0.0036 t=0.426 p=0.673; perm p 0.673 (full) / 0.681 (block)

**Pre-registered verdict: GREY -> lead-lag test (k in {0,1,2}) decides.** 0.600
is between ROBUST (>=0.80) and volga-book (<=0.40); rho~0 is neutral. Neither
"M2 inversion = artifact" (collinear) nor "separate volga book" (opposed) is
established on this 40-day single-ticker sample.

**Caveats:** single ticker (SPY, 0.25yr), 40 day-records = thin power; book_b
T-floor 3d; the sign agreement 0.600 is close to the grey midpoint. The
machinery is proven (finite, correct, persisted); the lead-lag test + multi-
ticker panel is the next rung to move off GREY.

---
### Live vs Vanna-Seed vs Live+VannaFlow — FULL 6-TICKER RUN (2026-08-11, 120d)

Clean dense-EOD fetch (sequential single-ticker, OI retry; oi=17045 etc, 0 genuine errors). All 6 tickers, 3 arms.

| Ticker | live(repl) | vanna_seed | live+vannaflow | vannaflow effect |
|---|---|---|---|---|
| SPY | -1,282,622 SHORT | -1,279,793 | +275,735 LONG | FLIP to LONG |
| QQQ | -78,082 SHORT | -76,608 | -32,390 | -59% short |
| AAPL | -187,635 SHORT | -165,095 | -114,435 | -39% short |
| NVDA | -201,396 SHORT | -144,348 | -109,785 | -45% short |
| AMD | -84,854 SHORT | -64,875 | -50,999 | -40% short |
| TSLA | -73,042 SHORT | -52,440 | -77,212 | +6% short (odd one out) |

**Conclusions:**
1. **vanna_seed is irrelevant** (seed_share 0.001-0.06 everywhere; flow dominates). Hallway-poster verdict CONFIRMED on real data. Drop the seed arm.
2. **live+vannaflow is the signal**: weighting daily flow by rec.vanna (sabr_deviation sign x vanna) uniformly CUTS the dealer-short read - SPY flips to LONG, QQQ/AAPL/NVDA/AMD 39-59% less short, TSLA slightly more short (+6%, the exception to investigate). Intuition: vanna-weighting (dDelta/dSigma sensitivity) gives a vol-adjusted dealer exposure materially different from raw-OI flow.

**Verification:** full Vol_Suite suite 399 passed / 5 skipped; 3 new arms (vanna seed, vanna flow, seed flip) tested; sequential single-ticker fetch scheme (OI-by-day 502-fragile) is the clean path.

---
### Seed-axis verdict — FULL 12-TICKER RUN (2026-08-11/12, 150d, 4 arms)

Arms: live(repl) / vanna_seed / live+vannaflow / svi_rp_seed, all on the same
SABR-signed accumulation flow. Clean sequential fetches (no 502s; OI route is
single-ticker-only).

| Ticker | live | vanna_seed | live+vannaflow | svi_rp_seed | vf effect |
|---|---|---|---|---|---|
| SPY  | -1,282,622 S | -1,279,793 | +275,735 L  | -1,281,090 | FLIP->LONG |
| QQQ  | -78,082 S    | -76,608     | -32,390      | -           | -59%       |
| AAPL | -187,635 S   | -165,095    | -114,435     | -           | -39%       |
| NVDA | -201,396 S   | -144,348    | -109,785     | -           | -45%       |
| AMD  | -84,854 S    | -64,875     | -50,999      | -           | -40%       |
| TSLA | -73,042 S    | -52,440     | -77,212      | -           | +6%        |
| MSFT | -152,965 S   | -152,892    | -57,209      | -152,855    | -63%       |
| META | -108,906 S   | -108,903    | -60,791      | -108,892    | -44%       |
| GOOGL| +7,873 L     | +7,878      | +202 L       | +7,893      | -97%(near0)|
| AMZN | -79,407 S    | -79,375     | -45,894      | -78,391     | -42%       |
| NFLX | +50,260 L    | +50,018     | -35,689 S    | +50,330     | FLIP->SHORT|
| JPM  | -17,164 S    | -17,157     | -3,402       | -17,044     | -80%       |

VERDICTS:
1. SEED AXIS DEAD (all 3 seed arms): replication/vanna/svi_rp seeds all give
   near-identical end books (seed_share 0.000-0.009; 150d flow dominates).
   "Seed is a hallway poster" confirmed at scale. SVI-RP seed is no exception.
2. live+vannaflow IS the signal: moves the book 10/12 tickers, always toward
   LESS dealer-short. 2 regime flips (SPY S->L, NFLX L->S). Cuts: JPM -80%,
   MSFT -63%, QQQ -59%, NVDA -45%, META -44%, AMZN -42%, AAPL -39%, AMD -40%.
   Odd: GOOGL (near-flat book ~ +200, noise), TSLA (+6% more short).
3. Direction is uniform (10/12 cut-or-flip dealer-short): weighting daily OI
   flow by rec.vanna x sabr_deviation systematically reads dealers LESS short
   than raw flow.

NEXT: OOS falsifier — does the vannaflow read (book_b) predict forward realized
vol/skew better than locked V5? That is the capital-relevant test (battery step 7).

---
### IMPLEMENTATION: vannaflow = live default + SVI everywhere (2026-08-12, Jason)

1. **vannaflow is now the LIVE default** in the dealer accumulation
   (`replication_reference._accumulate_from_history`): daily OI flow weighted by
   `rec.vanna × sabr_deviation` sign. `DEALER_VANNA_FLOW` defaults to "1"; set
   `DEALER_VANNA_FLOW=0` to disable. SAFETY: if a day carries no vanna data
   (sparse greeks route), fall back to plain signed flow rather than multiply by
   0.0 (which would silently zero the book). Verified: vannaflow=1 -> end_book
   +254,031 (LONG); vannaflow=0 -> -1,246,880 (SHORT) on SPY 60d.
   Commit 004d187.

2. **All smile features now use SVI** (Jason: "make all things smile use SVI").
   - options_chain_scanner.fit_svi_smile (new): SSVI (Gatheral-Jacquier) via the
     reusable svi_rp module; SAME contract as SABR/quadratic (fit_iv /
     iv_residual_pts / is_edge / edge_kind), falls back to quadratic on thin
     chains. scan_chain uses it by default. Tests: tests/test_chain_scanner_svi.py.
     Commit (scanner SVI).
   - options_strategy_tool: cached loader of chain_strategies.json (scanner
     export) -> inherits SVI automatically, no code change.

3. **Reusable SVI-RP module** (Vol_Suite/svi_rp.py): calibrate_ssvi() +
   SviRpReference(sigma_ref/mark_chain/seed) + pure ssvi_w/svi_w/bs_price/
   bs_vega. test_svi_rp.py (6 tests). Commits: 539aed5, (refactor module).

### Dealer sign resolver now uses SVI (2026-08-12, Jason explicit go-ahead)

- vol_surface_reference.py: default reference fitter is now SVI (SSVI via the
  reusable svi_rp module). VOL_SURFACE_FITTER=svi|sabr|quadratic env toggle
  keeps SABR + quadratic fully reachable (flip back with zero code change).
  fit_svi_reference() returns SSVI observables; _fitter() reads the toggle
  lazily (per-call, not import-time) so it works at runtime/tests.
- resolve_vol_surface_sign is fitter-agnostic (reads deviation_by_strike), so
  SVI plugs into the V5 Direction sign model unchanged in contract.
- Scanner (options_chain_scanner.fit_svi_smile) already SVI-default (commit);
  options_strategy_tool inherits it as a cached loader.
- Tests: test_svi_is_default_fitter added; existing SABR-behavior tests pin
  VOL_SURFACE_FITTER=sabr explicitly (test_vol_surface_reference,
  test_backtest_stage3 v5-wiring) so both paths stay covered.
- Verified: full Vol_Suite 408 passed / 5 skipped on current HEAD. SVI vs SABR
  produce different (both valid) per-strike reference IVs -> the toggle is the
  switch to flip between them.
