"""Corrected direct-API read: RSI/CCI/WilliamsR/EMA + 5min historicals."""
import asyncio, json, time, os, sys

HOME = r"C:/Users/bottl"
TOK = os.path.join(HOME, "AppData/Local/hermes/mcp-tokens")

def load(path):
    with open(path) as f:
        return json.load(f)

async def main():
    tok = load(os.path.join(TOK, "robinhood.json"))
    meta = load(os.path.join(TOK, "robinhood.meta.json"))
    client = load(os.path.join(TOK, "robinhood.client.json"))
    at = tok["access_token"]
    if time.time() > tok.get("expires_at", 0):
        import httpx as _h
        r = _h.post(meta["token_endpoint"],
                    data={"grant_type": "refresh_token",
                          "refresh_token": tok["refresh_token"],
                          "client_id": client["client_id"]}, timeout=30)
        r.raise_for_status(); new = r.json()
        tok["access_token"] = new.get("access_token", at)
        if new.get("refresh_token"): tok["refresh_token"] = new["refresh_token"]
        tok["expires_at"] = time.time() + new.get("expires_in", 3600) - 300
        json.dump(tok, open(os.path.join(TOK, "robinhood.json"), "w"))
        at = tok["access_token"]

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    START = "2026-08-12T13:30:00Z"
    END = "2026-08-12T19:27:00Z"
    symbols = ["TGB", "SOUN", "UUUU", "CRGY", "QQQ", "SPY"]

    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {at}"}, timeout=45)
    out = {}
    async with streamable_http_client(meta["issuer"], http_client=http_client) as (rs, ws, _sid):
        async with ClientSession(rs, ws) as sess:
            await sess.initialize()

            for s in symbols:
                # historicals
                try:
                    res = await sess.call_tool("get_equity_historicals",
                        {"symbols": [s], "interval": "5minute", "bounds": "regular",
                         "start_time": START, "end_time": END})
                    out.setdefault(s, {})["historicals"] = "".join(
                        c.text for c in res.content if hasattr(c, "text"))
                except Exception as e:
                    out.setdefault(s, {})["historicals"] = f"ERROR: {e}"

                # technicals
                for itype, period in [("rsi", 14), ("cci", 14), ("williams_r", 14),
                                      ("ema", 50), ("ema", 100), ("ema", 150)]:
                    args = {"symbol": s, "type": itype, "interval": "5minute",
                            "bounds": "regular", "start_time": START, "end_time": END,
                            "output": "last:30"}
                    if period is not None:
                        args["period"] = period
                    key = f"{itype}_{period}" if itype == "ema" else itype
                    try:
                        res = await sess.call_tool("get_equity_technical_indicators", args)
                        out.setdefault(s, {})[key] = "".join(
                            c.text for c in res.content if hasattr(c, "text"))
                    except Exception as e:
                        out.setdefault(s, {})[key] = f"ERROR: {e}"
            await http_client.aclose()

    with open(os.path.join(HOME, "rh_tech_out2.json"), "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("DONE -> rh_tech_out2.json")

asyncio.run(main())
