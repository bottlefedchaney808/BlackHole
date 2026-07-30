"""pytest fixtures and helpers for sentiment-scanner tests.

Sets up sys.path for module imports, provides sample ticker alert data
factories, and monkeypatches config paths to use temporary directories
so tests are network-free and self-contained.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ── Path setup ──────────────────────────────────────────────────────────
# sentiment-scanner root so ``import config`` and ``from scanner.xxx`` work
_SENTIMENT_ROOT = Path(__file__).resolve().parent.parent  # …/sentiment-scanner
if str(_SENTIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SENTIMENT_ROOT))

# Financial_Development root so ``from shared.schemas import …`` works
_FINDEV_ROOT = _SENTIMENT_ROOT.parent  # …/Financial_Development
if str(_FINDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_FINDEV_ROOT))

# ── Fixture: validate_sentiment_pack is a no-op so ticker_pack tests
#     don't need the real shared.schemas module.  We install the stub
#     early (before any test imports ticker_pack) so the lazy import
#     inside export_alert_group() resolves to our mock.
@pytest.fixture(autouse=True)
def _mock_validate_sentiment_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace shared.schemas.validate_sentiment_pack with a no-op."""
    import shared.schemas  # noqa: F811

    monkeypatch.setattr(shared.schemas, "validate_sentiment_pack", lambda _: None)


# ── Fixture: redirect config paths to tmp_path ──────────────────────────
@pytest.fixture(autouse=True)
def _patch_config_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redirect HIGHLIGHT_PACKS_DIR et al. so export_alert_group writes
    into a disposable temp directory."""
    import config  # noqa: F811

    packs_dir = tmp_path / "exports" / "highlighted_ticker_packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = packs_dir / "latest_manifest.json"

    monkeypatch.setattr(config, "HIGHLIGHT_PACKS_DIR", str(packs_dir))
    monkeypatch.setattr(config, "HIGHLIGHT_PACK_MANIFEST", str(manifest_path))
    monkeypatch.setattr(config, "HIGHLIGHT_PACK_TTL_DAYS", 14)
    monkeypatch.setattr(config, "HIGHLIGHT_PACK_MAX_MANIFEST_ENTRIES", 100)


# ── Sample ticker alert data factories ──────────────────────────────────

@pytest.fixture
def sample_alerts() -> List[Dict[str, Any]]:
    """Return a list of 3 realistic mock ticker alerts."""
    return [
        {
            "ticker": "AAPL",
            "contested_narrative_score": 60,
            "war_score": 0.45,
            "thesis_ratio": 0.25,
            "pump_ratio": 0.15,
            "volume": 30,
            "bullish_pct": 70.0,
            "bearish_pct": 20.0,
        },
        {
            "ticker": "TSLA",
            "contested_narrative_score": 75,
            "war_score": 0.6,
            "thesis_ratio": 0.35,
            "pump_ratio": 0.4,
            "volume": 55,
            "bullish_pct": 60.0,
            "bearish_pct": 30.0,
        },
        {
            "ticker": "GME",
            "contested_narrative_score": 40,
            "war_score": 0.25,
            "thesis_ratio": 0.1,
            "pump_ratio": 0.6,
            "volume": 15,
            "bullish_pct": 50.0,
            "bearish_pct": 40.0,
        },
    ]


@pytest.fixture
def neutral_messages() -> List[Dict[str, Any]]:
    """Messages with balanced Bullish/Bearish sentiment — war_score ~0."""
    return [
        {"body": "AAPL is fairly valued right now", "sentiment": "Bullish",
         "user": {"account_age_days": 500, "followers": 100}},
        {"body": "AAPL looks neutral to me", "sentiment": "Bearish",
         "user": {"account_age_days": 600, "followers": 200}},
        {"body": "Holding AAPL long term", "sentiment": "Bullish",
         "user": {"account_age_days": 700, "followers": 50}},
        {"body": "AAPL might dip next week", "sentiment": "Bearish",
         "user": {"account_age_days": 400, "followers": 10}},
        {"body": "AAPL is ok", "sentiment": "Neutral",
         "user": {"account_age_days": 300, "followers": 5}},
    ]


@pytest.fixture
def opposing_messages() -> List[Dict[str, Any]]:
    """Highly contested messages — should produce war_score > 0.3."""
    return [
        {"body": "TO THE MOON!! AAPL will 10x EASY MONEY!!",
         "sentiment": "Bullish",
         "user": {"account_age_days": 100, "followers": 10}},
        {"body": "AAPL is a complete scam, short it to zero",
         "sentiment": "Bearish",
         "user": {"account_age_days": 200, "followers": 5}},
        {"body": "MOONSHOT! Don't sleep on AAPL! ROCKET!",
         "sentiment": "Bullish",
         "user": {"account_age_days": 50, "followers": 2}},
        {"body": "AAPL is going bankrupt, total garbage",
         "sentiment": "Bearish",
         "user": {"account_age_days": 300, "followers": 20}},
        {"body": "This is the squeeze of a lifetime!!",
         "sentiment": "Bullish",
         "user": {"account_age_days": 10, "followers": 1}},
        {"body": "Overvalued piece of garbage, short it hard",
         "sentiment": "Bearish",
         "user": {"account_age_days": 500, "followers": 50}},
        {"body": "LAMBO! ROCKETS 🚀🚀 EASY MONEY!",
         "sentiment": "Bullish",
         "user": {"account_age_days": 30, "followers": 8}},
        {"body": "Complete disaster, bagholders everywhere",
         "sentiment": "Bearish",
         "user": {"account_age_days": 400, "followers": 100}},
    ]