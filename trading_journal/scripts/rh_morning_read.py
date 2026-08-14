#!/usr/bin/env python3
"""Morning positions + portfolio + quotes read via Robinhood MCP (read-only)."""
import asyncio, json, os, sys, time, datetime

TOK = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.json"
META = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.meta.json"
CLIENT = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.client.json"
URL = "https://agent.robinhood.com/mcp/trading"
ACCT = "751521659"

def load_token():
    return json.load(open(TOK, encoding="utf-8"))

def save_token(tok):
    json.dump(tok, open(TOK, "w", encoding="utf-8"))

def refresh(token, meta, client):
    import httpx
    ep = meta["token_endpoint"]
    cid = client["client_id"]
    r = httpx.post(ep, data={"grant_type":"refresh_token","refresh_token":token["refresh_token"],"client_id":cid}, timeout=30)
    r.raise_for_status()
    d = r.json()
    tok = {"access_token": d["access_token"], "token_type": d.get("token_type","Bearer"),
           "expires_in": d.get("expires_in",3600), "scope": d.get("scope",""),
           "refresh_token": d.get("refresh_token", token["refresh_token"]),
           "expires_at": time.time()+float(d.get("expires_in",3600))}
    save_token(tok)
    return tok

async def call(sess, name, args):
    res = await sess.call_tool(name, args)
    for c in res.content:
        if c.type == "text":
            return json.loads(c.text)
    return {"content": [x.model_dump() for x in res.content]}

async def main():
    tok = load_token()
    if time.time() >= float(tok["expires_at"])-60:
        print("token expired, refreshing...", file=sys.stderr)
        tok = refresh(tok, json.load(open(META)), json.load(open(CLIENT)))
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    headers = {"Authorization": f"{tok['token_type']} {tok['access_token']}"}
    http_client = httpx.AsyncClient(headers=headers, timeout=30)
    async with streamable_http_client(URL, http_client=http_client) as (rs, ws, _sid):
        async with ClientSession(rs, ws) as sess:
            await sess.initialize()
            out = {}
            try:
                out["portfolio"] = await call(sess, "get_portfolio", {"account_number": ACCT})
            except Exception as e:
                out["portfolio_error"] = str(e)
            try:
                out["positions"] = await call(sess, "get_equity_positions", {"account_number": ACCT})
            except Exception as e:
                out["positions_error"] = str(e)
            try:
                out["quotes"] = await call(sess, "get_equity_quotes", {"symbols": ["SOUN","UUUU","TGB","KOS","SPY","QQQ"]})
            except Exception as e:
                out["quotes_error"] = str(e)
            print(json.dumps(out))
    try:
        http_client.close()
    except Exception:
        pass

if __name__ == "__main__":
    asyncio.run(main())
