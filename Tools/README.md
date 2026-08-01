# Tools

An expandable framework of standalone quant-finance tools that sit
alongside the four suites (`Vol_Suite`, `Options_Suite`, `sentiment-scanner`,
`VaR_Tools_Simulations`) without being locked into any one of them. Any tool
here can pull shared context from whichever suite run you point it at.

## What "context" means, in plain terms

When you run any suite's unified flow (for example Vol_Suite's
`run_unified_flow`, or the root `orchestrator.py --unified`), it writes one
file called `suite_context.json` into that run's output folder. That file
is a snapshot of everything about the run that another program might need:
which ticker, which expiration, which basket of related tickers, which
sentiment data, and (if the run included an options chain scan) a list of
recommended multi-leg option strategies.

Think of it as a claim ticket: one suite does the work of pulling data and
running its analysis, then hands that ticket to whichever tool you want to
use next, instead of every tool having to redo the same setup.

You do not need to create this file yourself -- it is produced automatically
every time a suite's unified/orchestrated flow finishes.

## Picking a context and running a tool

1. List what's available:

   ```python
   from Tools.context_loader import list_available_contexts

   for summary in list_available_contexts():
       print(summary["created_at_utc"], summary["ticker"], summary["run_id"], summary["path"])
   ```

   This scans the known output folders of all four suites (plus the root
   orchestrator's own output folder) for `suite_context.json` files and
   lists them newest-first. Each entry shows the ticker, when the run
   happened, and the file's actual location -- pick the one you want.

2. Load it:

   ```python
   from Tools.context_loader import load_context

   context = load_context(summary["path"])
   ```

   This re-validates the file against the same schema the suite used to
   write it (`Vol_Suite/suite_context.py`), so a corrupted or half-written
   context fails immediately and clearly rather than confusing a tool later.

3. Run a tool against it:

   ```python
   from Tools.registry import get_tool

   options_tool = get_tool("options-strategy")
   result = options_tool.run(context)  # mode="cached" by default

   backtest_tool = get_tool("backtesting")
   context["mode"] = "strategy_pnl"
   context["entry_date"] = "20261001"
   result = backtest_tool.run(context)
   ```

   Every tool's `run()` takes the context dict and returns a plain,
   JSON-serializable result dict -- safe to print, save, or hand to a
   dashboard.

### The two tools shipped today

- **Options Strategy Tool** (`slug="options-strategy"`) -- recommends
  multi-leg option strategies (call/put spreads, straddles, strangles, iron
  condors, collars) based on the vol regime and edge strikes from a chain
  scan. By default (`mode="cached"`) it just reads back the
  `chain_strategies.json` that a prior suite run already computed and saved
  in its output folder -- instant, no data-vendor calls. Set
  `context["mode"] = "live"` to re-run the chain scan fresh instead.

- **Backtesting Tool** (`slug="backtesting"`) -- two independent backtests
  behind one tool, selected via `context["mode"]`:
  - `"dealer_gamma_study"` -- the Stage 3 dealer-positioning validation
    study: does dealer gamma sign (long vs. short) predict subsequent
    realized volatility, and does the newer sign convention track that
    better than the older one?
  - `"strategy_pnl"` -- simulates a specific strategy's profit/loss from an
    entry date to an exit (or expiration) date. Give it a strategy dict
    directly via `context["strategy"]`, or let it pick one out of
    `context["strategies"]` (the list a suite context already carries, if
    the run included a chain scan) via `context["strategy_index"]`.

## Adding a new tool (the plugin pattern)

1. Create a new module under `Tools/tools/`, e.g.
   `Tools/tools/var_stress_tool.py`.
2. Implement `run(context: dict) -> dict` in that module. `context` is
   always a validated `suite_context.json` dict, optionally carrying extra
   top-level keys your tool needs (a `"mode"` selector, parameter
   overrides, etc.) -- extra keys are always safe to add; the schema's
   validator only checks that the required fields are present.
3. At module scope, build a `ToolSpec`:

   ```python
   from Tools.registry import ToolSpec
   TOOL_SPEC = ToolSpec(
       name="VaR Stress Tool",
       slug="var-stress",
       description="Runs a stressed-scenario VaR sweep off a suite context.",
       run=run,
   )
   ```

4. In `Tools/registry.py`, import the new module and append its
   `TOOL_SPEC` to the list inside `_load_tools()`.

That's the entire integration surface. Nothing else in `Tools/` needs to
change, and nothing in the four suites needs to change either -- every tool
only ever depends on the shared `suite_context.json` schema, never on one
particular suite's internals.

## Layout

```
Tools/
  __init__.py            -- sys.path bootstrap so `import Tools` works from anywhere
  context_loader.py       -- list_available_contexts(), load_context()
  registry.py              -- ToolSpec, TOOLS list, get_tool()
  tools/
    __init__.py
    options_strategy_tool.py
    backtesting_tool.py
  tests/
    test_context_loader.py
    test_registry.py
```
