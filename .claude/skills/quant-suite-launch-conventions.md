# Quant Suite Launch Conventions (shared reference)

Not an invocable skill itself (no YAML frontmatter, by design) — a shared reference the five skills below link to instead of duplicating.

## Which skill do I need?

Debugging a suite you launched directly (`.bat`/`.sh` or its own `main.py`)? Read that suite's own skill: `var-tools`, `vol-suite`, `options-suite`, or `sentiment-scanner`. Debugging a run driven through `orchestrator.py`, `dashboard.bat`, or `tools.bat`? Read `tool-launcher` — it explains how each suite's own quirks (documented in their individual skills) surface as orchestrator-level PASS/FAIL verdicts, which is usually the more useful frame when the failure showed up from a `--unified`/`--suite` run rather than a direct launch.

Loaded by: `vol-suite`, `options-suite`, `var-tools`, `sentiment-scanner`, `tool-launcher`. Don't copy this content into those skills — link here instead, so a repo change only needs one edit.

## The three checks, in order

Every launcher failure in this repo traces back to one of these three, and checking out of order produces misleading downstream errors. Check in this order:

1. **Which venv, and is it complete.** `Vol_Suite`, `Options_Suite`, and `VaR_Tools_Simulations` share the root `.venv` (`C:\Users\bottl\FinancialDevelopment\.venv`). `sentiment-scanner` has its own project-local `.venv` at `sentiment-scanner\.venv` and auto-runs `pip install -q -r requirements.txt` on every launch — the other three do not auto-install, so a stale shared `.venv` (e.g. after `requirements.txt` changes) fails with an `ImportError`/`ModuleNotFoundError` rather than self-healing. `dashboard.bat`/`tools.bat` additionally pre-check `import fastapi, uvicorn, jinja2, slowapi` before starting uvicorn, specifically so a missing-package failure shows as one clear message instead of a mid-startup traceback.
2. **Is the target port already bound.**
   - Port `8787` — the dashboard app (`dashboard/app.py`), serving both `/` (dashboard.bat) and `/tools` (tools.bat). **These are the same uvicorn process, not two independent services** — `dashboard.bat` and `tools.bat` launch the identical `uvicorn app:app --host 127.0.0.1 --port 8787` command from `dashboard/`, differing only in which URL they open in the browser afterward. Both scripts check `netstat -ano | findstr 127.0.0.1:8787` before starting and refuse to double-launch; if you bypass that check (e.g. running uvicorn directly), a second instance racing the first against `swaps.db` is what produces "database is locked" errors — see `db_loader.py` / `shutdown_signal.py`.
   - Port `4416` — sentiment-scanner's `bgutil` PO-token server (`http://127.0.0.1:4416/ping`), required only for YouTube caption transcripts. `sentiment.bat` auto-starts it from `C:\Users\bottl\bgutil-ytdlp-pot-provider\server\build\main.js` if it isn't already reachable. If that build output doesn't exist, the scanner still runs — it just silently skips YouTube captions, which shows up later as unexpectedly thin sentiment data, not as a launch error.
3. **Stale environment variables.** `vol_suite.bat`, `options_suite.bat`, and `var.bat` each run `set PYTHONPATH=` and `set PYTHONHOME=` before invoking Python, to stop an unrelated "Hermes" venv from leaking into this repo's imports. `sentiment.bat` does the same. Verified: none of the four `.bat` files touch `PATH` itself — only `PYTHONPATH`/`PYTHONHOME` — despite `sentiment.bat`'s own comment claiming PATH is stripped too (that comment is stale; the code doesn't do it). If you're troubleshooting an import that's resolving to the wrong package, check these two env vars specifically, not PATH.

## The `suite_context.json` contract

One run of any suite's unified/orchestrated flow writes `suite_context.json` into that run's output folder — a snapshot of what the run was about (ticker, expiry, basket, sentiment data, recommended strategies) that other suites/tools can consume without redoing the work. Schema lives in `Vol_Suite/suite_context.py`; consumers read it through `Tools/context_loader.py` (`list_available_contexts()`, `load_context(path)`), which re-validates against the same schema so a corrupt/half-written context fails immediately rather than confusing a downstream tool.

Producer/consumer roles differ by suite:
- **sentiment-scanner** is the producer for the sentiment block — it doesn't take `--context`/`--context-out` like the other three; it exposes `--export-context <path>` instead.
- **Vol_Suite, Options_Suite, VaR_Tools_Simulations** are consumers, each exposing `run_context_mode` via `--context <path> --context-out <path>`. Two validation functions share the name `validate_vol_result` with different signatures — `shared/schemas.py::validate_vol_result` and `shared/suite_validation.py::validate_vol_result` — confirm which one a given call site actually uses before assuming; don't grab the first match.
- **VaR_Tools_Simulations is a partial consumer**: `run_context_mode` (`main.py:313`) only implements the corr_sim path (module 1). Passing any other `--module` value through context mode returns a `status: error` payload rather than running that module — `--module 1-10` (the interactive menu has 10 modules, not 9) only takes effect in the interactive/menu flow, not headlessly via `--context`.

## `Tools/registry.py` — the `ToolSpec` pattern

For anything under `Tools/` (as opposed to a new suite): implement `run(context: dict) -> dict` in a new module under `Tools/tools/`, wrap it in a `ToolSpec(name, slug, description, run)`, and register it in the `TOOLS` list in `Tools/registry.py`. Every tool depends only on the shared `suite_context.json` schema — nothing else in `Tools/` or the four suites needs to change to add one.
