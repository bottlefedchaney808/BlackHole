"""widget_archive_renderer.py (Phase 8 of Modularization Overhaul)

Generic renderer for archived module results from ArchiveIndex.
Dispatched by ModuleSpec.category / key_shape (with fallback).
"""
from typing import Any

try:
    from shared.module_registry import all_modules
except Exception:
    all_modules = lambda: []


def render_widget(module_slug: str, archive_row: dict[str, Any]) -> dict[str, Any]:
    """Return payload for archived module widget.

    For chart modules (exposure/surface/smile): image refs.
    For scalar (scanners/pricing): metrics tile.
    """
    # try to get category from registry for dispatch
    cat = None
    for m in all_modules():
        if m.slug == module_slug:
            cat = m.category
            break
    if not cat:
        cat = archive_row.get("category", "unknown")

    arts = archive_row.get("artifacts") or archive_row.get("artifact_paths_json") or []
    if isinstance(arts, str):
        import json
        try:
            arts = json.loads(arts)
        except Exception:
            arts = []

    if cat in ("exposure", "surface", "smile", "flow"):
        return {"type": "chart", "slug": module_slug, "artifacts": arts}
    else:
        metrics = archive_row.get("metrics") or archive_row.get("metrics_json") or {}
        if isinstance(metrics, str):
            import json
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        return {"type": "metrics", "slug": module_slug, "metrics": metrics}
