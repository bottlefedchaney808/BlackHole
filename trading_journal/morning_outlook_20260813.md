# Morning Outlook — 2026-08-13

**Generated:** 2026-08-13T09:10:29 | **Source:** Direction 5-signal engine (ThetaData-backed) + live ThetaData chains
**Watchlist:** SPY, QQQ, AAPL, NVDA, MSFT, AMD, MU, TSLA, META, GOOGL + positions (SOUN, UUUU, TGB, KOS)

> ⚠️ **IV data caveat:** the ATM-IV column was pulled via the same nearest-expiry-only chain path used by powerhour_prep, but several readings (SPY 25%, AMD 297%, MU 141%, TGB 328%) are clearly illiquid-strike/noise and conflict with the desk-note's VIX~14.7 / ~10-13% SPY readings. **Do not trade off these IV numbers** — treat the Direction scores/signals as the signal, IV as low-confidence. The 1d-move column inherits that noise.

---

## 1. Per-ticker table (Direction 5-signal + spot)

| Ticker | Spot | DirScore | Conv | whale | wave3 | squeeze | trend | liq | Call |
|---|---|---|---|---|---|---|---|---|---|
| **NVDA** | 226.33 | **4** | **HIGH** | ✅ | ✅ | ✅ | ✅ | ❌ | **BULL — BUY RULE HIT** |
| **SPY** | 778.21 | 2 | NONE | ❌ | ✅ | ❌ | ✅ | ❌ | NEUTRAL-bull drift |
| **QQQ** | 731.82 | 1 | NONE | ❌ | ✅ | ❌ | ❌ | ❌ | NEUTRAL |
| AAPL | 304.97 | 1 | NONE | ✅ | ❌ | ❌ | ❌ | ❌ | NEUTRAL (whale-only, weak) |
| MSFT | 500.48 | 1 | NONE | ❌ | ✅ | ❌ | ❌ | ❌ | NEUTRAL |
| AMD | 493.35 | 1 | NONE | ✅ | ❌ | ❌ | ❌ | ❌ | NEUTRAL (whale-only) |
| MU | 936.62 | 1 | NONE | ✅ | ❌ | ❌ | ❌ | ❌ | NEUTRAL (whale-only) |
| TSLA | 332.63 | 1 | NONE | ❌ | ❌ | ❌ | ✅ | ❌ | NEUTRAL (trend-only) |
| META | 589.27 | **0** | NONE | ❌ | ❌ | ❌ | ❌ | ❌ | **FLAT — no signal** |
| GOOGL | 346.37 | 2 | NONE | ✅ | ✅ | ❌ | ❌ | ❌ | NEUTRAL-bull (whale+wave3) |
| *SOUN* (pos) | 7.42 | 1 | NONE | ✅ | ❌ | ❌ | ❌ | ❌ | NEUTRAL (whale-only) |
| *UUUU* (pos) | 14.59 | 2 | NONE | ✅ | ❌ | ✅ | ❌ | ❌ | NEUTRAL (whale+squeeze) |
| *TGB* (pos) | 8.49 | **3** | NONE | ❌ | ✅ | ✅ | ✅ | ❌ | NEUTRAL-strong (no whale = untradeable) |
| *KOS* (pos) | 2.43 | 1 | NONE | ❌ | ✅ | ❌ | ❌ | ❌ | NEUTRAL (wave3-only) |

Rule: **score ≥3 AND whale=True = buy candidate.** Conviction HIGH = whale AND wave3 AND (squeeze OR trend) AND score≥3.

---

## 2. Top-ranked Direction signals (buy rule filter)

1. **NVDA — score 4 / HIGH / whale✅ wave3✅ squeeze✅ trend✅** — the **only** name on the watchlist that satisfies the full buy rule. The AI-semiconductor bid (confirmed in desk note: NVDA +5.5% 1M, near highs) is the cleanest expression today. Buy rule = **initiate/press long NVDA**.
2. **GOOGL — score 2** (whale+wave3) — next strongest watchlist name, but below the ≥3 buy threshold. Watch for squeeze/trend confirmation.
3. **SPY — score 2** (wave3+trend) — market itself shows momentum + trend, no whale flow = drift bias not a trigger.
4. **UUUU — score 2** (whale+squeeze) — position, showing whale+squeeze, close to the line but under 3.

**No other watchlist name clears score≥3.** META is dead flat (0 signals). AAPL/MSFT/AMD/MU/TSLA are all single-signal stragglers.

---

## 3. Position reads (SOUN, UUUU, TGB, KOS)

- **TGB — score 3 but NO whale = conviction NONE.** This is the subtle one: wave3 + squeeze + trend all fire (momentum, volatility compression, uptrend — genuine strength), but the **whale/dealer-flow gate is OFF**, so the engine explicitly refuses to call it tradeable. In a small-account real-money book, this means **respect the position's momentum but do NOT add** until whale flow confirms. It's the strongest of the 4 positions on pure momentum but unconfirmed by whale.
- **UUUU — score 2** (whale+squeeze): dealer flow present, volatility compressing = potential set-up, but no wave3/trend yet. Hold, watch for wave3.
- **SOUN — score 1** (whale only): dealer flow present but nothing confirming direction. Weak — don't add.
- **KOS — score 1** (wave3 only): momentum leg but no whale. Hold, no add.

Net: **no position is a clear add today.** TGB has the strongest technicals but is whale-gated off; UUUU is the closest to a set-up but needs a wave3/trend leg to confirm.

---

## 4. Market-level morning outlook

**Context (from desk note):** VIX ~14.7 (1st–5th percentile), SPY ATM straddle at a literal ~$5 / ~0.65% 1-day corridor, flat-to-inverted skew = *nobody hedged*. SPY +0.73% / QQQ +1.09% on the day, both near all-time highs. Rotation is **real but narrower than the headline**: AI-chips (NVDA/TSM/AVGO) + crude energy (XOM/CVX) are *bid*; the actual "payers" being sold are **power/electricity (VST, CEG)**. CPI = energy + freight cost-push (headline 3.4% vs core 2.5%) = bear-steepener tailwind, tame core caps tech damage.

**Dealer/vol regime:** post-OPEX vol expansion likely into Oct-Nov, near-term supported. With equity at highs and nobody hedging, dealers are net short-vol/long-gamma on the way up. That is the fragile leg — a sharp move gets amplified because hedges are thin. IV is at the floor, so **long convexity is cheap and preferred; do NOT press short vol.**

**Actionable bias for the day:**
- **Direction confirms the desk-note rotation → NVDA is the single highest-conviction long (score 4 / HIGH).** This is not just a macro read — the 5-signal engine independently flags whale + wave3 + squeeze + trend on NVDA. **Long NVDA (or hold NVDA exposure) is the top call.**
- **Broad market: NEUTRAL-to-mildly-bullish drift, not a fresh trigger.** SPY/QQQ show momentum+trend but no whale flow — they'll grind higher in the low-vol regime but there's no dealer-flow confirmation to press.
- **Do NOT press short vol** (IV at floor, cheap convexity, thin hedging). If holding premium, keep long-convexity posture into the CPI event/Oct-Nov expansion.
- **Avoid META** (0/5, dead flat). AMD/MU carry whale flow but no directional confirmation — momentum hasn't re-established after AMD's −8.7% 1M fade.
- **Rotation expression:** NVDA over the power-payers (VST short stays the cleanest sector fade per desk note, but that's not a Direction-buy; it's a sector thesis).

---

## BOTTOM LINE — today

1. **Top call: LONG NVDA** — only name clearing score≥3 + whale (4/5, HIGH). Highest-conviction signal on the board, aligned with the semis-bid rotation.
2. **Market bias: NEUTRAL-to-mildly-bullish drift** on SPY/QQQ (momentum+trend, no whale trigger). Near-term supported, post-OPEX vol expansion expected into Oct-Nov.
3. **Positions: no add.** TGB strongest technically (3/5) but whale-gated OFF = respect momentum, don't add. UUUU closest to a set-up (whale+squeeze), needs wave3/trend to confirm. SOUN/KOS weak — hold only.
4. **Vol regime:** IV at the floor, nobody hedged, short-vol inventory fragile — prefer long convexity, don't press short vol. VIX close >18–20 = first vol-shock break.
