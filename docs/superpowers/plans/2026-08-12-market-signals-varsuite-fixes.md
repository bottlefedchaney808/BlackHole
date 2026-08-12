# Market Signals / VaR / Vol Suite Data-Plumbing Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the data-plumbing defects in the dashboard's Market Signals block and VaR Tools
sims (max pain expiry, GARCH conditional vol, silent-zero MC-sim drift, corr_sim horizon,
vega_notional), add VRP-term-structure/price-distribution/social-sentiment Tools entries, and
make the Market Signals bundle render properly (with a distribution visualization) on the
dashboard's unified `/quant` tab.

**Architecture:** Vol_Suite's GARCH module starts writing its computed conditional vol into
`suite_context.json`/`vol_result.json` instead of only printing it; VaR_Tools_Simulations'
`data_loader.py` stops masking real fetch/fit failures as fake zeros; `main.py`'s context-mode
builders (`_build_mc_sim_from_context` etc.) prefer the new context field and emit a
`terminal_price_histogram` alongside their existing percentile summary; two new modules
(`price_dist`, a fixed `corr_sim` default) round out the sim builders; three new `Tools/tools/*`
entries (VRP term structure, price distribution, social-sentiment placeholder) follow the
existing `hedge_optimizer_tool.py` pattern; `shared/summary.py`'s sentiment extractor is
rewritten to understand the market-signals bundle shape it's actually fed today; and
`quant.html` gains a small histogram+percentile-table renderer reused by all four sims.

**Tech Stack:** Python 3.12, numpy, pytest, FastAPI/Jinja2 (dashboard), vanilla JS (no framework
in `quant.html`/`suite.html`).

## Global Constraints

- No changes to the real `sentiment-scanner` suite — the "Social Media Sentiment Scanner" is a
  Tools-only placeholder.
- `var_horizon_days`'s role in true 1-day VaR elsewhere is untouched — only the interactive/
  dashboard `corr_sim` module's own default horizon changes.
- No dashboard auth changes (deliberate, existing, out of scope per `CLAUDE.md`).
- Every new/changed function must have a docstring one-liner only if the WHY is non-obvious;
  no comments restating what the code does.
- `pytest` (full repo) and `pre-commit run --all-files` must pass before each commit that
  touches tracked files.

---

### Task 1: Capture GARCH conditional vol and write it into context

**Files:**
- Modify: `Vol_Suite/garch_analysis.py:326-351` (`run_garch_module`)
- Modify: `Vol_Suite/suite_context.py:109-135` (`build_suite_context`)
- Modify: `shared/schemas.py` (vol_result / suite_context focus validators — search for
  `"target_years"` and `"expiration_date"` in the focus-block validator to find the right spot)
- Modify: `Vol_Suite/volatility_suite.py:1196-1208` (GARCH invocation in `_run_core_analysis`),
  and the `build_suite_context(...)` call site at `Vol_Suite/volatility_suite.py:1464`
- Test: `Vol_Suite/tests/test_suite_context.py`

**Interfaces:**
- Produces: `run_garch_module(ticker, start=..., end=..., output_dir=...) -> tuple[list, str,
  Optional[float]]` — third element is annualized current conditional vol, or `None` on failure.
- Produces: `build_suite_context(..., garch_conditional_vol: Optional[float] = None, ...) ->
  dict` — adds `context["focus"]["garch_conditional_vol"]` (float or `None`).
- Consumes (Task 3): downstream builders read `payload["focus"]["garch_conditional_vol"]`.

- [ ] **Step 1: Write the failing test for `build_suite_context`'s new field**

```python
# Vol_Suite/tests/test_suite_context.py
def test_garch_conditional_vol_included_when_given(tmp_path):
    ctx = build_suite_context(
        output_dir=str(tmp_path), run_id="r1", ticker="AAPL", option_type="call",
        strike=None, target_years=0.25, expiration_date="2026-10-16",
        index_ticker="SPY", basket_tickers=["AAPL"], basket_weights=[1.0],
        sentiment_manifest_path=str(tmp_path / "manifest.json"),
        garch_conditional_vol=0.31,
    )
    assert ctx["focus"]["garch_conditional_vol"] == pytest.approx(0.31)


def test_garch_conditional_vol_defaults_to_none(tmp_path):
    ctx = build_suite_context(
        output_dir=str(tmp_path), run_id="r1", ticker="AAPL", option_type="call",
        strike=None, target_years=0.25, expiration_date="2026-10-16",
        index_ticker="SPY", basket_tickers=["AAPL"], basket_weights=[1.0],
        sentiment_manifest_path=str(tmp_path / "manifest.json"),
    )
    assert ctx["focus"]["garch_conditional_vol"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Vol_Suite/tests/test_suite_context.py -k garch_conditional_vol -v`
Expected: FAIL — `build_suite_context() got an unexpected keyword argument
'garch_conditional_vol'`

- [ ] **Step 3: Add the field to `build_suite_context`**

In `Vol_Suite/suite_context.py`, add the parameter to the signature (after
`var_positions: Optional[Sequence[Dict[str, Any]]] = None,`):

```python
    garch_conditional_vol: Optional[float] = None,
```

and inside the `"focus"` dict literal (after `"expiration_date": _normalize_expiration(expiration_date),`):

```python
            "garch_conditional_vol": (
                float(garch_conditional_vol) if garch_conditional_vol is not None else None
            ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Vol_Suite/tests/test_suite_context.py -k garch_conditional_vol -v`
Expected: PASS

- [ ] **Step 5: Extend `shared/schemas.py`'s focus-block validator to allow the new optional key**

Find the focus-block validation in `shared/schemas.py` (the function validating
`context["focus"]`, which currently checks `ticker`/`option_type`/`strike`/`target_years`/
`expiration_date`). Add a permissive check right after the existing required-key checks:

```python
    if "garch_conditional_vol" in focus:
        v = focus["garch_conditional_vol"]
        _require(v is None or isinstance(v, (int, float)),
                 "focus.garch_conditional_vol must be numeric or null")
```

(Match the existing `_require` helper name used in that file — if it's named differently there,
use that file's own assertion helper instead of introducing a second one.)

- [ ] **Step 6: Write the failing test for `run_garch_module`'s new return value**

```python
# Vol_Suite/tests/test_suite_context.py (or a new Vol_Suite/tests/test_garch_analysis.py if
# that file doesn't already exist for garch_analysis.py)
import numpy as np
import pandas as pd
import garch_analysis as ga


class _FakeParams(dict):
    def get(self, k, default=None):
        return dict.get(self, k, default)


class _FakeResult:
    def __init__(self):
        self.params = _FakeParams({"omega": 0.01, "alpha[1]": 0.08, "beta[1]": 0.9, "nu": 6.0})
        self.conditional_volatility = pd.Series([1.1, 1.2, 1.05])  # daily, percent scale

    def summary(self):
        return "fake summary"


def test_run_garch_module_returns_annualized_conditional_vol(monkeypatch, tmp_path):
    monkeypatch.setattr(ga, "run_garch_analysis", lambda ticker, start=None, end=None: _FakeResult())
    files, interp, vol = ga.run_garch_module("AAPL", output_dir=str(tmp_path))
    assert vol == pytest.approx(1.05 / 100.0 * np.sqrt(252.0), rel=1e-6)


def test_run_garch_module_vol_is_none_on_failure(monkeypatch, tmp_path):
    def _raise(*a, **k):
        raise RuntimeError("fit failed")
    monkeypatch.setattr(ga, "run_garch_analysis", _raise)
    files, interp, vol = ga.run_garch_module("AAPL", output_dir=str(tmp_path))
    assert vol is None
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest Vol_Suite/tests/test_suite_context.py -k run_garch_module -v` (or the new test file)
Expected: FAIL — `run_garch_module` still returns a 2-tuple, or raises instead of catching.

- [ ] **Step 8: Update `run_garch_module` to compute and return annualized conditional vol**

In `Vol_Suite/garch_analysis.py`, change the return type from `list` to reflect the new 3-tuple,
and wrap the whole body in error handling so a fit failure returns `(files, interp, None)`
instead of propagating (note: `log_returns` in `run_garch_analysis` is scaled by `*100`, matching
`_FakeResult`'s percent-scale conditional volatility above — divide by 100 before annualizing):

```python
def run_garch_module(ticker: str, start: str = DEFAULT_START, end: str = None, output_dir: str = None) -> tuple:
    """Wrapper that sets VS_OUTPUT_DIR and runs run_garch_analysis, returning
    (output_files, interpretation_text, annualized_conditional_vol_or_None)."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    os.environ['VS_OUTPUT_DIR'] = out_dir
    try:
        res = run_garch_analysis(ticker, start=start, end=end)
    except Exception as e:
        print(f"  GARCH module failed: {e}")
        return [], f"Ticker: {ticker} - GARCH analysis failed: {e}", None

    files = []
    for fn in os.listdir(out_dir):
        if fn.startswith(f"{ticker}_garch_") and (fn.lower().endswith('.png') or fn.lower().endswith('.pdf')):
            files.append(os.path.join(out_dir, fn))

    garch_conditional_vol = None
    try:
        daily_vol_pct = float(res.conditional_volatility.iloc[-1])
        garch_conditional_vol = daily_vol_pct / 100.0 * np.sqrt(252.0)
    except Exception:
        pass

    try:
        alpha = float(res.params.get('alpha[1]', np.nan))
        beta = float(res.params.get('beta[1]', np.nan))
        nu = float(res.params.get('nu', np.nan))
        persistence = alpha + beta
        interp_lines = [
            f"Ticker: {ticker}",
            f"GARCH(1,1) params: alpha={alpha:.4f}, beta={beta:.4f}, persistence={persistence:.4f}",
            f"t-distribution df: {nu:.2f}",
        ]
        if garch_conditional_vol is not None:
            interp_lines.append(f"Annualized conditional vol: {garch_conditional_vol:.2%}")
    except Exception:
        interp_lines = [f"Ticker: {ticker} - GARCH analysis completed."]
    interp = "\n".join(interp_lines)
    return files, interp, garch_conditional_vol
```

- [ ] **Step 9: Run test to verify it passes**

Run: `pytest Vol_Suite/tests/test_suite_context.py -k "run_garch_module or garch_conditional_vol" -v`
Expected: PASS

- [ ] **Step 10: Wire the return value through `_run_core_analysis` and into `build_suite_context`**

In `Vol_Suite/volatility_suite.py`, at the GARCH call site (`garch_analysis.py:326` call, around
line 1196-1208 in `volatility_suite.py`), capture the third value:

```python
    print("\n[Running] GARCH Analysis")
    garch_conditional_vol = None
    try:
        import garch_analysis as ga
        files, interp, garch_conditional_vol = ga.run_garch_module(ticker, output_dir=out_root)
        produced.extend(files)
        sections.append({
            "title": f"GARCH Analysis: {ticker}", "text": interp or "",
            "images": [f for f in files if f.lower().endswith(('.png', '.pdf'))],
        })
        artifacts["garch_ran"] = True
    except Exception as e:
        print(f"  GARCH failed: {e}")
        _note_error("garch", e)
    artifacts["garch_conditional_vol"] = garch_conditional_vol
```

Then, at the `build_suite_context(...)` call site (`volatility_suite.py:1464`), which must run
*after* the GARCH step above (`_run_core_analysis` runs before context is written per the
existing code order — confirm `artifacts` from `_run_core_analysis`'s return is in scope there;
if `build_suite_context` is called from a different function than `_run_core_analysis`, thread
`garch_conditional_vol` through that function's return/parameters the same way `artifacts` already
flows), add the new kwarg:

```python
    context = build_suite_context(
        ...,
        garch_conditional_vol=artifacts.get("garch_conditional_vol"),
    )
```

Also add `garch_conditional_vol` to `vol_result.json`'s per-ticker output dict wherever
`fair_vol_pct` is currently written (same block `_extract_vol` in `shared/summary.py` reads from)
so it round-trips into `quant_summary.json` metrics too — mirror the existing
`artifacts["garch_conditional_vol"]` value into that result dict under the same key.

- [ ] **Step 11: Run the full Vol_Suite test suite**

Run: `pytest Vol_Suite/tests/ -v`
Expected: PASS (no regressions)

- [ ] **Step 12: Commit**

```bash
git add Vol_Suite/garch_analysis.py Vol_Suite/suite_context.py Vol_Suite/volatility_suite.py shared/schemas.py Vol_Suite/tests/
git commit -m "feat(vol-suite): capture GARCH conditional vol and write it into suite_context/vol_result"
```

---

### Task 2: Stop masking real fetch/fit failures as fake zeros in `data_loader.py`

**Files:**
- Modify: `VaR_Tools_Simulations/var_engine/data_loader.py:177-208`
  (`estimate_garch_vol`, `estimate_geometric_return`)
- Test: `VaR_Tools_Simulations/tests/test_data_loader.py` (new file)

**Interfaces:**
- Produces: `estimate_garch_vol(ticker, lookback_days=504, trading_days=252.0) ->
  Optional[float]` — `None` on failure (was `0.0`).
- Produces: `estimate_geometric_return(ticker, lookback_days=504, trading_days=252.0) ->
  Optional[float]` — `None` on failure (was `0.0`).
- Consumes (Task 3): `main.py`'s builders switch their `or 0.25` / bare-call patterns to
  `is None`-aware fallback chains.

- [ ] **Step 1: Write the failing tests**

```python
# VaR_Tools_Simulations/tests/test_data_loader.py
import numpy as np
import pytest
from var_engine import data_loader


def test_estimate_garch_vol_returns_none_on_fetch_failure(monkeypatch, caplog):
    def _raise(*a, **k):
        raise RuntimeError("ThetaData 502")
    monkeypatch.setattr(data_loader, "fetch_log_returns", _raise)
    with caplog.at_level("WARNING"):
        result = data_loader.estimate_garch_vol("AAPL")
    assert result is None
    assert any("AAPL" in r.message for r in caplog.records)


def test_estimate_garch_vol_returns_none_on_insufficient_data(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_log_returns", lambda *a, **k: np.zeros(10))
    assert data_loader.estimate_garch_vol("AAPL") is None


def test_estimate_geometric_return_returns_none_on_fetch_failure(monkeypatch, caplog):
    def _raise(*a, **k):
        raise RuntimeError("ThetaData 502")
    monkeypatch.setattr(data_loader, "fetch_price_series", _raise)
    with caplog.at_level("WARNING"):
        result = data_loader.estimate_geometric_return("AAPL")
    assert result is None
    assert any("AAPL" in r.message for r in caplog.records)


def test_estimate_geometric_return_computes_real_value(monkeypatch):
    px = np.linspace(100.0, 120.0, 200)
    monkeypatch.setattr(data_loader, "fetch_price_series", lambda *a, **k: px)
    result = data_loader.estimate_geometric_return("AAPL")
    assert result is not None
    assert result == pytest.approx((px[-1] / px[0]) ** (252.0 / len(px)) - 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest VaR_Tools_Simulations/tests/test_data_loader.py -v`
Expected: FAIL — both functions currently return `0.0`, not `None`, on failure; no logging
happens.

- [ ] **Step 3: Implement — return `None` and log instead of silently swallowing**

```python
# VaR_Tools_Simulations/var_engine/data_loader.py
import logging

_LOGGER = logging.getLogger(__name__)


def estimate_garch_vol(ticker: str,
                       lookback_days: int = 504,
                       trading_days: float = 252.0) -> Optional[float]:
    """Quick GARCH(1,1) estimate: annualized conditional vol for *ticker*.
    Uses the same scipy-based fit as hist_sim.py (no arch/statsmodels dep).
    Returns None (not 0.0) on failure -- callers must not treat a fetch/fit
    error as a legitimately-computed zero volatility.
    """
    from .hist_sim import _garch_fit
    start, end = default_date_range(lookback_days)
    try:
        rets = fetch_log_returns(ticker, start, end)
        if len(rets) < 60:
            _LOGGER.warning("estimate_garch_vol(%s): only %d return points (<60), skipping fit",
                             ticker, len(rets))
            return None
        g = _garch_fit(rets)
        return float(g["current_vol"] * np.sqrt(trading_days))
    except Exception:
        _LOGGER.warning("estimate_garch_vol(%s) failed", ticker, exc_info=True)
        return None


def estimate_geometric_return(ticker: str,
                              lookback_days: int = 504,
                              trading_days: float = 252.0) -> Optional[float]:
    """Annualized geometric mean return from historical prices.
    Computed as (P_T / P_0)^(252/T) - 1. Returns None (not 0.0) on failure.
    """
    start, end = default_date_range(lookback_days)
    try:
        px = fetch_price_series(ticker, start, end)
        if len(px) < 60:
            _LOGGER.warning("estimate_geometric_return(%s): only %d price points (<60)",
                             ticker, len(px))
            return None
        r = (px[-1] / px[0]) ** (trading_days / len(px)) - 1.0
        return float(r)
    except Exception:
        _LOGGER.warning("estimate_geometric_return(%s) failed", ticker, exc_info=True)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest VaR_Tools_Simulations/tests/test_data_loader.py -v`
Expected: PASS

- [ ] **Step 5: Update every existing caller of these two functions to handle `None`**

`grep -n "estimate_garch_vol\|estimate_geometric_return" VaR_Tools_Simulations/main.py` (matches
lines 344-345, 385-386, 441, 523, 532, 1182, 1188 per current code). Every `or 0.25` pattern
already degrades gracefully since `None or 0.25 == 0.25` — no change needed there. The two bare
calls without a fallback (`drift = data_loader.estimate_geometric_return(tk)` at lines 345 and
386) will be handled in Task 3, which also adds the `data_quality` field — leave them as-is here
so this task stays isolated to `data_loader.py`. Confirm nothing else assumes a `float` return
type without a `None` check by running the full VaR suite test pass in the next step.

- [ ] **Step 6: Run the full VaR_Tools_Simulations test suite**

Run: `pytest VaR_Tools_Simulations/tests/ -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add VaR_Tools_Simulations/var_engine/data_loader.py VaR_Tools_Simulations/tests/test_data_loader.py
git commit -m "fix(var-tools): stop masking GARCH/drift fetch failures as fake zero values"
```

---

### Task 3: Wire context-sourced GARCH vol + `data_quality` flag into MC/copula/corr_sim_peer builders

**Files:**
- Modify: `VaR_Tools_Simulations/main.py:334-484`
  (`_build_mc_sim_from_context`, `_build_copula_from_context`, `_build_corr_sim_peer_from_context`)
- Test: `VaR_Tools_Simulations/tests/test_context_builders.py` (new file)

**Interfaces:**
- Consumes: `data_loader.estimate_garch_vol -> Optional[float]`,
  `data_loader.estimate_geometric_return -> Optional[float]` (Task 2);
  `payload["focus"]["garch_conditional_vol"]` (Task 1).
- Produces: each builder's result dict gains a `"data_quality"` key:
  `{"vol_source": "context" | "garch_fit" | "fallback", "expected_return_source": "computed" | "unavailable"}`.

- [ ] **Step 1: Write the failing test**

```python
# VaR_Tools_Simulations/tests/test_context_builders.py
import numpy as np
import pytest
import main as var_main
from var_engine import data_loader


def _base_payload(garch_vol=None):
    return {"focus": {"ticker": "AAPL", "garch_conditional_vol": garch_vol}, "ticker": "AAPL"}


def test_mc_sim_prefers_context_garch_vol(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_spot", lambda tk: 200.0)
    monkeypatch.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.40)  # should NOT be used
    monkeypatch.setattr(data_loader, "estimate_geometric_return", lambda tk: 0.10)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=0.22), "AAPL")
    assert result["vol"] == pytest.approx(0.22)
    assert result["data_quality"]["vol_source"] == "context"


def test_mc_sim_falls_back_to_garch_fit_when_context_vol_missing(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_spot", lambda tk: 200.0)
    monkeypatch.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.35)
    monkeypatch.setattr(data_loader, "estimate_geometric_return", lambda tk: 0.10)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.35)
    assert result["data_quality"]["vol_source"] == "garch_fit"


def test_mc_sim_falls_back_to_default_when_both_missing(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_spot", lambda tk: 200.0)
    monkeypatch.setattr(data_loader, "estimate_garch_vol", lambda tk: None)
    monkeypatch.setattr(data_loader, "estimate_geometric_return", lambda tk: None)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.25)
    assert result["data_quality"]["vol_source"] == "fallback"
    assert result["data_quality"]["expected_return_source"] == "unavailable"
    assert result["expected_return"] == pytest.approx(0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -v`
Expected: FAIL — `_build_mc_sim_from_context` still always calls `estimate_garch_vol` and has no
`data_quality` key.

- [ ] **Step 3: Add a shared vol-resolution helper and use it in all three builders**

In `VaR_Tools_Simulations/main.py`, add near `_context_seed` (around line 324-331):

```python
def _resolve_vol_and_quality(payload: dict, tk: str) -> tuple:
    """Prefer suite_context's Vol_Suite-computed GARCH vol; fall back to VaR's
    own GARCH fit, then to a fixed default. Returns (vol, vol_source)."""
    from var_engine import data_loader
    focus = payload.get("focus") if isinstance(payload.get("focus"), dict) else {}
    ctx_vol = focus.get("garch_conditional_vol")
    if isinstance(ctx_vol, (int, float)) and ctx_vol > 0:
        return float(ctx_vol), "context"
    fit_vol = data_loader.estimate_garch_vol(tk)
    if fit_vol is not None:
        return float(fit_vol), "garch_fit"
    return 0.25, "fallback"


def _resolve_drift_and_quality(tk: str) -> tuple:
    """VaR's own historical geometric drift -- Vol_Suite has no drift/expected-
    return concept to source from. Returns (drift, expected_return_source)."""
    from var_engine import data_loader
    drift = data_loader.estimate_geometric_return(tk)
    if drift is None:
        return 0.0, "unavailable"
    return float(drift), "computed"
```

- [ ] **Step 4: Update `_build_mc_sim_from_context`**

Replace lines 340-345 (`tk = _focus_ticker(...)` through `drift = ...`) with:

```python
    tk = _focus_ticker(payload, ticker)
    spot = data_loader.fetch_spot(tk)
    if spot <= 0:
        raise ContextModeError(f"Could not fetch live spot for {tk}.")
    vol, vol_source = _resolve_vol_and_quality(payload, tk)
    drift, drift_source = _resolve_drift_and_quality(tk)
    seed = _context_seed(payload)
```

and add `"data_quality"` to the returned dict (after `"expected_return": float(drift),`):

```python
        "data_quality": {"vol_source": vol_source, "expected_return_source": drift_source},
```

- [ ] **Step 5: Apply the same change to `_build_copula_from_context` (lines 375-415)**

Same substitution: replace the `vol = data_loader.estimate_garch_vol(tk) or 0.25` /
`drift = data_loader.estimate_geometric_return(tk)` pair with the two helper calls, and add the
same `"data_quality"` entry to its returned dict.

- [ ] **Step 6: Apply the same change to `_build_corr_sim_peer_from_context` (starting line 418)**

Read the rest of that function (`VaR_Tools_Simulations/main.py:418-484`) to find its own
`estimate_garch_vol`/`estimate_geometric_return` call sites (per-ticker, looped over the focus
ticker + peers) and apply the same helper substitution for the focus ticker only — peers keep
using `estimate_garch_vol(peer) or 0.25` directly since `garch_conditional_vol` in context is
only defined for the focus ticker, not each peer.

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -v`
Expected: PASS

- [ ] **Step 8: Run the full VaR_Tools_Simulations test suite**

Run: `pytest VaR_Tools_Simulations/tests/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add VaR_Tools_Simulations/main.py VaR_Tools_Simulations/tests/test_context_builders.py
git commit -m "feat(var-tools): prefer context GARCH vol in sim builders, surface data_quality"
```

---

### Task 4: Max Pain expiry from context

**Files:**
- Modify: `sentiment-scanner/scanner/max_pain_scanner.py:63-` (`scan_max_pain`)
- Modify: `orchestrator.py:304-327` (market-signals scanner loop)
- Test: `sentiment-scanner/tests/test_max_pain_scanner.py` (check whether this file already
  exists; if not, create it next to the suite's other scanner tests)

**Interfaces:**
- Produces: `scan_max_pain(ticker: str, expiry: Optional[str] = None) -> MaxPainScan` — when
  `expiry` (YYYYMMDD or YYYY-MM-DD) is given, it's used directly instead of the internal
  nearest-~30DTE self-selection.

- [ ] **Step 1: Write the failing test**

```python
# sentiment-scanner/tests/test_max_pain_scanner.py
from unittest.mock import MagicMock
from scanner.max_pain_scanner import scan_max_pain


def test_scan_max_pain_uses_explicit_expiry(monkeypatch):
    import scanner.max_pain_scanner as mp

    fake_td = MagicMock()
    fake_td.fetch_spot_price.return_value = 150.0
    monkeypatch.setattr(mp, "ThetaDataController", lambda: fake_td)
    nearest_called = {"called": False}

    def _fail_if_called(*a, **k):
        nearest_called["called"] = True
        return "20261016", 0.25
    monkeypatch.setattr(mp.vsi.expiry_selector, "nearest_expiry", _fail_if_called)
    fake_td.option_bulk_oi.return_value = []

    result = scan_max_pain("AAPL", expiry="20261120")
    assert result.expiry == "20261120"
    assert nearest_called["called"] is False
```

(Adjust mock targets to match the actual import names in `max_pain_scanner.py` — read the file's
top-of-file imports first if `ThetaDataController`/`vsi.expiry_selector` aren't the exact names
used there.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest sentiment-scanner/tests/test_max_pain_scanner.py -v`
Expected: FAIL — `scan_max_pain()` doesn't accept an `expiry` keyword yet.

- [ ] **Step 3: Add the `expiry` parameter**

In `sentiment-scanner/scanner/max_pain_scanner.py`, change the signature at line 63:

```python
def scan_max_pain(ticker: str, expiry: str = None) -> MaxPainScan:
```

and where the function currently self-selects (around line 96-100, `vsi.expiry_selector.nearest_expiry(...)`), guard it:

```python
    if expiry:
        exp_norm = str(expiry).replace("-", "")
        T_years = None  # recomputed below once spot/expiry are both known, same as the self-selected path
    else:
        try:
            exp_norm, T_years = vsi.expiry_selector.nearest_expiry(...)  # existing call, unchanged
        except Exception:
            ...  # existing error path, unchanged
```

Read the surrounding 20 lines in the actual file before writing this edit — `T_years` is used
later in the function (line 199's `MaxPainScan(...)` construction per the earlier grep), so if
`expiry` is supplied, compute `T_years` the same way the self-selected path does (likely
`(datetime.strptime(exp_norm, "%Y%m%d") - datetime.now()).days / 365.0` — match whatever
`nearest_expiry` itself uses so the two paths agree) instead of leaving it `None`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest sentiment-scanner/tests/test_max_pain_scanner.py -v`
Expected: PASS

- [ ] **Step 5: Pass the context expiry through from the orchestrator's market-signals stage**

In `orchestrator.py`, the scanner loop (lines 308-323) currently calls `scan_fn(ticker)`
uniformly for all four scanners. Change the `max_pain` entry to pass expiry:

```python
        for key, scan_fn, fmt_fn in (
            ('iv_rank', scan_iv_rank, format_iv_rank),
            ('max_pain', scan_max_pain, format_max_pain),
            ('skew', scan_skew, format_skew),
            ('unusual_oi', scan_unusual_oi, format_unusual_oi),
        ):
            try:
                if key == 'max_pain':
                    expiry = context.get('focus', {}).get('expiration_date')
                    scan = scan_fn(ticker, expiry=expiry.replace('-', '') if expiry else None)
                else:
                    scan = scan_fn(ticker)
```

- [ ] **Step 6: Run the sentiment-scanner test suite**

Run: `cd sentiment-scanner && python -m pytest tests/ -v` (uses its own project-local venv per
`.claude/skills/sentiment-scanner/SKILL.md` — check that skill if the venv path isn't obvious)
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add sentiment-scanner/scanner/max_pain_scanner.py orchestrator.py sentiment-scanner/tests/test_max_pain_scanner.py
git commit -m "fix(market-signals): max pain scanner uses the run's context expiry instead of self-selecting"
```

---

### Task 5: corr_sim horizon default (252d) + dashboard horizon field

**Files:**
- Modify: `VaR_Tools_Simulations/main.py:125-176` (`_build_corr_sim_from_context`)
- Modify: `dashboard/app.py:434-480` (`_focus_from_body`)
- Modify: `orchestrator.py` (wherever `var_horizon_days=int(focus.get('var_horizon_days') or 1)`
  is set — grep confirmed this at `orchestrator.py:602`; no change needed there, it already
  reads `focus['var_horizon_days']` when present)
- Test: `VaR_Tools_Simulations/tests/test_corr_sim.py` (context-builder tests likely belong in
  the new `test_context_builders.py` from Task 3 instead — check which convention the existing
  `test_corr_sim.py` follows before choosing)

**Interfaces:**
- Produces: `_build_corr_sim_from_context(payload)` — `var.horizon_days` becomes optional
  (defaults to 252 instead of raising); a new optional `corr_sim_days` top-level payload field
  takes priority over `var.horizon_days` when both are present.
- Produces: `_focus_from_body(body) -> (focus, error)` — `focus['var_horizon_days']` set from
  `body.get('var_horizon_days')` when provided (previously silently dropped).

- [ ] **Step 1: Write the failing test**

```python
# VaR_Tools_Simulations/tests/test_context_builders.py (append to the file from Task 3)
def test_corr_sim_defaults_to_252_days_when_horizon_omitted(monkeypatch):
    from var_engine import data_loader
    monkeypatch.setattr(data_loader, "live_price", lambda tk: 100.0)
    payload = {
        "ticker": "AAPL",
        "var": {"confidence": 0.99},
    }
    result = var_main._build_corr_sim_from_context(payload)
    assert result["horizon_days"] == 252


def test_corr_sim_respects_explicit_corr_sim_days_override(monkeypatch):
    from var_engine import data_loader
    monkeypatch.setattr(data_loader, "live_price", lambda tk: 100.0)
    payload = {
        "ticker": "AAPL",
        "var": {"confidence": 0.99, "horizon_days": 10},
        "corr_sim_days": 30,
    }
    result = var_main._build_corr_sim_from_context(payload)
    assert result["horizon_days"] == 30
```

(`live_price` is the helper `_build_corr_sim_from_context` already calls at line 248 for
spot-price defaulting — confirm its actual import path in `main.py` before writing the
monkeypatch target; it may be `from var_engine.data_loader import ...` imported at module scope
rather than referenced as `data_loader.live_price`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -k corr_sim_defaults -v`
Expected: FAIL — currently raises `ContextModeError("Missing required field 'var.horizon_days'.")`

- [ ] **Step 3: Relax the horizon requirement and prefer `corr_sim_days`**

In `VaR_Tools_Simulations/main.py`, replace lines 162-176:

```python
    var_cfg = payload.get("var")
    if not isinstance(var_cfg, dict):
        raise ContextModeError("Missing required object field 'var'.")
    if "confidence" not in var_cfg:
        raise ContextModeError("Missing required field 'var.confidence'.")

    horizon_source = payload.get("corr_sim_days", var_cfg.get("horizon_days", 252))
    try:
        horizon_days = int(horizon_source)
    except Exception as e:
        raise ContextModeError(
            "Field 'corr_sim_days' (or 'var.horizon_days') must be an integer.") from e
    if horizon_days <= 0:
        raise ContextModeError("Field 'corr_sim_days' must be > 0.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -k corr_sim -v`
Expected: PASS

- [ ] **Step 5: Add `var_horizon_days` passthrough to the dashboard trigger form**

In `dashboard/app.py`'s `_focus_from_body` (after the `index`/`index_ticker` block, around line
471-472), add:

```python
    horizon = body.get('var_horizon_days')
    if horizon not in (None, ''):
        try:
            focus['var_horizon_days'] = int(horizon)
        except (TypeError, ValueError):
            return {}, 'var_horizon_days must be an integer'
```

- [ ] **Step 6: Add a matching test for `_focus_from_body`**

Find `dashboard`'s existing app tests (check `tests/` for `test_dashboard` or similar — if none
exist for `_focus_from_body` specifically, add this alongside the closest existing coverage) and
add:

```python
def test_focus_from_body_passes_through_var_horizon_days():
    focus, error = _focus_from_body({'ticker': 'AAPL', 'var_horizon_days': '30'})
    assert error is None
    assert focus['var_horizon_days'] == 30
```

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest -k test_focus_from_body_passes_through_var_horizon_days -v`
Expected: PASS

- [ ] **Step 8: Run the full VaR_Tools_Simulations and dashboard test suites**

Run: `pytest VaR_Tools_Simulations/tests/ tests/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add VaR_Tools_Simulations/main.py dashboard/app.py VaR_Tools_Simulations/tests/
git commit -m "fix(var-tools): corr_sim defaults to a 1-year horizon, dashboard can override it"
```

---

### Task 6: vega_notional scales with spot instead of a flat 100000

**Files:**
- Modify: `Vol_Suite/variance_swap_live.py:327-528`
  (`main`, `run_variance_swap_live`)
- Test: `Vol_Suite/tests/test_variance_swap_replication.py` (append; this is the existing test
  file covering `variance_swap_live.py`/`vrp_term_structure.py` per the earlier `grep`)

**Interfaces:**
- Produces: `compute_vega_notional(spot: float, base_notional: float = 100_000.0,
  reference_spot: float = 100.0) -> float` — scales `base_notional` by `spot / reference_spot`,
  clamped to a sane floor (`base_notional`) so cheap tickers don't get a vega notional near zero.
- Produces: `run_variance_swap_live(...)`'s returned `result` dict gains `"vega_notional"` and
  `"variance_notional"` keys (previously absent from the programmatic path entirely).

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_variance_swap_replication.py (append)
from variance_swap_live import compute_vega_notional


def test_compute_vega_notional_scales_with_spot():
    low = compute_vega_notional(spot=20.0)
    high = compute_vega_notional(spot=2000.0)
    assert high > low


def test_compute_vega_notional_at_reference_spot_matches_base():
    assert compute_vega_notional(spot=100.0) == pytest.approx(100_000.0)


def test_compute_vega_notional_floors_at_base_for_cheap_tickers():
    assert compute_vega_notional(spot=1.0) >= 100_000.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Vol_Suite/tests/test_variance_swap_replication.py -k vega_notional -v`
Expected: FAIL — `compute_vega_notional` doesn't exist yet.

- [ ] **Step 3: Add `compute_vega_notional` and wire it into both call paths**

In `Vol_Suite/variance_swap_live.py`, add near `compute_forward_price` (around line 60):

```python
def compute_vega_notional(spot: float, base_notional: float = 100_000.0,
                          reference_spot: float = 100.0) -> float:
    """Target dollar-vega exposure, scaled so a $2000 stock and a $20 stock
    aren't sized identically. `base_notional` (the prior flat constant) is
    both the value at `reference_spot` and the floor for very cheap
    underlyings -- vega notional is a trade-size choice (see the variance-
    swap payoff convention N_var = N_vol / (2*sigma_strike)), not something
    that should shrink toward zero just because spot is small."""
    if spot <= 0:
        return base_notional
    return max(base_notional, base_notional * (spot / reference_spot))
```

Replace lines 411-413 in `main()`:

```python
    vega_notional = compute_vega_notional(S0)
    variance_notional = vega_notional / (2 * result["fair_variance_swap_strike_vol"])
    print(f"\nVega notional: ${vega_notional:,.0f} -> Variance notional: ${variance_notional:,.2f}")
```

And add the same computation to `run_variance_swap_live` (the programmatic path used by
`_run_core_analysis`, which never computed either value before), right before the final `return
files, interp, result` (around line 526-528):

```python
    try:
        vega_notional = compute_vega_notional(S0)
        variance_notional = vega_notional / (2 * result["fair_variance_swap_strike_vol"])
        result['vega_notional'] = vega_notional
        result['variance_notional'] = variance_notional
    except Exception:
        pass
    return files, interp, result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest Vol_Suite/tests/test_variance_swap_replication.py -k vega_notional -v`
Expected: PASS

- [ ] **Step 5: Run the full Vol_Suite test suite**

Run: `pytest Vol_Suite/tests/ -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Vol_Suite/variance_swap_live.py Vol_Suite/tests/test_variance_swap_replication.py
git commit -m "fix(vol-suite): vega_notional scales with spot instead of a flat 100000 for every ticker"
```

---

### Task 7: VRP term structure default-on in context mode + Tools entry

**Files:**
- Modify: `Vol_Suite/volatility_suite.py:1752`
- Create: `Tools/tools/vrp_term_structure_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py`, `Tools/tests/test_vrp_term_structure_tool.py` (new)

**Interfaces:**
- Produces: `Tools/tools/vrp_term_structure_tool.py::run(context: dict) -> dict` — delegates to
  `Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure`, returns a JSON-serializable dict
  shaped like `Vol_Suite/volatility_suite.py`'s existing `artifacts["vrp_term_structure"]` block
  (`{"available": bool, "shape": str, "points": [...], "chart_path": str}`).
- Produces: `TOOL_SPEC = ToolSpec(name="VRP Term Structure", slug="vrp-term-structure", ...)`
  registered in `Tools/registry.py::TOOLS`.

- [ ] **Step 1: Flip the context-mode default**

In `Vol_Suite/volatility_suite.py:1752`, change:

```python
    run_vrp_term_structure = _env_flag("VS_RUN_VRP_TERM_STRUCTURE", True)
```

- [ ] **Step 2: Run the existing VRP term structure test**

Run: `pytest Vol_Suite/tests/test_vrp_term_structure.py -v`
Expected: PASS (this test targets the pure computation, unaffected by the context-mode default
change; confirms nothing else broke)

- [ ] **Step 3: Run a context-mode smoke test to confirm VRP now runs by default**

Check `Vol_Suite/tests/test_context_mode.py` for its existing pattern (it already builds a fake
`suite_context.json` and calls the context-mode entry point) — add an assertion there (or a new
test in the same style) that `artifacts["vrp_term_structure"]["available"]` is `True` after a
context-mode run with `VS_RUN_VRP_TERM_STRUCTURE` unset, mirroring however that file currently
asserts on other `artifacts` keys.

- [ ] **Step 4: Write the failing test for the new Tools entry**

```python
# Tools/tests/test_vrp_term_structure_tool.py
from Tools.tools import vrp_term_structure_tool


def test_run_delegates_to_compute_vrp_term_structure(monkeypatch):
    calls = {}

    class _FakeResult:
        shape = "contango"
        points = []

    def _fake_compute(ticker, td, spot, r, q):
        calls['args'] = (ticker, spot, r, q)
        return _FakeResult()

    class _FakeTD:
        def fetch_spot_price(self, ticker):
            return 150.0
        def fetch_dividend_yield(self, ticker):
            return 0.01
        def fetch_risk_free_rate(self, t):
            return 0.05
        def close(self):
            pass

    monkeypatch.setattr(vrp_term_structure_tool, "_theta_client", lambda: _FakeTD())
    monkeypatch.setattr(vrp_term_structure_tool, "compute_vrp_term_structure", _fake_compute)

    result = vrp_term_structure_tool.run({"focus": {"ticker": "AAPL"}})
    assert result["available"] is True
    assert result["shape"] == "contango"
    assert calls['args'][0] == "AAPL"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest Tools/tests/test_vrp_term_structure_tool.py -v`
Expected: FAIL — module doesn't exist yet.

- [ ] **Step 6: Create `Tools/tools/vrp_term_structure_tool.py`**

```python
"""vrp_term_structure_tool.py

Wraps Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure as a
standalone Tool, independent of a full Vol_Suite run -- it only needs a
ticker, following the same lazy-import-under-unique-name pattern as
hedge_optimizer_tool.py to avoid colliding with Vol_Suite's own module names.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402
from vrp_term_structure import compute_vrp_term_structure  # noqa: E402


def _theta_client():
    from shared.config import load_env_once
    load_env_once()
    from thetadata_client import ThetaDataController
    return ThetaDataController()


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Returns
    {"available": bool, "shape": str, "points": [...], "error": str|None}."""
    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    if not ticker:
        return {"available": False, "error": "context.focus.ticker is required"}

    td = _theta_client()
    try:
        spot = td.fetch_spot_price(ticker)
        q = td.fetch_dividend_yield(ticker)
        r = td.fetch_risk_free_rate(0.25) or 0.05
        result = compute_vrp_term_structure(ticker, td, spot, r, q)
    except Exception as e:
        return {"available": False, "error": str(e)}
    finally:
        td.close()

    return {
        "available": True,
        "shape": result.shape,
        "points": [vars(p) for p in result.points],
    }


TOOL_SPEC = ToolSpec(
    name="VRP Term Structure",
    slug="vrp-term-structure",
    description=(
        "Computes the variance-risk-premium term structure (1-12mo) for a "
        "context's focus ticker: fair vol, ATM IV, and VRP at each tenor."
    ),
    run=run,
)
```

- [ ] **Step 7: Register it in `Tools/registry.py`**

Add the import (with the others in `_load_tools`) and append to the returned list:

```python
    from Tools.tools import vrp_term_structure_tool
    ...
        vrp_term_structure_tool.TOOL_SPEC,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest Tools/tests/test_vrp_term_structure_tool.py Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add Vol_Suite/volatility_suite.py Tools/tools/vrp_term_structure_tool.py Tools/registry.py Tools/tests/
git commit -m "feat: VRP term structure runs by default in context mode + new Tools entry"
```

---

### Task 8: Price-distribution context builder + terminal-price histogram + Tools entry

**Files:**
- Modify: `VaR_Tools_Simulations/main.py` (new `_build_price_dist_from_context`, plus add a
  `terminal_price_histogram` field to `_build_mc_sim_from_context` / `_build_copula_from_context`
  / `_build_corr_sim_peer_from_context` for Task 11's renderer)
- Create: `Tools/tools/price_dist_tool.py`
- Modify: `Tools/registry.py`
- Test: `VaR_Tools_Simulations/tests/test_context_builders.py` (append), `Tools/tests/test_price_dist_tool.py` (new)

**Interfaces:**
- Produces: `_build_price_dist_from_context(payload, ticker=None) -> dict` — flat dict shaped
  like `_build_mc_sim_from_context`'s output plus a `"distribution_table"` (from
  `price_dist.lognormal_dist`) and `"terminal_price_histogram"` field.
- Produces: `_histogram_bins(values: np.ndarray, n_bins: int = 20) -> list[dict]` — shared helper
  used by all four sim builders, each bin `{"low": float, "high": float, "count": int}`.

- [ ] **Step 1: Write the failing test**

```python
# VaR_Tools_Simulations/tests/test_context_builders.py (append)
def test_price_dist_builder_returns_distribution_table_and_histogram(monkeypatch):
    from var_engine import data_loader
    monkeypatch.setattr(data_loader, "fetch_spot", lambda tk: 200.0)
    monkeypatch.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.30)
    monkeypatch.setattr(data_loader, "estimate_geometric_return", lambda tk: 0.08)

    result = var_main._build_price_dist_from_context({"focus": {"ticker": "AAPL"}}, "AAPL")
    assert result["status"] == "ok"
    assert result["module"] == "price_dist_1yr"
    assert len(result["distribution_table"]) > 0
    assert len(result["terminal_price_histogram"]) > 0
    assert sum(b["count"] for b in result["terminal_price_histogram"]) == result["n_sims"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -k price_dist -v`
Expected: FAIL — function doesn't exist.

- [ ] **Step 3: Add `_histogram_bins` and `_build_price_dist_from_context` to `main.py`**

Add near `_context_seed` (VaR_Tools_Simulations/main.py):

```python
def _histogram_bins(values: np.ndarray, n_bins: int = 20) -> list:
    """Bucket *values* into n_bins equal-width bins for chart rendering --
    the dashboard has no path-level data to draw (mc_sim/copula/corr_sim are
    single-step-to-horizon, not multi-step path simulators), so a terminal-
    distribution histogram is the honest visual: it shows the actual shape
    of the simulated outcome, not a fabricated path."""
    counts, edges = np.histogram(values, bins=n_bins)
    return [{"low": float(edges[i]), "high": float(edges[i + 1]), "count": int(counts[i])}
            for i in range(n_bins)]
```

Add after `_build_mc_sim_from_context` (after line ~371, before `_build_copula_from_context`):

```python
def _build_price_dist_from_context(payload: dict, ticker: str = None) -> dict:
    """Non-interactive 1-year-out price-distribution table + MC terminal
    histogram, same seed/vol/spot/drift inputs as _build_mc_sim_from_context."""
    from var_engine import data_loader
    from var_engine.price_dist import lognormal_dist, mc_probabilities, MCProbInputs

    tk = _focus_ticker(payload, ticker)
    spot = data_loader.fetch_spot(tk)
    if spot <= 0:
        raise ContextModeError(f"Could not fetch live spot for {tk}.")
    vol, vol_source = _resolve_vol_and_quality(payload, tk)
    drift, drift_source = _resolve_drift_and_quality(tk)
    seed = _context_seed(payload)
    n_sims = 10_000

    table = lognormal_dist(spot, days=252, vol=vol, mu=drift, trading_days=252.0, n_points=50)
    mc = mc_probabilities(MCProbInputs(
        spot=spot, upper=spot * 1.5, lower=spot * 0.5, days=252,
        vol=vol, mu=drift, n_sims=n_sims, seed=seed,
    ))

    rng = np.random.default_rng(seed)
    T = 1.0
    terminal = spot * np.exp((drift - 0.5 * vol ** 2) * T + vol * np.sqrt(T) * rng.standard_normal(n_sims))

    return {
        "suite": "var", "status": "ok", "module": "price_dist_1yr",
        "ticker": tk, "spot": float(spot), "vol": float(vol), "expected_return": float(drift),
        "seed": seed, "horizon_days": 252, "n_sims": n_sims,
        "distribution_table": [
            {"price": e.price, "prob_at": e.prob_at, "prob_below": e.prob_below, "prob_above": e.prob_above}
            for e in table
        ],
        "terminal_price_histogram": _histogram_bins(terminal),
        "avg_end_price": mc.avg_end_price,
        "data_quality": {"vol_source": vol_source, "expected_return_source": drift_source},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
```

- [ ] **Step 4: Add `terminal_price_histogram` to the other three builders**

In `_build_mc_sim_from_context`, `_build_copula_from_context`, and
`_build_corr_sim_peer_from_context`, after each already computes `terminal = r.terminal_prices[:,
0]` (or the corr_sim_peer equivalent — read its body from Task 3's Step 6 changes to find the
matching local variable), add one line to their returned dict:

```python
        "terminal_price_histogram": _histogram_bins(terminal),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest VaR_Tools_Simulations/tests/test_context_builders.py -v`
Expected: PASS

- [ ] **Step 6: Write the failing test for the Tools wrapper**

```python
# Tools/tests/test_price_dist_tool.py
from Tools.tools import price_dist_tool


def test_run_delegates_to_build_price_dist(monkeypatch):
    var_main = price_dist_tool._import_var_main()
    monkeypatch.setattr(
        var_main, "_build_price_dist_from_context",
        lambda ctx, tk: {"status": "ok", "ticker": tk})
    result = price_dist_tool.run({"focus": {"ticker": "AAPL"}})
    assert result == {"status": "ok", "ticker": "AAPL"}
```

- [ ] **Step 7: Run test to verify it fails**

Run: `pytest Tools/tests/test_price_dist_tool.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 8: Create `Tools/tools/price_dist_tool.py`**

Copy `Tools/tools/hedge_optimizer_tool.py`'s structure exactly (same `_import_var_main` lazy
loader under the `var_tools_main` module-name guard), swapping the delegate call:

```python
"""price_dist_tool.py

Wraps VaR_Tools_Simulations/main.py's _build_price_dist_from_context as a
standalone Tool -- a 1-year-out price-distribution table plus MC terminal
histogram for a context's focus ticker.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VAR_SUITE_ROOT = _REPO_ROOT / "VaR_Tools_Simulations"

if str(_VAR_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAR_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def _import_var_main():
    module_name = "var_tools_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, str(_VAR_SUITE_ROOT / "main.py"))
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    var_main = _import_var_main()
    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    return var_main._build_price_dist_from_context(context, ticker)


TOOL_SPEC = ToolSpec(
    name="Price Distribution",
    slug="price-distribution",
    description=(
        "1-year-out lognormal price-distribution table and MC terminal-price "
        "histogram for a context's focus ticker."
    ),
    run=run,
)
```

- [ ] **Step 9: Register it in `Tools/registry.py`**

Same pattern as Task 7 Step 7: import + append `price_dist_tool.TOOL_SPEC`.

- [ ] **Step 10: Run test to verify it passes**

Run: `pytest Tools/tests/test_price_dist_tool.py Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 11: Run the full VaR_Tools_Simulations and Tools test suites**

Run: `pytest VaR_Tools_Simulations/tests/ Tools/tests/ -v`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add VaR_Tools_Simulations/main.py Tools/tools/price_dist_tool.py Tools/registry.py VaR_Tools_Simulations/tests/ Tools/tests/
git commit -m "feat: price-distribution context builder + terminal histogram on all sim builders + Tools entry"
```

---

### Task 9: Social Media Sentiment Scanner placeholder Tools entry

**Files:**
- Create: `Tools/tools/social_sentiment_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_social_sentiment_tool.py` (new)

**Interfaces:**
- Produces: `run(context: dict) -> dict` returning
  `{"status": "not_implemented", "ticker": str, "reddit": {"status": "not_implemented"},
  "youtube": {"status": "not_implemented"}, "stocktwits": {"status": "not_implemented"}}`.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_social_sentiment_tool.py
from Tools.tools import social_sentiment_tool


def test_run_returns_not_implemented_stub():
    result = social_sentiment_tool.run({"focus": {"ticker": "AAPL"}})
    assert result["status"] == "not_implemented"
    assert result["ticker"] == "AAPL"
    for source in ("reddit", "youtube", "stocktwits"):
        assert result[source]["status"] == "not_implemented"


def test_run_requires_ticker():
    result = social_sentiment_tool.run({"focus": {}})
    assert result["status"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest Tools/tests/test_social_sentiment_tool.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create `Tools/tools/social_sentiment_tool.py`**

```python
"""social_sentiment_tool.py

Placeholder Tools entry for a future Social Media Sentiment Scanner (Reddit /
YouTube / StockTwits). Deliberately NOT wired to the real sentiment-scanner
suite (sentiment-scanner/) -- that's a separate, already-functional project.
This is a UI stub so the tool appears in the dashboard's Tools listing ahead
of the real implementation.
"""
from __future__ import annotations

from typing import Any, Dict

from Tools.registry import ToolSpec


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    if not ticker:
        return {"status": "error", "error": "context.focus.ticker is required"}

    return {
        "status": "not_implemented",
        "ticker": ticker,
        "reddit": {"status": "not_implemented"},
        "youtube": {"status": "not_implemented"},
        "stocktwits": {"status": "not_implemented"},
    }


TOOL_SPEC = ToolSpec(
    name="Social Media Sentiment Scanner",
    slug="social-sentiment",
    description=(
        "Placeholder for a future Reddit/YouTube/StockTwits sentiment scan. "
        "Not yet implemented -- returns a status stub."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Same pattern as Task 7 Step 7.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest Tools/tests/test_social_sentiment_tool.py Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/social_sentiment_tool.py Tools/registry.py Tools/tests/test_social_sentiment_tool.py
git commit -m "feat: add Social Media Sentiment Scanner placeholder Tools entry"
```

---

### Task 10: Fix `_extract_sentiment` to understand the market-signals bundle shape

**Files:**
- Modify: `shared/summary.py:330-384` (`_extract_sentiment`)
- Test: `tests/test_report_generator.py` or a new `tests/test_summary_sentiment_extractor.py` —
  check whether `shared/summary.py` already has a dedicated test file (search `tests/` for
  `summary` before deciding); create one if not.

**Interfaces:**
- Produces: `_extract_sentiment(result: Any) -> Dict[str, Any]` — now branches on the presence of
  `result["scanners"]`/`result["simulations"]` (the market-signals bundle
  `orchestrator.py::run_market_signals_stage` actually writes) before falling back to the old
  `result["sentiment"]["ranked_tickers"]` shape (the real sentiment-scanner `--export-context`
  output), so whichever producer wrote `sentiment_result.json` is summarized correctly instead of
  the market-signals bundle always falling through to `degraded`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summary_sentiment_extractor.py
from shared.summary import _extract_sentiment


def _market_signals_bundle():
    return {
        "suite": "sentiment", "status": "ok", "ticker": "AAPL",
        "scanners": {
            "max_pain": {"pain_strike": 190.0, "spot": 188.5},
            "iv_rank": {"iv_rank": 62.0},
            "skew": {"error": "no data"},
            "unusual_oi": {"unusual_count": 3},
        },
        "simulations": {
            "mc_sim": {"terminal_price_mean": 210.0, "terminal_price_p5": 150.0, "terminal_price_p95": 280.0},
            "copula": {"terminal_price_mean": 209.0},
            "corr_sim": {"var_1yr": 0.0},
        },
        "direction": {"signal": "bullish"},
    }


def test_extract_sentiment_handles_market_signals_bundle():
    entry = _extract_sentiment(_market_signals_bundle())
    assert entry["status"] == "ok"
    assert entry["module"] == "sentiment"
    assert "max_pain_strike" in entry["metrics"]
    assert "mc_sim_terminal_price_mean" in entry["metrics"]
    assert any("skew" in w for w in entry["warnings"])


def test_extract_sentiment_still_handles_real_sentiment_export():
    old_shape = {"sentiment": {"ranked_tickers": ["AAPL", "MSFT"], "group_id": "g1"}}
    entry = _extract_sentiment(old_shape)
    assert entry["status"] == "ok"
    assert entry["metrics"]["ranked_ticker_count"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summary_sentiment_extractor.py -v`
Expected: FAIL — market-signals bundle shape currently falls to `degraded`
("missing 'sentiment' block").

- [ ] **Step 3: Rewrite `_extract_sentiment` to branch on shape**

Replace `shared/summary.py:330-384` with:

```python
def _extract_sentiment(result: Any) -> Dict[str, Any]:
    """`sentiment_result.json` -> a `modules[]` entry.

    Two different producers write this filename: `orchestrator.py`'s
    `run_market_signals_stage` (scanners/simulations/direction bundle, what
    every unified run actually writes today) and the real sentiment-scanner
    suite's `--export-context` (the old `sentiment.ranked_tickers` shape).
    Branch on which one is present rather than assuming the old shape --
    the market-signals bundle otherwise always falls through to `degraded`.
    """
    try:
        if not isinstance(result, dict):
            raise TypeError(
                f"sentiment result must be an object, got {type(result).__name__}"
            )
        if "scanners" in result or "simulations" in result:
            return _extract_market_signals_bundle(result)
        return _extract_legacy_sentiment_export(result)
    except Exception as exc:  # noqa: BLE001
        return _degraded(
            "sentiment",
            f"could not extract sentiment metrics: {type(exc).__name__}: {exc}",
        )


def _extract_market_signals_bundle(result: Dict[str, Any]) -> Dict[str, Any]:
    scanners = result.get("scanners") or {}
    simulations = result.get("simulations") or {}
    direction = result.get("direction")

    metrics: Dict[str, Any] = {}
    warnings: List[str] = []

    for scan_key, scan_val in scanners.items():
        if not isinstance(scan_val, dict):
            continue
        if "error" in scan_val:
            warnings.append(f"{scan_key} scanner failed: {scan_val['error']}")
            continue
        for k, v in scan_val.items():
            if isinstance(v, (int, float, str)):
                metrics[f"{scan_key}_{k}"] = v

    for sim_key, sim_val in simulations.items():
        if not isinstance(sim_val, dict):
            continue
        if "error" in sim_val:
            warnings.append(f"{sim_key} sim failed: {sim_val['error']}")
            continue
        for k, v in sim_val.items():
            if isinstance(v, (int, float, str)):
                metrics[f"{sim_key}_{k}"] = v

    if isinstance(direction, dict) and "signal" in direction:
        metrics["direction_signal"] = direction["signal"]

    ok_scanners = sum(1 for v in scanners.values() if isinstance(v, dict) and "error" not in v)
    ok_sims = sum(1 for v in simulations.values() if isinstance(v, dict) and "error" not in v)
    headline = (
        f"Market signals: {ok_scanners}/{len(scanners)} scanners, "
        f"{ok_sims}/{len(simulations)} 1yr sims ok"
    )

    return {
        "module": "sentiment",
        "status": result.get("status", "ok"),
        "headline": headline,
        "metrics": metrics,
        "warnings": warnings,
        "source_result": "",
    }


def _extract_legacy_sentiment_export(result: Dict[str, Any]) -> Dict[str, Any]:
    block = result.get("sentiment")
    if not isinstance(block, dict):
        raise ValueError("missing 'sentiment' block")

    ranked = block.get("ranked_tickers")
    if not isinstance(ranked, list):
        raise ValueError("sentiment.ranked_tickers missing or not a list")

    group_id = block.get("group_id")
    pack_json_path = block.get("pack_json_path")

    metrics: Dict[str, Any] = {"ranked_ticker_count": len(ranked)}
    if group_id:
        metrics["group_id"] = group_id

    top_names = ", ".join(str(t) for t in ranked[:5])
    headline = (
        f"Sentiment scan ranked {len(ranked)} tickers; top: {top_names}"
        if top_names else "Sentiment scan produced no ranked tickers"
    )

    warnings: List[str] = []
    if not pack_json_path:
        warnings.append("no pack_json_path recorded (sentiment pack not exported)")

    return {
        "module": "sentiment",
        "status": "ok",
        "headline": headline,
        "metrics": metrics,
        "warnings": warnings,
        "source_result": "",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_summary_sentiment_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Run the full root test suite**

Run: `pytest tests/ -v`
Expected: PASS (in particular `tests/test_schemas.py`,
`tests/test_integration_quant_orchestrator.py`, `tests/test_quant_synthesis.py`, which likely
exercise `shared/summary.py` indirectly)

- [ ] **Step 6: Commit**

```bash
git add shared/summary.py tests/test_summary_sentiment_extractor.py
git commit -m "fix(dashboard): market-signals bundle summarizes correctly on the unified quant tab"
```

---

### Task 11: Histogram + percentile-table renderer for MC/copula/corr_sim/price_dist

**Files:**
- Modify: `shared/schemas.py` (quant_summary module-entry validator — add an optional
  `distributions` key)
- Modify: `shared/summary.py::_extract_market_signals_bundle` (Task 10) to populate
  `distributions` from each simulation's `terminal_price_histogram` (Task 8)
- Modify: `dashboard/templates/quant.html` (`renderModuleEntry`)
- Test: `tests/test_summary_sentiment_extractor.py` (append)

**Interfaces:**
- Produces: module entry gains an optional `"distributions"` key:
  `[{"label": str, "bins": [{"low": float, "high": float, "count": int}], "percentiles":
  {"p5": float, "p50": float, "p95": float}}]`.
- Produces: `quant.html::renderModuleEntry` draws one inline SVG bar chart + a small percentile
  table per entry in `distributions`, after the existing metric-grid.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_summary_sentiment_extractor.py (append)
def test_extract_market_signals_bundle_includes_distributions():
    bundle = _market_signals_bundle()
    bundle["simulations"]["mc_sim"]["terminal_price_histogram"] = [
        {"low": 150.0, "high": 160.0, "count": 20},
        {"low": 160.0, "high": 170.0, "count": 80},
    ]
    entry = _extract_sentiment(bundle)
    assert "distributions" in entry
    dist = next(d for d in entry["distributions"] if d["label"] == "mc_sim")
    assert dist["bins"][1]["count"] == 80
    assert dist["percentiles"]["p50"] == pytest.approx(210.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summary_sentiment_extractor.py -k distributions -v`
Expected: FAIL — `distributions` key doesn't exist yet.

- [ ] **Step 3: Populate `distributions` in `_extract_market_signals_bundle`**

In the `for sim_key, sim_val in simulations.items():` loop (Task 10's code), collect a
distributions list alongside the flat metrics:

```python
    distributions: List[Dict[str, Any]] = []
    for sim_key, sim_val in simulations.items():
        if not isinstance(sim_val, dict):
            continue
        if "error" in sim_val:
            warnings.append(f"{sim_key} sim failed: {sim_val['error']}")
            continue
        for k, v in sim_val.items():
            if isinstance(v, (int, float, str)):
                metrics[f"{sim_key}_{k}"] = v
        hist = sim_val.get("terminal_price_histogram")
        if isinstance(hist, list) and hist:
            distributions.append({
                "label": sim_key,
                "bins": hist,
                "percentiles": {
                    "p5": sim_val.get("terminal_price_p5"),
                    "p50": sim_val.get("terminal_price_median") or sim_val.get("terminal_price_mean"),
                    "p95": sim_val.get("terminal_price_p95"),
                },
            })
```

and add `"distributions": distributions,` to the returned dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_summary_sentiment_extractor.py -v`
Expected: PASS

- [ ] **Step 5: Allow the new key in `shared/schemas.py`'s quant_summary module-entry validator**

Find the function validating each `modules[]` entry (search for `"headline"` and `"warnings"` in
`shared/schemas.py` to locate it) and add, alongside the existing optional-key checks:

```python
    if "distributions" in entry:
        _require(isinstance(entry["distributions"], list),
                 "modules[].distributions must be a list")
        for d in entry["distributions"]:
            _require(isinstance(d, dict) and "label" in d and "bins" in d,
                     "each distributions[] entry needs 'label' and 'bins'")
```

(Match the file's existing `_require` helper name.)

- [ ] **Step 6: Run the schema test suite**

Run: `pytest tests/test_schemas.py -v`
Expected: PASS

- [ ] **Step 7: Add the SVG histogram + percentile table renderer to `quant.html`**

In `dashboard/templates/quant.html`, after the existing warnings-list block inside
`renderModuleEntry` (around line 259-268), add:

```javascript
  var distributions = entry.distributions || [];
  distributions.forEach(function (dist) {
    var wrap = document.createElement('div');
    wrap.className = 'dist-chart';

    var label = document.createElement('div');
    label.className = 'small';
    label.textContent = dist.label + ' — terminal price distribution';
    wrap.appendChild(label);

    var bins = dist.bins || [];
    var maxCount = bins.reduce(function (m, b) { return Math.max(m, b.count); }, 1);
    var svgNS = 'http://www.w3.org/2000/svg';
    var W = 320, H = 80, barW = bins.length ? W / bins.length : W;
    var svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', H);
    bins.forEach(function (b, i) {
      var barH = (b.count / maxCount) * (H - 4);
      var rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('x', i * barW);
      rect.setAttribute('y', H - barH);
      rect.setAttribute('width', Math.max(barW - 1, 1));
      rect.setAttribute('height', barH);
      rect.setAttribute('class', 'dist-bar');
      var title = document.createElementNS(svgNS, 'title');
      title.textContent = fmtMetricValue(b.low) + '–' + fmtMetricValue(b.high) + ': ' + b.count;
      rect.appendChild(title);
      svg.appendChild(rect);
    });
    wrap.appendChild(svg);

    var pct = dist.percentiles || {};
    var table = document.createElement('div');
    table.className = 'small dist-percentiles';
    table.textContent = 'p5: ' + fmtMetricValue(pct.p5) +
      '  p50: ' + fmtMetricValue(pct.p50) +
      '  p95: ' + fmtMetricValue(pct.p95);
    wrap.appendChild(table);

    container.appendChild(wrap);
  });
```

Add matching CSS near the existing `.metric-grid`/`.metric-card` rules (search `quant.html`'s
`<style>` block for `.metric-card` to find the right spot):

```css
.dist-chart { margin-top: 8px; }
.dist-bar { fill: var(--accent, #4a9eff); }
.dist-percentiles { margin-top: 2px; opacity: 0.8; }
```

(Use whatever CSS custom property this file's existing accent color already uses instead of
`--accent` if it's named differently — check the `<style>` block's existing color tokens first.)

- [ ] **Step 8: Manual dashboard verification**

Run: launch the dashboard (`dashboard.bat`) per the `launching-dashboard` skill, trigger a
unified run for a liquid ticker (e.g. `SPY`), open the `/quant` tab once the run completes, and
confirm: the Market Signals card shows a real headline (not "degraded"), a populated metric grid
including `max_pain_*`, `garch_conditional_vol` (via Task 1/3), `mc_sim_*`/`copula_*`/`corr_sim_*`
keys, and a bar-chart + percentile line under each simulation.

- [ ] **Step 9: Commit**

```bash
git add shared/schemas.py shared/summary.py dashboard/templates/quant.html tests/test_summary_sentiment_extractor.py
git commit -m "feat(dashboard): render sim terminal-price distributions as histogram + percentile table"
```

---

## Post-plan note on scope

The user's ask included "a graph illustrating their paths." `mc_sim.py`/`copulas.py`/
`corr_sim.py` are all single-step-to-horizon simulators (`S_sim = spot * exp(single log-return
draw)`) — none of them retain per-step path data, only the terminal distribution across
`n_sims` draws. Task 11 delivers a terminal-distribution histogram + percentile table (the
honest visualization of the data that actually exists) rather than a fabricated path chart.
Restructuring the three simulators into true multi-step path generators (to support an actual
fan chart of sample paths over time) would be a materially larger, separate change — flag to the
user as a possible follow-up plan if they want it after seeing the histogram result.
