"""Deterministic direct-API read of Robinhood technicals + historicals."""
import asyncio, json, time, os, sys

HOME = r"C:/Users/bottl"
TOK = os.path.join(HOME, ".hermes/mcp-tokens")

def load(path):
    with open(path) as f:
        return json.load(f)

def refresh_token(tok, meta, client):
    url = meta["token_endpoint"]
    data = {
        "grant_type": "refresh_token",
        "refresh_token": tok["refresh_token"],
        "client_id": client["client_id"],
    }
    import httpx
    r = httpx.post(url, data=data, timeout=30)
    r.raise_for_status()
    new = r.json()
    if "access_token" in new:
        tok["access_token"] = new["access_token"]
        if "refresh_token" in new and new.get("refresh_token"):
            tok["refresh_token"] = new["refresh_token"]
        tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 300
        with open(os.path.join(TOK, "robinhood.json"), "w") as f:
            json.dump(tok, f)
        return new["access_token"]
    return tok["access_token"]

async def main():
    tok = load(os.path.join(TOK, "robinhood.json"))
    meta = load(os.path.join(TOK, "robinhood.meta.json"))
    client = load(os.path.join(TOK, "robinhood.client.json"))

    at = tok["access_token"]
    if time.time() > tok.get("expires_at", 0):
        at = refresh_token(tok, meta, client)
        print("token refreshed", file=sys.stderr)

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {at}"}, timeout=40
    )
    url = meta["issuer"]  # https://agent.robinhood.com/mcp/trading

    out = {}
    async with streamable_http_client(url, http_client=http_client) as (rs, ws, _sid):
        async with ClientSession(rs, ws) as sess:
            await sess.initialize()
            tools = await sess.list_tools()
            for t in tools.tools:
                if "technical" in t.name or "historical" in t.name:
                    out.setdefault("_schemas", {})[t.name] = {
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                    }

            symbols = ["TGB", "SOUN", "UUUU", "CRGY", "QQQ", "SPY"]

            for s in symbols:
                for tname, args in [
                    ("get_equity_technical_indicators", {"symbol": s}),
                    ("get_equity_historicals", {"symbol": s, "interval": "5minute", "bounds": "regular"}),
                ]:
                    try:
                        res = await sess.call_tool(tname, args)
                        content = []
                        for c in res.content:
                            if hasattr(c, "text"):
                                content.append(c.text)
                            elif isinstance(c, dict):
                                content.append(str(c))
                        out.setdefault(s, {})[tname] = "".join(content)
                    except Exception as e:
                        out.setdefault(s, {})[tname] = f"ERROR: {e}"
            await http_client.aclose()

    with open(os.path.join(HOME, "rh_tech_out.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("DONE -> rh_tech_out.json")

asyncio.run(main())
