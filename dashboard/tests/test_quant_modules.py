"""test_quant_modules.py

Covers Task 5 of docs/superpowers/plans/2026-08-01-quant-console.md:
`dashboard/quant_modules.py::MODULE_REGISTRY` / `get_module`.

No network/credentials involved -- this is a pure static-data module, so
every test here is `pytest.mark.unit`.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.quant_modules import MODULE_REGISTRY, get_module

pytestmark = pytest.mark.unit


def test_registry_has_exactly_five_entries():
    # One entry per suite (vol, options, var, sentiment) + orchestrator unified.
    assert len(MODULE_REGISTRY) == 5


def test_registry_entries_have_required_shape():
    expected_keys = {"id", "name", "suite", "focus", "runnable"}
    for entry in MODULE_REGISTRY:
        assert expected_keys <= set(entry.keys()), entry
        assert isinstance(entry["id"], str) and entry["id"]
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["suite"], str) and entry["suite"]
        assert isinstance(entry["focus"], str) and entry["focus"]
        assert isinstance(entry["runnable"], bool)


def test_registry_ids_are_unique():
    ids = [entry["id"] for entry in MODULE_REGISTRY]
    assert len(ids) == len(set(ids))


def test_registry_covers_the_four_suites_plus_unified():
    ids = {entry["id"] for entry in MODULE_REGISTRY}
    assert ids == {"vol", "options", "var", "sentiment", "unified"}


def test_options_runnable_is_false():
    """Options_Suite's context-mode stub currently fails validation
    (`shared/schemas.py::validate_options_result` -- see the `tool-launcher`
    skill's documented FAIL). `runnable=False` must not be silently flipped
    to `True` here; it changes only when that underlying bug is fixed,
    independently of this plan.
    """
    options_entry = get_module("options")
    assert options_entry["runnable"] is False


def test_non_options_modules_are_runnable():
    for entry in MODULE_REGISTRY:
        if entry["id"] == "options":
            continue
        assert entry["runnable"] is True, entry


def test_get_module_returns_matching_entry():
    entry = get_module("vol")
    assert entry["id"] == "vol"


def test_get_module_raises_keyerror_with_valid_options_on_bad_id():
    with pytest.raises(KeyError) as excinfo:
        get_module("not-a-real-module")

    message = str(excinfo.value)
    assert "not-a-real-module" in message
    # Every registered id should be named as a valid option in the message,
    # matching Tools/registry.py::get_tool's error-message convention.
    for entry in MODULE_REGISTRY:
        assert entry["id"] in message
