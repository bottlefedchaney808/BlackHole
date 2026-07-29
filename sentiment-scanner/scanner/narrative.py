"""Contested narrative scoring engine."""
import math
import re
from typing import List, Dict

PUMP_PATTERNS = [
    r'\b(moon|mooning|moonshot)\b', r'\b(to the\s*moon)\b',
    r'\b(10x|100x|1000x)\b', r'\b(lambo|lambos)\b',
    r'\b(rocket|rockets)\b', r'\b(don\'?t\s*sleep)\b',
    r'\b(easy\s*money)\b', r'\b(life\s*changing)\b',
    r'\b(squeeze|squeezing)\b', r'\b(diamond\s*hands|\U0001f48e|\U0001f64c)\b',
]

def pump_score(body: str) -> float:
    lower = body.lower()
    matches = sum(1 for p in PUMP_PATTERNS if re.search(p, lower))
    emoji_count = len(re.findall(r'[\U0001f680\U0001f48e\U0001f64c\U0001f525\U0001f4b0\U0001f4c8\U0001f3af\U0001f4af]', body))
    caps_ratio = sum(1 for c in body if c.isupper()) / max(len(body), 1)
    exclamation = body.count('!') / max(len(body), 1)
    score = (matches * 0.15 + min(emoji_count * 0.05, 0.25) +
             min(caps_ratio * 0.5, 0.2) + min(exclamation * 5, 0.15))
    return min(score, 1.0)

def thesis_depth(body: str) -> float:
    lower = body.lower()
    indicators = 0
    if re.search(r'\d+\.?\d*%', body): indicators += 1
    if re.search(r'\$\d+\.?\d*', body): indicators += 1
    if re.search(r'\b(short|long|position|hold|average|cost basis)\b', lower): indicators += 1
    if re.search(r'\b(earnings|revenue|pe\s*ratio|market\s*cap|valuation)\b', lower): indicators += 1
    if re.search(r'\b(option|call|put|strike|expir)\b', lower): indicators += 1
    if re.search(r'\b(analysis|dd\b|due\s*diligence|thesis|catalyst)\b', lower): indicators += 1
    if len(body) > 200: indicators += 1
    return min(indicators / 5.0, 1.0)

def score_messages(messages: List[Dict]) -> Dict:
    if not messages:
        return {"war_score": 0, "sentiment_divergence": 0, "thesis_ratio": 0,
                "pump_ratio": 0, "avg_account_age_days": 0, "avg_followers": 0,
                "volume": 0, "bullish_pct": 0, "bearish_pct": 0, "neutral_pct": 0,
                "contested_narrative_score": 0}
    bullish = bearish = neutral = thesis_count = pump_count = 0
    total_age = total_followers = 0
    for msg in messages:
        s = msg.get("sentiment")
        if s == "Bullish": bullish += 1
        elif s == "Bearish": bearish += 1
        else: neutral += 1
        if thesis_depth(msg.get("body", "")) > 0.4: thesis_count += 1
        if pump_score(msg.get("body", "")) > 0.5: pump_count += 1
        total_age += msg.get("user", {}).get("account_age_days", 0)
        total_followers += msg.get("user", {}).get("followers", 0)
    n = len(messages)
    pos_ratio = bullish / n
    neg_ratio = bearish / n
    war_score = 2 * min(pos_ratio, neg_ratio)
    avg_sentiment = (bullish - bearish) / n
    sentiment_divergence = abs(avg_sentiment) * math.log10(n + 1)
    cns = 0
    if war_score > 0.3: cns += 30
    if sentiment_divergence > 2.0: cns += 25
    if thesis_count / n > 0.15: cns += 25
    if pump_count / n < 0.3: cns += 10
    if total_age / n > 365: cns += 10
    if n > 50: cns += 10
    if pump_count / n > 0.5: cns -= 20
    return {"war_score": round(war_score, 3), "sentiment_divergence": round(sentiment_divergence, 3),
            "thesis_ratio": round(thesis_count / n, 3), "pump_ratio": round(pump_count / n, 3),
            "avg_account_age_days": round(total_age / n, 1), "avg_followers": round(total_followers / n, 1),
            "volume": n, "bullish_pct": round(100 * pos_ratio, 1),
            "bearish_pct": round(100 * neg_ratio, 1), "neutral_pct": round(100 * neutral / n, 1),
            "contested_narrative_score": cns}
