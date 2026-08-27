"""Regression guard for the swaps-dashboard split
(docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md).

dashboard/app.py must never import swaps_query, db_loader, or
shared.query_builder at module scope again -- that's exactly what made
home() slow (the queries themselves were route-scoped, but the split's whole
point is that this file has zero swap-DB code path left to regress into).
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP_PY = Path(__file__).resolve().parent.parent / 'app.py'
FORBIDDEN_MODULES = {'swaps_query', 'db_loader', 'shared.query_builder'}


def _imported_module_names(tree: ast.Module):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_dashboard_app_does_not_import_swap_db_modules():
    tree = ast.parse(APP_PY.read_text(encoding='utf-8'))
    imported = _imported_module_names(tree)
    overlap = imported & FORBIDDEN_MODULES
    assert not overlap, f'dashboard/app.py imports swap-DB modules: {overlap}'
