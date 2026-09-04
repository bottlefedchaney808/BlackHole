# Extending and Debugging

Adding to the repo means adding a module, not teaching a new method. The method is fixed in the earlier pages.

## Adding a module

1. Implement `run(context: dict) -> ModuleResult` in the owning suite.
2. Build a `ModuleSpec` in that suite's `module_registry.py`.
3. Add to the `MODULES` list.
4. `all_modules()` picks it up automatically.

No change to `run_unified` is required unless the module should be part of the default unified path.

## Module contract

```python
class ModuleResult:
    status: str          # "ok" | "skipped" | "failed"
    artifacts: list[ArtifactRef]
    metrics: dict[str, Any]
    context_patch: dict[str, Any] | None
```

`context_patch` threads values into downstream context — the generalized successor of `orchestrator.py::_thread_vol_stats_into_context`.

## Tests that fail when a value is missing

A good test for a new module:
- Asserts `status` is `failed` when a required input is missing.
- Asserts a missing value is not silently defaulted to 0.0 or "ok".
- Asserts the module does not secretly equal another metric (e.g. `book_gamma == GEX`).

## Reading a failed run

1. Find the `run_id` in `orchestrator_output/<run_id>/`.
2. Read the failing suite's `<suite>_result.json`.
3. Read the orchestrator log for the validation verdict.
4. Check `shared/suite_validation.py` for the exact rule that failed.
5. Fix the input, credential, or data issue; re-run.

## Common debugging paths

| Symptom | First file to read |
|---|---|
| Module not in `--list-modules` | `shared/module_registry.py` `_suite_modules()` |
| `ModuleNotFoundError` for a suite | The suite's local imports; run from the suite directory or use the orchestrator |
| `database is locked` | `shared/connection_pool.py`; check for duplicate dashboard/scheduler instances |
| Hermes venv leak into project venv | Clear `PYTHONPATH`/`PYTHONHOME` before invoking `.venv` |
| SPX chain empty | Check that the chain is queried under `SPXW`, not `SPX` |

## What this page is not

It is not an invitation to add new sign conventions, new "books," or new measurement objects without the locked conventions from [Standing Conventions](./02-standing-conventions.md) and [Objects and Hygiene](./03-objects-and-hygiene.md).

Next: the glossary, with every term defined by question, quantity, and sign.
