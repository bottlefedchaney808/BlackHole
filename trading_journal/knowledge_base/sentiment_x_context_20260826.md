# Sentiment Scanner + X Buzz Context — past two days
**Compiled: 2026-08-26 ~06:30 CDT** · For handoff to Claude · Research only, no orders/trade execution

---

## 1. Sentiment Scanner Backtest (recent run)

Run via `Vol_Suite/sentiment_backtest.py::run_sentiment_backtest(sentiment-scanner/data, forward_days=5)` on 2026-08-26.

| Metric | Value |
|---|---|
| Packs analyzed | 92 |
| Total signals | 193 |
| Date range | 2026-07-24 → 2026-08-25 |
| Forward window | 5 trading days |
| Hit rate (top vs bottom CNS) | 0.034 |
| Sharpe (long-only top CNS) | 1.21 |
| CNS ↔ fwd-return correlation | 0.005 |
| Top-quartile CNS names | 60 (list below) |
| Bottom-quartile CNS names | 0 (empty — bottom quartile had no forward-resolvable names) |

**Top-quartile CNS names (highest contested-narrative score):**
AAOI, AAPL, AEHR, AMD, AMKR, AMZN, ASTS, BE, BROS, CAPR, CAVA, CLS, CRWV, CVX, DASH, DJT, DUOT, ETH, FN, GLXY, GRRR, INTC, IONQ, JPM, LULU, LUNR, MCD, META, MSTR, MU, NBIS, NKE, NUAI, NVCR, NVDA, OCUL, ORCL, PLTR, QQQ, RDDT, REPL, SLS, SNAP, SNDK, SNDQ, SOXL, SPCX, SPY, STX, TLT, TSLA, TSSI, UCO, USO, UVXY, VCX, VST

**Read:** CNS-return correlation ≈ 0.005 (essentially zero) — the contested-narrative score is **not** a predictive signal for 5-day forward returns in this sample. Positive Sharpe on top-CNS long-only is a beta/selection artifact, not a clean edge. Bottom quartile empty → hit-rate top-vs-bottom comparison is degenerate (no denominator).

---

## 2. Highlighted Ticker Packs — per day (past two days)

Source: `sentiment-scanner/data/exports/highlighted_ticker_packs/<YYYYMMDD>/*.json`, deduped by (day, symbol), latest `created_at` kept. `cns` = contested-narrative score, `war` = war_score, `vol` = volume signal, `bull`/`bear` = % sentiment.

### 2026-08-24 (30 symbols)
| Sym | CNS | war | vol | bull% | bear% | src |
|---|---|---|---|---|---|---|
| ICP.X | 85 | 0.33 | 60 | 16.7 | 20.0 | stocktwits |
| DJT | 60 | 0.60 | 60 | 30.0 | 30.0 | stocktwits |
| MSTR | 60 | 0.33 | 60 | 46.7 | 16.7 | stocktwits |
| SLS | 60 | 0.53 | 60 | 50.0 | 26.7 | stocktwits |
| ALGO.X | 60 | 0.47 | 60 | 73.3 | 23.3 | stocktwits |
| NVDA | 60 | 0.33 | 60 | 23.3 | 16.7 | stocktwits |
| MRVL | 60 | 0.33 | 60 | 16.7 | 40.0 | stocktwits |
| MBLY | 60 | 0.60 | 60 | 30.0 | 60.0 | stocktwits |
| BTC.X | 60 | 0.53 | 60 | 50.0 | 26.7 | stocktwits |
| RGNX | 60 | 0.33 | 60 | 33.3 | 16.7 | stocktwits |
| F | 60 | 0.33 | 60 | 20.0 | 16.7 | stocktwits |
| BA | 60 | 0.33 | 60 | 16.7 | 16.7 | stocktwits |
| ASTS | 60 | 0.40 | 60 | 43.3 | 20.0 | stocktwits |
| XNDU | 60 | 0.47 | 60 | 26.7 | 23.3 | stocktwits |
| SMCI | 60 | 0.47 | 60 | 33.3 | 23.3 | stocktwits |
| XPEV | 55 | 0.27 | 60 | 20.0 | 13.3 | stocktwits |
| PDD | 55 | 0.27 | 60 | 33.3 | 13.3 | stocktwits |
| HD | 55 | 0.07 | 60 | 10.0 | 3.3 | stocktwits |
| RUM | 55 | 0.00 | 60 | 80.0 | 0.0 | stocktwits |
| VERA | 55 | 0.00 | 60 | 40.0 | 0.0 | stocktwits |
| BTCT.X | 55 | 0.00 | 60 | 63.3 | 0.0 | stocktwits |
| IMMX | 55 | 0.00 | 60 | 20.0 | 0.0 | stocktwits |
| UPS | 55 | 0.07 | 60 | 46.7 | 3.3 | stocktwits |
| GRRR | 55 | 0.13 | 60 | 66.7 | 6.7 | stocktwits |
| BABA | 55 | 0.00 | 60 | 43.3 | 0.0 | stocktwits |
| USAR | 55 | 0.00 | 60 | 66.7 | 0.0 | stocktwits |
| DIA | 55 | 0.13 | 60 | 6.7 | 13.3 | stocktwits |
| SBET | 55 | 0.00 | 60 | 46.7 | 0.0 | stocktwits |
| SIMO | 55 | 0.07 | 60 | 40.0 | 3.3 | stocktwits |
| V | 55 | 0.00 | 60 | 30.0 | 0.0 | stocktwits |

### 2026-08-25 (4 symbols)
| Sym | CNS | war | vol | bull% | bear% | src |
|---|---|---|---|---|---|---|
| MBLY | 60 | 0.53 | 60 | 26.7 | 70.0 | stocktwits |
| INTU | 60 | 0.47 | 60 | 30.0 | 23.3 | stocktwits |
| TSLA | 60 | 0.40 | 60 | 20.0 | 20.0 | stocktwits |
| DKS | 60 | 0.33 | 60 | 20.0 | 16.7 | stocktwits |

**Note:** Aug 25 pack set is thin (4 symbols vs 30 on Aug 24) — scanner output volume dropped; treat Aug 25 as low-coverage.

---

## 3. X Buzz (xAI Grok x_search pulls — past two days)

Source: `trading_journal/x_buzz/*.json` (native X via SuperGrok OAuth). Post times are **scan time, not original post time**. All posts credibility 0.5, engagement parsed from cited text.

### 2026-08-25 mid-day (asof 12:00 ET, 15 posts)
**Cashtags:**
| Ticker | n | buzz sum | skew |
|---|---|---|---|
| $NVDA | 8 | 49,059 | -12 |
| $SPY | 6 | 43,335 | -17 |
| $QQQ | 2 | 11,826 | 0 |
| $VIX | 2 | 8,654 | 0 |
| $SOX | 1 | 6,750 | 0 |
| $JPM | 1 | 6,750 | 0 |
| $DIA | 1 | 5,076 | 0 |
| $AVGO | 1 | 3,942 | 0 |

**Sentiment:** bull 0% / bear 7% / neutral 93% · net skew -7 · spikes: baseline warming (n=1/5), no spike test

**Top narratives:**
- **NVDA / OpenAI "Jalapeño" chip** — OpenAI claims new chip (built with $AVGO) beat $NVDA GB300/Blackwell on power efficiency & response speed (WhaleInsider, SemiAnalysis detail). Central theme.
- **Raymond James PT raise** — NVDA PT $352 from $330, Strong Buy; CPU revenue leadership thesis.
- **SPY below 20-day MA** — first time since July (Barchart).
- **CNN Fear/Greed** — not at Extreme Greed in 13+ months (Mr_Derivatives).

### 2026-08-26 morning (asof 07:30 ET, 15 posts)
**Cashtags:**
| Ticker | n | buzz sum | skew |
|---|---|---|---|
| $NVDA | 7 | 167,495 | -29 |
| $VIX | 4 | 130,896 | -25 |
| $SPY | 6 | 77,936 | -17 |
| $MSFT | 2 | 58,361 | 0 |
| $AAPL | 1 | 52,745 | 0 |
| $SPX | 2 | 20,696 | 0 |
| $QQQ | 1 | 9,855 | 0 |

**Sentiment:** bull 0% / bear 27% / neutral 73% · net skew -27 · spikes: baseline warming (n=2/5), no spike test

**Top narratives:**
- **NVDA red streak** — 6 straight red days, longest losing streak in 4 years (Barchart); 8 red candles SPY worst since late 2018 (TrendSpider).
- **VIX at YTD low** — "no fear left in the market" (Barchart).
- **NVDA $6B China deal** — plans to build one of most powerful open-weight AI models, competing with DeepSeek (WSJ via WhaleInsider).
- **NVDA earnings preview** — Bitget_TradFi earnings preview + AI giants breakdown; Raymond James CPU thesis repeated.
- **NVDA Groq 3 LPX slide** — 3,431 tokens/sec, "faster than Cerebras" (benitoz).
- **SPY low volume** — 27.4M shares, lowest since Feb 2025 (Barchart); dead market chatter.
- **DTCC $114T** — securities oversight headline (coinbureau).

---

## 4. Cross-source observations (for Claude to reason over)

- **NVDA is the dominant theme across all three sources** (sentiment pack Aug 24, X buzz both days). Bearish skew building on X (0%→-29% over the window) while sentiment-scanner CNS flags it as contested (CNS 60).
- **Sentiment scanner CNS is not predictive** (corr 0.005) — do not treat high-CNS names as directional picks; they are attention/controversy flags, not alpha.
- **X buzz skew is increasingly negative** (net -7 → -27) driven by NVDA losing-streak + VIX-low complacency narratives; this is sentiment/positioning chatter, not fundamental data.
- **Coverage gaps:** Aug 25 sentiment pack only 4 symbols; X post times are scan-time (not original); all X posts credibility 0.5.

---

## 5. Limitations
- No direct primary-source confirmation (IR/SEC) for any X-buzz narrative; all from xAI Grok x_search, engagement parsed from cited text.
- Sentiment backtest bottom-quartile empty → top-vs-bottom hit rate is not a valid comparison.
- Post "created_at" = scan time, not original post time (X buzz).
- Research only. **No orders placed. No trade execution. No execution instructions.**
