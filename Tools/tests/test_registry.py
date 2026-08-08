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


def test_tools_is_nonempty_list_of_toolspecs():
    assert isinstance(TOOLS, list)
    assert len(TOOLS) >= 2  # Options Strategy Tool + Backtesting Tool, at minimum
    for tool in TOOLS:
        assert isinstance(tool, ToolSpec)


def test_each_toolspec_has_well_formed_fields():
    for tool in TOOLS:
        assert isinstance(tool.name, str) and tool.name.strip()
        assert isinstance(tool.slug, str) and tool.slug.strip()
        assert isinstance(tool.description, str) and tool.description.strip()
        assert callable(tool.run)


def test_slugs_are_unique():
    slugs = [tool.slug for tool in TOOLS]
    assert len(slugs) == len(set(slugs))


def test_expected_first_two_tools_are_registered():
    slugs = {tool.slug for tool in TOOLS}
    assert "options-strategy" in slugs
    assert "backtesting" in slugs


def test_get_tool_resolves_known_slug():
    tool = get_tool("options-strategy")
    assert tool.slug == "options-strategy"
    assert tool.name == "Options Strategy Tool"


def test_get_tool_raises_keyerror_for_unknown_slug_and_lists_valid_ones():
    with pytest.raises(KeyError) as exc_info:
        get_tool("not-a-real-tool")
    message = str(exc_info.value)
    assert "not-a-real-tool" in message
    assert "options-strategy" in message


def test_whale_flow_tool_is_registered():
    tool = get_tool("whale-flow")
    assert tool.slug == "whale-flow"
    assert tool.name == "Whale Flow Tool"


def test_elliott_wave_tool_is_registered():
    tool = get_tool("elliott-wave")
    assert tool.slug == "elliott-wave"
    assert tool.name == "Elliott Wave Tool"


def test_bollinger_tool_is_registered():
    tool = get_tool("bollinger")
    assert tool.slug == "bollinger"
    assert tool.name == "Bollinger Bands Tool"


def test_trend_engine_tool_is_registered():
    tool = get_tool("trend-engine")
    assert tool.slug == "trend-engine"
    assert tool.name == "Trend Engine Tool"


def test_liquidity_map_tool_is_registered():
    tool = get_tool("liquidity-map")
    assert tool.slug == "liquidity-map"
    assert tool.name == "Liquidity Map Tool"
