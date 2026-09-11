"""test_registry.py

Covers registry.py's TOOLS list shape: every entry is a well-formed
ToolSpec (name/slug/description all non-empty strings, run callable),
slugs are unique, get_tool() resolves a known slug and raises KeyError
(with the valid options listed) for an unknown one.
"""
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import TOOLS, ToolSpec, get_tool  # noqa: E402


@pytest.mark.unit
def test_tools_is_nonempty_list_of_toolspecs():
    assert isinstance(TOOLS, list)
    assert len(TOOLS) >= 2  # Options Strategy Tool + Backtesting Tool, at minimum
    for tool in TOOLS:
        assert isinstance(tool, ToolSpec)


@pytest.mark.unit
def test_each_toolspec_has_well_formed_fields():
    for tool in TOOLS:
        assert isinstance(tool.name, str) and tool.name.strip()
        assert isinstance(tool.slug, str) and tool.slug.strip()
        assert isinstance(tool.description, str) and tool.description.strip()
        assert callable(tool.run)


@pytest.mark.unit
def test_slugs_are_unique():
    slugs = [tool.slug for tool in TOOLS]
    assert len(slugs) == len(set(slugs))


@pytest.mark.unit
def test_expected_first_two_tools_are_registered():
    slugs = {tool.slug for tool in TOOLS}
    assert "options-strategy" in slugs
    assert "backtesting" in slugs


@pytest.mark.unit
def test_get_tool_resolves_known_slug():
    tool = get_tool("options-strategy")
    assert tool.slug == "options-strategy"
    assert tool.name == "Options Strategy Tool"


@pytest.mark.unit
def test_get_tool_raises_keyerror_for_unknown_slug_and_lists_valid_ones():
    with pytest.raises(KeyError) as exc_info:
        get_tool("not-a-real-tool")
    message = str(exc_info.value)
    assert "not-a-real-tool" in message
    assert "options-strategy" in message


@pytest.mark.unit
def test_direction_signal_tool_is_registered():
    tool = get_tool("directional-engine")
    assert tool.slug == "directional-engine"
    assert tool.name == "Directional Engine"


@pytest.mark.unit
def test_hedge_optimizer_tool_is_registered():
    tool = get_tool("hedge-optimizer")
    assert tool.slug == "hedge-optimizer"
    assert tool.name == "Hedge Optimizer"


@pytest.mark.unit
def test_vrp_term_structure_tool_is_registered():
    tool = get_tool("vrp-term-structure")
    assert tool.slug == "vrp-term-structure"
    assert tool.name == "VRP Term Structure"


@pytest.mark.unit
def test_price_dist_tool_is_registered():
    tool = get_tool("simulations")
    assert tool.slug == "simulations"
    assert tool.name == "Simulations"


@pytest.mark.unit
def test_registered_tool_slugs_are_expected():
    slugs = {tool.slug for tool in TOOLS}
    expected = {
        "options-strategy", "backtesting", "directional-engine",
        "hedge-optimizer", "vrp-term-structure", "simulations",
    }
    assert expected.issubset(slugs)
    # The five Direction sub-signals are NOT standalone tools anymore -- they
    # live inside directional-engine (whale/elliott/bollinger/trend/liquidity
    # modes) and must not appear as separate entries on the Tools screen.
    assert not ({'whale-flow', 'elliott-wave', 'bollinger',
                 'trend-engine', 'liquidity-map'} & slugs)


def test_social_sentiment_removed():
    from Tools.registry import TOOLS
    assert all(t.slug != 'social-sentiment' for t in TOOLS)


class TestNoCircularImport:
    """Importing a tool module DIRECTLY must not deadlock against registry.

    Every tool did `from Tools.registry import ToolSpec` at module scope while
    registry._load_tools() imports those same modules and reads TOOL_SPEC, so
    a direct import hit a partially-initialized module:
        AttributeError: partially initialized module
        'Tools.tools.backtesting_tool' has no attribute 'TOOL_SPEC'
    The dashboard never saw it because it always imports Tools.registry first.
    ToolSpec now lives in Tools/spec.py, which imports nothing from Tools.
    """

    def test_tool_module_imports_standalone_in_a_fresh_interpreter(self):
        """Must run in a SUBPROCESS: once any test has imported Tools.registry,
        it is cached in sys.modules and the cycle cannot reproduce in-process."""
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, "-c",
             "import Tools.tools.backtesting_tool as t; print(t.TOOL_SPEC.slug)"],
            cwd=str(root), capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            f"direct import of a tool module failed:\n{proc.stderr[-1500:]}"
        )

    def test_spec_module_does_not_import_the_registry(self):
        """The property that makes the cycle impossible, asserted directly.

        Parsed from the AST, not grepped: spec.py's own docstring quotes the
        old `from Tools.registry import ToolSpec` line while explaining the
        bug, so a plain text search reports an import that is not there.
        """
        import ast
        from pathlib import Path

        src = Path(__file__).resolve().parents[1].joinpath("spec.py").read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(m == "Tools" or m.startswith("Tools.") for m in imported), (
            f"Tools/spec.py must not import from Tools, got {sorted(imported)}"
        )

    def test_registry_still_re_exports_toolspec(self):
        """12 tool modules and the documented recipe use the old import path."""
        from Tools.registry import ToolSpec as FromRegistry
        from Tools.spec import ToolSpec as FromSpec

        assert FromRegistry is FromSpec
