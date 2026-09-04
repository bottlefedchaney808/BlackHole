"""shared/module_execution.py -- module execution machinery.

Phase 7 (retire orchestrator launching machinery) relocation target.

This module owns the reusable module *execution* path -- resolve, expand
`requires`, topologically order, then run a set of registry modules. It
outlives the orchestrator launcher: `run_selected_modules` is called from
`orchestrator.py`'s `--modules`/`--modules-category`/`--all-modules` CLI flags
and from `dashboard/app.py::_execute_run`, and is the sole shared entry point
for running registry modules.

The functions here were relocated verbatim from `orchestrator.py`
(`_expand_module_requires`, `_topo_sort_modules`, `run_selected_modules`) so
that every existing importer (`from orchestrator import run_selected_modules`)
keeps working unchanged through the orchestrator re-export shim.

Phase 7 shim caveat -- registry/archiver lookups route through the
*orchestrator* namespace at call time (not bound at import): the existing
tests exercise this machinery by monkeypatching ``orchestrator.all_modules``,
``orchestrator.resolve_modules`` and ``orchestrator._archive_module_result``,
and the execution path must stay sensitive to those patches to remain
behaviourally identical to the pre-relocation code. A later phase may point
these accessors straight at ``shared.module_registry`` once callers and tests
are migrated off the orchestrator namespace.
"""

from typing import Any


def _all_modules() -> list[Any]:
    """Current registry modules, read through orchestrator's namespace.

    Phase 7 relocation shim -- see the module docstring. Delegating at call
    time (rather than binding at import) keeps the machinery sensitive to
    monkeypatches of ``orchestrator.all_modules`` that the existing tests
    rely on.
    """
    from orchestrator import all_modules

    return all_modules()


def _resolve_modules(slugs: list[str]) -> list[Any]:
    """Resolve slugs to ModuleSpecs, read through orchestrator's namespace.

    Phase 7 relocation shim -- see the module docstring.
    """
    from orchestrator import resolve_modules

    return resolve_modules(slugs)


def _expand_module_requires(selected: list[Any]) -> list[Any]:
    """Transitively add every `requires` dependency not already selected.

    `selected` are the explicitly-resolved ModuleSpecs; a dependency named in
    one of their `.requires` lists is added even if the caller never named it,
    and that dependency's own `.requires` are expanded in turn. Raises
    ValueError naming the module/slug pair if a `requires` slug isn't
    registered anywhere in `shared.module_registry.all_modules()`.
    """
    index = {module.slug: module for module in _all_modules()}
    included: dict[str, Any] = {module.slug: module for module in selected}
    pending = list(selected)
    while pending:
        module = pending.pop()
        for req_slug in module.requires:
            if req_slug in included:
                continue
            try:
                req_module = index[req_slug]
            except KeyError:
                raise ValueError(
                    f"Module {module.slug!r} requires unknown slug {req_slug!r}"
                ) from None
            included[req_slug] = req_module
            pending.append(req_module)
    return list(included.values())


def _topo_sort_modules(modules: list[Any]) -> list[Any]:
    """Order `modules` so every `requires` dependency runs before its
    dependent (Kahn's algorithm). Only edges between modules present in
    `modules` are honoured -- callers are expected to have already expanded
    `requires` via `_expand_module_requires`. Raises ValueError naming the
    remaining slugs if the requires graph among `modules` has a cycle.
    """
    by_slug = {module.slug: module for module in modules}
    in_degree = {slug: 0 for slug in by_slug}
    dependents: dict[str, list[str]] = {slug: [] for slug in by_slug}
    for module in modules:
        for req_slug in module.requires:
            if req_slug not in by_slug:
                continue
            in_degree[module.slug] += 1
            dependents[req_slug].append(module.slug)

    ready = sorted(slug for slug, degree in in_degree.items() if degree == 0)
    ordered_slugs: list[str] = []
    while ready:
        slug = ready.pop(0)
        ordered_slugs.append(slug)
        for dependent_slug in sorted(dependents[slug]):
            in_degree[dependent_slug] -= 1
            if in_degree[dependent_slug] == 0:
                ready.append(dependent_slug)
        ready.sort()

    if len(ordered_slugs) != len(modules):
        remaining = sorted(set(by_slug) - set(ordered_slugs))
        raise ValueError(
            f"Cycle detected in module requires graph among: {', '.join(remaining)}"
        )
    return [by_slug[slug] for slug in ordered_slugs]


def run_selected_modules(slugs: list[str], context: dict[str, Any]) -> dict[str, Any]:
    """Resolve, expand, order and execute a set of registry modules.

    New, additive execution path (Task 2 of the modularization overhaul),
    parallel to `run_suite`/`run_unified` -- neither of those calls this, and
    this does not call either of them.

    - `slugs` is resolved via `shared.module_registry.resolve_modules`
      (raises ValueError on an unknown slug). An empty `slugs` list falls
      back to every module with `default_selected=True`.
    - `requires` is expanded transitively via `_expand_module_requires`: a
      dependency named by a selected module always runs even if the caller
      didn't list it explicitly.
    - Execution order is topological (`_topo_sort_modules`): a `requires`
      dependency always runs before the module that requires it.
    - After each module's `run(context)` call, if the returned
      `ModuleResult.context_patch` is not None, it is merged into `context`
      (dict update) so later modules in this same call see it -- the
      generalized successor of `_thread_vol_stats_into_context`, but a
      separate mechanism: this function never calls that one, and vice versa.
    - `_archive_module_result` (currently a no-op stub) is called once per
      executed module, after that module's `run(context)` returns.

    Returns:
        {
            "status": "ok",
            "order": [slug, ...],      # modules actually executed, in order
            "results": {slug: ModuleResult, ...},
        }
    """
    if slugs:
        selected = _resolve_modules(slugs)
    else:
        selected = [module for module in _all_modules() if module.default_selected]

    expanded = _expand_module_requires(selected)
    ordered = _topo_sort_modules(expanded)

    # Phase 7 relocation: the archiver hook stays in orchestrator.py (it tags
    # archives with triggered_by="orchestrator"). Imported lazily at call time
    # so the existing `monkeypatch.setattr(orchestrator,
    # "_archive_module_result", ...)` tests keep working, and to avoid a
    # load-time circular import -- module_execution is imported by
    # orchestrator's re-export shim.
    from orchestrator import _archive_module_result

    results: dict[str, Any] = {}
    for module in ordered:
        result = module.run(context)
        results[module.slug] = result
        if result.context_patch is not None:
            context.update(result.context_patch)
        _archive_module_result(module.slug, result, context)

    return {
        "status": "ok",
        "order": [module.slug for module in ordered],
        "results": results,
    }
