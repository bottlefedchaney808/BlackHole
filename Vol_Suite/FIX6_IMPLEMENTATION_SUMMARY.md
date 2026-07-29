# FIX 6 Implementation Summary: bs_gamma/bs_gamma_vec Dividend Discount Factor

**Date:** 2026-07-28  
**Ticket:** FIX_PLAN_20260728, Issue 6  
**Status:** COMPLETE

## Problem Statement

Black-Scholes gamma with continuous dividend yield `q` is defined as:
```
Γ = e^{-qT} * N'(d1) / (S * σ * √T)
```

The code was computing `d1` correctly (with the `(r-q)` drift term) but **never applying the `e^{-qT}` discount factor** to the result. This caused gamma to be systematically overstated by a factor of `e^{qT}` whenever `q > 0`.

**Impact:** For SPY (`q≈1.2%`, short-dated `T`), the effect is small (~0.1-0.3%); it grows for higher-yield or longer-dated underlying (e.g., `q=5%`, `T=1yr` → ~5% overstatement).

---

## Changes Made

### 1. `bs_gamma` function (Line 197)

**File:** `C:\Users\bottl\FinancialDevelopment\Vol_Suite\dealer_positioning.py`

**Before:**
```python
def bs_gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sigma_sqrt_T = sigma * math.sqrt(T)
    if sigma_sqrt_T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    return norm_pdf(d1) / (S * sigma_sqrt_T)  # MISSING: exp(-q * T)
```

**After:**
```python
def bs_gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sigma_sqrt_T = sigma * math.sqrt(T)
    if sigma_sqrt_T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    return math.exp(-q * T) * norm_pdf(d1) / (S * sigma_sqrt_T)  # FIXED
```

**Change:** Added `math.exp(-q * T) *` before `norm_pdf(d1)`.

---

### 2. `bs_gamma_vec` function (Line 206)

**File:** `C:\Users\bottl\FinancialDevelopment\Vol_Suite\dealer_positioning.py`

**Before:**
```python
def bs_gamma_vec(S: float, K: np.ndarray, T: np.ndarray, r: float, q: float, sigma: float) -> np.ndarray:
    """Vectorized Black-Scholes gamma over arrays of strikes/TTEs..."""
    with np.errstate(divide='ignore', invalid='ignore'):
        sigma_sqrt_T = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
        gamma = np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi) / (S * sigma_sqrt_T)  # MISSING: exp(-q*T)
    valid = (T > 0) & (sigma_sqrt_T > 0) & (S > 0) & (K > 0)
    return np.where(valid, gamma, 0.0)
```

**After:**
```python
def bs_gamma_vec(S: float, K: np.ndarray, T: np.ndarray, r: float, q: float, sigma: float) -> np.ndarray:
    """Vectorized Black-Scholes gamma over arrays of strikes/TTEs..."""
    with np.errstate(divide='ignore', invalid='ignore'):
        sigma_sqrt_T = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
        gamma = np.exp(-q * T) * np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi) / (S * sigma_sqrt_T)  # FIXED
    valid = (T > 0) & (sigma_sqrt_T > 0) & (S > 0) & (K > 0)
    return np.where(valid, gamma, 0.0)
```

**Change:** Added `np.exp(-q * T) *` before `np.exp(-0.5 * d1 * d1)`.

---

## Tests Added

### Test File: `test_bs_gamma_dividend_discount.py`

Created comprehensive test suite with 10 test cases covering:

1. **Baseline test (q=0):** Verifies bs_gamma works with zero dividend yield
2. **Positive dividend test:** Confirms gamma with q > 0 is discounted by e^{-qT} relative to q=0
3. **Discount factor magnitude:** Tests high dividend yields (q=5%) to verify correct factor
4. **Vectorized version:** Verifies bs_gamma_vec applies dividend discount consistently
5. **Mixed TTEs:** Tests that each element in bs_gamma_vec gets its own individual discount based on its T
6. **Scalar vs vector agreement:** Confirms single-element vectorized calls match scalar results
7. **Edge cases:** Verifies T→0, q=0, and very high q=50% cases still work correctly

**Test Classes:**
- `TestBsGammaDividendDiscount` - 10 @pytest.mark.unit tests

**Key Assertions:**
- `gamma(q>0) / gamma(q=0) = e^{-qT}` with floating-point tolerance < 1e-10
- Scalar and vectorized functions produce identical results to machine precision
- All edge cases (T→0, large q) handled correctly

---

## Verification

### Mathematical Correctness

For any set of inputs `(S, K, T, r, q, sigma)`:

**Old (buggy) formula:**
```
Γ_old = N'(d1) / (S * σ * √T)
```

**New (correct) formula:**
```
Γ_new = e^{-qT} * N'(d1) / (S * σ * √T) = e^{-qT} * Γ_old
```

**Test verification:**
```python
gamma_q0 = bs_gamma(S, K, T, r, 0.0, sigma)
gamma_q_pos = bs_gamma(S, K, T, r, q, sigma)
assert gamma_q_pos ≈ math.exp(-q*T) * gamma_q0  # Within 1e-10
```

### Consistency with Codebase

- Pattern matches `replication_reference.py:_bs_vega()` which correctly uses `S * math.exp(-q * T) * norm_pdf(d1) * sqrtT`
- No other functions depend on the internal implementation of `bs_gamma`/`bs_gamma_vec`
- Changes are internal to these two functions only

---

## Usage Impact

### Where These Functions Are Called

1. **Live heatmap surface** (`dealer_positioning.py:590`):
   ```python
   g = bs_gamma_vec(s, K_arr, T_arr, r_use, dividend_yield, iv)
   ```
   - Now correctly applies dividend yield from ticker's actual dividend data
   - Used for dealer-gamma-vs-spot surface visualization

2. **Backtest Stage 3** (`backtest_stage3.py:242`):
   ```python
   bs_gamma_result = bs_gamma(spot, strike, time_to_expiry, r, q, implied_vol)
   ```
   - Now correctly includes dividend discount in historical gamma calculations

### Expected Behavior Changes

- With `q > 0`, bs_gamma output will be **smaller** than before (by factor e^{-qT})
- For SPY (`q=1.2%`, `T=30 days`): ~0.1% reduction
- For high-yield tickers (`q=5%`, `T=1 year`): ~5% reduction
- Charts and reports using bs_gamma will show lower gamma values, which is correct

---

## Testing Instructions

### Run pytest suite:
```bash
cd Vol_Suite
pytest tests/test_bs_gamma_dividend_discount.py -v
```

### Run standalone verification script:
```bash
cd Vol_Suite
python verify_fix6.py
```

---

## Files Modified

1. **dealer_positioning.py**
   - Line 197: bs_gamma function
   - Line 206: bs_gamma_vec function

## Files Added

1. **tests/test_bs_gamma_dividend_discount.py** - Comprehensive test suite (10 test cases)
2. **verify_fix6.py** - Standalone verification script for manual testing

---

## Cross-Reference

- **Fix Plan:** `FIX_PLAN_20260728.md`, Issue 6, lines 326-367
- **Related:** `replication_reference.py:_bs_vega()` for similar correct pattern
- **Related Issues:** Issue 1 (hedge_requirement), Issue 5 (DDKZ boundary), Issue 7 (forward price dividend)
