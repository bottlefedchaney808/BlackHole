"""test_social_sentiment_tool.py

Covers Tools/tools/social_sentiment_tool.py -- the placeholder Tools entry for
a future Social Media Sentiment Scanner (Reddit / YouTube / StockTwits).

It is deliberately a stub: it must NOT reach into sentiment-scanner/ (a
separate, already-functional project). What is pinned here is the stub
contract the dashboard listing relies on -- a "not_implemented" status per
source, and an "error" status when the context carries no focus ticker.
"""
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tools.registry FIRST -- see the note in test_vrp_term_structure_tool.py.
import Tools.registry  # noqa: E402,F401
from Tools.tools import social_sentiment_tool  # noqa: E402


@pytest.mark.unit
def test_run_returns_not_implemented_stub():
    result = social_sentiment_tool.run({"focus": {"ticker": "AAPL"}})
    assert result["status"] == "not_implemented"
    assert result["ticker"] == "AAPL"
    for source in ("reddit", "youtube", "stocktwits"):
        assert result[source]["status"] == "not_implemented"


@pytest.mark.unit
def test_run_requires_ticker():
    result = social_sentiment_tool.run({"focus": {}})
    assert result["status"] == "error"


@pytest.mark.unit
def test_run_handles_missing_focus_block():
    """context_loader can hand over a context whose focus key is absent or
    explicitly null; neither may raise."""
    for context in ({}, {"focus": None}):
        result = social_sentiment_tool.run(context)
        assert result["status"] == "error"
        assert result["error"]


@pytest.mark.unit
def test_social_sentiment_tool_is_registered():
    tool = Tools.registry.get_tool("social-sentiment")
    assert tool.slug == "social-sentiment"
    assert tool.name == "Social Media Sentiment Scanner"
    assert tool.run is social_sentiment_tool.run
