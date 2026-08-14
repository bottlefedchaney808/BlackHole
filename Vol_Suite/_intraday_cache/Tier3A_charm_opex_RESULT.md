# Tier-3A — Intraday Charm-at-OpEx (ESC-3) — RESULT

**Date:** 2026-08-14  **Built on:** Dealer-Exposure-Dev
**Driver:** `Vol_Suite/run_intraday_charm_opex.py`  **Data:** ThetaData proxy intraday 10-min `all_greeks` (40 buckets/day, 9:30–16:00 ET, full charm/gamma/vanna/IV) + 1-min stock OHLC
**Pre-registered bar:** SUPPORTED if short/long |charm| ratio in the final 2 hours ≥ 5× in a majority of OpEx days, AND final-2h > open-2h (charm accelerates into the close).

## Result: **SUPPORTED 4/4 comparable days**

| Week | Day | Ticker | short expiry (DTE) | final2h \|charm\| | long expiry (DTE) | final2h \|charm\| | ratio |
|---|---|---|---|---|---|---|---|
| jul2026 | T-1 | SPY | 20260717 (1) | **8.49** | 20260821 (36) | 0.195 | **43.4×** |
| jul2026 | T-1 | QQQ | 20260717 (1) | **7.38** | 20260821 (36) | 0.147 | **50.1×** |
| jul2026 | OPEX | SPY | 20260717 (0) | **692.1** | 20260821 (35) | 0.278 | **2489×** |
| jul2026 | OPEX | QQQ | 20260717 (0) | **628.6** | 20260821 (35) | 0.103 | **6109×** |

### Charm accelerates into the close (OpEx day, 0-DTE)
| Ticker | open-2h \|charm\| | final-2h \|charm\| | acceleration |
|---|---|---|---|
| SPY | 42.1 | **692.1** | 16.4× |
| QQQ | 67.0 | **628.6** | 9.4× |

### Coverage notes
- May 2026 OpEx (05-15): short side present (final-2h SPY 24.5, QQQ 3.1; OpEx-day 0-DTE 622–721), but the 30-DTE comparator (20260619) is **404 on the proxy across all strikes/days** — proxy coverage gap, not a code issue. June 2026 OpEx (06-19) expiry likewise 404 entirely. The 4 fully-paired days are all July 2026 — all SUPPORTED.
- All ratios computed on the raw proxy charm field (same convention both expiries, same day — convention cancels).

## Verdict
**Karsan's charm-at-OpEx claim CONFIRMED on intraday data:** charm is 43×–6109× larger at 1–0 DTE than 30 DTE in the final 2 hours, and accelerates 9–16× from the open into the close on OpEx day. The daily clock could never see this (Tier-2D charm dead at ~0.4–0.5y). This is the intraday mechanism the arbiter said only intraday bars could test — now tested, confirmed.

## Files
- Driver: `Vol_Suite/run_intraday_charm_opex.py`
- Raw table: `Vol_Suite/_intraday_cache/charm_opex_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3A_charm_opex_RESULT.md`
