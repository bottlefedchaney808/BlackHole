# Whale-Flow Sign Model (Backtest-Only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the whale-flow leg of the external "Direction" sign model (devnotes research package) into `Vol_Suite/backtest_stage3.py` as a fourth, backtest-only sign-model column, so evidence gets regenerated on this repo's own ThetaData feed instead of trusting the imported numbers.

**Architecture:** A new pure-function module `Vol_Suite/whale_scanner.py` classifies one day's option-chain rows (strike/right/volume/close) into a bullish/bearish/neutral bias, using large-single-contract-premium filtering + call-vs-put premium ratio — no network I/O of its own. `backtest_stage3.py` captures `volume` from rows it already fetches (zero new network calls), computes one whale bias per day, and applies it as a uniform per-day sign across the existing OTM-gated gamma×OI aggregation, mirroring the `_net_gamma_v1`/`_net_gamma_v2`/`_net_gamma_v3` pattern already in that file.

**Tech Stack:** Python 3.12, pytest, no new dependencies.

## Global Constraints

- No new network fetches — whale volume comes from rows `option_bulk_hist_eod` already returns to `_build_day_records`, currently discarded.
- Backtest-only this pass — no changes to `dealer_positioning.py`, `volatility_suite.py`, `quant_bridge.py`, or any live-reporting path.
- A neutral/no-signal whale day gets `regime_whale = None` and is excluded from the regression (via `_summarize`'s existing `== 'long'`/`== 'short'` filter) — never folded into `'short'`.
- Follow the existing module's pure-function/network-split convention: `whale_scanner.classify_whale_bias` takes already-fetched rows, never fetches anything itself.
- Full spec: `docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`.

---

## Task 1: `whale_scanner.py` — pure whale-flow bias classifier

**Files:**
- Create: `Vol_Suite/whale_scanner.py`
- Test: `Vol_Suite/tests/test_whale_scanner.py`

**Interfaces:**
- Produces: `WHALE_THRESHOLD: float` (module constant, `25_000.0`), `_effective_threshold(min_premium: float, threshold_bps: Optional[float], price: Optional[float]) -> float`, `classify_whale_bias(rows: Iterable[dict], min_premium: float = WHALE_THRESHOLD, threshold_bps: Optional[float] = None, price: Optional[float] = None) -> Tuple[str, dict]` where the returned string is one of `'bullish'`/`'bearish'`/`'neutral'` and `dict` has keys `whale_calls`, `whale_puts`, `call_premium`, `put_premium`. Task 2 imports this module and calls `classify_whale_bias`.

- [ ] **Step 1: Write the failing tests**

Create `Vol_Suite/tests/test_whale_scanner.py`:

```python
"""Tests for whale_scanner.py -- pure classification of large single-contract
options premium into a bullish/bearish/neutral bias for one day's chain.

Network-free: works entirely off synthetic row dicts, matching the rest of
Vol_Suite/tests/'s convention (see test_backtest_stage3.py's module
docstring for why this matters).
"""
import pytest

import whale_scanner as ws


def _row(strike, right, volume, close):
    return {"strike": strike, "right": right, "volume": volume, "close": close}


@pytest.mark.unit
def test_classify_whale_bias_bullish_on_call_heavy_premium():
    rows = [
        _row(110, 'C', 20, 15.0),   # premium = 20*15*100 = $30,000 >= $25K bar
        _row(90, 'P', 5, 10.0),     # premium = 5*10*100 = $5,000 -- filtered out
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bullish'
    assert stats['whale_calls'] == 1
    assert stats['whale_puts'] == 0
    assert stats['call_premium'] == pytest.approx(30_000.0)
    assert stats['put_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_bearish_on_put_heavy_premium():
    rows = [
        _row(90, 'P', 20, 15.0),    # $30,000
        _row(110, 'C', 5, 10.0),    # $5,000 -- filtered out
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bearish'
    assert stats['whale_puts'] == 1
    assert stats['whale_calls'] == 0


@pytest.mark.unit
def test_classify_whale_bias_neutral_when_balanced():
    """Both sides clear the threshold but neither exceeds the other by the
    1.2x ratio -- neither side 'wins', so the day is neutral."""
    rows = [
        _row(110, 'C', 20, 15.0),   # $30,000
        _row(90, 'P', 20, 15.0),    # $30,000
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'neutral'


@pytest.mark.unit
def test_classify_whale_bias_neutral_when_nothing_clears_threshold():
    rows = [_row(110, 'C', 1, 1.0)]  # premium = $100
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'neutral'
    assert stats['whale_calls'] == 0
    assert stats['call_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_neutral_on_empty_rows():
    bias, stats = ws.classify_whale_bias([])
    assert bias == 'neutral'
    assert stats['call_premium'] == 0.0
    assert stats['put_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_skips_malformed_rows():
    """A row with a non-numeric volume/close must be skipped, not crash the
    whole classification."""
    rows = [
        {"strike": 110, "right": "C", "volume": "not-a-number", "close": 15.0},
        _row(110, 'C', 20, 15.0),   # the one valid row -- still $30,000, bullish
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bullish'
    assert stats['whale_calls'] == 1


@pytest.mark.unit
def test_effective_threshold_uses_bps_when_set_and_price_available():
    # 3000 bps = 30% of one ATM contract's notional (price * 100)
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=3000, price=100.0)
    assert bar == pytest.approx(3000 / 10_000.0 * 100.0 * 100.0)  # $30,000


@pytest.mark.unit
def test_effective_threshold_falls_back_to_absolute_without_price():
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=3000, price=None)
    assert bar == 25_000.0


@pytest.mark.unit
def test_effective_threshold_absolute_when_bps_param_none_and_env_unset(monkeypatch):
    monkeypatch.delenv("WHALE_THRESHOLD_BPS", raising=False)
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=None, price=100.0)
    assert bar == 25_000.0


@pytest.mark.unit
def test_effective_threshold_reads_env_when_param_is_none(monkeypatch):
    monkeypatch.setenv("WHALE_THRESHOLD_BPS", "3000")
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=None, price=100.0)
    assert bar == pytest.approx(30_000.0)


@pytest.mark.unit
def test_classify_whale_bias_applies_bps_threshold_when_passed():
    # price=100 -> bps=3000 bar is $30,000; a $28,000 call premium clears
    # the legacy $25K bar but NOT the bps-relative bar, so it must be
    # filtered out and the day must read neutral.
    rows = [_row(110, 'C', 20, 14.0)]  # premium = 20*14*100 = $28,000
    bias, _ = ws.classify_whale_bias(rows, threshold_bps=3000, price=100.0)
    assert bias == 'neutral'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && python -m pytest tests/test_whale_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'whale_scanner'` (or collection error) — the module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `Vol_Suite/whale_scanner.py`:

```python
"""whale_scanner.py

Pure classification of large single-contract ("whale") options premium into
a bullish/bearish/neutral bias for ONE day's chain. Ported from the
whale-flow leg of an external devnotes research package (2026-08-06 CARL
debate, `Direction/whale_scanner.py`) -- see
docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md for
what was ported, what was deliberately left out (the other 4 "Direction"
signals, the live-report NO_CALL gate, the AMD/SPY sign-caveat registry),
and why.

No network I/O here, unlike the source package's version: this repo's
backtest_stage3.py already fetches per-contract volume/close from the same
route it uses for greeks/IV (option_bulk_hist_eod), so this module only
classifies rows the caller already has -- zero new network calls.
"""
import os
from typing import Dict, Iterable, Optional, Tuple

WHALE_THRESHOLD = 25_000.0  # legacy absolute $ premium bar (volume * close * 100)
_CONTRACT_MULTIPLIER = 100  # options are 100 shares per contract
_CALL_PUT_RATIO = 1.2       # one side's premium must exceed the other's by this multiple to call a direction


def _effective_threshold(min_premium: float, threshold_bps: Optional[float],
                          price: Optional[float]) -> float:
    """Resolve the premium filter bar.

    `threshold_bps` (or the WHALE_THRESHOLD_BPS env var, when threshold_bps
    is None) switches to a premium-RELATIVE bar when it clears 0 AND a
    price is available: bar = bps/10_000 * price * 100 (bps of one
    contract's ATM notional). 0/unset, or a missing price, keeps the
    legacy absolute `min_premium` bar unchanged -- this mirrors the source
    package's E4 fix for the $25K bar being price-level correlated (easier
    to clear on richly-priced names).
    """
    bps = (float(os.environ.get("WHALE_THRESHOLD_BPS", "0") or 0)
           if threshold_bps is None else float(threshold_bps))
    if bps > 0 and price:
        return bps / 10_000.0 * float(price) * _CONTRACT_MULTIPLIER
    return min_premium


def classify_whale_bias(rows: Iterable[dict], min_premium: float = WHALE_THRESHOLD,
                         threshold_bps: Optional[float] = None,
                         price: Optional[float] = None) -> Tuple[str, Dict]:
    """Classify one day's chain rows into a whale-flow bias.

    `rows`: iterable of {'strike': float, 'right': 'C'|'P', 'volume': float,
    'close': float} for a SINGLE trading day -- a snapshot, not a
    cumulative window. Callers are responsible for pre-filtering to one day
    (backtest_stage3.py does this via its existing per-date grouping).

    Returns (bias, stats): bias is 'bullish' | 'bearish' | 'neutral';
    stats carries whale_calls/whale_puts/call_premium/put_premium for
    diagnostics. 'bullish' when call premium exceeds put premium by more
    than _CALL_PUT_RATIO, 'bearish' for the inverse, else 'neutral' --
    including when nothing in the chain clears the premium bar at all.
    Malformed rows (non-numeric volume/close) are skipped, never raise.
    """
    eff_threshold = _effective_threshold(min_premium, threshold_bps, price)

    whale_calls = whale_puts = 0
    call_premium = put_premium = 0.0
    for row in rows:
        try:
            volume = float(row.get('volume') or 0)
            close = float(row.get('close') or 0)
        except (TypeError, ValueError):
            continue
        premium = volume * close * _CONTRACT_MULTIPLIER
        if premium < eff_threshold:
            continue
        right = str(row.get('right', '')).upper()[:1]
        if right == 'C':
            whale_calls += 1
            call_premium += premium
        elif right == 'P':
            whale_puts += 1
            put_premium += premium

    if call_premium > put_premium * _CALL_PUT_RATIO:
        bias = 'bullish'
    elif put_premium > call_premium * _CALL_PUT_RATIO:
        bias = 'bearish'
    else:
        bias = 'neutral'

    stats = {
        'whale_calls': whale_calls, 'whale_puts': whale_puts,
        'call_premium': call_premium, 'put_premium': put_premium,
    }
    return bias, stats
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && python -m pytest tests/test_whale_scanner.py -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/whale_scanner.py Vol_Suite/tests/test_whale_scanner.py
git commit -m "$(cat <<'EOF'
feat: add whale-flow bias classifier (Vol_Suite/whale_scanner.py)

Pure port of the whale-flow leg from an external devnotes research
package -- large single-contract premium filtering + call/put ratio
into a bullish/bearish/neutral bias. No network I/O; backtest_stage3.py
(next commit) feeds it rows it already fetches.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire whale bias into `backtest_stage3.py` as a fourth backtest column

**Files:**
- Modify: `Vol_Suite/backtest_stage3.py`
- Modify: `Vol_Suite/tests/test_backtest_stage3.py`

**Interfaces:**
- Consumes: `whale_scanner.classify_whale_bias(rows, price=...) -> Tuple[str, dict]` from Task 1.
- Produces: `DayRecord.net_gamma_whale: float`, `DayRecord.regime_whale: Optional[str]`, `_net_gamma_whale(gamma_map, oi_map, chain_iv, spot, T, whale_bias) -> float`, `BacktestResult.whale_n_long/whale_n_short/whale_long_mean_vol/whale_short_mean_vol/whale_diff/whale_tstat/whale_pvalue`. Nothing outside this file consumes these yet (backtest-only scope).

- [ ] **Step 1: Write the failing tests**

Open `Vol_Suite/tests/test_backtest_stage3.py`. Replace the `_make_day_rows` helper (lines 50-68) with an extended version that can optionally stamp specific strikes with whale-sized premium, and add the new whale tests after the existing `test_net_gamma_v2_runs_and_restricts_to_otm` test (after line 189, before the `# The actual point` section header on line 192).

Replace this block:

```python
def _make_day_rows(date: str, spot: float, call_oi: int, put_oi: int, gamma: float = 0.02):
    """One day's greeks + OI rows, with a deliberately lopsided OI split
    (call_oi vs put_oi) so the day's TRUE embedded gamma-sign label is known
    by construction -- heavy call OI => v1's flat convention sums positive
    (long gamma), heavy put OI => sums negative (short gamma).
    """
    chain_iv = _flat_smile_chain(spot)
    greek_rows, oi_rows = [], []
    for (k, right), iv in chain_iv.items():
        # hist/option/eod (what hist_greek_rows is shaped after) echoes
        # strike in plain dollar form, NOT theta-scaled -- only the OI route
        # below returns theta-scaled integers. See _build_day_records's own
        # comment (backtest_stage3.py) for the live-verified bug this
        # fixture used to silently share with the buggy production code.
        greek_rows.append({"date": date, "strike": k, "right": right,
                           "implied_vol": iv, "delta": 0.0, "gamma": gamma})
        oi = call_oi if right == 'C' else put_oi
        oi_rows.append({"date": date, "strike": _theta(k), "right": right, "open_interest": oi})
    return greek_rows, oi_rows
```

with:

```python
def _make_day_rows(date: str, spot: float, call_oi: int, put_oi: int, gamma: float = 0.02,
                    whale_calls: set = None, whale_puts: set = None,
                    whale_premium: float = 30_000.0):
    """One day's greeks + OI rows, with a deliberately lopsided OI split
    (call_oi vs put_oi) so the day's TRUE embedded gamma-sign label is known
    by construction -- heavy call OI => v1's flat convention sums positive
    (long gamma), heavy put OI => sums negative (short gamma).

    whale_calls/whale_puts: optional sets of strikes to stamp with a
    'volume'/'close' pair whose product (x100) equals whale_premium --
    lets a test construct a known whale-flow bias without touching the OI
    story. Strikes not in either set get no 'volume'/'close' fields at all
    (matching a real row where nothing whale-sized traded), so they never
    enter whale_scanner.classify_whale_bias's premium sum.
    """
    chain_iv = _flat_smile_chain(spot)
    greek_rows, oi_rows = [], []
    for (k, right), iv in chain_iv.items():
        # hist/option/eod (what hist_greek_rows is shaped after) echoes
        # strike in plain dollar form, NOT theta-scaled -- only the OI route
        # below returns theta-scaled integers. See _build_day_records's own
        # comment (backtest_stage3.py) for the live-verified bug this
        # fixture used to silently share with the buggy production code.
        row = {"date": date, "strike": k, "right": right,
               "implied_vol": iv, "delta": 0.0, "gamma": gamma}
        is_whale = (whale_calls and right == 'C' and k in whale_calls) or \
                   (whale_puts and right == 'P' and k in whale_puts)
        if is_whale:
            row["volume"] = 10.0
            row["close"] = whale_premium / (10.0 * 100.0)
        greek_rows.append(row)
        oi = call_oi if right == 'C' else put_oi
        oi_rows.append({"date": date, "strike": _theta(k), "right": right, "open_interest": oi})
    return greek_rows, oi_rows
```

Then insert these tests right after `test_net_gamma_v2_runs_and_restricts_to_otm` (after line 189):

```python
@pytest.mark.unit
def test_net_gamma_whale_flips_sign_between_bullish_and_bearish():
    """The whale sign model applies ONE uniform sign per day, so bullish
    and bearish on the identical gamma/OI map must be exact mirrors, and
    neutral must contribute nothing."""
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    oi_map = {k: 100 for k in chain_iv}
    T = 0.25

    bullish_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'bullish')
    bearish_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'bearish')
    neutral_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'neutral')

    assert bullish_total != 0.0
    assert bullish_total == pytest.approx(-bearish_total)
    assert neutral_total == 0.0


@pytest.mark.unit
def test_build_day_records_excludes_neutral_whale_days():
    """A day with no whale-sized flow anywhere in the chain must get
    regime_whale=None and net_gamma_whale=0.0 -- NOT folded into 'short'
    the way v1/v2/v3's `> 0 else 'short'` convention would."""
    d = "20260901"
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 1000)  # no whale_calls/whale_puts
    price_rows = [_price_row(d, SPOT0)]

    records = bt3._build_day_records("SYN", "20261231", greek_rows, oi_rows, price_rows)
    assert len(records) == 1
    assert records[0].regime_whale is None
    assert records[0].net_gamma_whale == 0.0


@pytest.mark.unit
def test_build_day_records_labels_bullish_whale_day():
    """A day with heavy call-side whale premium must classify as bullish
    and produce a real (non-None) regime_whale."""
    d = "20260901"
    call_strikes = {k for (k, right) in _flat_smile_chain(SPOT0).keys() if right == 'C'}
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 1000, whale_calls=call_strikes)
    price_rows = [_price_row(d, SPOT0)]

    records = bt3._build_day_records("SYN", "20261231", greek_rows, oi_rows, price_rows)
    assert len(records) == 1
    assert records[0].regime_whale in ("long", "short")
    assert records[0].net_gamma_whale != 0.0


@pytest.mark.unit
def test_run_backtest_from_history_wires_whale_column():
    """End-to-end sanity: whale_n_long/whale_n_short must reflect ONLY the
    non-neutral days, and never exceed the total day count."""
    dates = [f"202607{d:02d}" for d in range(1, 15)]
    greek_rows, oi_rows, price_rows = [], [], []
    call_strikes = {k for (k, right) in _flat_smile_chain(SPOT0).keys() if right == 'C'}

    price = SPOT0
    for i, d in enumerate(dates):
        # Alternate bullish-whale days and no-whale (neutral) days.
        whale_calls = call_strikes if i % 2 == 0 else None
        g_rows, o_rows = _make_day_rows(d, price, 500, 500, whale_calls=whale_calls)
        greek_rows.extend(g_rows)
        oi_rows.extend(o_rows)
        price_rows.append(_price_row(d, price))
        price *= 1.01 if i % 2 == 0 else 0.99

    result = bt3._run_backtest_from_history("SYN", "20261231", greek_rows, oi_rows, price_rows,
                                             forward_window_days=3)
    total_days = len(result.day_records)
    assert result.whale_n_long + result.whale_n_short <= total_days
    assert result.whale_n_long + result.whale_n_short > 0
    # Roughly half the days were stamped bullish, half neutral (excluded) --
    # confirms neutral days are genuinely dropping out, not all landing in
    # one bucket by convention.
    assert result.whale_n_long + result.whale_n_short < total_days
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && python -m pytest tests/test_backtest_stage3.py -v`
Expected: FAIL — `AttributeError: module 'backtest_stage3' has no attribute '_net_gamma_whale'` (and/or `TypeError: DayRecord.__init__() missing ... 'net_gamma_whale'`) once the new tests run, since `backtest_stage3.py` hasn't been touched yet.

- [ ] **Step 3: Add the whale-flow function block and DayRecord/BacktestResult fields**

In `Vol_Suite/backtest_stage3.py`, add the import. Find:

```python
import dealer_positioning
```

Replace with:

```python
import dealer_positioning
import whale_scanner
```

Find the `DayRecord` dataclass:

```python
@dataclass
class DayRecord:
    date: str
    spot: float
    net_gamma_v1: float
    net_gamma_v2: float
    net_gamma_v3: float
    regime_v1: str              # 'long' or 'short'
    regime_v2: str
    regime_v3: str
    fwd_realized_vol: Optional[float]  # annualized, None if too close to the end of the sample
```

Replace with:

```python
@dataclass
class DayRecord:
    date: str
    spot: float
    net_gamma_v1: float
    net_gamma_v2: float
    net_gamma_v3: float
    net_gamma_whale: float
    regime_v1: str              # 'long' or 'short'
    regime_v2: str
    regime_v3: str
    regime_whale: Optional[str]  # 'long'/'short', or None on a neutral/no-signal whale day
    fwd_realized_vol: Optional[float]  # annualized, None if too close to the end of the sample
```

Find the end of the `BacktestResult` dataclass's v3 block:

```python
    # v3 (vol_surface_replication_weighted -- backtest-only experiment)
    v3_n_long: int = 0
    v3_n_short: int = 0
    v3_long_mean_vol: float = float('nan')
    v3_short_mean_vol: float = float('nan')
    v3_diff: float = float('nan')
    v3_tstat: float = float('nan')
    v3_pvalue: float = float('nan')
```

Replace with (adds the whale block right after):

```python
    # v3 (vol_surface_replication_weighted -- backtest-only experiment)
    v3_n_long: int = 0
    v3_n_short: int = 0
    v3_long_mean_vol: float = float('nan')
    v3_short_mean_vol: float = float('nan')
    v3_diff: float = float('nan')
    v3_tstat: float = float('nan')
    v3_pvalue: float = float('nan')
    # whale (whale-flow, backtest-only experiment -- see whale_scanner.py)
    whale_n_long: int = 0
    whale_n_short: int = 0
    whale_long_mean_vol: float = float('nan')
    whale_short_mean_vol: float = float('nan')
    whale_diff: float = float('nan')
    whale_tstat: float = float('nan')
    whale_pvalue: float = float('nan')
```

- [ ] **Step 4: Add `_net_gamma_whale`**

Find `def _forward_realized_vol(closes_from_today` and insert this new block immediately BEFORE it (i.e., after the end of the existing v3 block's `_net_gamma_v3` function, before the `_forward_realized_vol` def):

```python
# ---------------------------------------------------------------------------
# whale (whale-flow) -- BACKTEST-ONLY experiment, ported from the whale-flow
# leg of an external devnotes research package (see
# docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md for
# the port rationale and what was deliberately left out: the other 4
# "Direction" signals, the NO_CALL live-report gate, and the AMD/SPY
# sign-caveat registry calibrated on a different environment's data).
# Not wired into dealer_positioning.py's live/validated sign models
# (VALID_SIGN_MODELS is untouched).
# ---------------------------------------------------------------------------

def _net_gamma_whale(gamma_map: Dict[Tuple[float, str], float],
                      oi_map: Dict[Tuple[float, str], int],
                      chain_iv: Dict[Tuple[float, str], float],
                      spot: float, T: float, whale_bias: str) -> float:
    """whale (whale-flow): same OTM gating as v2, but applies ONE uniform
    sign for the whole day (from that day's whale-flow bias) instead of a
    per-strike sign. 'bullish' -> customers bought call convexity / sold
    puts -> dealer short calls (-1), long puts (+1); 'bearish' is the
    mirror; 'neutral' -> 0 contribution everywhere (no signal, no trade --
    the caller leaves regime_whale as None for these days rather than
    folding them into a default sign).
    """
    if whale_bias == 'neutral':
        return 0.0
    otm_strikes = set(replication_reference._otm_leg_weights(chain_iv, spot, T).keys())
    direction_bias = 1.0 if whale_bias == 'bullish' else -1.0

    total = 0.0
    for (k, right), gamma in gamma_map.items():
        if (k, right) not in otm_strikes:
            continue
        oi = oi_map.get((k, right), 0)
        if oi <= 0 or gamma == 0:
            continue
        leg_direction = 1.0 if right == 'C' else -1.0
        sign = -direction_bias * leg_direction
        total += sign * gamma * oi
    return total


```

- [ ] **Step 5: Run tests to verify the pure-function tests now pass**

Run: `cd Vol_Suite && python -m pytest tests/test_backtest_stage3.py::test_net_gamma_whale_flips_sign_between_bullish_and_bearish -v`
Expected: PASS. (The `_build_day_records`/`_run_backtest_from_history` tests still fail — that's next.)

- [ ] **Step 6: Wire volume capture and whale bias into `_build_day_records`**

Find this block inside `_build_day_records` (the declarations right before the `hist_greek_rows` loop):

```python
    gamma_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    iv_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    n_derived = n_vendor = n_unrecoverable = 0
```

Replace with:

```python
    gamma_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    iv_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    # whale_rows_by_date feeds whale_scanner.classify_whale_bias directly --
    # {'strike', 'right', 'volume', 'close'} per row, same route
    # (option_bulk_hist_eod) already being iterated below for gamma/IV, so
    # this costs zero extra network calls.
    whale_rows_by_date: Dict[str, List[dict]] = defaultdict(list)
    n_derived = n_vendor = n_unrecoverable = 0
```

Find the `k`/`right` parse at the top of the `hist_greek_rows` loop:

```python
        try:
            # hist/option/eod (the route hist_greek_rows comes from) echoes
            # strike in plain dollar form ("650.000"), NOT theta-scaled --
            # unlike option_bulk_hist_oi_by_day below, which does return
            # theta-scaled integers. Running this through strike_from_theta()
            # silently divided every strike by another 1000 (e.g. 650 ->
            # 0.65), which pushed every real market price wildly outside
            # implied_vol()'s no-arbitrage bounds and made every single row
            # "unrecoverable" -- confirmed live, 2026-08-04 (see
            # docs/PROJECT_AUDIT_AND_SPEC.md Part 5). Also normalize `right`
            # to a single uppercase char here: this route returns the full
            # word ("CALL"/"PUT"), while oi_by_date below is keyed on
            # ThetaData's normal single-char "C"/"P" -- left unnormalized,
            # every (k, right) lookup into oi_map in _net_gamma_v1/_v2 would
            # silently miss and read OI as 0 for every strike, making net
            # gamma always exactly 0.0 and every day classify as "short"
            # regardless of the real chain.
            k = float(row['strike'])
            right = str(row['right']).upper()[:1]
        except (KeyError, TypeError, ValueError):
            continue
```

Immediately after this `try/except` block (still inside the `for row in hist_greek_rows:` loop, before `iv = float(row.get('implied_vol', 0) or 0)`), insert:

```python
        # Whale-flow capture: independent of whether IV/gamma are vendor or
        # price-derived below -- 'volume'/'close' come straight off the same
        # row. A row missing either (0 or non-numeric) simply isn't added,
        # which is exactly right: classify_whale_bias treats an empty day
        # as neutral, not a crash.
        try:
            whale_vol = float(row.get('volume', 0) or 0)
            whale_close = float(row.get('close', 0) or 0)
        except (TypeError, ValueError):
            whale_vol = whale_close = 0.0
        if whale_vol > 0 and whale_close > 0:
            whale_rows_by_date[d].append(
                {'strike': k, 'right': right, 'volume': whale_vol, 'close': whale_close})
```

- [ ] **Step 7: Wire whale bias computation into the per-day record loop**

Find:

```python
        net_v1 = _net_gamma_v1(gamma_map, oi_map)
        net_v2 = _net_gamma_v2(gamma_map, oi_map, chain_iv, spot, forward, T)
        net_v3 = _net_gamma_v3(gamma_map, oi_map, chain_iv, spot, forward, T)
```

Replace with:

```python
        net_v1 = _net_gamma_v1(gamma_map, oi_map)
        net_v2 = _net_gamma_v2(gamma_map, oi_map, chain_iv, spot, forward, T)
        net_v3 = _net_gamma_v3(gamma_map, oi_map, chain_iv, spot, forward, T)
        whale_bias, _whale_stats = whale_scanner.classify_whale_bias(
            whale_rows_by_date.get(d, []), price=spot)
        net_whale = _net_gamma_whale(gamma_map, oi_map, chain_iv, spot, T, whale_bias)
```

Find:

```python
        records.append(DayRecord(
            date=d, spot=spot, net_gamma_v1=net_v1, net_gamma_v2=net_v2, net_gamma_v3=net_v3,
            regime_v1='long' if net_v1 > 0 else 'short',
            regime_v2='long' if net_v2 > 0 else 'short',
            regime_v3='long' if net_v3 > 0 else 'short',
            fwd_realized_vol=fwd_vol,
        ))
```

Replace with:

```python
        records.append(DayRecord(
            date=d, spot=spot, net_gamma_v1=net_v1, net_gamma_v2=net_v2, net_gamma_v3=net_v3,
            net_gamma_whale=net_whale,
            regime_v1='long' if net_v1 > 0 else 'short',
            regime_v2='long' if net_v2 > 0 else 'short',
            regime_v3='long' if net_v3 > 0 else 'short',
            regime_whale=(None if whale_bias == 'neutral'
                          else ('long' if net_whale > 0 else 'short')),
            fwd_realized_vol=fwd_vol,
        ))
```

- [ ] **Step 8: Run tests to verify `_build_day_records` tests pass**

Run: `cd Vol_Suite && python -m pytest tests/test_backtest_stage3.py::test_build_day_records_excludes_neutral_whale_days tests/test_backtest_stage3.py::test_build_day_records_labels_bullish_whale_day -v`
Expected: both PASS.

- [ ] **Step 9: Wire `_run_backtest_from_history` and `format_backtest_report`**

Find:

```python
    v1 = _summarize(records, 'regime_v1')
    v2 = _summarize(records, 'regime_v2')
    v3 = _summarize(records, 'regime_v3')

    return BacktestResult(
        ticker=ticker, expiry=expiry, forward_window_days=forward_window_days,
        day_records=records,
        v1_n_long=v1['n_long'], v1_n_short=v1['n_short'],
        v1_long_mean_vol=v1['long_mean_vol'], v1_short_mean_vol=v1['short_mean_vol'],
        v1_diff=v1['diff'], v1_tstat=v1['tstat'], v1_pvalue=v1['pvalue'],
        v2_n_long=v2['n_long'], v2_n_short=v2['n_short'],
        v2_long_mean_vol=v2['long_mean_vol'], v2_short_mean_vol=v2['short_mean_vol'],
        v2_diff=v2['diff'], v2_tstat=v2['tstat'], v2_pvalue=v2['pvalue'],
        v3_n_long=v3['n_long'], v3_n_short=v3['n_short'],
        v3_long_mean_vol=v3['long_mean_vol'], v3_short_mean_vol=v3['short_mean_vol'],
        v3_diff=v3['diff'], v3_tstat=v3['tstat'], v3_pvalue=v3['pvalue'],
    )
```

Replace with:

```python
    v1 = _summarize(records, 'regime_v1')
    v2 = _summarize(records, 'regime_v2')
    v3 = _summarize(records, 'regime_v3')
    whale = _summarize(records, 'regime_whale')

    return BacktestResult(
        ticker=ticker, expiry=expiry, forward_window_days=forward_window_days,
        day_records=records,
        v1_n_long=v1['n_long'], v1_n_short=v1['n_short'],
        v1_long_mean_vol=v1['long_mean_vol'], v1_short_mean_vol=v1['short_mean_vol'],
        v1_diff=v1['diff'], v1_tstat=v1['tstat'], v1_pvalue=v1['pvalue'],
        v2_n_long=v2['n_long'], v2_n_short=v2['n_short'],
        v2_long_mean_vol=v2['long_mean_vol'], v2_short_mean_vol=v2['short_mean_vol'],
        v2_diff=v2['diff'], v2_tstat=v2['tstat'], v2_pvalue=v2['pvalue'],
        v3_n_long=v3['n_long'], v3_n_short=v3['n_short'],
        v3_long_mean_vol=v3['long_mean_vol'], v3_short_mean_vol=v3['short_mean_vol'],
        v3_diff=v3['diff'], v3_tstat=v3['tstat'], v3_pvalue=v3['pvalue'],
        whale_n_long=whale['n_long'], whale_n_short=whale['n_short'],
        whale_long_mean_vol=whale['long_mean_vol'], whale_short_mean_vol=whale['short_mean_vol'],
        whale_diff=whale['diff'], whale_tstat=whale['tstat'], whale_pvalue=whale['pvalue'],
    )
```

Now find `format_backtest_report`'s table block:

```python
        f"{'':20s}{'v1 (oi_heuristic)':>22s}{'v2 (vol_surface_replication)':>32s}{'v3 (weighted, experimental)':>32s}",
        f"{'long-gamma days':20s}{result.v1_n_long:>22d}{result.v2_n_long:>32d}{result.v3_n_long:>32d}",
        f"{'short-gamma days':20s}{result.v1_n_short:>22d}{result.v2_n_short:>32d}{result.v3_n_short:>32d}",
        f"{'mean vol | long':20s}{result.v1_long_mean_vol:>22.4f}{result.v2_long_mean_vol:>32.4f}{result.v3_long_mean_vol:>32.4f}",
        f"{'mean vol | short':20s}{result.v1_short_mean_vol:>22.4f}{result.v2_short_mean_vol:>32.4f}{result.v3_short_mean_vol:>32.4f}",
        f"{'short - long':20s}{result.v1_diff:>22.4f}{result.v2_diff:>32.4f}{result.v3_diff:>32.4f}",
        f"{'t-stat':20s}{result.v1_tstat:>22.3f}{result.v2_tstat:>32.3f}{result.v3_tstat:>32.3f}",
        f"{'p-value':20s}{result.v1_pvalue:>22.4f}{result.v2_pvalue:>32.4f}{result.v3_pvalue:>32.4f}",
        "",
        "Hypothesis: short-gamma days should show HIGHER forward realized vol "
        "(dealers trade with the tape) -- a positive, statistically significant "
        "diff supports the model; a larger, more significant diff for v2/v3 than "
        "v1 means the richer sign convention is reading something real, not "
        "just producing a more sophisticated-looking chart. v3 (vol_surface_"
        "replication_weighted) is a backtest-only experiment: same OTM gating "
        "as v2, but the Layer 1a flip is gated on materiality vs. the SABR "
        "fit's own RMSE and weighted by deviation magnitude instead of a flat "
        "+/-1 -- see _resolve_sign_weighted's docstring. Not wired into the "
        "live dealer_positioning.py sign models.",
    ]
    return "\n".join(lines)
```

Replace with:

```python
        f"{'':20s}{'v1 (oi_heuristic)':>22s}{'v2 (vol_surface_replication)':>32s}{'v3 (weighted, experimental)':>32s}{'whale (backtest-only)':>28s}",
        f"{'long-gamma days':20s}{result.v1_n_long:>22d}{result.v2_n_long:>32d}{result.v3_n_long:>32d}{result.whale_n_long:>28d}",
        f"{'short-gamma days':20s}{result.v1_n_short:>22d}{result.v2_n_short:>32d}{result.v3_n_short:>32d}{result.whale_n_short:>28d}",
        f"{'mean vol | long':20s}{result.v1_long_mean_vol:>22.4f}{result.v2_long_mean_vol:>32.4f}{result.v3_long_mean_vol:>32.4f}{result.whale_long_mean_vol:>28.4f}",
        f"{'mean vol | short':20s}{result.v1_short_mean_vol:>22.4f}{result.v2_short_mean_vol:>32.4f}{result.v3_short_mean_vol:>32.4f}{result.whale_short_mean_vol:>28.4f}",
        f"{'short - long':20s}{result.v1_diff:>22.4f}{result.v2_diff:>32.4f}{result.v3_diff:>32.4f}{result.whale_diff:>28.4f}",
        f"{'t-stat':20s}{result.v1_tstat:>22.3f}{result.v2_tstat:>32.3f}{result.v3_tstat:>32.3f}{result.whale_tstat:>28.3f}",
        f"{'p-value':20s}{result.v1_pvalue:>22.4f}{result.v2_pvalue:>32.4f}{result.v3_pvalue:>32.4f}{result.whale_pvalue:>28.4f}",
        "",
        "Hypothesis: short-gamma days should show HIGHER forward realized vol "
        "(dealers trade with the tape) -- a positive, statistically significant "
        "diff supports the model; a larger, more significant diff for v2/v3/whale "
        "than v1 means the richer sign convention is reading something real, not "
        "just producing a more sophisticated-looking chart. v3 (vol_surface_"
        "replication_weighted) is a backtest-only experiment: same OTM gating "
        "as v2, but the Layer 1a flip is gated on materiality vs. the SABR "
        "fit's own RMSE and weighted by deviation magnitude instead of a flat "
        "+/-1 -- see _resolve_sign_weighted's docstring. whale (whale-flow) is a "
        "second backtest-only experiment: ONE uniform daily sign from large-"
        "premium call-vs-put option flow (bullish/bearish/neutral), ported from "
        "an external devnotes research package -- see whale_scanner.py and "
        "docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md. "
        "Neutral/no-signal whale days are EXCLUDED from n_long/n_short (not "
        "folded into 'short'), so its n may be smaller than the other columns'. "
        "Neither v3 nor whale is wired into the live dealer_positioning.py sign "
        "models.",
    ]
    return "\n".join(lines)
```

- [ ] **Step 10: Run the full test file to verify everything passes**

Run: `cd Vol_Suite && python -m pytest tests/test_backtest_stage3.py -v`
Expected: all tests PASS, including the pre-existing v1/v2/v3 tests (no regressions) and the four new whale tests from Step 1.

- [ ] **Step 11: Run the whole Vol_Suite test suite as a final regression check**

Run: `cd Vol_Suite && python -m pytest tests/ -v`
Expected: all tests PASS. If anything outside `test_backtest_stage3.py`/`test_whale_scanner.py` fails, stop and investigate before committing — it means this change had an unintended side effect (it shouldn't, since `whale_scanner.py` is new and `backtest_stage3.py`'s only non-additive edit is the `DayRecord`/`BacktestResult` field insertions, which are purely additive to existing dataclasses).

- [ ] **Step 12: Commit**

```bash
git add Vol_Suite/backtest_stage3.py Vol_Suite/tests/test_backtest_stage3.py
git commit -m "$(cat <<'EOF'
feat: add whale-flow sign model as a 4th backtest_stage3.py column

Wires whale_scanner.classify_whale_bias into the existing v1/v2/v3
backtest pattern: one uniform daily sign from large-premium call-vs-put
option flow, applied to the same OTM-gated gamma x OI aggregation.
Zero new network calls (volume comes from rows already fetched for
greeks/IV). Neutral/no-signal days get regime_whale=None and are
excluded from the regression rather than folded into 'short'.

Backtest-only -- no changes to dealer_positioning.py's live sign
models. See docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Post-plan note

This plan does not include a live/network run of `backtest_stage3.py` against real ThetaData — that costs real API calls and wasn't part of the approved scope. Once both tasks are committed, the natural next step (separate from this plan) is running `python backtest_stage3.py <TICKER> <LOOKBACK_DAYS>` against a real ticker to see what the whale column actually reports on this repo's own data — that's a live decision for the user to trigger, not something to run unprompted mid-plan.
