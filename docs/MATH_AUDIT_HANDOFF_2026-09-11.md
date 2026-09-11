# Math audit handoff — 2026-09-11

**Purpose.** Give a fresh math review (CARL or otherwise) current ground truth, so it spends its
budget on unreviewed surfaces instead of re-deriving `PROJECT_AUDIT_AND_SPEC.md`.

**Read this before that doc.** `docs/PROJECT_AUDIT_AND_SPEC.md` is dated 2026-08-04. Its
*architecture* sections (Parts 1–3) are substantially stale — they describe `run_unified`,
`mcp-stockflow`, and the pre-flatten repo, all since removed. Its **Part 5 math findings were not
stale**: spot-checked on 2026-09-11, findings #3, #7 and #8 were still open exactly as written,
five weeks later. Math bugs do not rot the way architecture docs do, because the files they live
in were not what the Phase 7 dashboard rewrite touched.

---

## 1. Part 5 disposition — what is closed as of 2026-09-11

Every item was re-verified against current source before being touched. That doc retracted one of
its own findings during review, so none of it was trusted blind.

| # | Finding | Status |
|---|---|---|
| 1 | `var_agg._component_var` missing `positions[i]·` | Closed earlier (2026-08) |
| 2 | NR American IV solved against BAW, not LR | **Closed** `096c85e` |
| 3 | Clayton copula ignores `corr_matrix` | **Closed** `c1ba63e` — and see §2, it was worse |
| 4 | `vrp_pct` names two different quantities | **Partially closed** `5604049` — see §3 |
| 5 | `tail_mass` unnormalized | **Partially closed** `5604049` — see §3 |
| 6 | GARCH persistence/ADF never gate the narrative | **Closed** `5604049` |
| 7 | `hedge_independent=False` is a no-op | **Closed** `c1ba63e` |
| 8 | `hist_sim` GARCH never checks `res.success` | **Closed** `c1ba63e` |
| 9 | `correlate_with_oi` rules dead | **Closed** `7ee7f11` — finding was partly stale, see §4 |
| 10 | `iv_spread_z` is not a z-score | **Closed** `7ee7f11` |
| 11–12 | Doc drift (`--demo`, options-suite skill) | Already corrected in CLAUDE.md |
| 13 | `Tools/` circular import | **Closed** `5604049` — affected all 12 tools, not 1 |
| 14, 17, 18 | Marked fixed in the doc | Unchanged |
| 15 | Test-coverage gaps | **Still open** — see §5 |
| 16 | ThetaData OI coverage hole, expiry `20261030` | **Still open** — vendor-side, not code |

---

## 2. What the 2026-08-04 review MISSED, and why

Worth reading before designing the next pass — these are the shapes that survived a full
adversarial review.

**a) The Clayton exponent.** The audit caught that `corr_matrix` was dropped (an API mismatch,
visible by reading). It did not catch that the sampler applied `(1+E/V)^(-alpha)` where Clayton
requires `^(-1/alpha)` — *while the comment on the line above documented the correct form*.
Impact: marginals had mean 0.672 instead of 0.5 at the default alpha, thinning the loss tail. A
3-name $1M book at 25% vol / 10 days reported **99% VaR of $160,897 against a true $312,749 —
understating risk by 49%.**

Why it survived: **Kendall's tau is invariant under monotone transforms of the marginals**, so
every test of the copula's *dependence* passed at every alpha. Only the marginals broke. And
`test_returns_uniform_marginals` asserted `U.min() >= 0 and U.max() <= 1` — a *range* check
wearing a *distribution* check's name, which values with mean 0.672 pass trivially.

**b) The identifiability bug in the IV solver** (fixed `e26b67d` / `096c85e`). `|price - C| < tol`
is a textbook convergence test. It is also satisfied by an *entire interval* of sigma when price
is flat in vol, so the solver returned whatever the iteration path landed on and reported
`converged=True`. Invisible by reading; only a price-solve-compare round trip shows it.

**c) `tail_mass` was near-degenerate.** The audit called it "unnormalized." Measured: on an
*identical* synthetic chain varying only vol, it reads exactly `0.0000` for every name below ~40%
vol and `0.0432` at 100%. Its score term therefore hands a constant full 15/100 to most tickers.
It is a disguised volatility penalty, not a tail-risk measure.

**d) The `Tools/` cycle affected all 12 tool modules**, not just `backtesting_tool` — that was
simply the one the audit happened to script directly.

### The generalizable lesson

> Adversarial review reliably catches **"code contradicts its own comment/caller."**
> It structurally misses **"code is self-consistent and still produces a meaningless number."**

The second class is only caught by **executable round-trip / invariant checks**: price-solve-
recover the input; sample and test the marginal *distribution*, not its range; hold the chain
fixed and vary one input to see what the metric actually tracks. **A math pass that only reads
code will miss the same bugs again.** Pair it with property tests.

---

## 3. Two deliberate non-fixes — operator decisions, not oversights

**(a) `tail_mass` is still what feeds the screener score.** `tail_mass_z` (vol-normalized, the
ranking-safe version) is computed and published, but the composite score still reads the old
`tail_mass` against its hardcoded `0.20` threshold. Rewiring it re-calibrates a **live trading
signal**, and re-tuning the threshold needs a pass over real chains — billed ThetaData pulls
across many tickers. A test pins the current wiring so the migration is a conscious edit.
*Decision needed: approve the recalibration pass.*

**(b) `vrp_pct` was not renamed.** `VrpTermPoint` gained `convexity_pct` (same value, correct
name) and `vrp_vs_realized_pct` (the real VRP, which the module never had). The misleading
`vrp_pct` remains because **54 files reference it** and two other agents were editing the tree.
*Decision needed: approve a single cross-repo rename pass.*

---

## 4. Corrections to the old doc

- **#9 is partly stale.** It says "both call sites pass `oi_snapshot={}`". There are now **four**
  call sites and two build a real snapshot. The two that pass `{}` do so legitimately (avoiding
  billed chain pulls on a reporting path); the defect was that it was invisible, now reported via
  `oi_rules_evaluated`.
- **#2's "Medium" severity was too low**, and **#3's "Medium — API mismatch" was much too low**
  (it was a 49% VaR understatement). Treat the old severity column as a starting point only.

---

## 5. Where a fresh pass should actually spend its budget

Ordered by (unreviewed × consequence). Nothing below has been through a math review.

1. **Jump-diffusion calibration** (`Vol_Suite/jump_diffusion/`) — Bates/Merton, Nelder-Mead in IV
   space, `MAX_CALIB_STRIKES=15`. Landed 2026-08-30, feeds dealer positioning, VRP and the
   strategy recommender. Never reviewed. `rmse_iv` is scoped to the calibration mask, not the full
   smile — verify that is honestly reported.
2. **Context-Store numeric threading** — `correlation_matrix` re-indexed onto a basket **by label**
   via `correlation_tickers`. CLAUDE.md records two silent key-mismatch breakages here already. A
   matrix stored for `[QQQ, SPY]` applied to `[SPY, QQQ]` is wrong with no error.
3. **The 56 module adapters** (`*/module_registry.py`) — the widget path. The pricing-sigma bug
   (`fallback:0.25` presented as a market price) lived here. Check every `_resolve_*` for
   fallbacks that do not announce themselves.
4. **`_garch_fit` fits a GARCH effect that is not there.** Found 2026-09-11, **not yet fixed**: on
   iid-normal returns it returns `persistence = 0.9990`, pinned against its own `0.999` bound, so
   `long_run_vol = sqrt(omega/(1-persistence))` is meaningless. The new `converged` flag reads
   True — scipy's simplex *did* converge, to a boundary. Consider gating on persistence, or the
   `arch` package. This is Part 5 #6's disease in a second module.
5. **`var_agg` EWMA + PCA + Euler allocation** — #1 was found here once; the rest of the file has
   never been checked.
6. **Dealer positioning's three sign conventions** — Part 4 of the old doc has the analysis, but
   **read `Vol_Suite/docs/Dealer posistioning notes/HANDOFF_dealer_exposure_dev_20260814.md`
   first**: the same "fail loud on missing data" change has been proposed and reverted **twice**. A
   live-render fail-loud check is correct; the identical check inside `backtest_stage3.py`'s
   multi-day loop aborts the whole run on one bad day.

---

## 6. Traps that will otherwise waste the pass

- **9 pre-existing Vol_Suite test failures** — `test_dealer_position_book`,
  `test_dual_pipeline_gate_v2` (x1), `test_dual_pipeline_gate_v7` (x6), `test_vrp_term_structure`.
  Unrelated to any of today's work; do not attribute them to a diff.
- **sentiment-scanner needs its own venv**: `sentiment-scanner/.venv/Scripts/python.exe`. From the
  root venv it produces ~211 collection errors that mean nothing. 6 `test_main.py` failures there
  are also pre-existing (a cwd/`tmp_path` issue looking for `module_registry.py`).
- **Flat cwd-relative imports.** `var_engine/__init__.py` pulls `shared.*`; several suite modules
  import as flat modules. Load a file standalone with `importlib.util.spec_from_file_location`, or
  run from the suite's own directory.
- **ThetaData is billed and rate-limited.** Sleep 0.3–0.5s, never fan out. A math pass should work
  from synthetic chains wherever possible — every measurement in this document was produced that
  way.
- **Two `.py` files were normalized CRLF to LF** in `5604049` (`garch_analysis.py`,
  `variance_swap_screener.py`), so their diffs read as whole-file rewrites. The content change is
  small. Only 9 of ~400 `.py` files were CRLF-in-index; these were legacy outliers.

---

## 7. Commits from this pass

| Commit | Contents |
|---|---|
| `096c85e` | IV solver: LR as the model end to end; identifiability guard on both solvers |
| `c1ba63e` | VaR: Clayton marginals, GARCH convergence, hedge-correlation flag |
| `5604049` | Vol/Tools: `tail_mass_z`, stationarity gating, VRP naming, tool import cycle |
| `7ee7f11` | Sentiment: `iv_spread_ratio` rename, `oi_rules_evaluated` |
