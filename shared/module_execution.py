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
            try:
                from shared.module_registry import SLUG_ALIASES

                req_slug = SLUG_ALIASES.get(req_slug, req_slug)
            except Exception:
                pass
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


def _write_run_manifest(
    results: dict[str, Any],
    ordered: list[Any],
    context: dict[str, Any],
) -> None:
    """Best-effort write run_manifest.json to run output directory.

    Writes a manifest file next to artifacts that says what happened -
    the validation target for widget runs. A failure to write the manifest
    is logged but does NOT fail the run (mirrors module_archive discipline).

    Args:
        results: Dict of slug -> ModuleResult from module runs
        ordered: List of ModuleSpecs in execution order
        context: Execution context (contains run_id, output_dir)
    """
    import datetime as dt
    import json
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)

    run_id = context.get("run_id")
    output_dir = context.get("output_dir")

    if not run_id or not output_dir:
        logger.warning(
            "run_manifest write skipped: missing run_id (%r) or output_dir (%r)",
            run_id,
            output_dir,
        )
        return

    try:
        # Build results dict: slug -> {status, error, ticker, expiry}
        # and build artifacts dict: slug -> [relative paths]
        results_out: dict[str, dict[str, Any]] = {}
        artifacts_out: dict[str, list[str]] = {}
        for slug, result in results.items():
            # Extract ticker/expiry from context or module archive convention
            focus = context.get("focus") or {}
            ticker = (
                str(context.get("ticker") or focus.get("ticker") or "").strip().upper()
            )
            ticker = ticker or None
            expiry = str(context.get("expiry") or focus.get("expiry") or "").strip()
            expiry = expiry or None

            # Build artifacts list: [repo-relative paths]
            artifact_paths: list[str] = []
            for artifact in result.artifacts or []:
                try:
                    from shared.artifact_paths import to_rel

                    artifact_paths.append(to_rel(str(artifact.path)))
                except Exception:
                    # If path access/conversion fails, degrade to the raw path
                    try:
                        artifact_paths.append(str(artifact.path))
                    except Exception:
                        pass

            results_out[slug] = {
                "status": result.status,
                "error": None,  # No error field in ModuleResult; could add if needed
                "ticker": ticker,
                "expiry": expiry,
            }
            artifacts_out[slug] = artifact_paths

        manifest = {
            "run_id": run_id,
            "order": [module.slug for module in ordered],
            "results": results_out,
            "artifacts": artifacts_out,
            "started_at": context.get(
                "run_started_at", dt.datetime.now(dt.UTC).isoformat()
            ),
            "ended_at": dt.datetime.now(dt.UTC).isoformat(),
        }

        # Write to <output_dir>/run_manifest.json
        output_path = Path(output_dir)
        manifest_path = output_path / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
    except Exception:
        logger.warning(
            "run_manifest write failed for run_id=%r (manifest is best-effort; "
            "the module run itself is unaffected)",
            run_id,
            exc_info=True,
        )


def _persist_context_patch_to_store(
    context_patch: dict[str, Any],
    context: dict[str, Any],
    source_slug: str,
) -> None:
    """Persist a context_patch to the Context Store (best-effort, never fail the run).

    Keys the entry by the context scope (ticker/ticker+expiry or basket) plus run_id.
    If Context Store operations fail, logs a warning but does NOT fail the module run.

    Args:
        context_patch: The patch dict from ModuleResult.context_patch
        context: The execution context (should contain ticker/scope + run_id)
        source_slug: The module slug that produced this patch
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        # Lazy import ContextStore and Scope
        from shared.context_store import ContextStore

        # Build scope from context: prefer ticker+expiry if available, fall back to basket
        scope_dict: dict[str, Any] = {}
        if "basket" in context:
            scope_dict["basket"] = context["basket"]
        elif "ticker" in context:
            scope_dict["ticker"] = context["ticker"]
            if context.get("expiry"):
                scope_dict["expiry"] = context["expiry"]

        # If we have a valid scope, persist each key in context_patch
        if scope_dict:
            store = ContextStore()
            try:
                for key, value in context_patch.items():
                    store.put(scope_dict, key, value, source_slug=source_slug)
            finally:
                store.close()
        else:
            # No usable scope in context - log at debug level
            logger.debug(
                "context_patch write skipped: no ticker/scope in context for module %s",
                source_slug,
            )
    except Exception:
        logger.warning(
            "context_patch store failed for module %s (best-effort; "
            "the module run itself is unaffected)",
            source_slug,
            exc_info=True,
        )


def _make_console_lossy() -> None:
    """Stop a console-encoding failure from killing a module run.

    Several suites print banner lines containing box-drawing characters
    (U+2500 and friends). On Windows a console/pipe that defaults to cp1252
    cannot encode those, so the bare `print` raises UnicodeEncodeError --
    inside the module, mid-computation. That is how every Options_Suite
    pricer ended up silently falling back to a flat 0.25 sigma: not because
    the IV solver failed, but because `VolManager.get_sigma` printed a
    reference banner on the way and the print threw.

    Rendering an un-encodable character as "?" is the correct trade here:
    the alternative is losing the computation over a decoration. This only
    affects how bytes reach the console -- no value a module returns passes
    through it. Idempotent, and a no-op on a stream that has no
    `reconfigure` (a StringIO under pytest, for instance).
    """
    import sys

    # "surrogateescape" (Python's default for stdout on Windows here) is NOT
    # sufficient: it round-trips undecodable *input* bytes, and does nothing
    # for encoding a legitimate character like U+2500 that simply has no
    # cp1252 mapping. Only the lossy handlers below actually prevent the
    # raise, so anything outside that set gets replaced.
    _SAFE = ("replace", "backslashreplace", "xmlcharrefreplace", "ignore")
    for stream in (sys.stdout, sys.stderr):
        try:
            if getattr(stream, "errors", None) not in _SAFE:
                stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001 -- best effort; never fail a run for this
            pass


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

    Entry-time mutation: if `context` lacks `run_id`, mint one
    'YYYYMMDDTHHMMSSZ-<4 hex>'. If `context` lacks `output_dir`, set it to
    `<repo>/outputs/<run_id>/` and create the directory. Explicit caller
    `output_dir` is respected; `run_id` is always assigned.

    Returns:
        {
            "status": "ok",
            "order": [slug, ...],      # modules actually executed, in order
            "results": {slug: ModuleResult, ...},
        }
    """
    _make_console_lossy()

    # Entry-time mutation: mint run_id and set default output_dir if missing.
    # Explicit caller output_dir is respected; run_id is always assigned.
    if "run_id" not in context:
        import datetime as dt
        import secrets

        now = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        ran = secrets.token_hex(2)
        context["run_id"] = f"{now}-{ran}"

    if "output_dir" not in context:
        from shared.artifact_paths import repo_root

        out_dir = repo_root() / "outputs" / context["run_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        context["output_dir"] = str(out_dir)

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
            # Persist context_patch to Context Store (best-effort, never fail the run)
            _persist_context_patch_to_store(result.context_patch, context, module.slug)
        _archive_module_result(module.slug, result, context)

    # Write run_manifest.json best-effort (never fail the run)
    _write_run_manifest(results, ordered, context)

    return {
        "status": "ok",
        "order": [module.slug for module in ordered],
        "results": results,
    }
