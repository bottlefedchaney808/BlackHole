#!/usr/bin/env python3
"""Direct Robinhood MCP probe — no LLM, no MCP-tool session.
Refreshes the OAuth token if needed, connects to agent.robinhood.com/mcp/trading
via the mcp client lib, and calls get_equity_quotes. Prints a JSON result.
"""
import asyncio, json, os, sys, time

TOK = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.json"
META = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.meta.json"
CLIENT = r"C:\Users\bottl\AppData\Local\hermes\mcp-tokens\robinhood.client.json"
URL = "https://agent.robinhood.com/mcp/trading"

def load_token():
    return json.load(open(TOK, encoding="utf-8"))

def save_token(tok):
    json.dump(tok, open(TOK, "w", encoding="utf-8"))

def refresh(token, meta, client):
    import httpx
    ep = meta["token_endpoint"]
    cid = client["client_id"]
    r = httpx.post(ep, data={
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id": cid,
    }, timeout=30)
    r.raise_for_status()
    d = r.json()
    tok = {"access_token": d["access_token"],
           "token_type": d.get("token_type", "Bearer"),
           "expires_in": d.get("expires_in", 3600),
           "scope": d.get("scope", ""),
           "refresh_token": d.get("refresh_token", token["refresh_token"]),
           "expires_at": time.time() + float(d.get("expires_in", 3600))}
    save_token(tok)
    return tok

async def main():
    tok = load_token()
    if time.time() >= float(tok["expires_at"]) - 60:
        print("token expired, refreshing...", file=sys.stderr)
        tok = refresh(tok, json.load(open(META)), json.load(open(CLIENT)))
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    headers = {"Authorization": f"{tok['token_type']} {tok['access_token']}"}
    http_client = httpx.AsyncClient(headers=headers, timeout=30)
    async with streamable_http_client(URL, http_client=http_client) as (read_s, write_s, _sid):
        async with ClientSession(read_s, write_s) as sess:
            await sess.initialize()
            res = await sess.call_tool("get_equity_quotes", {"symbols": ["SPY", "SOUN", "UUUU", "TGB", "KOS"]})
            # print first content block
            for c in res.content:
                if c.type == "text":
                    d = json.loads(c.text)
                    if isinstance(d, dict) and "data" in d and isinstance(d["data"], dict):
                        print(json.dumps(d["data"].get("results", d["data"]))[:2000])
                    else:
                        print(c.text[:2000])
                    break
            else:
                print(json.dumps(res.model_dump()))

if __name__ == "__main__":
    asyncio.run(main())
