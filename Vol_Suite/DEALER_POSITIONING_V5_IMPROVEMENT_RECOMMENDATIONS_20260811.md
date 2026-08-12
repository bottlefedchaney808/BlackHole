# Dealer Positioning — V5 Direction Improvement Recommendations (2026-08-11)

> **Status: RECOMMENDATIONS, not a plan.** This is the output of a CARL-style
> multi-lens debate reviewing `dealer_positioning_package_20260811.zip`
> (the "V5 Direction" model, locked 2026-08-09 in the sibling WSL repo)
> against the model actually implemented in this Windows tree
> (`Vol_Suite/dealer_positioning.py`, `oi_heuristic`/`replication`/
> `vol_surface_replication`). Nothing here has been implemented or approved.
> **Do not port V5 into this Windows tree based on this document alone** —
> that's a separate decision (see §0).

## 0. Context and a live risk to check first

This doc originated from two rounds of review:

1. A CARL comparative debate (methods, evidence, pros/cons of current vs.
   proposed) — see the companion analysis; bottom line: V5 wins on
   architecture/governance/honesty-gating, but is **not yet an empirically
   proven win** — its headline per-expiry result is block-perm p=0.068 (not
   significant), and its own "winning" backtest row does not reproduce under
   its shipped default configuration.
2. A five-lens improvement debate (this doc) — five independent reviewers
   (Flow Integrity, Surface Mechanics, Statistical Rigor, Decision Hygiene,
   Devil's Advocate), each with full file access, asked to propose concrete
   testable improvements without relitigating the locked sign-model decision.

**Live risk, independently re-confirmed by the Decision Hygiene reviewer:**
this Windows tree's `Vol_Suite/dealer_positioning.py` (last touched
2026-08-01, commit `a4a6f0e`) still has
`VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')`
— no `direction`, no `CANONICAL_SIGN_MODEL`, no gate, none of the 08-09 lock.
The package's own record treats this Windows copy as an intentional,
undisturbed development snapshot ("its from my other suite in windows using
the old model... not make them agree"). That's fine as a standing decision —
but it means **any live/dashboard run on this machine will silently run
ungated `vol_surface_replication`**, not V5, if anyone assumes otherwise.
Recommendation R-T1-5 below (verify which checkout is live) exists
specifically to prevent that assumption from going unchecked.

## 1. Method

Five subagents, each briefed on the same package and the same "don't
relitigate the locked sign-model choice" constraint, independently reviewed
different facets and proposed ranked, concrete, testable improvements:

| Lens | Scope |
|---|---|
| **Flow Integrity** | Is the whale/flow signal itself (the thing that determines the sign) as good as it could be? |
| **Surface Mechanics** | Is the SABR per-expiry decomposition mechanism sound, robust, correctly calibrated? |
| **Statistical Rigor** | Is the claimed evidence for the model actually solid? What would make it solid? |
| **Decision Hygiene** | Is the NO_CALL gate correctly calibrated and monitored? Is the "lock + delete alternates" governance model itself introducing new risk? |
| **Devil's Advocate** | Should the whole architecture-level direction be second-guessed before investing further in incremental fixes? |

Two items were independently proposed by more than one lens without
prompting — flagged inline below — which is a stronger signal than any
single lens's opinion.

## 2. Recommendations, by tier

Tiering is by cost/urgency: Tier 1 uses only data already collected (no new
runs); Tier 2 is cheap new runs on existing infrastructure; Tier 3 is real
but well-scoped code work; Tier 4 is bigger or explicitly gated on earlier
tiers landing first.

### Tier 1 — cheap, no new data pulls, resolve the biggest open questions

**R-T1-1. Add a magnitude-only "no sign" baseline arm to the existing backtest sweep**
*Lens: Devil's Advocate.*
Nobody in the package's history has run the actual null-hypothesis-for-
architecture test: pure `|gamma|×OI` (or `|dollar gamma|×OI`) by strike, no
±1 directional guess at all. Every debate round compared sign models against
*each other*, never against "don't guess sign." Given the package's own
finding that the one significant pooled relation (M2) is **inverted** —
points against the short-gamma-amplifies premise — there's a live
possibility V5's sign is adding noise, not information, relative to
reporting magnitude and staying silent on direction.
*Test:* add a "V0 magnitude-only" arm to the existing per-expiry backtest
harness (§5.2 of the master spec); compare against whatever the honest
evaluation target is.
*Cost:* ~1 day. Reuses 100% of existing infrastructure.

**R-T1-2. Pull v1's regime counts for the SPY/AAPL per-expiry test and run the existing degenerate-split diagnostic**
*Lens: Statistical Rigor.*
`docs/per-expiry-direction-backtest-20260808.md` reports, almost in
passing, that "v1 (oi_heuristic) was consistently significant across all
variants: All runs: block p < 0.02" on the same SPY+AAPL test that produced
V5's headline number (block p 0.068) — beating it. This is never reconciled
against the pooled panel where v1 was null with a documented false-positive
pattern (MU's degenerate 1-of-112 split). No regime-count (long/short split)
is reported for v1 on this test, unlike every other model discussed.
*Test:* pull the split; run the same degenerate-split check that caught the
MU false positive.
*Resolution criterion:* balanced split + surviving block-perm test ⇒ v1 is a
second, independently significant result needing its own accounting.
Near-constant split ⇒ presumptively the same artifact; the "v1 outperformed
V5 here" claim should be struck as materially misleading unqualified.
*Cost:* cheap — query against already-collected backtest artifacts.

**R-T1-3. Apply that same degenerate-split diagnostic to V5's own AAPL leg**
*Lens: Statistical Rigor.*
V5's AAPL result is 46/46 (100% short-gamma, zero classification variance)
across all four per-expiry variants — more degenerate than the MU split
already thrown out as a false positive elsewhere in the same package. This
is currently a double standard: the skepticism applied to v1 hasn't been
applied to V5's own headline number.
*Test:* recompute the SPY+AAPL result as SPY-only (drop AAPL's contentless
leg).
*Resolution criterion:* SPY-alone still significant ⇒ the pooled result is
real, AAPL was confirmatory. SPY-alone materially weaker ⇒ the reported
t=−1.919/p=0.068 is substantially a ticker-FE pooling artifact against a
constant series, and the headline number needs re-reporting as SPY-only
with an honest N.
*Cost:* cheap — re-run OLS/permutation with one ticker dropped.

**R-T1-4. Multiple-comparisons correction across the full searched model/mode/knob space**
*Lens: Statistical Rigor.*
Across the 08-05→08-11 timeline: a 5×6 model×config matrix (08-06 CARL),
4 per-expiry modes (08-08), plus the proposed min_score×bps sweep (R-T2-1)
— dozens of cells searched, no family-wise correction applied anywhere to
the "closest to significance" p=0.068/p=0.0135 pair. This is the exact
failure class (search-space inflation) that already produced the MU false
positive.
*Test:* enumerate every distinct comparison actually run; apply Holm-
Bonferroni (or equivalent) to the sabr_deviation cell.
*Resolution criterion:* survives correction ⇒ the existing "suggestive, not
significant" framing (already used at 0.0825 in §4.2) is validated as
appropriately cautious. Doesn't survive ⇒ "closest to significance" framing
throughout §5.2 needs to drop to "not distinguishable from the other three
modes at this sample size."
*Cost:* cheap — arithmetic over already-collected p-values.

**R-T1-5. Confirm which checkout is live before trusting any "current model" claim for a real run**
*Lens: Decision Hygiene.* See §0 above — independently re-confirmed as a
live risk, not just a documentation nuance.
*Action:* `grep -n "CANONICAL_SIGN_MODEL\|VALID_SIGN_MODELS"` on whichever
tree is about to execute a non-demo run, before trusting the output as "V5
Direction." Consider a startup assertion in `dealer_positioning.py`'s
module-load path (fail loud if the sign model isn't what the caller
expects) rather than relying on humans remembering to check.
*Cost:* cheap to check now; ~3 lines to make permanent.

### Tier 2 — cheap new runs, existing infrastructure

**R-T2-1. Run the min_score × bps sweep the package itself promised and never executed**
*Lens: Statistical Rigor.* §8 item 2 of the master spec: "Do not claim the
model validated or invalidated on backtest rows until [this] lands." As of
the package date, it hadn't. Grid: `DEALER_DIRECTION_MIN_SCORE ∈ {1,2,3}` ×
`WHALE_THRESHOLD_BPS ∈ {0, 3000, ...}` on SPY+AAPL, gated model, recording
coef/t/perm-p **and** short-count splits per cell.
*Resolution criterion:* a cell reproduces the recorded row (coef ±0.0001,
short-counts ±2/46) **and** is the shipped default or a documented,
deliberate override ⇒ reproducibility gap closed. No cell reproduces it ⇒
the recorded decision row must be formally retracted as evidence for the
current default.
*Cost:* cheap — env-var sweep, no code change, infra already staged.

**R-T2-2. Fix the whale scanner's single-nearest-expiry selection**
*Lens: Flow Integrity.* `Direction/whale_scanner.py::scan()` (WSL tree)
picks the single soonest available expiry — virtually always 0DTE/next-daily
on SPY/QQQ — and feeds only that expiry's chain to the classifier. This is
the literal upstream root cause of the documented 0DTE-contamination item
(ESC-1), one level deeper than "ticker-level bias applied uniformly to all
expiries."
*Fix:* pull chains for the nearest N (3-5) liquid expiries; either compute a
per-bucket (short-dated vs. monthly) bias, or volume-weight the aggregate so
0DTE can't structurally dominate by being picked first.
*Test:* shadow-run both the current and multi-expiry-weighted read for 30
trading days (this is exactly SHADOW-2, already specified but unconfirmed
deployed — see R-T3-3); measure sign-disagreement rate and correlation with
downstream backtest outcomes.
*Cost:* cheap — the chain-fetch/OI plumbing already exists; this is a
`scan()` rewrite, not new infrastructure. **Flagged as the single
highest-leverage fix on the flow side**, since sign is 100% whale-derived
today (the other 4 Direction signals only gate conviction, never supply an
alternate direction — see R-T3-6/7 for why this matters).

**R-T2-3. Sweep `CALL_PUT_RATIO` jointly with `WHALE_THRESHOLD_BPS`**
*Lens: Flow Integrity.* `_CALL_PUT_RATIO = 1.2` (hardcoded) controls how
easily the classifier goes neutral (⇒ NO_CALL downstream) — exactly as
load-bearing as `WHALE_THRESHOLD_BPS`, which *was* calibrated (3000 bps),
but `CALL_PUT_RATIO` never has been.
*Test:* 2D grid sweep of `(WHALE_THRESHOLD_BPS, CALL_PUT_RATIO)` jointly
(not bps alone, since they interact) on the existing calibration panel.
*Cost:* cheap — reuses the existing calibration harness (E4).

**R-T2-4. Soft/continuous sign confidence instead of a hard deadband cutoff**
*Lens: Surface Mechanics.* Any hard cutoff (flat, moneyness-scaled, or
RMSE-scaled) has a deviation of `deadband−ε` and `deadband+ε` producing
maximally different outputs (0 vs. full ±1) despite being economically
almost identical — the textbook mechanism for the exact class of noise-
driven sign flip ESC-2/ESC-3 worry about. A deadband relocates the
discontinuity; it doesn't remove it.
*Proposal:* `confidence = tanh(dev / deadband)`; weight each strike's
contribution by `|confidence|` instead of full-strength ±1.
*Test:* rerun the 08-08 SPY+AAPL panel with soft-sign vs. hard-deadband-sign;
diff the day-by-day sign map (not just aggregate coef/t) and check whether
disagreement clusters near the deadband boundary as predicted.
*Cost:* cheap to prototype (post-processing on already-computed deviations);
moderate to ship (downstream NO_CALL gate thresholds tuned against the old
binary output would need re-calibrating against the new dynamic range).

**R-T2-5. Hard-code a permanent gate-existence regression test**
*Lens: Decision Hygiene.* The gate was removed once (`5f9833c`) with a
plausible-sounding rationale and shipped silently; nothing caught it until a
human noticed a sign flip on a specific backtest panel days later. No
existing test is a *permanent* regression guard against this specific
regression — it was re-verified reactively, not prevented.
*Test:* one un-skippable test asserting `fallback_bias == 0` on realistic
(not degenerate-mock) input ⇒ output exactly `0.0`/NO_CALL, in the fast/
default test tier. Verify by replaying `5f9833c`'s diff and confirming this
test fails red before any full-suite run.
*Cost:* cheap — ~10 lines; the expensive part is remembering to write it.

**R-T2-6. Tripwire `DEALER_DIRECTION_GATE=0` against live-run leakage**
*Lens: Decision Hygiene.* Documented as "BACKTEST REPRODUCTION ONLY, never
live" but enforced only by a docstring — a bare env var with no code-level
guard, in an environment already noted to have concurrent-writer/stale-env
risk.
*Fix:* add a check at the live (non-backtest) entry point that raises or
loudly warns if this var is set outside a recognized backtest invocation
path.
*Cost:* cheap.

### Tier 3 — moderate, real code work, well-scoped

**R-T3-1. Build the accumulation seam in the backtest's v5 leg**
*Lens: Statistical Rigor AND Devil's Advocate — independently proposed by
both, unprompted. Treat this as the strongest-corroborated single item on
the whole list.*
§8 item 1 of the master spec: "even gated+SABR-wired rows understate the
live model" — the backtest uses per-day level OI; the live model consumes a
150-day accumulated book. Every number currently in the package's evidence
section, including the closest-to-reproduction config found so far, was
generated against a structurally different input than what the live model
actually uses. This is not fine-tuning — it's testing a different model and
calling it validation.
*Test:* wire `replication_reference.get_accumulated_position` /
`_accumulate_from_history` into the backtest's day-record builder; re-run
R-T2-1's sweep on the corrected backtest.
*Resolution criterion:* results diverge from the level-OI backtest ⇒ the
entire §5 evidence table is invalidated for live-model claims and must be
regenerated. Results are stable ⇒ the level-OI backtest can be retroactively
certified as an adequate proxy, but that certification must be stated
explicitly, not assumed.
*Cost:* moderate — a real code change, plus re-running everything downstream
of it. **Devil's Advocate's companion recommendation: stop tuning further
daily-horizon parameters (deadbands, thresholds, min_scores) until this
lands** — tuning knobs on a backtest that doesn't reproduce its own decision
risks presenting a "better" sweep result with false confidence next time
someone asks "is this validated."

**R-T3-2. Fit-quality (RMSE) + moneyness-scaled deadband, superseding the plain ESC-2 formula**
*Lens: Surface Mechanics.* The already-proposed ESC-2 fix
(`deadband = IV_DEADBAND_VOL * (1 + 2*abs(log(K/spot)))`) fixes the
liquidity-proxy problem at the wings but ignores a signal already sitting in
the SABR fit output and currently discarded at the sign-resolution step: fit
RMSE. A chain with a genuinely bad fit (post-earnings IV collapse, thin
quotes on an otherwise-liquid name) is unreliable everywhere, not just at
the wings — moneyness alone is blind to that.
*Proposal:* `deadband(K) = IV_DEADBAND_VOL * (1 + 2*abs(log(K/spot))) *
max(1, rmse / RMSE_REFERENCE)`.
*Test:* rerun the 08-08 panel with three variants (flat / moneyness-only /
moneyness×RMSE); compare sign-flip counts against the observed RMSE
distribution, and confirm the added suppression doesn't erase the
significant sabr_deviation edge.
*Cost:* cheap — RMSE is already computed and stored; this threads one more
field into an existing call.

**R-T3-3. Per-strike jackknife stability score, operationalizing the still-unconfirmed SHADOW-3**
*Lens: Surface Mechanics.* SHADOW-3 (illiquid-chain SABR stability monitor)
was specified 08-08 but its deployment status is unconfirmed. The
underlying finding it's meant to watch — 40-52% sign-flip rates on
<15-strike chains — is currently mitigated only by a static "all current
tickers are liquid enough" assumption, explicitly called fragile for
LEAPS/far-dated expiries even on current liquid names.
*Proposal:* leave-one-out refit per OTM strike per expiry; strikes whose
sign flips in >20% of leave-one-out refits get forced to `0.0` regardless of
deviation magnitude.
*Test:* validate against the original 40-52%-flip-rate synthetic setup, then
shadow-run (log-only) for 30 trading days across the live universe including
LEAPS.
*Cost:* moderate — refitting `n_strikes`× per expiry multiplies compute;
consider gating the jackknife to only expiries below some `n_points`
threshold (e.g. <20) to target exactly the illiquid case and bound cost.

**R-T3-4. Ship the three specified shadow tests**
*Lens: Decision Hygiene.* SHADOW-1/2/3 were specified 08-08 with named env
flags and alert thresholds — grepped, confirmed absent from both the live
call path and the tests. This is the actual safety net for the failure mode
(silent sign-source drift) that has already caused an incident once, and
it's currently vaporware.
*Test:* after implementing, force synthetic triggers for each (fabricated
0DTE-dominated day for SHADOW-2, <15-strike chain for SHADOW-3) and confirm
the alerts fire; then check 5 days of real logs to confirm the hooks are
wired into the live call path, not just defined.
*Cost:* moderate — logging hooks are cheap; wiring them into every call site
(dealer 4-panel, scanner, backtest) per the package's own "one shared
sign-resolution function" rule is real work.

**R-T3-5. Aggressor-side (buy/sell) classification in the whale scanner**
*Lens: Flow Integrity.* `classify_whale_bias` sums `volume × close × 100`
per side and calls the larger side the bias — it never asks whether that
volume was buyer- or seller-initiated. A large *sold* call (covered-write)
prints as "bullish" premium even though it's flow a dealer would hedge in
the opposite direction of a bought call. Bid/ask quote data already exists
in `Direction/data.py` to build a near-ask=buy/near-bid=sell proxy; it's
just not wired into the classifier.
*Test:* backfill whale-flagged strikes with aggressor-inferred sign vs. the
current right-only sign; compare both against the existing 08-08 per-expiry
backtest panel.
*Cost:* moderate — needs a new data-fetch path (quote-at-time-of-volume,
not just EOD close); more ThetaData rate/availability risk than R-T2-2/3.

**R-T3-6. Distinguish opening from closing flow via day-over-day ΔOI**
*Lens: Flow Integrity.* Whale-sized volume is currently treated as fresh
directional conviction regardless of whether it's a new position or an
unwind — opposite economic signals. `get_chain_oi` already exists; pulling
it for two consecutive sessions and computing ΔOI per strike would let the
classifier weight/gate volume by whether OI actually grew at that strike.
*Test:* recompute the whale-bias column with an OI-confirmed-opening filter;
rerun the 08-08-style panel; check whether filtering closing-flow strikes
improves coef/t/perm-p or just shrinks sample size for no gain (a plausible,
worth-knowing null result).
*Cost:* moderate — plumbing exists but needs a second day's fetch, correct
handling of the existing cache TTL, and care around OI-reporting lag.

**R-T3-7. Mechanize "sign-source changed → propagate everywhere" as a pre-commit checklist**
*Lens: Decision Hygiene.* Three separate incidents in one week (gate
removal, two-vanna divergence, stale-label drift) share one root cause: a
change to what determines the sign didn't propagate to every place that
describes or consumes that decision. The two-vanna fix already established
the right pattern for the code-parity instance (cross-file parity test
between `dealer_positioning.py` and `options_chain_scanner.py`) but it's
documented, not enforced, and doesn't cover labels/docs/UI strings — exactly
where the stale `_gamma_subtitles` bug fell through.
*Test:* write a grep-based pre-commit check requiring N specific files
touched whenever the sign-resolution function changes (seed the file list
from the two-vanna doc's own audit, which already found 6 stale locations);
replay the `5f9833c` diff in isolation and confirm the hook blocks it; add
the label fix and confirm it passes.
*Cost:* moderate — the check itself is cheap; enumerating the complete
"must also touch" list requires a careful audit.

### Tier 4 — bigger, or explicitly gated on earlier tiers

**R-T4-1. Pre-registered out-of-sample replication before any "validated" claim ships**
*Lens: Statistical Rigor.* Every number in the package's evidence section is
in-sample relative to the search that produced it — the config was chosen
by looking at which combination scored best on SPY+AAPL, then "validated"
by trying to reproduce that same number on the same tickers. That's
confirmation, not validation.
*Test:* once R-T2-1 + R-T3-1 identify a specific reproducing config, freeze
it and re-test unmodified on a disjoint sample — different date range and/or
a genuinely untouched liquid ticker — with a pre-registered sign/magnitude
prediction.
*Resolution criterion:* sign correct + effect size within ~2-3× of the
in-sample estimate ⇒ real signal. Sign flips or vanishes ⇒ the SPY+AAPL
result was sample-specific overfitting, and the per-expiry-mode ranking
needs to be re-opened, not treated as settled.
*Cost:* moderate-to-expensive — fresh ThetaData pulls; the package already
notes 5-ticker/120-day pulls hit circuit breakers, so budget staged fetches.

**R-T4-2. Audit rendered artifacts for magnitude/forecast-implying language**
*Lens: Devil's Advocate.* The package's own §4.2 conclusion is
"classification-only labeling is mandatory" — but the described system
renders real dollar gamma/vanna/delta/charm numbers on charts that a human
reads to make sized decisions. Nothing in the record shows a UI change
enforcing the classification-only claim; the fixes so far (two-vanna saga)
have been about making the *numbers* internally consistent, not about
constraining what claim they're allowed to carry to the viewer.
*Action:* audit chart labels, CSV/JSON export field names, and dashboard
copy for language implying magnitude/forecast confidence (e.g. "net dealer
gamma exposure: $X" reads as a hedge-flow forecast) vs. what's defensible
("book is more likely short than long here, low confidence, no magnitude
claim").
*Cost:* low (~half a day, docs/UI pass) — currently zero work done on this
despite being flagged mandatory by the program's own strongest finding.

**R-T4-3. Scope (not build) the intraday/trade-level track**
*Lens: Devil's Advocate.* Both the original empirical referee
(`DEALER_METHOD_V3_SPEC.md`) and the master spec (§8 item 3) independently
name this as "the only remaining lever" for real predictive skill — yet the
entire 08-04→08-11 timeline is 100% daily-horizon SABR/accumulation/whale-
threshold work, with a hard ceiling already measured (endogeneity, not
power — more daily-horizon tuning won't fix it).
*Action:* not "build it" — scope what trade-level/intraday data is actually
available (tick trades vs. quotes, latency, cost), and write a short
feasibility note including a fast/cheap version of the original empirical-
referee test (pooled panel, permutation null) run at intraday horizon, to
check whether the same endogeneity problem reappears before committing real
build effort.
*Cost:* scoping only, ~1-2 days. The point is to stop the track being purely
aspirational in every debate round while zero hours go to it.

**R-T3-6/7 note (referenced above):** the Flow Integrity reviewer found,
independent of any package doc, that only `whale_scanner.py` ever returns a
directional field — Elliott Wave, Bollinger, trend, and liquidity signals in
the `Direction` package only ever return a fire/no-fire boolean that raises
conviction score, never an alternate direction. So "is whale-only vs.
multi-signal settled" has a source-level answer already: for *sign*, there
is architecturally no alternate direction source to disagree with whale.
This raises the stakes on R-T2-2 (fixing whale's expiry selection) and
R-T3-5/6 (improving whale's classification) — errors there propagate into
100% of sign decisions, not a fraction, since nothing else in the composite
can veto or correct them.

## 3. What this doc does not decide

- Whether to port any of this into the Windows tree at all (§0).
- Whether the intraday track (R-T4-3) is worth real build investment — that
  depends on the scoping note's outcome, not on this document.
- Any ranking beyond the tiering above — Tier 1 items are cheap enough that
  running all five before deciding what's next is itself a reasonable plan.
