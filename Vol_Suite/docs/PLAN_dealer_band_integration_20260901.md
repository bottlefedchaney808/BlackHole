# PLAN — Delta-Band Integration into the Live Dealer Exposure Book — 2026-09-01

Status: IMPLEMENTED Phases 1-8 + 8b (2026-09-01). Phases 9.x CONDITIONAL, awaiting gates. Plan-only sections below retained as written.
Artifact history: v1 e1aa52c9… (CARL R1 input) → v2 2a8381bb… (post-review patches) → v3 this (addendum §8 + status).
Author: Hermes Agent (glm-5.3-flash), drafted from session 20260825_172550_fb796b + Obsidian `Trading/Cem charm clock insight.md`.

## 1. Grounding (read fully before drafting)

| Source | Contributes |
|---|---|
| `Vol_Suite/expiry_book_production.py` (703 lines, read fully) | Integration target: `fetch_production_result`, `ProductionDealerExposure` dataclass, `format_production_interp`. No band code exists today. |
| `C:/Users/bottl/AppData/Local/Temp/band_monitor.py` (120 lines, read fully) | Working prototype: live N, z, regime, history append. Verified live 2026-08-31 (N −$9M, z −0.93). NOT in the worktree. |
| `docs/PLAN_dealer_flows_book_20260825.md` | Companion flows-book plan; this plan is the band/timing layer, separate from the seed/flow build. |
| `docs/THEORY_dealer_flows_book_20260825.md` | Cem greek-structure constraints (delta-neutral target, vanna carry, monthly = the reset). |
| Obsidian `Trading/Cem charm clock insight.md` | All empirical numbers below; Cem's co-sign and caveats. |
| `Vol_Suite/_expiry_falsifier_cache/opex_full_book/` | 251-day greeks+OI pull (0 missed days), `ou_band_fit.json`, `delta_band_series.json`. |
| `repo conventions` (`.claude/skills/`, conftest pytest venue, fake-controller pattern) | Test venue and network-free test patterns. |

## 2. Empirical basis (the honest numbers — lead with them)

Four-day falsifier arc on the 251-day SPY full book (2026-08-28 → 08-31), Cem co-signed:

**Falsified:** "charm = 100% of dealer flow." β_charm = 0.0041 (claim needs ≈1);
β_gamma = 0.264 opposite sign; charm-leg R² = 0.136; quiet-day corr(F_charm, realized)
= −0.063 (kill shot); final-30-min concentration 12.6% on expiry days vs 10.7% on
controls (generic into-the-close flattening, not 1/(2T) charm acceleration).

**Confirmed — the band:** book net delta N(t) = Σ delta_i × OI_i × 100 is a bounded
mean-reverting process, not a random walk:
- AR(1) on ΔN = −0.177; variance ratios collapse VR(2)=0.83 → VR(5)=0.56 → VR(10)=0.39 → VR(20)=0.27 → VR(40)=0.21 (walk holds ≈1).
- OU fit: κ=41.6/yr, μ=$68M, daily step σ=$48M, stationary half-width σ_eq=$83M, half-life 4.2 days, ±2sd ≈ ±$166M (matches observed ±$250M envelope).
- Release scales with band position: median |ΔN| $21.8M (center quintile) → $47.7M (edge quintile).
- Out-of-sample tape confirmation on 71 matched days: daily two-way option shares 9.35M (center) → 12.14M (edge); corr(|z|, shares) = **+0.416** (~+30% print volume at the edge).

**Null (do not build on it):** signed direction NOT confirmed — corr(dev, dealer_net)
= −0.060; |z|>1 days oppose dev only 16/30 (53%). The edge is a VOLUME event, not a
signed-flow event, at daily resolution. Cem: "log it as verified and stop touching it
until the signed-tape pull is ready."

**Standing caveat (Cem, cite with every band number):** N from daily snapshots mixes
dealer action with mechanical delta repricing on static OI. Part of the mean reversion
may be the book marking back toward the money. The tape corr carries the conclusion;
the 4.2d half-life and $83M half-width are book-side numbers carrying that contamination.

**Consequence for the exposure book:** the book answers "how much exposure exists"
and implicitly treats exposure ≈ flow. The band says exposure only *becomes* flow
when N approaches the band edge. The band is the missing **timing layer**: it tells
the dealer model when its own numbers are tradeable. Exposure × release-probability(z)
is the upgrade.

## 3. Design

### 3.1 A load-bearing defect to fix first: N is not the same quantity live as historical

The OU fit was computed on the **full book** — all tenors (32–36 expiries/day) across
251 sessions. The live monitor computes N over only the **first 3 expiries inside a
45-day window** (it found 3: 0–2 DTE pre-OpEx). Live z therefore compares a
3-expiry numerator to a full-book band (μ=$68M, σ=$83M). That z is not the fitted
statistic. Phase 1 reconciles before any wiring:

- Recompute the historical N series restricted to the **same bucket set the live
  fetch uses** (`near`/`mid`/`far` per `_bucket()`: 2–10 / 20–45 / 80–180 DTE) from
  the existing 251-day cache. Refit OU on the like-for-like series.
- **R1-verified prerequisite: the near bucket must cover 0–10 DTE, not 2–10.** All
  251 cache days carry a 0–1 DTE expiry (verified programmatically), and the front
  expiry dominates N on OpEx eve — the exact session the monitor most matters.
  `_bucket()` (expiry_book_production.py:191) returns None for 0–1 DTE, so either
  (a) the Phase-1 refit uses a widened near bucket (0–10 DTE) and, if live wiring
  keeps `_bucket()` as-is, the plan must patch `_bucket` to match (one-line change,
  additive: `0 <= dte <= 10`), or (b) the expiry-week N discontinuity is documented
  in provenance as expected behavior. Default: (a) — widen both the refit and
  `_bucket()` together, so fit and live stay like-for-like. R1's independent
  bucketed refit (2–10) already showed structure survives bucketing (κ 39.1,
  σ_eq $49.3M, VR40 0.23, AR1 −0.131); the 0–10 refit must repeat this gate.
- Ship BOTH band definitions in the fit JSON (`full_book` and `bucketed`), use
  `bucketed` for live z. Expect μ and σ to shrink (long tenors carry most OI);
  if the refit loses band structure (VR no longer collapses), STOP — that is a
  falsifier failure for the live wiring, not an implementation snag.

### 3.2 Module: `Vol_Suite/delta_band.py` (new, pure functions)

Promote `Temp/band_monitor.py` logic into the worktree as pure, testable functions:

- `load_band_fit(path) -> BandFit` (dataclass: mu, sigma_eq, kappa, half_life, source, fit_window) — fit JSON is data, not code; loader validates ranges (σ_eq > 0, 0 < half_life < 60d).
- `net_delta_from_books(books) -> float` — Σ delta × OI × 100 over whatever books the caller passes (no ThetaData import; input is normalized rows).
- `band_position(n, fit) -> BandPosition` (dev, z, regime) — regime bands: |z|<1 QUIET/ABSORBED, 1≤|z|<2 EDGE APPROACH (volume regime), |z|≥2 AT EDGE (release regime).
- `append_history(path, record)` — jsonl append, one line per reading.

### 3.3 Wiring into `expiry_book_production.py` (additive only)

`fetch_production_result` already merges greeks+OI per book and builds `extra_books`
(near/mid/far). Add, without changing any existing signature or field:

- New optional fields on `ProductionDealerExposure` (default `None`/`""` so every
  existing consumer and constructor call site is untouched):
  `band_n`, `band_mu`, `band_sigma`, `band_z`, `band_regime`, `band_fit_provenance`.
- Computation: after `books`/`buckets` are assembled, compute `band_n` over the
  bucketed books via `delta_band.net_delta_from_books`, position vs the bucketed fit.
  Band failure must NEVER fail the book: wrap in try/except → fields stay None,
  `band_fit_provenance="unavailable: <reason>"`. The exposure book is the contract;
  the band is decoration until proven otherwise.
- Provenance entries: fit path, fit window end date, bucket definition. Units entry:
  `band_n: "shares"`.
- `format_production_interp` gains one line, e.g.
  `Band: N=$-9M z=-0.93 QUIET/ABSORBED (mu/sd from bucketed fit JSON, fit: 0-10DTE buckets thru 20260824)`
  — values shown are placeholders; the real line always reads mu/sd from the fit
  JSON for the active definition, never hardcodes the full-book numbers.

### 3.4 Band-gated charm display (interpretation only — cost side untouched)

The engine's charm math is verified correct; the broken step is presenting `charm_1d`
as if it prints daily. Cem's verdict: cost side ≠ flow side; charm accrues inside the
band and releases in lumps (half-life 4.2d). Change the INTERP TEXT ONLY:

- Mid-band (|z|<1): label charm_1d as `accruing (absorbed in band; hedge unlikely today)`.
- |z|≥1: label as `releasing (band-edge; charm + gamma may print together)`.
- `charm_1d` itself, its units, and every computation stay byte-identical. The
  charm-falsifier numbers (β≈0.004) are the documented reason this label is honest.

### 3.5 Live history accrual

Every production fetch (and/or monitor run) appends `{ts, N, spot, z, regime,
per_expiry}` to `Vol_Suite/_expiry_falsifier_cache/opex_full_book/band_history.jsonl`.
Purpose: intraday N series for a future band refit and for the z-bucket gate (§5).
Guard: append-only, never read back into z (no self-calibration double-count until
a deliberate refit lands).

## 4. Phased breakdown

### Phase 1 — N-definition reconciliation + module promotion
- Build `delta_band.py` (§3.2). Recompute bucketed historical N from the 251-day
  cache; refit OU; write `ou_band_fit.json` with both definitions.
- Gate: bucketed series must still show VR collapse + negative AR(1). Record numbers
  either way in the fit JSON (`vr_2..vr_40`, `ar1` per definition).
- Tests: BandFit loader on fixture JSON (valid, σ_eq≤0, missing keys);
  `net_delta_from_books` hand-computed on a 2-contract fixture (delta×OI×100);
  regime boundaries exactly at |z|=1 and 2 (parametrized).

### Phase 2 — Production wiring
- Dataclass fields + computation + interp line per §3.3. Band unavailable → fields
  None, book succeeds.
- Tests: `production_result_from_rows` with synthetic rows + fixture fit → fields
  populated, provenance correct; fit-file-missing → band fields None, result still
  `status="available"`; all pre-existing fields byte-equal with and without band
  (regression against silent mutation).

### Phase 3 — Charm display gate
- §3.4 interp-only change, driven off band_regime.
- Tests: interp string contains the accrual label at z=0 and the release label at
  z=1.5; charm_1d numeric value unchanged in both.

### Phase 4 — Monitor promotion + history accrual
- Move monitor to `Vol_Suite/band_monitor.py` using `delta_band` functions and the
  bucketed fit; append history per §3.5.
- Tests: history append is one-line jsonl, monotone appends, malformed existing
  history file does not crash the run.

### Phase 5 — CONDITIONAL: z-bucket conditioning gate (pre-registered)
Before ANY signal-layer promotion (regime-conditional direction/fade logic, cron
alerts wired to trading behavior), run on the 251-day history: forward realized vol,
intraday range, and spread-capture conditional on z bucket (|z|<1, 1–2, ≥2).
Pre-registered verdict: edge buckets must show materially elevated realized range
(the +30% print-volume result already supports the volume half). If they don't,
the band stays a monitor — exactly the falsifier discipline Cem watched pass.

## 5. Scope taxonomy

**SELECTED** (committed core)
- `Vol_Suite/delta_band.py` with the N-definition reconciliation (§3.1) and both-fit JSON.
- Additive `ProductionDealerExposure` band fields + interp line + provenance.
- Band-gated charm display label (interp only).
- Monitor promotion to `Vol_Suite/band_monitor.py` + `band_history.jsonl` accrual.
- Widen `_bucket()` near bucket to 0–10 DTE together with the Phase-1 refit
  (R1-F2: all 251 cache days carry a 0–1 DTE expiry; fit and live must agree).

**CONDITIONAL** (only if its gate passes / prerequisite verifies)
- Phase 5 z-bucket conditioning backtest → any trading-signal use of z.
- Cron of the monitor every 15–30 min during session (needs a delivery target; harmless locally).
- "When does this print" strip rendered in the live 4-panel charts (dashboard surface; separate UI pass).
- Band refit from live `band_history.jsonl` once ≥60 sessions accrued.

**PRESERVED** (do not touch)
- `fetch_production_result` signature and the existing `ProductionDealerExposure`
  fields/units — every current consumer sees zero change (new fields default None).
- `charm_1d` computation, units, and all engine charm math (verified correct; only
  the display label changes).
- The existing 4-panel GEX/VEX/CEX charts and `suite_context.json` schema.
- SVI/SABR sign machinery and deadband conventions (0.01) — unrelated to this layer.

**EXCLUDED** (explicitly out of scope, with the reason)
- Signed-flow/directional edge signal — honest null (corr −0.060, 53% coin flip);
  Cem ordered stop until a signed-tape pull exists.
- Charm-as-daily-flow display (the pre-falsifier framing) — falsified, β≈0.004.
- Multi-ticker band fits — the OU fit is SPY-specific ($-scaled N, spot ≈ $760);
  extending to QQQ/etc. requires its own pull + fit, not a parameter rescale.
- Intraday OU refit / self-calibrating band — double-counts; defer until history
  accrual is deliberate.

## 6. Risks / pitfalls

1. **N mismatch (§3.1) is load-bearing.** Wiring live 3-expiry N to the full-book
   band would ship a z that has never been validated. Phase 1 exists to kill this.
2. **Repricing contamination** (Cem's caveat) — z is a regime flag, never a signed
   flow predictor. Every interp line and doc reference must carry it.
2b. **z is sign-asymmetric** (R1-F4: 251-day z min −3.83 vs max +2.09; z≤−2 on
   4.4% of days vs z≥2 on 0.4% — 11× downside skew, consistent with the put-heavy
   book). Symmetric |z| thresholds remain the display convention, but Phase-5
   pre-registration must use SIGNED z buckets (z≤−2 / −2..−1 / −1..+1 / +1..+2 /
   z≥+2), not |z| buckets, so the release regime's downside skew is measured
   rather than averaged away.
3. **EOD-fit vs intraday readings.** The OU fit is on daily close snapshots. A 10:30am
   N compared to a close-calibrated band overstates edge proximity mechanically
   (intraday greeks are hotter). Mitigation: record `ts` in history, and when enough
   intraday data accrues, fit an intraday band rather than time-warping the daily one.
4. **Fit staleness.** Fit window ends 2026-08-24. Regime shifts (crash, skew regime
   change) move μ and σ. Record `fit_window` in provenance; refit is CONDITIONAL on
   accrued history, not automatic.
5. **Spot/level dependence.** N is dollar-scaled; the band is SPY-at-$760-specific.
   No cross-ticker reuse without a new fit.
6. **Band code must never fail the book.** The exposure book is the production
   contract; all band paths are try/except-with-None.

## 7. Open questions

1. Bucket definition for the live N: reuse `_bucket()` (near/mid/far) as-is, or the
   monitor's simpler "first 3 expiries ≤45d"? Plan assumes `_bucket()` (it is what
   production already assembles) — confirm in Phase 1.
2. Should the monitor ALSO report the full-book z (both fits) during Phase 4, as a
   live comparison while confidence in the bucketed fit accrues?
3. Phase 5 gate thresholds: what counts as "materially elevated realized range"?
   Propose pre-registering: edge-bucket median range ≥ 1.25× center-bucket, else monitor-only.
4. History file location: inside `_expiry_falsifier_cache` (research cache) vs a
   production data dir — cache is where the fit lives today, but "cache" naming
   invites deletion (the 205MB `opex_event_study` purge precedent).

## 8. ADDENDUM (2026-09-01, Jason) — Two-book architecture: exposure snapshot + assumed dealer position

Two books, one engine. Both print the SAME exposure outputs (GEX, DEX, charm,
vanna, band z/regime — the band fields are already on `ProductionDealerExposure`);
they differ only in the N source:

| Book | N source | Meaning | Changes |
|---|---|---|---|
| **Expiry exposure book** (exists) | Live chain snapshot per expiry | Total exposure *owed* on that expiry, right now | **STRIP the current 1d+flow layer** — becomes a pure snapshot. The `apply_vannacharm_flow` / scanner_trades intraday layer moves OUT of this book. |
| **Assumed dealer position book** (new) | Accumulated flow over a rolling 150d lookback | What we think dealers are actually holding/doing, built from flow | New accumulator; neutral start (no seed). |

### 8.1 Seed = nil — LOCKED (evidence-backed)
The seed question left open in `PLAN_dealer_flows_book_20260825.md` §3/§7 is
answered by the falsifier arc: the band half-life is ~4.5d (bucketed fit), so a
seed's contribution to a 150d accumulator decays to e^(−150/4.5) ≈ 0. Seed = nil
is not a simplification; it is the justified default. Neutral start, accumulate
flow, the band gates release timing.

### 8.2 Flow sign for the position book — use the existing machinery
Reuse `resolve_vol_surface_sign` (vol_surface_reference.py:593) as-is:
- Reference smile: SVI default (`VOL_SURFACE_FITTER` env, SABR fallback), per-strike
  deviation `deviation_by_strike`.
- **Deadband 0.01 (1 vol pt)** — `IV_DEADBAND_VOL` = `BOOK_SIGN_DEADBAND` = 0.01.
  dev > +0.01 → −1.0 (rich = dealer short); dev < −0.01 → +1.0 (cheap = dealer
  long); |dev| ≤ 0.01 → 0.0 (no confident read).
- **Do NOT drop or retune the deadband for the band work.** It is a per-strike
  IV-noise floor, a different object from the N band. Dropping it previously
  destroyed the accumulation signal (vol_surface_reference.py:129-132: SPY
  +254K LONG → −128K SHORT). Same deadband setting jives with the new band
  because they gate different things (per-strike sign confidence vs book-level
  release timing).
- **SELECTED VARIANT (Jason, 2026-09-01): Cem's ΔIV-signed flow (Phase 7 primary, not 9.2).**
  The position book's daily increment follows the deepdive §4.4 / signal #1 rule:
  `day_flow = Σ_strikes [ delta_oi × vanna_dealer_frame / mean|vanna| × sign(ΔIV_day) ]`
  — the **ΔIV sign flips the daily flow** (vol-down day → buy-side adds; vol-up day →
  sell-side subtracts even while the cumulative book persists), replacing the static
  per-strike SVI/SABR sign as the PRIMARY arm. The fixed-sign arm is kept for the A/B.
  - ΔIV source: **day-over-day ATM IV per expiry** (`atm_iv_otm`, expiry_book_exposure.py:535)
    — historical per-day is required for the 150d accumulator; vendor 7d ΔIV cannot
    reconstruct it. Per-day deadband: |ΔIV| ≤ 0.01 (reuse `VANNA_FLOW_DIV_DEADBAND`,
    expiry_book_production.py:19) → no confident vol-direction read → that day's flow
    contribution is 0 (not fallback −1.0; unlike per-strike sign, a missing vol read must
    not invent a direction).
  - Vanna weighting ON (full variant): `vanna / day_mean_abs_vanna` (same normalization
    as replication_reference.py:926-929).
  - The static SVI/SABR per-strike sign (§8.2, deadband 0.01 → fallback −1.0) is kept as
    arm (a) for the Phase 9.2 A/B and as per-day fallback when ATM IV is unavailable.
  - Cem's falsifier gates (deepdive #2/#3): accumulated book must show sustained negative
    excursion in vol-spike windows (VIX > ~30-35) and same-sign persistence ~90%+ in
    calm regimes (VIX < 20, contango). Pre-register both for the Phase 9.1 quality gate.
- Known variant (Cem deep-dive, `[UNVERIFIED]`): sign daily increments by
  ΔIV × vanna exposure instead of static per-strike sign. ~~Log as a Phase-9.2
  A/B, not a blocker.~~ **PROMOTED TO PRIMARY per Jason 2026-09-01 — see above.**

### 8.3 Direction caveat (carried from the falsifier, honest)
Accumulated flow gives a **magnitude/inventory** read. The signed-direction test
was an honest null (corr −0.060, 53% coin flip at daily resolution). The position
book's "assumed dealer position" is therefore an inventory claim, NOT a
directional forecast. Jason's ruling: use the SVI/SABR sign for now, build on it
later — accepted with this caveat attached.

### 8.4 The disagreement IS signal
When exposure-book N and position-book N diverge materially, the gap is the
residual the falsifier never explained (~70% unexplained churn = customer
direction + OI staleness + band absorption). Display both books side by side;
the spread is a first-class output, not an error.

### 8.5 Phases (addendum build list)
- **Phase 6 — Snapshot conversion:** remove the 1d+flow layer from the expiry
  exposure book path (move `apply_vannacharm_flow` usage to the position book;
  keep the function). Tests: snapshot book produces identical output with
  quote_rows=[] as today's book with empty flow; flow fields no longer populated
  on the exposure book.
- **Phase 7 — Position accumulator:** new `Vol_Suite/dealer_position_book.py`,
  150d rolling window, daily flow per §8.2 with the **Cem ΔIV-signed variant as
  primary** (day-over-day ATM ΔIV, 0.01 deadband → 0-contribution; vanna-weighted
  `vanna / mean|vanna|`; static SVI/SABR sign kept as A/B arm + fallback),
  neutral seed, same output dataclass as the exposure book. Reuses
  `replication_reference` day-loop conventions (delta_oi, sparse-gap skip) but
  window = rolling 150d, not OpEx-cycle. Tests: hand-computed 3-day accumulation
  (vol-down day adds, vol-up day subtracts, |ΔIV|≤0.01 day contributes 0);
  deadband zero-sign rows contribute 0; window rollover drops day 1.
- **Phase 8 — Dual-book output:** production layer returns both books; interp
  prints both N lines + the spread. Tests: both books present, spread computed,
  band fields populated on BOTH.
- **Phase 8b — 4-panel review (Jason):** after both books exist, review the
  dealer 4-panel output set — one panel set per book (exposure snapshot vs
  assumed dealer position), decide per-panel content, verify the flow layer
  renders on the position book only, and check chart labels/interp reflect
  which book they came from. Review, not rebuild.
- **Phase 9 — CONDITIONAL:** (9.1) convergence/quality gate — position-book N vs
  realized dealer-side tape across the 150d window, INCLUDING Cem's two
  falsifier gates from §8.2 (vol-spike negative excursion, calm-regime
  persistence); (9.2) fixed-sign vs ΔIV-signed A/B (ΔIV-signed is primary;
  this measures how much the static sign differs, not to replace it);
  (9.3) spread-as-signal backtest, pre-registered before any trading use.

