# PLAN — Dealer Direction: Judged Winner + Pre-Plan Spec

> **SUPERSEDED 2026-08-09** — the interim states described here (headless
> default `vol_surface_replication`, `DEALER_SIGN_MODEL` env passthrough,
> direction as an opt-in) are OBSOLETE. Shipped reality (commit `9366d1a` +
> 2026-08-09 lock): `CANONICAL_SIGN_MODEL='direction'` is the ONLY sign model
> (`VALID_SIGN_MODELS=(CANONICAL_SIGN_MODEL,)`), `DEALER_SIGN_MODEL` was
> deleted (no env selector exists), per-expiry mode is hard-coded to
> `DEALER_DIRECTION_PER_EXPIRY='sabr_deviation'` with 150-day accumulation.
> The debate verdict below (direction at min_score 1) remains the historical
> basis for the winner; the "Phase 4" integration details are historical only.

**Status:** DRAFT 2026-08-06 (plan-only; NO production code changed by the debate)
**Source:** CARL multi-agent debate (R1 = 5-panelist debate, R2 = cross-examination, R3 = judgment)
**Artifact reviewed:** `CARL_reference_dealer_direction.md` (sha256 `2640c9b2…`)
**Debate rule:** judged majority — weaker than unanimous submission; dissent recorded.

---

## 1. The verdict

> **WINNER (judged majority): `sign_model='direction'` operated at
> `DEALER_DIRECTION_MIN_SCORE=1` — the whale-only direction leg — through the
> EXISTING shared NO_CALL gate (`resolve_direction_call`), with per-name
> sign-caveat suppression for AMD and SPY, and classification-only labeling.**
>
> The "hybrid" is not a separate method: the NO_CALL gate is already a shared
> output layer for every model (`dealer_positioning.py:291-308`). The winner is
> an operating configuration of the shipped V5 code, plus two amendments the
> cross-examiner required: a per-name sign-caveat registry and classification
> labeling. **Historical (superseded 2026-08-09):** the headless default claim
> below — "stays `vol_surface_replication` (locked)" — is obsolete; the shipped
> default is now `CANONICAL_SIGN_MODEL='direction'`.

**Criteria (weighted):** referee compliance 0.25 · cross-name stability 0.20 ·
non-degeneracy 0.15 · live operational utility (gate matches backtest) 0.15 ·
data cost 0.10 · simplicity 0.15.

**Scorecard:** only family with a hypothesis-consistent significant cell (AAPL
−0.0004 / perm p 0.0030; −0.0005 / 0.0015 at @3) vs v4's best p 0.15 and v2.1's
0.25 (both zero significant cells); balanced splits (NVDA 71/37, TSLA 68/40,
AMD 64/43); the ONLY variant that is both backtested AND live-firing at its
backtested gate; lowest data cost (EOD volume rows, zero extra fetches in the
single-name path); one leg, one rule, one env change.

**Dissent recorded:** v2.1 panelist (continuity ≠ evidence); v4 panelist ranks
v4 #1 on mechanism but concedes v5-AAPL "is the line's best evidence"; v5@3
panelist prefers keeping the confirmation legs but concedes the whale leg is
load-bearing. Three of five honest best_methods converge on the winner; no
panelist disputes the corrected ledger.

**Why the runner-ups lost:** v4 oi_flow — all-null in its own 90d matrix,
never pooled-tested (cousin v3 pooled null 0.798), heaviest plumbing; its case
is mechanism, not referee evidence ("untested defense is a dodge dressed as
rigor" — R2). v5@3 — identical referee evidence but live-silent 0/6 at its own
faithful gate; five legs for 1.0× result. v2.1 — continuity + coverage only;
surface family's significant cells are 2-for-2 wrong (v1-NVDA 0.011, v2-AAPL
0.047). v1/v2 — degenerate (62/1, 5/58) + significant wrong-sign cells.

**Why it wins despite AMD:** AMD falsifies the UNIFORM-sign premise
(dealer-short → higher vol everywhere), not the existence of a name-specific
dealer-positioning–vol link. The winner ships with AMD/SPY suppressed and a
classification-only claim — the evidence-consistent posture.

## 2. Corrected facts the debate established (do not let these regress)

1. **6 significant cells in the 5-name × 6-model matrix, 2 right / 4 wrong**
   (v5-AAPL@3, v5-AAPL@1 right; v1-NVDA, v2-AAPL, v5-AMD@3, v5-AMD@1 wrong).
   The referee's "4 cells, 2-2" is arithmetically WRONG — the corrected count
   is worse for the family. Family-wise Bonferroni over the 10 v5 tests keeps
   BOTH AAPL cells (0.015 / 0.03); cross-model ×30 fails whale-only (0.09).
2. **Name-dependence beats noise:** AAPL negative at BOTH independent gate
   configs, AMD positive at BOTH — noise does not reproduce sign per name.
3. **Whale premium = volume × OPTION close × 100** (`Direction/whale_scanner.py:71-75`),
   NOT spot. One ATM SPY contract ≈ $1–1.5K, not $76,977. The $25K threshold is
   still easier to clear on rich names, but ~20–30× less broken than claimed.
4. **Both pooled v5 runs are unusable** (dead v5 column `pooled_panel_v5_90d.log:28`;
   whale-only run truncated mid-fetch after 502s). The fix is in code
   (`pooled_panel_backtest.py:79`). The cross-name verdict does not exist on disk.
5. **The NO_CALL gate cannot stop confident wrong-sign days** (AMD/SPY) — hence
   per-name suppression, not gate tuning.

## 3. Escalated decisions — JASON MUST RULE (E1–E4)

- **E1 — Pooled re-run: PERMITTED BY JASON 2026-08-06, EXECUTED, RESOLVED.** Verdict (n=494,
  SPY/AAPL/NVDA/TSLA/AMD, ticker-FE, 2000 perms, whale-only gate @1, `pooled_20260806_011706/`):
  v5 is the ONLY model with a negative aggregate coef (−0.0002, t −1.13; block perm p 0.0825 —
  best of any sign model, suggestive but NOT <0.05). AAPL survived pooling (family sign holds);
  AMD's wrong sign is a name effect that does not flip the aggregate. **M2 is the only
  significant pooled relationship (perm block 0.0245) and it is INVERTED (positive)** — the
  strongest significant dealer-flow signal in the line points against the short-gamma→amplify
  premise. Consequences: winner's classification-only labeling is now mandatory (pooled result
  supports book-side sign, not prediction); no model validates the channel's core premise.
- **E2 — Product purpose: RECOMMEND the chart prints a read, but a LABELED classification,
  not a forecast.** Pure refusal guts the product; confident printing overclaims (referee
  confidence 0.90: no model predicts daily vol). Middle path: AMPLIFY/DAMPEN/NO_CALL + per-name
  caveat flags + classification label + evidence table beside every call. *Commercial call, not
  statistical.*
- **E3 — Release narrative: disclosure is mandatory if v5 ships** (see §6). *Jason decides:*
  ship under classification-only labeling (recommended) or wait for the E1 pooled verdict
  (stricter, defensible). E1 has now run — the disclosure list in §6 gains the pooled numbers.
- **E4 — Premium-relative threshold: APPROVED BY JASON, IMPLEMENTED (Phase 3).**
  `WHALE_THRESHOLD_BPS` (env) + `threshold_bps` (param) in `Direction/whale_scanner.py`:
  bar = bps/10_000 × spot × 100 (bps of one-contract ATM notional); 0/unset = legacy $25K
  unchanged. 5 new network-free tests (`Direction/tests/test_whale_scanner.py`); full package
  suite 71 passed. **CALIBRATION SWEEP DONE (2026-08-06, `bps_calibration_sweep.sh`):**
  whale-only 90d referee at bps ∈ {0, 1500, 3000} on SPY+AAPL — AAPL perm p: 0.0030 → 0.0045
  → **0.0015** (3000 bps is the strongest); SPY wrong-direction drift: 0.072 → 0.133 → **0.265**
  (3000 bps suppresses the noise most). **Recommended winner-config default:
  `WHALE_THRESHOLD_BPS=3000`** (30% of one-contract ATM notional — selects only outsized
  flow; stricter bar both strengthens AAPL and quiets SPY). Legacy $25K (0) stays the
  backward-compat default in code.

## 4. Pre-Plan Spec — implementation phases (TDD; tests network-free)

Venue for all runs: `env -u PYTHONPATH -u VIRTUAL_ENV Financial_Dev_Env/bin/python3 -m pytest …`

**Phase 1 — Winner operating config.** `Vol_Suite/dealer_positioning.py`:
- `DEALER_DIRECTION_SUPPRESS_SIGNS` env (comma-separated tickers, default `"AMD,SPY"`)
  + `_direction_sign_caveats(ticker)` returning the registry row.
- `read_label` field on `DealerPositioningResult` ("book-side classification, not a vol forecast")
  when `sign_model='direction'` and ticker is suppressed.
- NO_CALL gate untouched, applied after sign resolution (already the case).
- Tests: `Vol_Suite/tests/test_dealer_positioning_direction.py` — env parsing,
  suppression lookup, label injection, gate interaction (suppression changes the
  label, never the call).

**Phase 2 — Sign-caveat registry as data.** New `Vol_Suite/direction_sign_caveats.json`:
per-name backtest sign — AAPL −0.0004/0.0030 ✓, AMD +0.0059/0.0055 ✗ suppressed,
SPY +0.0001/0.072 ✗ suppressed, NVDA −0.0002/0.159, TSLA −0.0008/0.553, QQQ gap —
rendered beside every call. Tests: schema validation + report-string rendering.

**Phase 3 — Threshold calibration (E4).** `Direction/whale_scanner.py`:
`WHALE_THRESHOLD_BPS` (default 0 → current $25K absolute unchanged), relative
mode = bps × ATM notional. **IMPLEMENTED 2026-08-06** (env + `threshold_bps`
param, 5 tests, 71/71 green). Remaining: calibration sweep — whale-only 90d
referee at bps thresholds on SPY/AAPL to pick a defensible default.

**Phase 4 — Function integration.** *(Historical — superseded 2026-08-09: the
`DEALER_SIGN_MODEL` passthrough below never shipped as described; sign model is
now fixed to direction with no env selector.)*
- ~~`quant_bridge.py` MODULE_REGISTRY: env passthrough `DEALER_SIGN_MODEL=direction`
  + `DEALER_DIRECTION_MIN_SCORE=1` (no new module).~~ Deleted 2026-08-09 — no
  env override exists; only `DEALER_DIRECTION_MIN_SCORE` remains forwardable.
- `volatility_suite.py:1113` comment update; `_SIGN_MODEL_LABELS` (×2) →
  "Direction Whale-Flow (classification)"; report prints `direction_call` +
  `read_label` + registry row.
- gex_scanner + quant plugin Options tab: direction@1 selector, NO_CALL reason surfaced.

## 5. Expansion track

- Intraday/trade-level research (the track the gate defers to — the only
  remaining honest lever per the spec).
- M3/M5 robustness: multi-expiry inverse-TTE pooling (SPEC:18-19).
- bps-threshold calibration sweep; per-name registry growth (each newly
  backtested name → registry row).
- The pooled verdict when E1 is permitted.

## 6. Risks & Disclosure (a release note must say, verbatim-ish)

1. AAPL: one name, one expiry (20261120), n=108, unpooled; p 0.0015/0.0030
   survives family-wise correction (×10: 0.015/0.03) but NOT cross-model ×30
   (whale-only 0.09 fails).
2. AMD significant wrong-sign at both gates — sign is name-dependent; the
   uniform-sign premise is falsified.
3. Corrected matrix: 6 significant cells, 2R/4W — do not launder the wrong
   "4-2-2" statistic.
4. SPY 0.072 wrong-direction trend — suppressed by the registry.
5. Shipped gate (min_score=1) equals the backtested gate — no mismatch.
6. Premium = volume × option close × 100; $25K threshold is calibration debt,
   not a validation.
7. Classification read only — not a vol forecast.
8. Failure modes: 502s, row drops, outage → silent → NO_CALL (acceptable).
9. **Pooled (n=494, 5 names, ticker-FE, 2000 perms):** v5 is the only negative-sign
   model (−0.0002, t −1.13, block perm p 0.0825 — NOT <0.05); AAPL survived pooling;
   M2 is the only significant pooled relation (block p 0.0245) and it is INVERTED
   (positive) — no model validates the short-gamma→amplify premise in aggregate.

## 7. Non-goals (locked, not relitigated)

Demeterfi replication byte-for-byte untouched · headless default stays
`vol_surface_replication` until a release-note flip ~~(historical — flipped
2026-08-09 to CANONICAL_SIGN_MODEL='direction')~~ · no daily-horizon
prediction claim · no removal of any sign model (all selectable) · no
suppression of the NO_CALL gate itself · no new data dependency beyond existing
EOD volume rows.

## 8. Debate record (rounds)

- **R1 (5-panelist debate):** v4 oi_flow SHIP_WITH_CAVEATS · v5@3 NEEDS_REVISION ·
  v5@1 SHIP_WITH_CAVEATS · v2.1 SHIP_WITH_CAVEATS · referee → hybrid.
- **R2 (cross-examination):** hybrid NEEDS_REVISION as-specified (per-name
  suppression missing); referee arithmetic corrected (6 cells 2R/4W); premium
  math corrected (option close); gate-mismatch resolved (ship @1 = intersection
  of backtested {1,3} and live {1,2}).
- **R3 (judgment):** winner declared (above); E1–E4 recommendations; spec.
- **Convergence:** partial — human-owned decisions E1–E4 remain; reviewer
  diversity reduced (single model family; fresh contexts).
