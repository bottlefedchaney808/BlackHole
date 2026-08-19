#!/usr/bin/env python3
"""rh_monitor.py — deterministic read-only position monitor for Agentic (751521659).

Calls the Robinhood MCP endpoint DIRECTLY over HTTP (no LLM, no Hermes MCP-tool
session), so it runs reliably as a no_agent cron. Refreshes the OAuth token if
needed, pulls quotes + portfolio + positions, checks each holding against its
mental stop (2% alert band) and scale-out trigger (+8% from avg), appends a
checkpoint to monitor_<date>.md, and writes an ALERT_<HHMM>_<date>.md file on a
trip. Prints NOTHING when all is well (silent cron); prints alert text when a
trip fires.

One exception to READ-ONLY: the UUUU_STOP_SCALE_IN block below is a pre-
authorized, one-shot contingent trade (see trading_journal/pten_momentum_20260818.md)
— if the UUUU GTC stop order fills, proceeds go straight into a PTEN buy, no
LLM/user confirmation needed. Everything else here never places, modifies, or
cancels an order.

POSITIONS (qty, avg, mental stop, scale-out trigger):
  UUUU 3 @14.17 stop 14.00 (real GTC stop live, order 6a845c49-...) scale>=15.30
  TGB  8 @8.86  stop 7.50 (mental only — no live GTC stop as of 2026-08-18) scale>=9.60
  KOS  3 @2.57  stop 2.20 (mental only — no live GTC stop as of 2026-08-18) scale>=3.00
  PTEN 12 @12.47 stop n/a (discretionary signal-gated exit, see journal)   scale n/a

SOUN was stopped out 2026-08-14 (filled @7.45) — removed from tracking.
"""
import asyncio, datetime as dt, json, os, sys, time

TOKDIR = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens"
TOK = os.path.join(TOKDIR, "robinhood.json")
META = os.path.join(TOKDIR, "robinhood.meta.json")
CLIENT = os.path.join(TOKDIR, "robinhood.client.json")
URL = "https://agent.robinhood.com/mcp/trading"
ACCT = "751521659"
JOURNAL = r"C:\Users\bottl\FinancialDevelopment\trading_journal\monitor_%s.md"
PYRAMID_JOURNAL = r"C:\Users\bottl\FinancialDevelopment\trading_journal\pten_momentum_20260818.md"
SCALE_IN_MARKER = r"C:\Users\bottl\FinancialDevelopment\trading_journal\.uuuu_scale_in_done"

UUUU_STOP_ORDER_ID = "6a845c49-f8d4-4dbf-9934-8f0cc1414e7e"
SPY_800C_OPTION_ID = "43bd34ee-5bc3-4397-80c6-3bc47b43fd7a"

POSITIONS = [  # sym, qty, avg, stop, scale
    ("UUUU", 3, 14.17, 14.00, 15.30),
    ("TGB", 8, 8.86, 7.50, 9.60),
    ("KOS", 3, 2.57, 2.20, 3.00),
    ("PTEN", 12, 12.47, None, None),  # discretionary exit only, no mental stop
]

def _load(p): return json.load(open(p, encoding="utf-8"))
def _save(p, o): json.dump(o, open(p, "w", encoding="utf-8"))

def refresh_token():
    import httpx
    tok, meta, client = _load(TOK), _load(META), _load(CLIENT)
    r = httpx.post(meta["token_endpoint"], data={
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": client["client_id"],
    }, timeout=30)
    r.raise_for_status()
    d = r.json()
    nt = {"access_token": d["access_token"], "token_type": d.get("token_type", "Bearer"),
          "expires_in": d.get("expires_in", 3600), "scope": d.get("scope", ""),
          "refresh_token": d.get("refresh_token", tok["refresh_token"]),
          "expires_at": time.time() + float(d.get("expires_in", 3600))}
    _save(TOK, nt)
    return nt

def get_token():
    tok = _load(TOK)
    if time.time() >= float(tok["expires_at"]) - 120:
        return refresh_token()
    return tok

def _text_block(res):
    for c in res.content:
        if c.type == "text":
            return json.loads(c.text)
    return None

async def call(sess, tool, args):
    res = await sess.call_tool(tool, args)
    return _text_block(res)

def evaluate_position(sym, last, avg, stop, scale):
    """Pure logic for one holding. Returns (checkpoint_line, alert_or_None).
    Alert on: last at/below stop OR within 2% of it; up +8%+ from avg (scale-out).
    Positions with stop=None (e.g. PTEN, discretionary exit) skip stop/scale checks.
    """
    if last is None:
        return f"{sym} n/a", f"ALERT {sym}: no quote"
    if stop is None:
        return f"{sym} {last:.2f} (no mental stop)", None
    dist_pct = (last - stop) / stop * 100 if stop else 0.0
    ret_pct = (last - avg) / avg * 100 if avg else 0.0
    line = f"{sym} {last:.2f}/{stop:.2f}"
    if last <= stop or dist_pct <= 2.0:
        return line, f"ALERT {sym}: last {last:.2f} at/below stop {stop} ({dist_pct:+.1f}%) — stop likely triggered"
    if scale is not None and ret_pct >= 8.0:
        return line, f"ALERT {sym}: up {ret_pct:+.1f}% from avg {avg} (last {last:.2f}) — scale-out candidate (trigger {scale})"
    return line, None

async def check_uuuu_scale_in(sess, alerts, lines):
    """Pre-authorized, one-shot: if the UUUU stop has filled and this hasn't
    fired yet, buy PTEN with the proceeds immediately. See
    trading_journal/pten_momentum_20260818.md 'Funding tranche' section.
    """
    if os.path.exists(SCALE_IN_MARKER):
        return  # already fired, never repeat
    orders = await call(sess, "get_equity_orders", {"account_number": ACCT, "order_id": UUUU_STOP_ORDER_ID})
    order_list = ((orders or {}).get("data", {}) or {}).get("orders", [])
    if not order_list:
        return
    order = order_list[0]
    if order.get("state") != "filled":
        return

    fill_price = float(order.get("average_price") or 0)
    fill_qty = float(order.get("cumulative_quantity") or 0)
    proceeds = fill_price * fill_qty
    if proceeds <= 0:
        alerts.append("ALERT UUUU stop shows filled but proceeds computed as $0 — scale-in skipped, needs manual review")
        return

    q = await call(sess, "get_equity_quotes", {"symbols": ["PTEN"]})
    results = (q or {}).get("data", {}).get("results", [])
    if not results:
        alerts.append("ALERT UUUU stop filled ($%.2f) but PTEN quote unavailable — scale-in skipped, needs manual buy" % proceeds)
        return
    quote = results[0]["quote"]
    ask = float(quote["ask_price"])
    limit_price = round(ask + 0.03, 2)  # marketable limit, small cushion over ask
    shares = int(proceeds // limit_price)
    if shares < 1:
        alerts.append("ALERT UUUU stop filled ($%.2f proceeds) but too little for 1 PTEN share at $%.2f — scale-in skipped" % (proceeds, limit_price))
        return

    await call(sess, "review_equity_order", {
        "account_number": ACCT, "symbol": "PTEN", "side": "buy", "type": "limit",
        "quantity": str(shares), "limit_price": str(limit_price), "time_in_force": "gfd",
    })
    placed = await call(sess, "place_equity_order", {
        "account_number": ACCT, "symbol": "PTEN", "side": "buy", "type": "limit",
        "quantity": str(shares), "limit_price": str(limit_price), "time_in_force": "gfd",
    })
    new_order_id = ((placed or {}).get("data", {}) or {}).get("order", {}).get("id", "unknown")

    msg = (f"UUUU STOP-TRIGGERED SCALE-IN FIRED: UUUU stopped out {fill_qty:.0f} sh @ ${fill_price:.2f} "
           f"(proceeds ${proceeds:.2f}) -> bought {shares} more PTEN @ limit ${limit_price:.2f} "
           f"(order {new_order_id})")
    alerts.append("ALERT " + msg)
    with open(SCALE_IN_MARKER, "w", encoding="utf-8") as f:
        f.write(dt.datetime.now().isoformat() + " " + msg + "\n")

    now = dt.datetime.now()
    try:
        with open(PYRAMID_JOURNAL, "r", encoding="utf-8") as f:
            content = f.read()
        marker = "| Date | Spot | Direction score"
        idx = content.find(marker)
        if idx != -1:
            end_of_header_row = content.find("\n", content.find("\n", idx) + 1) + 1
            new_row = (f"| {now.strftime('%Y-%m-%d %H:%M')} ET | PTEN ${limit_price:.2f} limit | n/a (stop scale-in) | "
                       f"— | **Stop-triggered scale-in**: UUUU {fill_qty:.0f}sh@${fill_price:.2f} -> "
                       f"{shares} more PTEN @ ${limit_price:.2f} | automated via rh_monitor.py cron, order {new_order_id} |\n")
            content = content[:end_of_header_row] + new_row + content[end_of_header_row:]
            with open(PYRAMID_JOURNAL, "w", encoding="utf-8") as f:
                f.write(content)
    except OSError:
        pass  # journal write is best-effort; the alert file + marker are authoritative

async def main():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    tok = get_token()
    headers = {"Authorization": f"{tok['token_type']} {tok['access_token']}"}
    http_client = httpx.AsyncClient(headers=headers, timeout=30)
    now = dt.datetime.now()
    stamp = now.strftime("%H:%M")
    date = now.strftime("%Y%m%d")
    alerts = []
    lines = []
    try:
        async with streamable_http_client(URL, http_client=http_client) as (rs, ws):
            async with ClientSession(rs, ws) as sess:
                await sess.initialize()
                q = await call(sess, "get_equity_quotes", {"symbols": [p[0] for p in POSITIONS]})
                pf = await call(sess, "get_portfolio", {"account_number": ACCT})
                ps = await call(sess, "get_equity_positions", {"account_number": ACCT})
                opt = await call(sess, "get_option_quotes", {"instrument_ids": [SPY_800C_OPTION_ID]})

                await check_uuuu_scale_in(sess, alerts, lines)
    finally:
        await http_client.aclose()

    # build quote map
    quotes = {}
    if q and isinstance(q, dict):
        for r in (q.get("data", {}) or {}).get("results", []):
            quote = r.get("quote", {}) or {}
            sym = quote.get("symbol")
            if sym:
                last = quote.get("last_trade_price")
                try: quotes[sym] = float(last)
                except (TypeError, ValueError): quotes[sym] = None
    total = None
    if pf and isinstance(pf, dict):
        d = pf.get("data", {}) or {}
        try: total = float(d.get("total_value"))
        except (TypeError, ValueError): total = None
    # quantity map from positions
    qty = {}
    if ps and isinstance(ps, dict):
        for p in (ps.get("data", {}) or {}).get("positions", []):
            sym = p.get("symbol")
            try: qty[sym] = float(p.get("quantity", 0))
            except (TypeError, ValueError): qty[sym] = 0.0

    for sym, exp_qty, avg, stop, scale in POSITIONS:
        last = quotes.get(sym)
        q_now = qty.get(sym, exp_qty)
        q_now = int(q_now) if q_now == int(q_now) else q_now
        line, alert = evaluate_position(sym, last, avg, stop, scale)
        lines.append(line)
        if alert:
            alerts.append(alert)
        if q_now != exp_qty:
            alerts.append(f"ALERT {sym}: qty changed {exp_qty}->{q_now} — likely a fill/stop")

    spy_mark = None
    if opt and isinstance(opt, dict):
        results = (opt.get("data", {}) or {}).get("results", [])
        if results:
            try: spy_mark = float(results[0]["quote"]["mark_price"])
            except (KeyError, TypeError, ValueError): spy_mark = None
    spy_line = f"SPY800C {spy_mark:.2f}" if spy_mark is not None else "SPY800C n/a"
    lines.append(spy_line)

    ckpt = f"[{stamp} CT] total=${total if total is not None else 'n/a'} | " + " | ".join(lines) + (f" | ALERT:{len(alerts)}" if alerts else " | ok")
    jpath = JOURNAL % date
    os.makedirs(os.path.dirname(jpath), exist_ok=True)
    with open(jpath, "a", encoding="utf-8") as f:
        f.write(ckpt + "\n")

    if alerts:
        apath = os.path.join(os.path.dirname(jpath), f"ALERT_{now.strftime('%H%M')}_{date}.md")
        with open(apath, "w", encoding="utf-8") as f:
            f.write(f"# ALERT {now.isoformat(timespec='seconds')}\n" + "\n".join(alerts) + f"\ntotal=${total}\n")
        print("\n".join(alerts))
        print(f"total=${total}")
    # else: print nothing -> silent cron tick

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
    except Exception as e:
        # fail closed: print error so the cron alerts on a broken monitor
        print(f"[monitor ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
