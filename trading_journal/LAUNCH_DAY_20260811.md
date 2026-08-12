# LAUNCH DAY — 2026-08-11 checklist

Orders placed 08-10 22:38 ET (GFD limits, regular hours). Execute this checklist at/after market open.

## Pre-open (8:30–9:30 AM CT)
- [ ] Confirm services: quant bridge 8765 (200), dashboard 8787 (200), TradingView CDP (tv_health_check → cdp_connected), ThetaData probe
- [ ] Pre-market reads: quotes for SOUN/UUUU/TGB/NEXA + overnight gaps vs limits
- [ ] If a name gaps ABOVE its limit → likely no fill; decide whether to chase (default: no — discipline, log the miss)

## At open (9:30 ET)
- [ ] Fill check: `get_equity_orders(account 751521659, placed_agent=agentic)` — state filled/partially_filled/queued
- [ ] Record actual fill prices in `ledger.csv` (update status queued → filled, note avg price)
- [ ] Verify positions: `get_equity_positions(751521659)` — qty × 4 matches

## Position monitoring (day)
- [ ] Mental stops: SOUN < 6.40, UUUU < 13.00, TGB < 7.50 (EMA20), NEXA < 13.40 (EMA20)
- [ ] Cadence: quote check + TradingView chart read (RSI/EMA per Jason's method) at 10:30, 12:00, 14:00 CT
- [ ] No options, no margin. Cash-account rule: round-trips need settled funds (T+1)

## EOD (after 15:00 CT)
- [ ] Daily journal entry 2026-08-11.md: fills, P&L vs entry, lessons, any stop hits
- [ ] Update ledger + commit trading_journal/
- [ ] Covered-call accumulation note: 100-share costs SOUN ~$746 / UUUU ~$1,429 / TGB ~$877 / NEXA ~$1,485 (NOT now)

## Automated
- [ ] 9:40 AM CT one-shot cron: fill check + `trading_journal/fills_20260811.md` (delivered to desktop)
