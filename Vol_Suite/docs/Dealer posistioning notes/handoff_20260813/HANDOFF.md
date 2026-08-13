# Dealer Positioning — Windows Session Handoff (2026-08-13)

**Author:** Claude Code (Sonnet 5), Windows FinancialDevelopment repo, 2026-08-13
**Purpose:** Self-contained context so work can continue on another system without
re-deriving the audit, the debate, or the implementation — and, critically, **without
repeating the exact failure this whole investigation was about** (real work done in one
environment never landing in the git tree another environment reads from).

---

## 0. READ THIS FIRST — the one rule that matters most

**Everything in this package is UNCOMMITTED.** `git status` at the end of this session
(see `git_status_snapshot_20260813.txt`) shows all of it sitting in the working tree,
nothing staged, nothing pushed. This is exactly the condition that caused Finding 1 of
the audit below (a whole model's worth of work existed only in a WSL working tree and
was never carried into this Windows repo's git history). **Before switching systems,
either commit this work on this machine, or make sure whatever "continue on another
system" means actually reads from this same working tree/filesystem — not a fresh
`git pull` that will silently not have any of it.**

There is ALSO other, unrelated uncommitted work sitting in this same repo right now
from what appears to be a different, concurrent process (`Backtests/*`,
`Options_Suite/MCHestonLSM.py`, `Options_Suite/sabr_market_calib.py`,
`Vol_Suite/sabr_dealer_calib.py`, `shared/thetadata.py`, `_cron_envcheck.py`,
`iv_watchdog_scan.py`, `sentiment-scanner/...` exports). **None of that is from this
session — do not commit it by accident with a broad `git add -A`.** See
`git_status_snapshot_20260813.txt` for the exact split, and `CHANGED_FILES.md` for
the itemized list of what this session actually touched.

---

## 1. TL;DR — where this session landed

1. **Audited the live dealer-positioning wiring** (CARL Mode B) and ran a 5-person
   debate (quant / 2 MMs / broker-dealer PM / risk manager) on seed-vs-flow,
   improvements, and testing methodology. Full report: `debate_report_20260813.md`.
2. **Headline finding**: the "V5 Direction / sabr_deviation / 150d accumulation"
   model believed to be live was never merged into this repo. The accumulation engine
   existed but had zero live callers. The two-vanna bug (scanner vs. 4-panel chart
   computing vanna independently) was confirmed still live.
3. **Human decision**: keep the seed, don't kill it on an unfinished test battery. Wire
   accumulation into the live path for real, fix the vanna bug, then run the
   pre-registered OOS falsifier + lead-lag test against real (cached, not live-pulled)
   data.
4. **Implemented and tested (TDD throughout, 440 passed / 5 pre-existing skips at the
   end)**:
   - Real multi-day accumulation wired into `compute_dealer_positioning(accumulate=True)`
     for the anchor expiry (opt-in; `DEALER_ACCUMULATION=1` env override).
   - Two-vanna bug fixed: scanner and dealer-positioning chart now share one
     `DealerPositioningResult`, one vanna computation.
   - Ported `seed_data_loader.py` / `seed_data_maker.py` into `Vol_Suite/` proper
     (previously only inside the extracted WSL handoff zip).
   - Built `backtest_accumulation_falsifier.py` — the pre-registered OOS falsifier +
     lead-lag test, single-ticker and cross-sectional-pooled variants, defaulting to
     the cached local dataset (no live ThetaData pull).
   - **Found and fixed two real, separate bugs** in `backtest_stage3.py` while running
     the falsifier against real cached data (see §4) — not hypothetical, both
     reproduced with a failing test first, both confirmed against real numbers.
5. **Current falsifier state (real data, not synthetic)**: single-ticker QQQ run
   showed `n=14` usable days → `INCONCLUSIVE` (correctly, per the pre-registered
   `<20 days` floor). Pooled run across all 12 cached tickers (1439 pooled ticker-days,
   well above the 200-day floor) came back `delta_r2 = 0.0000` exactly →
   `VERDICT: REDUNDANT` — **but this is very likely a design artifact, not a real
   answer; see §5, do not treat REDUNDANT as settled.**
6. **Live re-pull of QQQ/SPY's thin cached history was explicitly declined by the repo
   owner mid-session** (their correction: "I meant option 2" — pool across tickers
   instead of re-pulling). That re-pull was never executed. If it's wanted later, the
   ticker-specific gap is documented in §5.

---

## 2. Code inventory — what this session actually changed

See `CHANGED_FILES.md` for the full itemized list and `session_diff_20260813.patch`
for the exact diff of every already-tracked file this session touched. Summary:

### Modified (tracked, diff in the patch file)
- `Vol_Suite/dealer_positioning.py` — `accumulate`/`accumulation_lookback_days`/
  `accumulation_seed_mode`/`_accumulation_hist_rows` params on
  `compute_dealer_positioning`; anchor-expiry-only accumulation wiring;
  `vanna_call_shares`/`vanna_put_shares` fields; `sign_model_render_label()` (the
  render-time model-identity function the risk-manager seat asked for).
- `Vol_Suite/replication_reference.py` — new
  `compute_accumulated_position_for_expiry()` entry point (accepts a pre-resolved
  expiry + `_hist_rows` injection for offline/cached use); `compute_accumulated_position`
  refactored to delegate to it.
- `Vol_Suite/options_chain_scanner.py` — `compute_vanna_positioning()` now takes a
  `DealerPositioningResult` and reads its numbers verbatim (was: independent
  recomputation from its own chain fetch); `scan_chain`/`run_chain_scanner` accept
  `dealer_result=` to share one computation.
- `Vol_Suite/volatility_suite.py` — threads the dealer-positioning step's result into
  the chain-scanner step instead of letting it recompute independently.
- `Vol_Suite/backtest_stage3.py` — **two real bugs fixed** (see §4): the combined
  `iv<=0 or gamma<=0` gate that discarded a perfectly good pre-solved `implied_vol`;
  and the greek-row strike-scale auto-detection (theta-scaled vs. plain-dollar).
- `Vol_Suite/tests/test_run_modes_smoke.py` — updated a stub signature to match the
  new `dealer_result` kwarg.
- `Vol_Suite/tests/test_backtest_stage3.py`,
  `Vol_Suite/tests/test_replication_reference_accumulation.py` — new regression tests
  for the above.

### New (untracked)
- `Vol_Suite/backtest_accumulation_falsifier.py` — the OOS falsifier + lead-lag
  harness, single-ticker and pooled.
- `Vol_Suite/seed_data_loader.py`, `Vol_Suite/seed_data_maker.py` — ported from the
  WSL handoff zip into tracked code.
- `Vol_Suite/tests/test_dealer_positioning_accumulation.py`,
  `test_scanner_vanna_parity.py`, `test_seed_data_loader.py`,
  `test_backtest_accumulation_falsifier.py`, `test_pooled_accumulation_falsifier.py`.
- `Vol_Suite/docs/Dealer posistioning notes/` — the audit dossier, screenshots, the
  extracted WSL handoff zip (with its own 12-ticker cached dataset under
  `_extracted/handoff_20260812/seed_data/`), and this handoff package.

### NOT touched by this session (pre-existing uncommitted work from elsewhere)
`Backtests/*`, `Options_Suite/MCHestonLSM.py`, `Options_Suite/sabr_market_calib.py`,
`Vol_Suite/sabr_dealer_calib.py`, `shared/thetadata.py`, `Vol_Suite/variance_swap_live.py`,
`Vol_Suite/tests/test_thetadata_client.py`, `Vol_Suite/tests/test_variance_swap_replication.py`,
`_cron_envcheck.py`, `iv_watchdog_scan.py`, `sentiment-scanner/...` exports. These were
already modified/untracked at session start or changed by a concurrent process during
the session — leave them alone unless you know what they are.

---

## 3. Verification

```bash
cd Vol_Suite
../.venv/Scripts/python.exe -m pytest tests -q
# -> 440 passed, 5 skipped (pre-existing floor cases in test_implied_vol.py), 0 failed
```
Full verbatim output: `test_results_full_suite_20260813.txt`.

Manual end-to-end check (real cached SPY 150d data, synthetic today's-snapshot chain —
see the audit conversation for why: the cached historical rows don't carry
gamma/bid/ask, only IV/vanna, so they can't stand in for a live snapshot fetch):
snapshot net gamma `0.0` vs. accumulated net gamma `-439.01` (genuinely different,
not a relabeled snapshot); scanner vanna == dealer engine vanna exactly in both modes.

---

## 4. The two real bugs found running the falsifier against real cached data

Both were found by symptom (not theorized), reproduced with a failing test first, then
fixed. Both are in `backtest_stage3.py`, which this session's falsifier reuses for its
same-day-snapshot arm.

**Bug 1 — IV/gamma combined gate.** `_build_day_records` treated "no IV" and "no
gamma" as one condition (`if iv <= 0 or gamma <= 0: <re-solve IV from bid/ask/close>`).
The cached seed_data payload carries an already-solved, valid `implied_vol` but no
`gamma`/`bid`/`ask` (illiquid strikes legitimately have `close: '0.00'`). The combined
gate discarded the good IV and tried to re-derive it from missing bid/ask, failing on
~78% of rows. Fixed: IV and gamma are now separate gates; an already-good IV is used
directly to derive gamma via Black-Scholes, never discarded.
Test: `test_gamma_derived_from_an_already_solved_iv_without_bid_ask`.

**Bug 2 — greek-row strike scale.** `_build_day_records` assumed greek-row strikes
arrive in plain-dollar form ("650.000") — true for the *live* `option_bulk_hist_eod`
route, but the *cached* `seed_data_maker.py` payload stores them theta-scaled
("650000"), matching the OI-row convention. Unnormalized, `gamma_map` keys never
matched `oi_map` keys, so `net_gamma_v1` was **silently exactly 0.0 on every single
day for all 12 tickers** — not "no signal," a total aggregation miss. This is the
exact same *observable* failure the module's own 2026-08-04 incident comment already
documents, in the opposite direction. Fixed: auto-detect scale (a real strike is never
≥ $10,000) instead of assuming one producer's convention.
Test: `test_gamma_map_matches_oi_map_when_greek_rows_use_theta_scaled_strikes`.

**Both bugs were silent** (no exception, no error, just produced degenerate output
that looked plausible at a glance — `net_gamma_v1=0.0` reads as "flat/short," not as
"broken"). Worth remembering next time a backtest number looks suspiciously clean or
constant.

---

## 5. Current falsifier state — NOT a settled answer, read this before citing it

The corrected pooled run (12 tickers, 1439 ticker-days) still came back
`delta_r2 = 0.0000` exactly and `VERDICT: REDUNDANT`. Diagnosed why:

```
AAPL snap unique: {1.0, -1.0}   acc unique: {-1.0}
```

After the strike-scale fix, the **snapshot** signal now varies day-to-day as expected.
But the **accumulated** signal is **constant across the entire sample window, for
every one of the 12 tickers** — the 150d-accumulated dealer-short read never flips
sign within any ticker's own history. That's a real, interesting finding on its own
(it independently corroborates the WSL battery's "the book settles into one persistent
regime" narrative, from a completely different code path and completely different
data) — but it also means the pooled test's **ticker-fixed-effects (within-ticker
demeaning)** design is mechanically incapable of detecting anything: demeaning a
constant series produces exactly zero, regardless of whether real *cross-sectional*
signal exists (i.e., which tickers ended up long vs. short, and whether that
predicts anything).

`PooledFalsifierResult.constant_signal_tickers` now surfaces this explicitly in the
report (added specifically because the first run silently said `REDUNDANT` with no
explanation, which cost real investigation time to diagnose) — check it before trusting
any pooled verdict.

**Recommended next step, now BUILT (2026-08-13, Hermes session)**: the genuinely
cross-sectional test — 12 data points (one accumulated sign per ticker) against
each ticker's own realized-vol level over the same window, rather than a day-level
panel. Added to `backtest_accumulation_falsifier.py` as
`_run_cross_sectional_falsifier_from_histories` / `run_cross_sectional_falsifier`
(CLI: `python backtest_accumulation_falsifier.py --cross`), reusing
`_extract_paired_signals` per ticker so it never re-derives signal logic, and
gated on `n_tickers >= 8` + permutation p-value so the thin 12-point sample can't
false-positive. Tests: `tests/test_cross_sectional_falsifier.py` (6 network-free
tests, incl. a constructed signal-detection case + a no-signal anti-case).
Run it with `env -u PYTHONPATH -u VIRTUAL_ENV ../.venv/Scripts/python.exe
backtest_accumulation_falsifier.py --cross` (slow — recomputes the per-day
accumulated book for every ticker; ~6+ min on 12 tickers).

**QQQ and SPY specifically remain thin** (14-21 and 55-62 usable days respectively,
vs. 110-163 for the other 10 tickers) because their cached `seed_data_*.json` files
only cover the last ~20-60 calendar days of option-chain history (the underlying spot
price history is fully dense for all 12 — 171 days each — only the option-chain pull
was truncated when these files were originally built). A live re-pull via
`Vol_Suite/seed_data_maker.py QQQ 150 <out_dir>` (ported and ready to run, sequential,
never concurrent with another ticker per `.claude/skills/rate-limit-options/`) would
fix this but was explicitly deferred this session in favor of the pooled approach.

---

## 6. Operational rules carried forward from the audit (don't relearn these)

1. No result computed in an environment other than this repo's actual git tree gets
   called "the winner" in any doc that reaches a human, until reproduced in-tree with
   a commit hash attached. This is what caused Finding 1 in the first place.
2. Every chart/report that states a sign model must source that label from
   `dealer_positioning.sign_model_render_label(result)` — never a hardcoded string.
3. Any change to sign conventions (dealer OI sign, vanna sign, `VALID_SIGN_MODELS`)
   needs explicit review — this codebase has flipped a sign convention at least three
   times in its documented history.
4. Never run 2+ concurrent whole-chain ThetaData fan-outs (see
   `.claude/skills/rate-limit-options/SKILL.md`) — `option_bulk_hist_oi_by_day` is the
   most fragile route.
5. `env -u PYTHONPATH` before running the venv python if a Hermes/WSL session's
   environment variables might be inherited (site-packages contamination, per
   `CLAUDE.md`'s "Hermes-venv leaks" note).
