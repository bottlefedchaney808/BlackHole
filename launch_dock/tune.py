"""Walk-forward from the dock: build the job, read its report, adopt a winner.

The job itself is `chart_app.backtest_runner --stage tune`, the same command
the tune-sleeve skill runs by hand, so the dock and the CLI cannot disagree
about what "validated" means. Pure functions here; `server.py` does the I/O.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from launch_dock.launch import PY

# The sleeve's fee rate. The tester's 2bp default turned BTC-PERP 15m's
# +17.67% into +0.02% at 5bp on the same bars -- a tune scored at the wrong
# rate is fiction, so the dock does not offer the knob.
SLEEVE_COST_BPS = 5.0
SHORTS = ("keep", "on", "off")


def report_path(tune_dir: Path, card_id: str, shorts: str) -> Path:
    return tune_dir / f"{card_id}_short_{shorts}.json"


def tune_argv(card: dict, *, shorts: str, seed_from: str, report: Path) -> list[str]:
    """One runner invocation: incumbent vs refit on the same OOS folds."""
    if card.get("seed") not in ("perp", "stocks") or not card.get("instrument"):
        raise ValueError("walk-forward is for perp and stock cards")
    if shorts not in SHORTS:
        raise ValueError(f"shorts must be one of {SHORTS}")
    argv = [
        PY, "-m", "chart_app.backtest_runner",
        "--stage", "tune",
        "--tickers", str(card["instrument"]),
        "--interval", str(card.get("interval") or "5m"),
        "--cost-bps", str(SLEEVE_COST_BPS),
        "--allow-short", shorts,
        "--report", str(report),
    ]
    if seed_from.strip():
        argv += ["--seed-from", seed_from.strip()]
    return argv


def summarize(report: dict[str, Any]) -> dict[str, Any] | None:
    """The one row of a tune report the card shows. None if it has no result."""
    rows = report.get("tune") or []
    row = rows[0] if rows else None
    if not row:
        return None
    if "error" in row:
        return {"error": row["error"]}

    def side(wf: dict) -> dict:
        return {
            "oos_pct": wf.get("oos_total_return_pct"),
            "worst_pct": wf.get("oos_worst_fold_pct"),
            "positive": f"{wf.get('oos_positive_folds')}/{wf.get('oos_folds_scored')}",
            "trades": wf.get("oos_trades"),
        }

    return {
        "ticker": row.get("ticker"),
        "interval": row.get("interval"),
        "cost_bps": row.get("cost_bps"),
        "verdict": row.get("verdict"),
        "delta_pp": row.get("oos_delta_pct"),
        "incumbent": side(row.get("incumbent_oos") or {}),
        "tuned": side(row.get("tuned_oos") or {}),
        "candidate_config": row.get("candidate_config"),
        "candidate_elmo": row.get("candidate_elmo") or {},
        "seed_source": row.get("seed_source"),
        "allow_short": row.get("allow_short"),
    }


def read_summary(path: Path) -> dict[str, Any] | None:
    try:
        return summarize(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def adoption(summary: dict[str, Any], *, existing: dict | None) -> dict[str, Any]:
    """The profile record an ADOPT writes. Refuses anything that is not one.

    Only the tune's own verdict can promote a profile to `walk_forward`; a
    KEEP INCUMBENT means the running tune is already the better one.
    """
    if summary.get("verdict") != "ADOPT":
        raise ValueError(f"verdict is {summary.get('verdict')!r}, not ADOPT")
    config = summary.get("candidate_config")
    if not config:
        raise ValueError("report has no candidate config")
    tuned = summary["tuned"]
    note = (
        f"walk-forward tuned {datetime.now(UTC):%Y-%m-%d} at cost_bps="
        f"{summary.get('cost_bps')} (sleeve rate) from the launch dock; "
        f"OOS {tuned['oos_pct']:+.2f}% ({tuned['positive']} folds, "
        f"{tuned['trades']} trades) vs incumbent "
        f"{summary['incumbent']['oos_pct']:+.2f}%"
        + (f"; searched from {summary['seed_source']}" if summary.get("seed_source") else "")
    )
    return {
        "config": dict(config),
        "elmo": dict(summary.get("candidate_elmo") or {}),
        "capital": (existing or {}).get("capital"),
        "validation": "walk_forward",
        "metrics": {"oos": tuned, "incumbent_oos": summary["incumbent"]},
        "note": note,
    }


def backup_store(store: Path, backup_dir: Path) -> Path | None:
    """Copy the profile store aside. It is untracked JSON with no undo."""
    if not store.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"chart_app_profiles.{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    shutil.copy2(store, dest)
    return dest
