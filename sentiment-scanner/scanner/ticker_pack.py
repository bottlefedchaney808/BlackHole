"""Highlighted ticker-pack cache/export for downstream model handoff."""
import csv
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import config
from shared.artifact_paths import to_rel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_manifest(path: str) -> Dict:
    if not os.path.exists(path):
        return {"version": 1, "updated_at": "", "packs": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("packs"), list):
            return data
    except Exception:
        pass
    return {"version": 1, "updated_at": "", "packs": []}


def _save_manifest(path: str, manifest: Dict) -> None:
    manifest["updated_at"] = _utc_now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=True, indent=2)


def _entry_is_fresh(entry: Dict, ttl_days: int) -> bool:
    ts = entry.get("last_updated_at") or entry.get("created_at")
    if not ts:
        return False
    try:
        updated = datetime.fromisoformat(ts)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        # Strict >, not >=: at ttl_days=0 the freshness window has zero width,
        # so nothing should count as fresh -- including an entry timestamped
        # this instant. `>=` let ttl=0 admit an equal timestamp, which is
        # reachable in practice on platforms where two back-to-back
        # _utc_now() calls can return the identical value (observed on
        # Windows; masked on Linux by finer wall-clock resolution).
        return updated > (_utc_now() - timedelta(days=ttl_days))
    except ValueError:
        return False


def export_alert_group(
    alerts: List[Dict],
    group_name: str = "cns-threshold-alerts",
    source_run_id: Optional[str] = None,
    social_sources: Optional[List[str]] = None,
) -> Dict:
    if not alerts:
        return {}
    os.makedirs(config.HIGHLIGHT_PACKS_DIR, exist_ok=True)
    now = _utc_now()
    source_run_id = source_run_id or now.strftime("%Y%m%dT%H%M%SZ")
    social_sources = social_sources or ["stocktwits"]
    ranked = sorted(
        alerts,
        key=lambda a: (_safe_int(a.get("contested_narrative_score")), _safe_float(a.get("war_score"))),
        reverse=True,
    )
    tickers = [str(a.get("ticker", "")).upper() for a in ranked if a.get("ticker")]
    if not tickers:
        return {}
    fingerprint_source = "|".join(sorted(set(tickers)))
    fingerprint = hashlib.sha1(f"{group_name}|{fingerprint_source}".encode("utf-8")).hexdigest()[:12]
    group_id = f"{group_name}-{fingerprint}"
    day_dir = os.path.join(config.HIGHLIGHT_PACKS_DIR, now.strftime("%Y%m%d"))
    os.makedirs(day_dir, exist_ok=True)
    json_path = os.path.join(day_dir, f"{group_id}.json")
    csv_path = os.path.join(day_dir, f"{group_id}.csv")

    pack_tickers = []
    for idx, row in enumerate(ranked, start=1):
        pack_tickers.append({
            "symbol": str(row.get("ticker", "")).upper(),
            "rank": idx,
            "cns": _safe_int(row.get("contested_narrative_score")),
            "war_score": round(_safe_float(row.get("war_score")), 3),
            "thesis_ratio": round(_safe_float(row.get("thesis_ratio")), 3),
            "pump_ratio": round(_safe_float(row.get("pump_ratio")), 3),
            "volume": _safe_int(row.get("volume")),
            "bullish_pct": round(_safe_float(row.get("bullish_pct")), 1),
            "bearish_pct": round(_safe_float(row.get("bearish_pct")), 1),
            "confidence": round(
                min(1.0, (0.5 * (_safe_int(row.get("contested_narrative_score")) / 100.0)) + (0.5 * min(1.0, _safe_int(row.get("volume")) / 100.0))),
                3,
            ),
            "social_sources": social_sources,
        })

    thesis_summary = f"{len(pack_tickers)} highlighted tickers crossing CNS threshold"
    pack = {
        "schema_version": 1,
        "version": 1,
        "group_id": group_id,
        "group_name": group_name,
        "created_at": now.isoformat(),
        "source_run_id": source_run_id,
        "priority": "high",
        "thesis_summary": thesis_summary,
        "tickers": pack_tickers,
        "downstream_hints": {
            "volatility_suite": True,
            "var_suite": True,
            "options_suite": False,
        },
    }

    from shared.schemas import validate_sentiment_pack
    validate_sentiment_pack(pack)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=True, indent=2)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "symbol", "rank", "cns", "war_score", "thesis_ratio", "pump_ratio",
                "volume", "bullish_pct", "bearish_pct", "confidence",
            ],
        )
        writer.writeheader()
        for row in pack_tickers:
            writer.writerow({k: row.get(k) for k in writer.fieldnames})

    # Route json_path and csv_path through to_rel() for portability
    rel_json_path = to_rel(json_path)
    rel_csv_path = to_rel(csv_path)

    manifest = _load_manifest(config.HIGHLIGHT_PACK_MANIFEST)
    fresh_existing = []
    for item in manifest.get("packs", []):
        if isinstance(item, dict) and _entry_is_fresh(item, config.HIGHLIGHT_PACK_TTL_DAYS):
            fresh_existing.append(item)
    manifest_entry = {
        "group_id": group_id,
        "group_name": group_name,
        "fingerprint": fingerprint,
        "created_at": now.isoformat(),
        "last_updated_at": now.isoformat(),
        "ticker_count": len(pack_tickers),
        "priority": "high",
        "source_run_id": source_run_id,
        "thesis_summary": thesis_summary,
        "tickers": [row["symbol"] for row in pack_tickers],
        "json_path": rel_json_path,
        "csv_path": rel_csv_path,
    }
    deduped = [manifest_entry]
    for item in fresh_existing:
        if item.get("group_id") != group_id:
            deduped.append(item)
    manifest["packs"] = deduped[: config.HIGHLIGHT_PACK_MAX_MANIFEST_ENTRIES]
    _save_manifest(config.HIGHLIGHT_PACK_MANIFEST, manifest)
    return {
        "group_id": group_id,
        "json_path": rel_json_path,
        "pack_json_path": rel_json_path,
        "csv_path": rel_csv_path,
        "manifest_path": config.HIGHLIGHT_PACK_MANIFEST,
        "ticker_count": len(pack_tickers),
        "ranked_tickers": [row["symbol"] for row in pack_tickers],
    }
