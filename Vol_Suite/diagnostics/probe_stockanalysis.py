#!/usr/bin/env python3
"""probe_stockanalysis.py

stockanalysis.com's holdings endpoint returns 200 when fetched from some
networks and 404 from this machine's urllib, even with a full browser header
set. Correct headers were the first theory and they did not fix it, so the
next suspect is the TLS/HTTP fingerprint of the client stack itself:
Cloudflare fingerprints the handshake (JA3) and can serve 404 to a client
that looks automated no matter what headers it sends.

This tries the same URL through every stack available in this venv and reports
which, if any, gets a 200. Whichever wins is what index_membership should use.

Run:  python probe_stockanalysis.py [ETF]
"""
import sys

URL_TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
URL = f"https://stockanalysis.com/api/symbol/e/{URL_TICKER}/holdings"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"https://stockanalysis.com/etf/{URL_TICKER.lower()}/holdings/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


def report(label, status, body="", err=None):
    if err:
        print(f"  [ERR ] {label}\n         {type(err).__name__}: {err}")
        return
    n = ""
    if status == 200:
        try:
            import json
            d = json.loads(body)
            n = f"  ({len(d['data']['holdings'])} holdings parsed)"
        except Exception:
            n = "  (200 but body did not parse as expected)"
    print(f"  [{status}] {label}{n}")
    if status != 200:
        print(f"         body: {body[:100]!r}")


def try_urllib_bare():
    from urllib.request import Request, urlopen
    try:
        with urlopen(Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
            report("urllib, bare UA (the ORIGINAL code)", r.status, r.read().decode())
    except Exception as e:
        report("urllib, bare UA (the ORIGINAL code)", getattr(e, "code", "ERR"), str(e))


def try_urllib_headers():
    from urllib.request import Request, urlopen
    try:
        with urlopen(Request(URL, headers=HEADERS), timeout=15) as r:
            report("urllib, full browser headers (CURRENT code)", r.status, r.read().decode())
    except Exception as e:
        report("urllib, full browser headers (CURRENT code)", getattr(e, "code", "ERR"), str(e))


def try_httpx_h1():
    try:
        import httpx
        r = httpx.get(URL, headers=HEADERS, timeout=15, follow_redirects=True)
        report("httpx HTTP/1.1", r.status_code, r.text)
    except Exception as e:
        report("httpx HTTP/1.1", None, err=e)


def try_httpx_h2():
    try:
        import httpx
        r = httpx.get(URL, headers=HEADERS, timeout=15, follow_redirects=True, http2=True)
        report("httpx HTTP/2 (needs: pip install httpx[http2])", r.status_code, r.text)
    except Exception as e:
        report("httpx HTTP/2 (needs: pip install httpx[http2])", None, err=e)


def try_requests():
    try:
        import requests
        r = requests.get(URL, headers=HEADERS, timeout=15)
        report("requests", r.status_code, r.text)
    except Exception as e:
        report("requests", None, err=e)


def try_curl_cffi():
    try:
        from curl_cffi import requests as creq
        r = creq.get(URL, impersonate="chrome124", timeout=15)
        report("curl_cffi impersonate=chrome124  (real browser TLS)", r.status_code, r.text)
    except ImportError:
        print("  [skip] curl_cffi not installed  ->  pip install curl_cffi")
        print("         This is the one that defeats JA3 fingerprinting. If every")
        print("         other stack 404s, install it and re-run this probe.")
    except Exception as e:
        report("curl_cffi impersonate=chrome124", None, err=e)


def main():
    print(f"Probing {URL}\n")
    try_urllib_bare()
    try_urllib_headers()
    try_httpx_h1()
    try_httpx_h2()
    try_requests()
    try_curl_cffi()
    print("\nHow to read this:")
    print("  Any stack returns 200 -> headers/TLS was the issue. Switch")
    print("     index_membership._fetch_holdings_raw to that stack.")
    print("  ALL stacks 404        -> the block is on this IP or the endpoint")
    print("     is geo/ASN-restricted. Headers and TLS are not the lever; either")
    print("     route through a different network, or accept the yfinance")
    print("     fallback (top-10 holdings) as the permanent source.")


if __name__ == "__main__":
    main()
