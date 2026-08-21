#!/usr/bin/env python3
"""
X.com Fintwit Buzz Report Renderer v1.0 — per spec t_e49c9d78 §6

CLI: xcom_buzz_report.py --in <scan.json> --window <id> --out-dir <dir>

Reads scanner JSON output, produces:
  - {YYYY-MM-DD}_{window_id}.md   (markdown report)
  - {YYYY-MM-DD}_{window_id}.json (sidecar with computed fields)
  - LATEST_{window_id}.md          (symlink/copy of latest per-window)
  - LATEST.md                      (symlink/copy of latest overall)
"""
import argparse
import json
import os
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Optional

# ── Topic aggregation ──────────────────────────────────────────────────────

def _aggregate_topics(posts: List[Dict]) -> List[Dict]:
    """Group posts by topic, sum buzz scores, compute net_skew per topic."""
    topic_map = defaultdict(lambda: {"n": 0, "sum_B": 0.0, "bull": 0, "bear": 0, "neutral": 0})
    for p in posts:
        topic = p.get("topic", "untagged")
        m = topic_map[topic]
        m["n"] += 1
        m["sum_B"] += p.get("buzz_score", 0)
        sent = p.get("sentiment", "neutral")
        if sent == "bullish":
            m["bull"] += 1
        elif sent == "bearish":
            m["bear"] += 1
        else:
            m["neutral"] += 1
    result = []
    for topic, m in topic_map.items():
        total = m["n"]
        skew = ((m["bull"] - m["bear"]) / total * 100) if total > 0 else 0
        result.append({
            "topic": topic,
            "n": m["n"],
            "sum_B": round(m["sum_B"], 2),
            "net_skew": int(round(skew)),
        })
    result.sort(key=lambda x: (-x["sum_B"], -x["n"]))
    return result


def _aggregate_cashtags(posts: List[Dict]) -> List[Dict]:
    """Extract cashtags from all posts, aggregate."""
    cashtag_re = re.compile(r'\$([A-Z]{1,5})')
    ct_map = defaultdict(lambda: {"n": 0, "sum_B": 0.0, "bull": 0, "bear": 0, "neutral": 0})
    for p in posts:
        tags = cashtag_re.findall(p.get("text", ""))
        seen = set()
        for t in tags:
            tag = f"${t}"
            if tag in seen:
                continue
            seen.add(tag)
            m = ct_map[tag]
            m["n"] += 1
            m["sum_B"] += p.get("buzz_score", 0)
            sent = p.get("sentiment", "neutral")
            if sent == "bullish":
                m["bull"] += 1
            elif sent == "bearish":
                m["bear"] += 1
            else:
                m["neutral"] += 1
    result = []
    for tag, m in ct_map.items():
        total = m["n"]
        skew = ((m["bull"] - m["bear"]) / total * 100) if total > 0 else 0
        result.append({
            "ticker": tag,
            "n": m["n"],
            "sum_B": round(m["sum_B"], 2),
            "skew": int(round(skew)),
        })
    result.sort(key=lambda x: (-x["sum_B"], -x["n"]))
    return result


def _compute_sentiment(posts: List[Dict]) -> Dict:
    """Compute overall sentiment skew."""
    n_bull = sum(1 for p in posts if p.get("sentiment") == "bullish")
    n_bear = sum(1 for p in posts if p.get("sentiment") == "bearish")
    n_neut = sum(1 for p in posts if p.get("sentiment") == "neutral")
    total = n_bull + n_bear + n_neut
    if total == 0:
        return {"bull_pct": 0, "bear_pct": 0, "neutral_pct": 0, "net_skew": 0}
    bull_pct = round(n_bull / total * 100)
    bear_pct = round(n_bear / total * 100)
    neutral_pct = 100 - bull_pct - bear_pct
    net_skew = bull_pct - bear_pct
    return {"bull_pct": bull_pct, "bear_pct": bear_pct, "neutral_pct": neutral_pct, "net_skew": net_skew}


# ── Markdown report ────────────────────────────────────────────────────────

def _render_report(scan: Dict, topics: List[Dict], cashtags: List[Dict],
                   sentiment: Dict, spikes_text: str, window_id: str) -> str:
    """Render the markdown report per spec §6 template."""
    asof = scan.get("asof_et", "?")
    start = scan.get("lookback_start_et", "?")
    end = scan.get("lookback_end_et", "?")
    source = scan.get("source", "?")
    n_posts = scan.get("data_quality", {}).get("n_posts", 0)
    quality = "partial" if scan.get("data_quality", {}).get("partial", False) else "ok"
    date_str = asof[:10] if asof and len(asof) >= 10 else "unknown"

    lines = []
    lines.append(f"# X.com Fintwit Buzz — {window_id} — {date_str}")
    lines.append(f"asof_et: {asof} | lookback: {start} → {end} | source: {source} | n_posts: {n_posts} | quality: {quality}")
    lines.append("")

    # §1. Top buzz topics
    lines.append("## 1. Top buzz topics")
    lines.append("| rank | topic | n | sum_B | net_skew |")
    lines.append("|---:|---|---:|---:|---:|")
    for i, t in enumerate(topics[:8], 1):
        lines.append(f"| {i} | {t['topic']} | {t['n']} | {t['sum_B']} | {t['net_skew']:+d} |")
    lines.append("")

    # §2. Notable posts (top 10 by B)
    lines.append("## 2. Notable posts (top 10 by B)")
    lines.append("| B | C | EV | @handle | created_et | L/R/RT | text (≤140) | url |")
    lines.append("|---:|---:|---:|---|---|---|---|---|")
    posts = scan.get("posts", [])
    for p in posts[:10]:
        b = p.get("buzz_score", 0)
        c = p.get("credibility", 0)
        ev = p.get("ev", 0)
        handle = p.get("handle", "?")
        cat = p.get("created_at", "")
        # Extract HH:MM from created_at
        if "T" in cat:
            time_part = cat.split("T")[1][:5]
        elif " " in cat:
            time_part = cat.split(" ")[3][:5] if len(cat.split(" ")) > 3 else cat[:5]
        else:
            time_part = cat[:5]
        likes = p.get("likes", 0)
        replies = p.get("replies", 0)
        reposts = p.get("reposts", 0)
        text = (p.get("text", "") or "")[:140]
        text = text.replace("|", "\\|")
        url = p.get("url", "")
        lines.append(f"| {b} | {c} | {ev} | {handle} | {time_part} | {likes}/{replies}/{reposts} | {text} | {url} |")
    lines.append("")

    # §3. Sentiment skew
    lines.append("## 3. Sentiment skew")
    lines.append(f"bull {sentiment['bull_pct']}% | bear {sentiment['bear_pct']}% | neutral {sentiment['neutral_pct']}% | net_skew {sentiment['net_skew']:+d}")
    # One-liner on which topics drive the skew
    if topics:
        bull_topics = [t["topic"] for t in topics if t["net_skew"] > 0][:3]
        bear_topics = [t["topic"] for t in topics if t["net_skew"] < 0][:3]
        drivers = []
        if bull_topics:
            drivers.append(f"bullish: {', '.join(bull_topics)}")
        if bear_topics:
            drivers.append(f"bearish: {', '.join(bear_topics)}")
        if drivers:
            lines.append(f"Drivers: {'; '.join(drivers)}")
    lines.append("")

    # §4. Spikes vs baseline
    lines.append("## 4. Spikes vs baseline")
    lines.append(spikes_text)
    lines.append("")

    # §5. Extracted cashtags
    lines.append("## 5. Extracted cashtags (watchlist)")
    lines.append("| ticker | n | sum_B | skew |")
    lines.append("|---|---:|---:|---:|")
    for ct in cashtags[:15]:
        lines.append(f"| {ct['ticker']} | {ct['n']} | {ct['sum_B']} | {ct['skew']:+d} |")
    lines.append("")

    # §6. Data quality
    lines.append("## 6. Data quality")
    dq = scan.get("data_quality", {})
    lines.append(f"partial: {str(dq.get('partial', False)).lower()}")
    if dq.get("reason"):
        lines.append(f"reason: {dq['reason']}")
    lines.append(f"source: {source}")
    lines.append(f"n_posts: {n_posts}")

    return "\n".join(lines) + "\n"


# ── Baseline / spike detection ────────────────────────────────────────────

def _load_baseline(baseline_path: str) -> Dict:
    """Load baseline data or return empty."""
    if os.path.exists(baseline_path):
        try:
            with open(baseline_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_baseline(baseline_path: str, baseline: Dict):
    """Save baseline data."""
    os.makedirs(os.path.dirname(baseline_path) or ".", exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2)


def _update_baseline(baseline: Dict, window_id: str, date_str: str,
                     n_posts: int, sum_B: float, net_skew: int) -> str:
    """Update baseline and return spike detection text."""
    import math

    if window_id not in baseline:
        baseline[window_id] = []

    sessions = baseline[window_id]
    n_sessions = len(sessions)

    if n_sessions < 5:
        sessions.append({
            "date": date_str,
            "n_posts": n_posts,
            "sum_B": sum_B,
            "net_skew": net_skew,
        })
        # Keep only last 20
        if len(sessions) > 20:
            sessions[:] = sessions[-20:]
        return f"baseline warming (n={len(sessions)}/5), no spike test"

    # Compute stats from existing sessions (before adding current)
    n_vals = [s["n_posts"] for s in sessions]
    b_vals = [s["sum_B"] for s in sessions]
    s_vals = [s["net_skew"] for s in sessions]

    mean_n = sum(n_vals) / len(n_vals)
    sd_n = math.sqrt(sum((x - mean_n) ** 2 for x in n_vals) / len(n_vals)) if len(n_vals) > 1 else 0
    mean_b = sum(b_vals) / len(b_vals)
    sd_b = math.sqrt(sum((x - mean_b) ** 2 for x in b_vals) / len(b_vals)) if len(b_vals) > 1 else 0
    mean_s = sum(s_vals) / len(s_vals)
    sd_s = math.sqrt(sum((x - mean_s) ** 2 for x in s_vals) / len(s_vals)) if len(s_vals) > 1 else 0

    spikes = []
    # Post count spike
    if n_posts > mean_n + 2 * sd_n or n_posts > 2 * mean_n:
        ratio = n_posts / mean_n if mean_n > 0 else float("inf")
        spikes.append(f"n_posts: today={n_posts}, mean={mean_n:.1f}, sd={sd_n:.1f}, ratio={ratio:.1f}x")
    # Buzz score spike
    if sum_B > mean_b + 2 * sd_b or sum_B > 2 * mean_b:
        ratio = sum_B / mean_b if mean_b > 0 else float("inf")
        spikes.append(f"sum_B: today={sum_B:.1f}, mean={mean_b:.1f}, sd={sd_b:.1f}, ratio={ratio:.1f}x")
    # Skew shift
    if abs(net_skew - mean_s) >= 25:
        spikes.append(f"net_skew shift: today={net_skew:+d}, mean={mean_s:+.1f}, sd={sd_s:.1f}")

    # Append current session
    sessions.append({
        "date": date_str,
        "n_posts": n_posts,
        "sum_B": sum_B,
        "net_skew": net_skew,
    })
    if len(sessions) > 20:
        sessions[:] = sessions[-20:]

    if spikes:
        return "\n".join(spikes)
    return "none"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="X.com Fintwit Buzz Report Renderer v1.0")
    parser.add_argument("--in", "-i", required=True, dest="scan_json", help="Input scanner JSON path")
    parser.add_argument("--window", "-w", required=True, choices=["morning", "mid-day", "power-hour"],
                        help="Window ID")
    parser.add_argument("--out-dir", "-d", required=True, help="Output directory")
    args = parser.parse_args()

    # Read scanner JSON
    with open(args.scan_json, "r", encoding="utf-8") as f:
        scan = json.load(f)

    posts = scan.get("posts", [])
    date_str = scan.get("asof_et", "")[:10] if scan.get("asof_et") else "unknown"
    n_posts = len(posts)
    window_id = args.window

    # Aggregate
    topics = _aggregate_topics(posts)
    cashtags = _aggregate_cashtags(posts)
    sentiment = _compute_sentiment(posts)

    # Sum_B for baseline
    sum_B = round(sum(p.get("buzz_score", 0) for p in posts), 2)
    net_skew = sentiment["net_skew"]

    # Baseline / spikes
    baseline_path = os.path.join(args.out_dir, "_baseline.json")
    baseline = _load_baseline(baseline_path)
    spikes_text = _update_baseline(baseline, window_id, date_str, n_posts, sum_B, net_skew)
    _save_baseline(baseline_path, baseline)

    # Render report
    report = _render_report(scan, topics, cashtags, sentiment, spikes_text, window_id)

    # Build sidecar JSON (scanner data + computed fields)
    sidecar = dict(scan)
    sidecar["computed"] = {
        "topics": topics,
        "cashtags": cashtags[:15],
        "sentiment": sentiment,
        "spikes_text": spikes_text,
    }

    # Write outputs
    os.makedirs(args.out_dir, exist_ok=True)
    md_path = os.path.join(args.out_dir, f"{date_str}_{window_id}.md")
    json_path = os.path.join(args.out_dir, f"{date_str}_{window_id}.json")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[ok] Report written to {md_path}", file=sys.stderr)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(sidecar, f, indent=2, ensure_ascii=False)
    print(f"[ok] Sidecar written to {json_path}", file=sys.stderr)

    # LATEST_{window}.md
    latest_window = os.path.join(args.out_dir, f"LATEST_{window_id}.md")
    shutil.copy2(md_path, latest_window)
    print(f"[ok] LATEST_{window_id}.md updated", file=sys.stderr)

    # LATEST.md (overall latest)
    latest_all = os.path.join(args.out_dir, "LATEST.md")
    shutil.copy2(md_path, latest_all)
    print(f"[ok] LATEST.md updated", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
