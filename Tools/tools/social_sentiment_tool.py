"""social_sentiment_tool.py

Placeholder Tools entry for a future Social Media Sentiment Scanner (Reddit /
YouTube / StockTwits).

Deliberately NOT wired to the real sentiment-scanner suite (sentiment-scanner/)
-- that is a separate, already-functional project with its own project-local
venv and its own scan loop. This module is a UI stub so the tool appears in the
dashboard's Tools listing ahead of the real implementation; every source
reports status "not_implemented" and no network call is made.
"""
from __future__ import annotations

from typing import Any, Dict

from Tools.registry import ToolSpec

_SOURCES = ("reddit", "youtube", "stocktwits")


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context).

    Returns a stub result: status "not_implemented" overall and per source, or
    status "error" if the context carries no focus ticker.
    """
    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    if not ticker:
        return {"status": "error", "error": "context.focus.ticker is required"}

    result: Dict[str, Any] = {
        "status": "not_implemented",
        "ticker": ticker,
        "note": (
            "Placeholder tool -- no scan was performed. The functional "
            "implementation lives in the separate sentiment-scanner/ suite."
        ),
    }
    for source in _SOURCES:
        result[source] = {"status": "not_implemented"}
    return result


TOOL_SPEC = ToolSpec(
    name="Social Media Sentiment Scanner",
    slug="social-sentiment",
    description=(
        "Placeholder for a future Reddit/YouTube/StockTwits sentiment scan. "
        "Not yet implemented -- returns a status stub."
    ),
    run=run,
)
