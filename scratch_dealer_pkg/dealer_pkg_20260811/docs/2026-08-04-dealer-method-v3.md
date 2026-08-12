# Dealer-Positioning Method v3 — M1/M2 (Real Flow Proxies) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two real-flow dealer-positioning models to the Stage-3 backtest — an OI-change flow net-gamma (M1) and a delta-OI reference measure (M2) — so the micro-structure hypothesis ("more dealer-short → higher forward vol") can be tested with signals that are independent of the IV/vol level (breaking the endogeneity found in CARL R1-F1), and compared against v1/v2 with the existing regression-with-controls machinery.

**Architecture:** Two new pure functions in `backtest_stage3.py` (`_net_gamma_v3_oi_flow`, `_net_delta_oi`) plus a new Black-Scholes delta helper in `dealer_positioning.py` (`bs_delta`). `_build_day_records` computes net-gamma-v3 (needs previous-day OI → track `prev_oi` across the sorted trading dates) and net-delta-OI (needs per-strike delta + IV) per day, stores them on `DayRecord`, and they flow through the existing `_summarize` (Welch, descriptive) and `_summarize_regression` (PRIMARY: OLS of fwd vol on continuous net-gamma controlling for ATM IV + TTE, with a permutation null). Report + `backtest_summary.json` export gain the new columns.

**Tech Stack:** Python 3.14, numpy, scipy.stats, pytest. Run everything through `../findev.sh` (from `Vol_Suite/`).

## Global Constraints
- All pytest must run from the `Vol_Suite/` cwd: `cd Vol_Suite && ../findev.sh -m pytest tests/...`
- TDD: write the failing test first, watch it fail, then implement, then watch it pass.
- Do NOT change the v2 sign convention or the OTM gate (locked decisions). M1/M2 are NEW additive models.
- Keep public `_run_backtest_from_history` / `run_backtest` signatures unchanged (only internals + the result dataclass grow).
- `sign_convention` for dealer position (verify against `dealer_positioning._dealer_sign`: call=+1, put=-1; customer buys → dealer takes the other side).

---
## Task 1: OI-change flow net-gamma model (M1) — `_net_gamma_v3_oi_flow`

**Files:**
- Modify: `Vol_Suite/backtest_stage3.py` — add `_net_gamma_v3_oi_flow`; add `net_gamma_v3` to `DayRecord` and `v3_*` fields to `BacktestResult`; compute it in `_build_day_records`; wire into `_run_backtest_from_history`, `format_backtest_report`, `export_backtest_summary`.
- Test: `Vol_Suite/tests/test_backtest_stage3.py` — add unit test + a DayRecord-presence assertion.

**Interfaces:**
- Consumes: `dealer_positioning._dealer_sign(right)` (call=+1, put=-1); `replication_reference._otm_leg_weights(chain_iv, spot, T)` (dict of OTM (k,right)→weight); `gamma_map`/`oi_today`/`oi_prev` dicts of `(float strike, str right)→value`; `chain_iv`.
- Produces: `_net_gamma_v3_oi_flow(gamma_map, oi_today, oi_prev, chain_iv, spot, T) -> float`; `DayRecord.net_gamma_v3: float`; `BacktestResult.v3_n_long/v3_n_short/v3_long_mean_vol/v3_short_mean_vol/v3_diff/v3_tstat/v3_pvalue/n_reg/v3_reg_coef/v3_reg_t/v3_reg_p/v3_reg_perm_p`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_backtest_stage3.py`)

```python
@pytest.mark.unit
def test_net_gamma_v3_oi_flow_signs_flow():
    """M1: a strike GAINING OI means customers added there, so the dealer
    takes the other side. Call gaining OI -> dealer short call (short gamma,
    negative); put losing OI -> dealer long put? No: put gaining OI ->
    customer long put -> dealer short put = LONG gamma (positive)."""
    chain_iv = _flat_smile_chain(SPOT0)          # existing test helper
    gamma_map = {k: 0.02 for k in chain_iv}
    # today: every strike OTM, calls at 2 OTM strikes gained 100 OI,
    # one OTM put gained 100 OI.
    otm_ks = [k for (k, r) in chain_iv if r == 'C' and k > SPOT0]
    put_ks = [k for (k, r) in chain_iv if r == 'P' and k < SPOT0]
    today = {k: 100 for k in chain_iv}
    prev = {k: 0 for k in chain_iv}
    # call (right='C') gaining OI -> dealer SHORT -> negative contribution
    call_k = otm_ks[0]
    today[(call_k, 'C')] = 200
    prev[(call_k, 'C')] = 0
    # put (right='P') gaining OI -> dealer LONG put -> positive contribution
    put_k = put_ks[0]
    today[(put_k, 'P')] = 200
    prev[(put_k, 'P')] = 0
    net = bt3._net_gamma_v3_oi_flow(gamma_map, today, prev, chain_iv, SPOT0, 0.25)
    # one negative (short call) + one positive (long put) same magnitude
    assert net == pytest.approx(0.0, abs=1e-9), f"two equal/opposite flows should cancel, got {net}"
    # and a pure call-inflow day is clearly negative (dealer short):
    prev2 = {k: 0 for k in chain_iv}
    today2 = {k: 100 for k in chain_iv}
    today2[(call_k, 'C')] = 200   # only the call gained OI
    net2 = bt3._net_gamma_v3_oi_flow(gamma_map, today2, prev2, chain_iv, SPOT0, 0.25)
    assert net2 < 0, "call inflow alone -> dealer short -> negative net gamma"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ../findev.sh -m pytest tests/test_backtest_stage3.py::test_net_gamma_v3_oi_flow_signs_flow -v`
Expected: FAIL with `AttributeError: module 'backtest_stage3' has no attribute '_net_gamma_v3_oi_flow'`

- [ ] **Step 3: Write minimal implementation** (add to `backtest_stage3.py`, after `_net_gamma_v2`)

```python
def _net_gamma_v3_oi_flow(gamma_map: Dict[Tuple[float, str], float],
                          oi_today: Dict[Tuple[float, str], int],
                          oi_prev: Optional[Dict[Tuple[float, str], int]],
                          chain_iv: Dict[Tuple[float, str], float],
                          spot: float, T: float) -> float:
    """M1: OI-change FLOW net-gamma. Sign comes from the net open-interest
    CHANGE per OTM leg -- customers adding a leg -> dealer takes the other
    side -> dealer short call / long put. Magnitude is |ΔOI|*gamma (the new
    flow the dealer must carry), not a fitted IV read, so this is
    independent of the IV/vol level and breaks the vol-clustering
    endogeneity that the IV-richness sign (v2) suffers from.
    """
    otm = replication_reference._otm_leg_weights(chain_iv, spot, T)
    total = 0.0
    for (k, right), gamma in gamma_map.items():
        if gamma <= 0:
            continue
        if (k, right) not in otm:
            continue
        before = oi_prev.get((k, right), 0) if oi_prev else oi_today.get((k, right), 0)
        d_oi = oi_today.get((k, right), 0) - before
        if d_oi == 0:
            continue
        flow = 1 if d_oi > 0 else -1
        w = -dealer_positioning._dealer_sign(right) * flow
        total += w * gamma * abs(d_oi)
    return total
```

- [ ] **Step 4: Wire into `_build_day_records`** (add `net_gamma_v3`, track `prev_oi`)

```python
    prev_oi: Optional[Dict[Tuple[float, str], int]] = None
    for i, d in enumerate(trading_dates):
        ...
        gamma_map = gamma_by_date[d]
        oi_map = oi_by_date[d]
        chain_iv = iv_by_date[d]
        net_v1 = _net_gamma_v1(gamma_map, oi_map)
        net_v2 = _net_gamma_v2(gamma_map, oi_map, chain_iv, spot, forward, T)
        net_v3 = _net_gamma_v3_oi_flow(gamma_map, oi_map, prev_oi, chain_iv, spot, T)
        ...
        records.append(DayRecord(
            date=d, spot=spot, net_gamma_v1=net_v1, net_gamma_v2=net_v2,
            net_gamma_v3=net_v3,
            regime_v1='long' if net_v1 > 0 else 'short',
            regime_v2='long' if net_v2 > 0 else 'short',
            regime_v3='long' if net_v3 > 0 else 'short',
            fwd_realized_vol=fwd_vol, atm_iv=atm_iv, T=T))
        prev_oi = oi_map
```

- [ ] **Step 5: Add `net_gamma_v3` + `regime_v3` to `DayRecord` and `v3_*` to `BacktestResult`**

In the `DayRecord` dataclass add fields after `regime_v2`:
```python
    net_gamma_v3: float = 0.0
    regime_v3: str = 'short'
```
In `BacktestResult` add (mirroring v2, plus regression stats):
```python
    v3_n_long: int = 0
    v3_n_short: int = 0
    v3_long_mean_vol: float = float('nan')
    v3_short_mean_vol: float = float('nan')
    v3_diff: float = float('nan')
    v3_tstat: float = float('nan')
    v3_pvalue: float = float('nan')
    v3_reg_coef: float = float('nan')
    v3_reg_t: float = float('nan')
    v3_reg_p: float = float('nan')
    v3_reg_perm_p: float = float('nan')
```

- [ ] **Step 6: Wire into `_run_backtest_from_history`**

```python
    v1 = _summarize(records, 'regime_v1')
    v2 = _summarize(records, 'regime_v2')
    v3 = _summarize(records, 'regime_v3')
    r1 = _summarize_regression(records, 'net_gamma_v1')
    r2 = _summarize_regression(records, 'net_gamma_v2')
    r3 = _summarize_regression(records, 'net_gamma_v3')
    return BacktestResult(...,  # include all existing args
        v2_reg_coef=r2['coef'], v2_reg_t=r2['t'], v2_reg_p=r2['p'], v2_reg_perm_p=r2['perm_p'],
        v3_n_long=v3['n_long'], v3_n_short=v3['n_short'],
        v3_long_mean_vol=v3['long_mean_vol'], v3_short_mean_vol=v3['short_mean_vol'],
        v3_diff=v3['diff'], v3_tstat=v3['tstat'], v3_pvalue=v3['pvalue'],
        v3_reg_coef=r3['coef'], v3_reg_t=r3['t'], v3_reg_p=r3['p'], v3_reg_perm_p=r3['perm_p'],
    )
```

- [ ] **Step 7: Add v3 rows to `format_backtest_report`** (append after the v2 Welch row, before the PRIMARY TEST block)

```python
        f"{'long-gamma days':20s}{result.v1_n_long:>22d}{result.v2_n_long:>32d}",
        f"{'short-gamma days':20s}{result.v1_n_short:>22d}{result.v2_n_short:>32d}",
```
becomes a 3-column header/loop — add `result.v3_*` as a third column and a `v3 reg` line. Update the header string widths to fit 3 models.

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd Vol_Suite && ../findev.sh -m pytest tests/test_backtest_stage3.py -q`
Expected: all pass (existing 18 + new unit test + any DayRecord-presence assertions added; watch for `regime_v3`/`net_gamma_v3` defaults keeping old tests green).

- [ ] **Step 9: Commit**

```bash
git add Vol_Suite/backtest_stage3.py Vol_Suite/tests/test_backtest_stage3.py
git commit -m "feat(backtest): M1 — OI-change flow net-gamma (v3) sign + wire into regression/report"
```

---
## Task 2: `bs_delta` helper + delta-OI reference measure (M2)

**Files:**
- Modify: `Vol_Suite/dealer_positioning.py` — add `bs_delta`.
- Modify: `Vol_Suite/backtest_stage3.py` — add `_net_delta_oi`; add `net_delta_oi` to `DayRecord` + `delta_oi_reg_*`/`n_delta_oi` to `BacktestResult`; compute in `_build_day_records`; wire into `_run_backtest_from_history` + report + export.
- Test: `Vol_Suite/tests/test_dealer_positioning.py` (new `bs_delta` test) + `Vol_Suite/tests/test_backtest_stage3.py` (delta-OI test).

**Interfaces:**
- Consumes: `bs_delta(S, K, T, r, q, sigma, right)`; `iv_by_date[d]` as `chain_iv`; `_dealer_sign(right)`.
- Produces: `bs_delta(S,K,T,r,q,sigma,right) -> float`; `_net_delta_oi(oi_today, iv_today, spot, T) -> float`; `DayRecord.net_delta_oi: float`; `BacktestResult.n_delta_oi/delta_oi_reg_coef/t/p/perm_p`.

- [ ] **Step 1: Write the failing test for `bs_delta`** (append to `tests/test_dealer_positioning.py`)

```python
import math

def test_bs_delta_atm_call_is_half():
    """BS delta of an ATM call ~= 0.5 (ln(K/S)=0, T>0, r=q)."""
    d = dealer_positioning.bs_delta(S=100.0, K=100.0, T=0.25, r=0.04, q=0.012, sigma=0.20, right='C')
    assert d == pytest.approx(0.5, abs=0.05)
    p = dealer_positioning.bs_delta(S=100.0, K=100.0, T=0.25, r=0.04, q=0.012, sigma=0.20, right='P')
    assert p == pytest.approx(-0.5, abs=0.05)  # put delta negative
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ../findev.sh -m pytest tests/test_dealer_positioning.py::test_bs_delta_atm_call_is_half -v`
Expected: FAIL with `AttributeError: ... has no attribute 'bs_delta'`

- [ ] **Step 3: Write minimal implementation** (add to `dealer_positioning.py`, near `bs_gamma`; note `scipy.stats.norm` is already available there)

```python
def bs_delta(S: float, K: float, T: float, r: float, q: float, sigma: float,
             right: str) -> float:
    """Black-Scholes delta (per unit option, undiscounted for the tiny
    carry term): call = e^{-qT}*N(d1), put = e^{-qT}*(N(d1)-1)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sq)
    nd1 = norm.cdf(d1)
    if right == 'C':
        return math.exp(-q * T) * nd1
    return math.exp(-q * T) * (nd1 - 1.0)
```
(Ensure `from scipy.stats import norm` is imported in `dealer_positioning.py`; add it if missing.)

- [ ] **Step 4: Write the failing test for `_net_delta_oi`** (append to `tests/test_backtest_stage3.py`)

```python
@pytest.mark.unit
def test_net_delta_oi_matches_dealer_side():
    """M2: net delta-OI = sum over chain of _dealer_sign(right)*|delta|*OI.
    On a synthetic symmetric smile, calls+ and puts- should roughly net near
    zero; all-put OI should net negative (dealers long puts)."""
    chain_iv = _flat_smile_chain(SPOT0)
    oi_map = {k: 100 for k in chain_iv}          # symmetric call & put OI
    net = bt3._net_delta_oi(oi_map, chain_iv, SPOT0, 0.25)
    assert abs(net) < 1.0, f"symmetric chain should net ~0, got {net}"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd Vol_Suite && ../findev.sh -m pytest tests/test_backtest_stage3.py::test_net_delta_oi_matches_dealer_side -v`
Expected: FAIL with `AttributeError: ... no attribute '_net_delta_oi'`

- [ ] **Step 6: Write minimal implementation** (add to `backtest_stage3.py` after `_net_gamma_v3_oi_flow`)

```python
def _net_delta_oi(oi_today: Dict[Tuple[float, str], int],
                  iv_today: Dict[Tuple[float, str], float],
                  spot: float, T: float) -> float:
    """M2: net delta-OI -- the market's dealer-neutral delta book. Sums
    _dealer_sign(right)*|bs_delta|*OI across the whole chain (no OTM gate,
    by design: it is a book-level measure). Pure data, no fitted sign.
    Sign convention: call=+ (dealer short call = short delta), put=-.
    Returns a float (can be NaN if T/iv invalid)."""
    total = 0.0
    for (k, right), oi in oi_today.items():
        if oi <= 0:
            continue
        iv = iv_today.get((k, right))
        if iv is None or iv <= 0 or T <= 0 or T != T:
            continue
        dlt = dealer_positioning.bs_delta(spot, k, T, _BACKTEST_R, _BACKTEST_Q, iv, right)
        total += dealer_positioning._dealer_sign(right) * abs(dlt) * oi
    return total
```

- [ ] **Step 7: Compute in `_build_day_records`** and store on `DayRecord`

```python
        net_v3 = _net_gamma_v3_oi_flow(gamma_map, oi_map, prev_oi, chain_iv, spot, T)
        delta_oi = _net_delta_oi(oi_map, chain_iv, spot, T)
        ...
        records.append(DayRecord(
            date=d, spot=spot, net_gamma_v1=net_v1, net_gamma_v2=net_v2,
            net_gamma_v3=net_v3, net_delta_oi=delta_oi,
            regime_v1='long' if net_v1 > 0 else 'short',
            regime_v2='long' if net_v2 > 0 else 'short',
            regime_v3='long' if net_v3 > 0 else 'short',
            fwd_realized_vol=fwd_vol, atm_iv=atm_iv, T=T))
        prev_oi = oi_map
```

- [ ] **Step 8: Add fields + regression wiring**

`DayRecord`: add `net_delta_oi: float = 0.0` after `net_gamma_v3`.
`BacktestResult`: add `n_delta_oi: int = 0` and `delta_oi_reg_coef/t/p/perm_p: float = nan`.
In `_run_backtest_from_history`: `rd = _summarize_regression(records, 'net_delta_oi')`; pass `n_delta_oi=rd['n'], delta_oi_reg_coef=rd['coef'], delta_oi_reg_t=rd['t'], delta_oi_reg_p=rd['p'], delta_oi_reg_perm_p=rd['perm_p']`.

- [ ] **Step 9: Add a delta-OI line to `format_backtest_report`** (after the v3 reg line)

```python
        f"{'delta-OI coef (M2)':20s}{result.delta_oi_reg_coef:>22.4f}{result.delta_oi_reg_perm_p:>32.4f}",
```

- [ ] **Step 10: Run the whole suite to verify**

Run: `cd Vol_Suite && ../findev.sh -m pytest tests -q`
Expected: all Vol tests pass (existing 319 + new). No regressions.

- [ ] **Step 11: Commit**

```bash
git add Vol_Suite/dealer_positioning.py Vol_Suite/backtest_stage3.py \
        Vol_Suite/tests/test_dealer_positioning.py Vol_Suite/tests/test_backtest_stage3.py
git commit -m "feat(backtest): M2 — bs_delta helper + net delta-OI reference measure, wired into regression/report"
```

---
## Self-Review

1. **Spec coverage:** M1 (Task 1) + M2 (Task 2) implement the spec's recommended first deliverable ("M1+M2 together ... directly testable against the new regression"). M4 (dollar-gamma/strip weights), M3 (grounded σ sweep), M5 (multi-expiry), M6 are intentionally a **separate follow-up plan** (distinct concerns: magnitude design, robustness, plumbing) — flagged per the spec's own sequencing.
2. **Placeholder scan:** every step has real code + exact commands. The report/export column additions are specified precisely (only column widths are left for the implementer to align with the existing 3-model layout).
3. **Type consistency:** `_net_gamma_v3_oi_flow` and `_net_delta_oi` signatures are used identically in Task 2's `_build_day_records` wiring as defined in Task 1. `bs_delta` is defined in Task 2 Step 3 before its first use in Step 4/6. `DayRecord`/`BacktestResult` fields match across steps.

**Follow-up plan (separate):** M3 (data-grounded σ + sweep), M4 (dollar-gamma + replication-strip magnitude), M5 (multi-expiry book), M6 (de-confound IV reference).
