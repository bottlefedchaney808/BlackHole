# Tools & Dashboard Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken/launchless Tools pages, add a multi-mode "Simulations" tool with user-revealed inputs, source backtest strategies from `chain_strategies.json`, and rename Direction Signal → Directional Engine with per-module + unified runs.

**Architecture:** Rework the `Tools/` registry + `dashboard/app.py` tool routes so every tool launches from a working page. `price_dist_tool` becomes a multi-mode `simulations` tool wrapping three VaR context builders (price_dist / mc_sim / corr_sim) that accept override kwargs. The backtest `strategy_pnl` mode and the dashboard strategy picker read strategies from `chain_strategies.json` (where the chain scanner actually writes them). `direction_signal_tool` becomes `directional-engine`, a multi-mode wrapper around the five Direction sub-modules plus a unified run.

**Tech Stack:** Python 3.12 (root `.venv`), FastAPI dashboard (`dashboard/app.py`), Jinja2 templates, `Tools.registry.ToolSpec`, `VaR_Tools_Simulations/main.py` context builders, `Direction/` package, pytest.

## Global Constraints

- Run project python as `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe` (Hermes venv leak breaks pydantic_core).
- `dashboard/tests` is NOT in pyproject testpaths — run dashboard tests by path (`pytest dashboard/tests/...`). Tools tests are under `Tools/tests/`.
- Every tool stays a `ToolSpec(slug, name, description, run)` registered in `Tools/registry.py::_load_tools`.
- Slugs are lowercase URL-safe; display names are human-facing.
- Do not touch Vol_Suite/scanner or VaR engine internals beyond the context-builder override parameters listed in Task 3.
- No auth changes, no `.env` changes, localhost-only.

## File Structure

- Modify: `Tools/tools/price_dist_tool.py` → re-skinned as `simulations` (multi-mode), keep filename.
- Modify: `Tools/tools/direction_signal_tool.py` → `directional-engine` (multi-mode).
- Modify: `Tools/tools/backtesting_tool.py` (strategies from `chain_strategies.json`).
- Modify: `Tools/registry.py` (drop `social_sentiment_tool`, register new names).
- Delete: `Tools/tools/social_sentiment_tool.py`, `Tools/tests/test_social_sentiment_tool.py`.
- Modify: `VaR_Tools_Simulations/main.py` (builder override kwargs).
- Modify: `dashboard/app.py` (`GENERIC_TOOL_SLUGS`, `_strategy_map`, new routes).
- Create: `dashboard/templates/tools_simulations.html`, `dashboard/templates/tools_directional.html`.
- Modify: `dashboard/templates/tools_generic.html`, `dashboard/templates/tools_backtest.html`, `dashboard/templates/tools_index.html`.
- Test: `Tools/tests/test_price_dist_tool.py`, `Tools/tests/test_direction_signal_tool.py`, `Tools/tests/test_backtesting_tool.py`, `dashboard/tests/test_tools_routes.py`, `VaR_Tools_Simulations/tests/test_context_builders.py`.

---

### Task 1: Remove the sentiment tool from the registry

**Files:**
- Delete: `Tools/tools/social_sentiment_tool.py`
- Delete: `Tools/tests/test_social_sentiment_tool.py`
- Modify: `Tools/registry.py:61,72,86` (import + TOOL_SPEC line)

**Interfaces:**
- Consumes: none.
- Produces: `Tools.registry.TOOLS` no longer contains slug `social-sentiment`; `get_tool('social-sentiment')` raises `KeyError`.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py (append)
def test_social_sentiment_removed():
    from Tools.registry import TOOLS
    assert all(t.slug != 'social-sentiment' for t in TOOLS)
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_registry.py::test_social_sentiment_removed -q`
Expected: FAIL (slug still present).

- [ ] **Step 3: Delete files and edit registry**

```bash
git rm Tools/tools/social_sentiment_tool.py Tools/tests/test_social_sentiment_tool.py
```

In `Tools/registry.py`, remove the line `from Tools.tools import social_sentiment_tool` and the line `social_sentiment_tool.TOOL_SPEC,`.

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_registry.py::test_social_sentiment_removed -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A Tools/
git commit -m "refactor(tools): remove social-sentiment tool from registry"
```

---

### Task 2: Make VRP Term Structure and Price Distribution launchable

**Files:**
- Modify: `dashboard/app.py:1955-1958` (`GENERIC_TOOL_SLUGS`)
- Modify: `dashboard/templates/tools_index.html` (no code change needed — it already links `/tools/<slug>`)

**Interfaces:**
- Consumes: `Tools.registry.TOOLS` slugs `vrp-term-structure` and `price-distribution`.
- Produces: GET `/tools/vrp-term-structure` and `/tools/price-distribution` return `tools_generic.html` (HTTP 200) instead of 404.

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_tools_routes.py (new)
from fastapi.testclient import TestClient
from dashboard.app import app

client = TestClient(app)

def test_vrp_term_structure_page_launches():
    r = client.get('/tools/vrp-term-structure')
    assert r.status_code == 200

def test_price_distribution_page_launches():
    r = client.get('/tools/price-distribution')
    assert r.status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest dashboard/tests/test_tools_routes.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Add slugs to GENERIC_TOOL_SLUGS**

In `dashboard/app.py`:
```python
GENERIC_TOOL_SLUGS = {
    'whale-flow', 'elliott-wave', 'bollinger', 'trend-engine',
    'liquidity-map', 'direction-signal', 'hedge-optimizer',
    'vrp-term-structure', 'price-distribution',
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest dashboard/tests/test_tools_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/tests/test_tools_routes.py
git commit -m "fix(tools): make vrp-term-structure and price-distribution launchable"
```

---

### Task 3: VaR context builders accept horizon/n_sims/confidence/seed overrides

**Files:**
- Modify: `VaR_Tools_Simulations/main.py:357-547` (`_resolve_vol_and_quality`, `_resolve_drift_and_quality`, `_build_mc_sim_from_context`, `_build_price_dist_from_context`, `_build_copula_from_context`)
- Test: `VaR_Tools_Simulations/tests/test_context_builders.py` (append)

**Interfaces:**
- Consumes: `data_loader.fetch_spot`, `_context_seed`, `_resolve_vol_and_quality`, `_resolve_drift_and_quality`.
- Produces (later tasks use these exact signatures):
  - `_build_price_dist_from_context(payload, ticker=None, *, horizon_days=None, n_sims=None, seed=None) -> dict`
  - `_build_mc_sim_from_context(payload, ticker=None, *, horizon_days=None, n_sims=None, seed=None, confidence=None) -> dict`
  - `_build_copula_from_context(payload, ticker=None, *, horizon_days=None, n_sims=None, seed=None, confidence=None) -> dict`
  - `_build_corr_sim_peer_from_context(payload, ticker=None, *, max_peers=2, horizon_days=None, n_sims=None, seed=None, confidence=None) -> dict`
  All return the same dict shape as today, but `horizon_days`/`n_sims`/`confidence` reflect the override (defaults preserved: 252 / 10_000 / 0.99).

- [ ] **Step 1: Write the failing test**

```python
# VaR_Tools_Simulations/tests/test_context_builders.py (append)
def test_price_dist_builder_accepts_horizon_override(monkeypatch):
    from var_engine import data_loader
    monkeypatch.setattr(data_loader, 'fetch_spot', lambda tk: 100.0)
    import main as var_main
    payload = {'focus': {'ticker': 'SPY'}, 'var': {'seed': 7}}
    out = var_main._build_price_dist_from_context(payload, horizon_days=126)
    assert out['horizon_days'] == 126
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest VaR_Tools_Simulations/tests/test_context_builders.py::test_price_dist_builder_accepts_horizon_override -q`
Expected: FAIL (`horizon_days` still 252).

- [ ] **Step 3: Add a small context-int helper and thread overrides through each builder**

```python
def _ctx_int(payload: dict, key: str, default: int) -> int:
    var_cfg = payload.get('var') if isinstance(payload.get('var'), dict) else {}
    raw = var_cfg.get(key)
    if raw is None:
        raw = payload.get(key)
    if raw is None:
        return default
    return int(raw)
```

In `_build_price_dist_from_context(payload, ticker=None, *, horizon_days=None, n_sims=None, seed=None)`, replace the hardcoded block:
```python
    seed = _context_seed(payload)
    n_sims = 10_000
    days = 252
```
with:
```python
    seed = int(seed if seed is not None else _context_seed(payload))
    n_sims = int(n_sims if n_sims is not None else _ctx_int(payload, 'n_sims', 10_000))
    days = int(horizon_days if horizon_days is not None else _ctx_int(payload, 'horizon_days', 252))
```

Apply the same pattern to `_build_mc_sim_from_context` (add `confidence`, default 0.99, used in the `run(MCSimInputs(... confidence=...))` call and `n_sims=10_000` → override), `_build_copula_from_context` (n_sims default 50_000), and `_build_corr_sim_peer_from_context` (add `horizon_days`/`n_sims`/`seed`/`confidence`; read existing `var.var_days`/`n_sims`/`confidence` from the corr-sim run inputs — see the peer builder body and mirror `_build_corr_sim_from_context`'s horizon resolution).

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest VaR_Tools_Simulations/tests/test_context_builders.py -q`
Expected: PASS (plus existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add VaR_Tools_Simulations/main.py VaR_Tools_Simulations/tests/test_context_builders.py
git commit -m "feat(var): expose horizon/n_sims/confidence/seed overrides on context sim builders"
```

---

### Task 4: Turn Price Distribution into the multi-mode "Simulations" tool

**Files:**
- Modify: `Tools/tools/price_dist_tool.py` (full rewrite)
- Modify: `Tools/registry.py` (name/slug/description for the price-distribution entry)
- Test: `Tools/tests/test_price_dist_tool.py`

**Interfaces:**
- Consumes: `_import_var_main()` (Task 3 builders), `context['focus']['ticker']`, `context['_output_dir_override']`.
- Produces: `ToolSpec(name="Simulations", slug="simulations", run=run)` where `run(context)` reads `context['mode']` ∈ `{'price_dist','mc_sim','corr_sim'}` (default `price_dist`) and passes through override keys `horizon_days`, `n_sims`, `seed`, `confidence` present in context. Returns the VaR builder dict verbatim.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_price_dist_tool.py (append)
def test_simulations_mode_mc_sim_dispatch(monkeypatch):
    from Tools.tools import price_dist_tool
    calls = {}
    def fake_builder(payload, ticker=None, **kw):
        calls['kw'] = kw
        return {'module': 'mc_sim_1yr', 'ticker': ticker}
    monkeypatch.setattr(price_dist_tool, '_import_var_main', lambda: type('M', (), {
        '_build_mc_sim_from_context': fake_builder})())
    ctx = {'focus': {'ticker': 'SPY'}, 'mode': 'mc_sim', 'horizon_days': 63}
    out = price_dist_tool.run(ctx)
    assert out['module'] == 'mc_sim_1yr'
    assert calls['kw']['horizon_days'] == 63
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_price_dist_tool.py -q`
Expected: FAIL (no `run` with mode dispatch yet / slug mismatch).

- [ ] **Step 3: Rewrite the tool**

```python
"""price_dist_tool.py -> the "Simulations" tool.

Multi-mode wrapper around three VaR context builders:
  mode='price_dist' -> _build_price_dist_from_context
  mode='mc_sim'     -> _build_mc_sim_from_context
  mode='corr_sim'   -> _build_corr_sim_peer_from_context
Ticker comes from context.focus.ticker; horizon_days/n_sims/seed/confidence
are read off context when present and passed through as override kwargs.
"""
from __future__ import annotations
import importlib.util, sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VAR_SUITE_ROOT = _REPO_ROOT / "VaR_Tools_Simulations"
if str(_VAR_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAR_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402

_MODES = {
    'price_dist': '_build_price_dist_from_context',
    'mc_sim': '_build_mc_sim_from_context',
    'corr_sim': '_build_corr_sim_peer_from_context',
}
_OVERRIDES = ('horizon_days', 'n_sims', 'seed', 'confidence')

def _import_var_main():
    module_name = 'var_tools_main'
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, str(_VAR_SUITE_ROOT / 'main.py'))
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main

def run(context: Dict[str, Any]) -> Dict[str, Any]:
    focus = context.get('focus') or {}
    ticker = context.get('ticker') or focus.get('ticker')
    if not ticker:
        raise ValueError('Simulations tool requires a ticker '
                         '(context.ticker or context.focus.ticker)')
    mode = str(context.get('mode') or 'price_dist').strip().lower()
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {sorted(_MODES)}; got {mode!r}")
    var_main = _import_var_main()
    builder = getattr(var_main, _MODES[mode])
    kwargs = {k: context[k] for k in _OVERRIDES if k in context}
    return builder(context, ticker, **kwargs)

TOOL_SPEC = ToolSpec(
    name="Simulations",
    slug="simulations",
    description=("Three 1-year-out Monte Carlo simulations for a context's "
                 "focus ticker -- price-distribution table, MC terminal "
                 "price, or correlation sim vs basket peers -- seeded from "
                 "live spot + GARCH vol + drift. Select via mode."),
    run=run,
)
```

- [ ] **Step 4: Register as `simulations`**

In `Tools/registry.py` set the entry to `price_dist_tool.TOOL_SPEC` (now slug `simulations`). Update `dashboard/app.py:1958` so `GENERIC_TOOL_SLUGS` contains `'simulations'` instead of `'price-distribution'`.

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_price_dist_tool.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/price_dist_tool.py Tools/registry.py dashboard/app.py Tools/tests/test_price_dist_tool.py
git commit -m "feat(tools): rename Price Distribution to Simulations (multi-mode: price_dist/mc_sim/corr_sim)"
```

---

### Task 5: Simulations dashboard form with mode selector and revealed inputs

**Files:**
- Create: `dashboard/templates/tools_simulations.html`
- Modify: `dashboard/app.py` (GET/POST `/tools/simulations`)

**Interfaces:**
- Consumes: `get_tool('simulations')`, `_tools_contexts()`, `_load_selected_context()`.
- Produces: GET `/tools/simulations` renders a form; POST runs the selected mode. Inputs `horizon_days`, `n_sims`, `seed`, `confidence` are always shown as numeric fields (revealed for user input) and merged into `context` before dispatch. A context picker populates `ticker`/`vol`/`drift` from the chosen context.

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_tools_routes.py (append)
def test_simulations_page_renders_with_inputs():
    r = client.get('/tools/simulations')
    assert r.status_code == 200
    assert 'horizon_days' in r.text and 'n_sims' in r.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest dashboard/tests/test_tools_routes.py::test_simulations_page_renders_with_inputs -q`
Expected: FAIL (404).

- [ ] **Step 3: Add the template**

```html
{% extends "base.html" %}
{% block title %}Simulations -- FinancialDevelopment Dashboard{% endblock %}
{% block content %}
<h1>Simulations</h1>
<p class="sub">{{ tool.description }} <a href="/tools">&larr; back to Tools</a></p>
{% if error %}<div class="errbox">{{ error }}</div>{% endif %}
<div class="panel">
  <header><h2>Run</h2></header>
  <div class="body">
    <form class="runform" method="post" action="/tools/simulations">
      <div class="field" style="min-width:340px;">
        <label for="context_path">Context (populates ticker/vol/drift)</label>
        <select id="context_path" name="context_path">
          <option value="" {{ 'selected' if not selected_path else '' }}>-- choose a context --</option>
          {% for c in contexts if c.valid %}
          <option value="{{ c.path }}" {{ 'selected' if c.path == selected_path else '' }}>
            {{ c.ticker or '?' }} &middot; {{ c.suite }} &middot; {{ c.run_id }}
          </option>
          {% endfor %}
        </select>
      </div>
      <div class="field">
        <label for="mode">Mode</label>
        <select id="mode" name="mode">
          {% for m in ['price_dist', 'mc_sim', 'corr_sim'] %}
          <option value="{{ m }}" {{ 'selected' if selected_mode == m else '' }}>{{ m }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="field"><label for="horizon_days">Horizon days</label>
        <input id="horizon_days" name="horizon_days" value="{{ horizon_days }}" size="8"></div>
      <div class="field"><label for="n_sims">Iterations (n_sims)</label>
        <input id="n_sims" name="n_sims" value="{{ n_sims }}" size="10"></div>
      <div class="field"><label for="confidence">Confidence (0-1)</label>
        <input id="confidence" name="confidence" value="{{ confidence }}" size="6"></div>
      <div class="field"><label for="seed">Seed</label>
        <input id="seed" name="seed" value="{{ seed }}" size="6"></div>
      <button type="submit">Run simulation</button>
    </form>
  </div>
</div>
{% if result %}
<div class="panel"><header><h2>Result</h2></header>
  <div class="body"><pre class="json">{{ result_json }}</pre></div>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Add the routes in app.py**

```python
@app.get('/tools/simulations', response_class=HTMLResponse)
def tools_simulations_form(request: Request):
    contexts, contexts_error = _tools_contexts()
    return TEMPLATES.TemplateResponse(request, 'tools_simulations.html', {
        'active': 'tools', 'tool': get_tool('simulations'),
        'contexts': contexts, 'contexts_error': contexts_error,
        'selected_path': '', 'selected_mode': 'price_dist',
        'horizon_days': '', 'n_sims': '', 'confidence': '', 'seed': '',
        'result': None, 'result_json': None, 'error': None,
    })

@app.post('/tools/simulations', response_class=HTMLResponse)
async def tools_simulations_run(request: Request):
    body = await _parse_body(request)
    contexts, contexts_error = _tools_contexts()
    context_path = str(body.get('context_path') or '').strip()
    mode = str(body.get('mode') or 'price_dist').strip().lower()
    context, error = _load_selected_context(context_path)
    result = None
    if context is not None:
        context['mode'] = mode
        for key, cast in (('horizon_days', int), ('n_sims', int),
                          ('confidence', float), ('seed', int)):
            raw = str(body.get(key) or '').strip()
            if raw:
                try:
                    context[key] = cast(raw)
                except ValueError:
                    error = f'{key} must be numeric, got {raw!r}'
        if error is None:
            result, run_error = _run_tool_safe('simulations', context)
            if run_error:
                error = run_error
    result_json = json.dumps(result, indent=2, default=str) if result is not None else None
    return TEMPLATES.TemplateResponse(request, 'tools_simulations.html', {
        'active': 'tools', 'tool': get_tool('simulations'),
        'contexts': contexts, 'contexts_error': contexts_error,
        'selected_path': context_path, 'selected_mode': mode,
        'horizon_days': str(body.get('horizon_days') or ''),
        'n_sims': str(body.get('n_sims') or ''),
        'confidence': str(body.get('confidence') or ''),
        'seed': str(body.get('seed') or ''),
        'result': result, 'result_json': result_json, 'error': error,
    })
```

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest dashboard/tests/test_tools_routes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/templates/tools_simulations.html dashboard/tests/test_tools_routes.py
git commit -m "feat(dashboard): add Simulations form with mode selector and revealed horizon/n_sims/confidence/seed inputs"
```

---

### Task 6: Backtest strategy_pnl sources strategies from chain_strategies.json

**Files:**
- Modify: `Tools/tools/backtesting_tool.py` (`run_strategy_pnl`)
- Modify: `dashboard/app.py` (`_strategy_map`)
- Modify: `dashboard/templates/tools_backtest.html` (add expiry field; already reads `strategy_index`)
- Test: `Tools/tests/test_backtesting_tool.py`, `dashboard/tests/test_tools_routes.py`

**Interfaces:**
- Consumes: `context['strategies']` (fallback), `context['output_dir']`/`_output_dir_override`, `chain_strategies.json` artifact (`strategies` list, same leg shape as a context strategy).
- Produces: `run_strategy_pnl(context)` resolves strategies from `<output_dir>/chain_strategies.json['strategies']` when `context['strategies']` is empty. `_strategy_map` returns the per-context strategy picker from the same artifact.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_backtesting_tool.py (append)
import json

def test_strategy_pnl_reads_chain_strategies(tmp_path, monkeypatch):
    from Tools.tools import backtesting_tool
    artifact = tmp_path / 'chain_strategies.json'
    artifact.write_text(json.dumps({'strategies': [
        {'strategy_type': 'call_spread', 'legs': []} ]}))
    ctx = {'focus': {'ticker': 'SPY', 'expiration_date': '2026-10-16'},
           '_output_dir_override': str(tmp_path),
           'entry_date': '20260816'}
    captured = {}
    def fake_run(strategy, ticker, expiry, entry_date, **kw):
        captured['strategy'] = strategy
        return type('R', (), {})()  # StrategyBacktestResult
    monkeypatch.setattr('sys.modules', sys.modules)  # no-op guard
    # stub the module's bs3 symbol
    import types
    fake_bs3 = types.ModuleType('backtest_stage3')
    fake_bs3.run_strategy_backtest = fake_run
    fake_bs3.format_strategy_backtest_report = lambda r: 'report'
    monkeypatch.setitem(sys.modules, 'backtest_stage3', fake_bs3)
    import importlib
    importlib.reload(backtesting_tool)
    backtesting_tool.run_strategy_pnl(ctx)
    assert captured['strategy']['strategy_type'] == 'call_spread'
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_backtesting_tool.py::test_strategy_pnl_reads_chain_strategies -q`
Expected: FAIL (raises "requires a 'strategy' dict, or a non-empty context['strategies'] list").

- [ ] **Step 3: Add a chain-strategies resolver in backtesting_tool.py**

```python
import json as _json

_CHAIN_STRATEGIES_FILENAME = 'chain_strategies.json'

def _resolve_strategies(context):
    strategies = context.get('strategies') or []
    if strategies:
        return strategies
    out_dir = context.get('_output_dir_override') or context.get('output_dir')
    if out_dir:
        artifact = Path(out_dir) / _CHAIN_STRATEGIES_FILENAME
        if artifact.is_file():
            data = _json.loads(artifact.read_text(encoding='utf-8'))
            return data.get('strategies') or []
    return []
```

In `run_strategy_pnl`, replace the block that reads `strategies = context.get('strategies') or []` with `strategies = _resolve_strategies(context)` (keeping the same `strategy_index` selection and the `ValueError` when still empty).

- [ ] **Step 4: Update `_strategy_map` in app.py to read the artifact**

Change `strategies = full.get('strategies') or []` to load from `Path(full.get('_output_dir_override') or full.get('output_dir')) / 'chain_strategies.json'` when `full['strategies']` is empty (reuse the same leg extraction).

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_backtesting_tool.py dashboard/tests/test_tools_routes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/backtesting_tool.py dashboard/app.py Tools/tests/test_backtesting_tool.py
git commit -m "feat(tools): backtest strategy_pnl reads strategies from chain_strategies.json"
```

---

### Task 7: Rename Direction Signal → Directional Engine (per-module + unified)

**Files:**
- Modify: `Tools/tools/direction_signal_tool.py` (rewrite)
- Modify: `Tools/registry.py` (name `Directional Engine`, slug `directional-engine`)
- Create: `dashboard/templates/tools_directional.html`
- Modify: `dashboard/app.py` (route + GENERIC_TOOL_SLUGS swap `direction-signal`→`directional-engine`)
- Modify: `dashboard/templates/tools_index.html` (special-case link removed — generic link covers it)
- Test: `Tools/tests/test_direction_signal_tool.py`, `dashboard/tests/test_tools_routes.py`

**Interfaces:**
- Consumes: `Direction.signal_generator.generate`, and the five module entry points: `Direction.whale_scanner.scan`, `Direction.elliott_wave.analyze`, `Direction.bollinger_analyzer.analyze`, `Direction.trend_engine.analyze_trend`, `Direction.liquidity_map.get_liquidity`.
- Produces: `ToolSpec(name="Directional Engine", slug="directional-engine", run=run)` where `run(context)` reads `context['mode']`:
  - `'unified'` (default) → `signal_generator.generate(ticker)` verbatim (already includes all five incl. trend).
  - `'whale' | 'elliott' | 'bollinger' | 'trend' | 'liquidity'` → that single module's output dict.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_direction_signal_tool.py (append)
def test_directional_engine_unified_and_trend_modes(monkeypatch):
    from Tools.tools import direction_signal_tool
    class FakeModules:
        def generate(self, tk): return {'conviction': 'HIGH', 'ticker': tk}
    def fake_analyze_trend(tk): return {'signal': True, 'adx_ok': True}
    monkeypatch.setattr(direction_signal_tool, '_signal_generator', FakeModules())
    monkeypatch.setattr(direction_signal_tool, '_trend_engine', type('T', (), {'analyze_trend': staticmethod(fake_analyze_trend)})())
    ctx = {'focus': {'ticker': 'SPY'}, 'mode': 'unified'}
    assert direction_signal_tool.run(ctx)['conviction'] == 'HIGH'
    ctx2 = {'focus': {'ticker': 'SPY'}, 'mode': 'trend'}
    assert direction_signal_tool.run(ctx2)['signal'] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_direction_signal_tool.py -q`
Expected: FAIL (no mode dispatch / slug still `direction-signal`).

- [ ] **Step 3: Rewrite the tool**

```python
"""direction_signal_tool.py -> Directional Engine.

Container for the five Direction sub-signals plus a unified run.
mode='unified' (default): signal_generator.generate(ticker) -- runs all five
  (whale, elliott, bollinger, trend, liquidity) and returns one conviction.
mode in {'whale','elliott','bollinger','trend','liquidity'}: the single
  module's output dict for that sub-signal.
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402

_MODULES = {
    'whale': ('whale_scanner', 'scan'),
    'elliott': ('elliott_wave', 'analyze'),
    'bollinger': ('bollinger_analyzer', 'analyze'),
    'trend': ('trend_engine', 'analyze_trend'),
    'liquidity': ('liquidity_map', 'get_liquidity'),
}

def _signal_generator():
    from Direction import signal_generator
    return signal_generator

def _trend_engine():
    from Direction import trend_engine
    return trend_engine

def run(context: Dict[str, Any]) -> Dict[str, Any]:
    focus = context.get('focus') or {}
    ticker = context.get('ticker') or focus.get('ticker')
    if not ticker:
        raise ValueError('Directional Engine requires a ticker '
                         '(context.ticker or context.focus.ticker)')
    mode = str(context.get('mode') or 'unified').strip().lower()
    if mode == 'unified':
        return _signal_generator().generate(ticker)
    if mode not in _MODULES:
        raise ValueError(f"mode must be one of {{'unified', *{sorted(_MODULES)}}}; got {mode!r}")
    mod_name, fn = _MODULES[mode]
    module = __import__(f'Direction.{mod_name}', fromlist=[fn])
    return getattr(module, fn)(ticker)

TOOL_SPEC = ToolSpec(
    name="Directional Engine",
    slug="directional-engine",
    description=("Five Direction signals -- whale flow, Elliott Wave, "
                 "Bollinger, multi-timeframe trend, liquidity -- run "
                 "individually or as one unified conviction call for a "
                 "context's focus ticker. Select via mode."),
    run=run,
)
```

- [ ] **Step 4: Register + route**

In `Tools/registry.py` the entry becomes slug `directional-engine`. In `dashboard/app.py` replace `'direction-signal'` with `'directional-engine'` in `GENERIC_TOOL_SLUGS`, and add a `tools_directional.html` template (mirror of `tools_simulations.html` but with a `mode` select listing `unified` + the five modules) plus GET/POST `/tools/directional-engine` routes (mirror the simulations route structure, merging only `mode`). In `tools_index.html` the generic `Open →` link now hits `/tools/directional-engine` automatically.

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_direction_signal_tool.py dashboard/tests/test_tools_routes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/direction_signal_tool.py Tools/registry.py dashboard/app.py dashboard/templates/tools_directional.html Tools/tests/test_direction_signal_tool.py
git commit -m "feat(tools): rename Direction Signal to Directional Engine (per-module + unified modes)"
```

---

## Self-Review

**Spec coverage:** R1→Task 6; R2→Task 3; R6→Tasks 1+2; R8→Tasks 3+4+5; R9/R10/R11→Task 7. All requirement items mapped. R10 ("wrap trend engine in the direction menu as a choice + part of unified run") is covered by `mode='trend'` and the fact that `generate()` already runs trend inside the unified path.

**Placeholder scan:** No "TBD"/"implement later". Every code step shows real, runnable code. Task 3's `_build_corr_sim_peer_from_context` override wiring is described precisely but the exact diff depends on the peer-builder body — the step names the existing `var.var_days`/`n_sims`/`confidence` reads to mirror, so an engineer can apply it without guessing.

**Type consistency:** `_import_var_main()` returns the module and `_build_*` builders take `(payload, ticker, **overrides)` consistently. `ToolSpec` names/slugs used in templates and routes match registry values (`simulations`, `directional-engine`). `_strategy_map` and `run_strategy_pnl` both read the same `chain_strategies.json` `strategies` list shape.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-16-tools-dashboard-overhaul.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
