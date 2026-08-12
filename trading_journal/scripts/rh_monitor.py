#!/usr/bin/env python3
"""rh_monitor.py — deterministic read-only position monitor for Agentic (751521659).

Calls the Robinhood MCP endpoint DIRECTLY over HTTP (no LLM, no Hermes MCP-tool
session), so it runs reliably as a no_agent cron. Refreshes the OAuth token if
needed, pulls quotes + portfolio + positions, checks each holding against its
mental stop (2% alert band) and scale-out trigger (+8% from avg), appends a
checkpoint to monitor_<date>.md, and writes an ALERT_<HHMM>_<date>.md file on a
trip. Prints NOTHING when all is well (silent cron); prints alert text when a
trip fires. READ-ONLY: never places, modifies, or cancels orders.

POSITIONS (qty, avg, mental stop, scale-out trigger):
  SOUN 6 @7.42  stop 6.40  scale>=8.00
  UUUU 3 @14.17 stop 13.00 scale>=15.30
  TGB  8 @8.86  stop 7.50  scale>=9.60
  KOS  3 @2.57  stop 2.20  scale>=3.00
"""
import asyncio, datetime as dt, json, os, sys, time

TOKDIR = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens"
TOK = os.path.join(TOKDIR, "robinhood.json")
META = os.path.join(TOKDIR, "robinhood.meta.json")
CLIENT = os.path.join(TOKDIR, "robinhood.client.json")
URL = "https://agent.robinhood.com/mcp/trading"
ACCT = "751521659"
JOURNAL = r"C:\Users\bottl\FinancialDevelopment\trading_journal\monitor_%s.md"

POSITIONS = [  # sym, qty, avg, stop, scale
    ("SOUN", 6, 7.42, 6.40, 8.00),
    ("UUUU", 3, 14.17, 13.00, 15.30),
    ("TGB", 8, 8.86, 7.50, 9.60),
    ("KOS", 3, 2.57, 2.20, 3.00),
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
    try:
        async with streamable_http_client(URL, http_client=http_client) as (rs, ws, _sid):
            async with ClientSession(rs, ws) as sess:
                await sess.initialize()
                q = await call(sess, "get_equity_quotes", {"symbols": [p[0] for p in POSITIONS]})
                pf = await call(sess, "get_portfolio", {"account_number": ACCT})
                ps = await call(sess, "get_equity_positions", {"account_number": ACCT})
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

    lines = []
    alerts = []
    for sym, exp_qty, avg, stop, scale in POSITIONS:
        last = quotes.get(sym)
        q = qty.get(sym, exp_qty)
        q = int(q) if q == int(q) else q
        if last is None:
            lines.append(f"{sym} n/a/{stop}"); alerts.append(f"ALERT {sym}: no quote"); continue
        dist_pct = (last - stop) / stop * 100 if stop else 0.0
        ret_pct = (last - avg) / avg * 100 if avg else 0.0
        lines.append(f"{sym} {last:.2f}/{stop}")
        if last <= stop or dist_pct <= 2.0:
            alerts.append(f"ALERT {sym}: last {last:.2f} at/below stop {stop} ({dist_pct:+.1f}%) — stop likely triggered")
        elif ret_pct >= 8.0:
            alerts.append(f"ALERT {sym}: up {ret_pct:+.1f}% from avg {avg} (last {last:.2f}) — scale-out candidate (trigger {scale})")
        if q != exp_qty:
            alerts.append(f"ALERT {sym}: qty changed {exp_qty}->{q} — likely a fill/stop")

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
