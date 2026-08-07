# Whale-Flow Sign Model — Backtest-Only Port

**Status:** Approved 2026-08-06 · **Scope:** `Vol_Suite/` only, backtest harness, no live wiring

## Background

An external devnotes package (`Dealer_pos_devnotes.tar.gz`, sourced from a separate
`/home/bottl/Financial_Development` environment — not this repo) documented a two-round
multi-agent debate over dealer-positioning sign conventions. The second round picked a
`sign_model='direction'` built on a 5-signal "Direction" package (whale flow / Elliott
Wave / Bollinger / trend / liquidity), operated whale-only (`DEALER_DIRECTION_MIN_SCORE=1`)
because the other four signals were near-noise on that dataset.

Independent review of that package (see conversation history / `docs/PROJECT_AUDIT_AND_SPEC.md`
context) found the debate's own numbers do not hold up under proper multiple-comparisons
correction: the winning cell (AAPL) survives family-wise correction within the v5 family but
fails cross-model correction; the one correctly pooled, cross-ticker test (n=494, ticker
fixed-effects) is not significant (perm p=0.0825); and the only *significant* pooled result
in the whole package points against the project's core hypothesis (short gamma → amplified
vol). None of this code exists in this repo yet — it's unmerged external research.

Decision: port just the whale-flow leg (not the other four Direction signals) into this
repo's own `Vol_Suite/backtest_stage3.py` harness, so evidence gets regenerated fresh on
this repo's own ThetaData feed and environment, rather than trusting the imported numbers.
Backtest-only for now — no live `dealer_positioning.py` wiring in this pass.

## What gets built

### 1. `Vol_Suite/whale_scanner.py` (new file, pure functions, no network I/O)

Ported from the devnotes' `Direction/whale_scanner.py`, stripped of the network-fetch layer
(`data.get_chain_eod_volume` et al.) since the backtest already has the rows it needs from
`option_bulk_hist_eod` — no new fetch, no new module dependency on `Direction/data.py`.

```python
WHALE_THRESHOLD = 25_000.0          # legacy absolute $ premium bar
WHALE_THRESHOLD_BPS = <env override, default 0 = disabled>

def _effective_threshold(min_premium, threshold_bps, price) -> float:
    """bps-relative bar (bps/10_000 * price * 100) when threshold_bps clears
    0 and a price is available; else the legacy absolute bar. Ported as-is
    from the devnotes (their E4 fix for the $25K bar being price-level
    correlated)."""

def classify_whale_bias(rows, min_premium=WHALE_THRESHOLD, threshold_bps=None,
                         price=None) -> Tuple[str, dict]:
    """rows: iterable of {'strike': float, 'right': 'C'|'P', 'volume': float,
    'close': float} for ONE day's chain. Sums call vs put premium
    (volume * close * 100) over legs clearing the effective threshold;
    returns ('bullish'|'bearish'|'neutral', stats) where stats carries
    whale_calls/whale_puts/call_premium/put_premium for diagnostics.
    'bullish' when call_premium > put_premium * 1.2, 'bearish' when the
    inverse, else 'neutral' -- same 1.2x ratio the devnotes used."""
```

Tests: `Vol_Suite/tests/test_whale_scanner.py`, network-free, synthetic row lists covering
bullish/bearish/neutral classification, the bps-vs-absolute threshold switch, and the
missing-price-degrades-to-absolute fallback.

### 2. `Vol_Suite/backtest_stage3.py` changes

- `_build_day_records`'s existing `hist_greek_rows` parse loop captures one more field per
  row: `volume` (already present in those rows via `option_bulk_hist_eod`, currently
  discarded). New `volume_by_date: Dict[str, Dict[Tuple[float, str], float]]` alongside the
  existing `gamma_by_date`/`iv_by_date`.
- New `_net_gamma_whale(gamma_map, oi_map, chain_iv, spot, T, whale_bias)`: same OTM-gate as
  `_net_gamma_v2` (via `replication_reference._otm_leg_weights`), but applies ONE uniform
  sign for the whole day instead of a per-strike sign — `bullish` → dealer short calls (-1),
  long puts (+1); `bearish` → mirror; `neutral` → 0 contribution everywhere. This matches the
  devnotes' `_resolve_sign`'s `'direction'` branch (`-direction_bias * direction`).
- `DayRecord` gains `net_gamma_whale: float` and `regime_whale: Optional[str]` — `None` on
  neutral/no-signal days (excluded from the regression, not folded into `'short'`). Reuses
  `_summarize()` unchanged: it already only buckets `regime == 'long'/'short'`, so `None`
  rows drop out for free.
- `BacktestResult` gains `whale_n_long/n_short/long_mean_vol/short_mean_vol/diff/tstat/pvalue`,
  same shape as v1/v2/v3.
- `format_backtest_report` gains a `v_whale (whale-flow, backtest-only)` column in the table.
  n_long+n_short will legitimately be smaller than the total day count for this column alone —
  that's the neutral-day exclusion working as intended, not a bug.
- `_run_backtest_from_history` wires whale bias computation + `_net_gamma_whale` into the
  per-day loop alongside v1/v2/v3, using `WHALE_THRESHOLD`/`WHALE_THRESHOLD_BPS` defaults
  (bps mode off by default, matching upstream's own default).

Tests: extend `Vol_Suite/tests/test_backtest_stage3.py` with synthetic-data cases — a
bullish day, a bearish day, and a neutral day verifying it's excluded from `n_long`/`n_short`
(not folded into `'short'`).

## Explicitly out of scope for this pass

- The other 4 Direction signals (wave/bollinger/trend/liquidity) — devnotes' own evidence
  says they're near-noise; can be added later if whale-only proves interesting on this repo's
  data.
- The NO_CALL honesty gate (`AMPLIFY`/`DAMPEN`/`NO_CALL` labeling) — a live-report concept;
  the backtest already gets the equivalent effect for free via neutral-day exclusion.
- The AMD/SPY sign-caveat suppression registry — calibrated on the other environment's run;
  skipped so this repo's own backtest generates independent evidence instead of inheriting
  numbers already flagged as statistically weak.
- Any change to `dealer_positioning.py`'s live `VALID_SIGN_MODELS` / `_resolve_sign` / CLI /
  reports, `volatility_suite.py`, or `quant_bridge.py` — backtest-only per the approved scope.
- No new network fetches — whale data comes from rows `backtest_stage3.py` already pulls.

## Open follow-up (not blocking this pass)

Once a fresh backtest run exists on this repo's data, the natural next decision is whether
whale-only clears the same bar the devnotes' import failed to clear (proper multiple-
comparisons correction across tickers, ideally a pooled panel rather than single-ticker
reads) before any live wiring is considered.
