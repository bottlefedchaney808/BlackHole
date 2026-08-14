# web_search / Firecrawl (Nous managed web tools) outage — 2026-08-13 root-cause & fix

**Status:** CONFIRMED ROOT CAUSE identified, reproduced, and fix specified.
**Prepared by:** Hermes coder/debugging agent
**Date:** 2026-08-13

---

## TL;DR

`web_search` and `web_extract` (the managed web tools that route through the Nous
subscription → Firecrawl) were **not** down due to an auth failure, an expired OAuth
token, a subscription/quota issue, or a Firecrawl/upstream outage. They were failing
because of a **local misconfiguration in `C:\Users\bottl\AppData\Local\hermes\.env`**:

> `FIRECRAWL_GATEWAY_URL` (and `TOOL_GATEWAY_DOMAIN`) are set to the hostname
> `firecrawl-gateway.nousresearch.com` **with no `https://` scheme.**

That makes the Firecrawl client build request URLs with **no scheme**, so every
request fails *before it ever reaches the network* — deterministically, on every call,
in every session that uses the web tools.

- `web_search` error in logs: `'NoneType' object has no attribute 'status_code'`
- `web_extract` error in logs: `Invalid URL '/v2/scrape': No scheme supplied`

Both are reproduced 1:1 by the Firecrawl SDK against the malformed URL (verified below).

The fix is to add the scheme: `FIRECRAWL_GATEWAY_URL=https://firecrawl-gateway.nousresearch.com`
(and correct `TOOL_GATEWAY_DOMAIN` to the bare suffix `nousresearch.com`).

---

## 1. Evidence — what actually happened (confirmed)

### 1.1 The failing session and the exact errors

The only session today that actually invoked the web tools was **`20260813_034524_058906`**
(the quant run). Earlier sessions today (01:47–02:52) only *registered* the firecrawl
providers but never called search/extract. From `logs/errors.log`:

```
2026-08-13 03:45:45,222 WARNING plugins.web.firecrawl.provider: Firecrawl search error: 'NoneType' object has no attribute 'status_code'
2026-08-13 03:45:45,225 WARNING agent.tool_executor: Tool web_search returned error (1.63s): { "success": false, "error": "Firecrawl search failed: 'NoneType' object has no attribute 'status_code'" }
2026-08-13 03:50:28,549 WARNING agent.tool_executor: Tool web_extract returned error (1.68s): {
  "results": [ { "url": "https://www.bls.gov/news.release/cpi.nr0.htm", "content": "", "error": "Invalid URL '/v2/scrape': No scheme supplied. ..." } ]
}
```

From `logs/agent.log`, the searches that triggered them (03:45:43) were the exact CPI /
INTC queries the quant agent reported as UNVERIFIED:
```
Web search via firecrawl: 'US CPI inflation report August 2026 headline core' (limit: 5)
Web search via firecrawl: 'Intel INTC stock offering deal secondary convertible August 13 2026' (limit: 5)
```

### 1.2 Why this is a config error, not auth/quota/upstream

| Hypothesis | Ruled in/out | Evidence |
|---|---|---|
| OAuth token expired | **RULED OUT** | `auth.json` for the `nous` provider: `expires_at = 2026-08-13T09:55:56+00:00`. At the failure time (03:45 local CDT = 08:45 UTC) the token had **~70 minutes remaining**. Scope `inference:invoke billing:manage` present. Token refreshed 08:55 UTC (03:55 local) — *after* the failures. |
| Subscription not active / no entitlement | **RULED OUT (not the cause)** | DNS + TLS handshake to `firecrawl-gateway.nousresearch.com:443` succeeds (resolves to 216.150.1.193, valid cert). The gateway host is reachable — the client just never sends a request to it. |
| Rate limit / quota (429) | **RULED OUT** | No 429 in any log; the request never left the process (see below). |
| Upstream Firecrawl outage (5xx/504) | **RULED OUT for today** | The error is raised by the SDK's URL builder *before* any HTTP call. A prior *separate* incident on **2026-08-08 09:15** was a genuine upstream `504 UPSTREAM_ERROR … firecrawl request timed out` against `https://api.firecrawl.dev/v2/search` — that was a real (transient) upstream issue and is unrelated to today's failure. |
| **Misconfiguration: scheme-less gateway URL** | **CONFIRMED** | See §1.3–1.4. |

### 1.3 Root cause traced to `.env`

`config.yaml` correctly routes web tools through the managed gateway:
```yaml
web:
  backend: firecrawl
  extract_backend: firecrawl
  use_gateway: true
```

`web.use_gateway: true` → `prefers_gateway("web")` returns True → the provider takes the
**managed Tool Gateway path** (`tools/managed_tool_gateway.py::resolve_managed_tool_gateway`)
and builds the client as:
```python
kwargs = { "api_key": <nous user token>, "api_url": <gateway origin> }
```
The gateway origin comes from `build_vendor_gateway_url("firecrawl")`, which returns the
`FIRECRAWL_GATEWAY_URL` env var verbatim (after `.rstrip("/")`).

`C:\Users\bottl\AppData\Local\hermes\.env` currently has (values verified, scheme visibly missing):
```
FIRECRAWL_GATEWAY_URL = firecrawl-gateway.nousresearch.com        # <-- NO https://
TOOL_GATEWAY_DOMAIN   = firecrawl-gateway.nousresearch.com        # <-- should be bare suffix
TOOL_GATEWAY_SCHEME   = (not set → code default "https")
```
`FIRECRAWL_API_KEY` is **not** set, so there is no direct-Firecrawl fallback — every web
call is forced down the (broken) gateway path.

### 1.4 Exact failure mechanism — reproduced

The Firecrawl SDK's `HttpClient._build_url()` does:
```python
base_str = "firecrawl-gateway.nousresearch.com/"   # no scheme
return urljoin(base_str, "/v2/search")             # → "/v2/search"  (scheme stripped!)
```
Running the actual installed SDK (`firecrawl` v4.17.0, `hermes-agent/venv`) with the
malformed `api_url` reproduces **both** today's errors verbatim:

```
search  -> AttributeError : 'NoneType' object has no attribute 'status_code'
scrape  -> MissingSchema  : Invalid URL '/v2/scrape': No scheme supplied. Perhaps you meant https:///v2/scrape?
```

(For `search`, the SDK wraps the URL-building failure and reports the NoneType; for
`extract`/`scrape` it surfaces the MissingSchema. Both are the same root cause.)

### 1.5 Why it hit "this session" specifically

It is **not** a transient mid-session failure (no stream drop, no OAuth refresh failure,
no provider 503). It is a **persistent, deterministic misconfiguration** that would fail
every session that calls `web_search`/`web_extract`. It surfaced today only because the
quant run (`20260813_034524`) was the first session to actually *use* the web tools since
the misconfig was introduced. (The earlier 504 on 08-08 used the direct cloud URL, so the
gateway URL override appears to have been added/repointed between 08-08 and today.)

---

## 2. Root-cause classification

- **CONFIRMED ROOT CAUSE:** `FIRECRAWL_GATEWAY_URL` in `C:\Users\bottl\AppData\Local\hermes\.env`
  is set to a scheme-less hostname (`firecrawl-gateway.nousresearch.com`), so the Firecrawl
  client builds requests with no scheme and every `web_search`/`web_extract` fails before
  hitting the network.
- **LIKELY HYPOTHESIS (context, not today's cause):** `TOOL_GATEWAY_DOMAIN` is also wrong —
  it is set to the full vendor host (`firecrawl-gateway.nousresearch.com`) instead of the
  bare domain suffix (`nousresearch.com`) that the code documents and expects. Today it is
  shadowed by `FIRECRAWL_GATEWAY_URL`, but if that override is ever removed, the derived
  URL would be `https://firecrawl-gateway.firecrawl-gateway.nousresearch.com` (double host)
  and break again.
- **UNVERIFIED / UNKNOWN:** Exactly when the `.env` values were introduced (`.env` mtime is
  2026-08-06; whether it predates or postdates the 08-12 WSL→Windows move isn't provable
  from current artifacts). Also whether any other Nous-managed tool (browser-firecrawl,
  image-gen, tts, transcription) is affected — all use the same `build_vendor_gateway_url`
  for their own `*_GATEWAY_URL`, but only `FIRECRAWL_GATEWAY_URL` is set, so today only web
  tools are broken.

---

## 3. Recommended fix (exact)

These are **env vars in `.env`**, not `config.yaml` keys, so they are edited in `.env`
(that is the documented "secrets / environment" layer — `config.yaml` settings are not
touched; `web.use_gateway: true` is already correct and must stay).

**Option A — minimal (preferred): add the scheme to the explicit override**
```
FIRECRAWL_GATEWAY_URL = https://firecrawl-gateway.nousresearch.com
```
**And** fix the domain suffix so the derived URL is also correct:
```
TOOL_GATEWAY_DOMAIN = nousresearch.com
```

**Option B — remove the override, rely on the derived URL (also fine):**
Delete/blank `FIRECRAWL_GATEWAY_URL`, keep `TOOL_GATEWAY_DOMAIN = nousresearch.com`,
`TOOL_GATEWAY_SCHEME = https`. The code then derives
`https://firecrawl-gateway.nousresearch.com` automatically.

> The end state both options converge on: the gateway origin handed to the Firecrawl
> client is `https://firecrawl-gateway.nousresearch.com` (with scheme).

I did **not** edit `.env` — per instructions this is a recommendation only; the change is
reversible and localized to two env lines. Applying it requires an editor save to `.env`
and a restart of the running Hermes gateway/CLI (a fresh session reads the updated env).
There is no `hermes config set` for these because they live in `.env`, not `config.yaml`.

---

## 4. Verification (do after applying)

1. **Static check** — confirm the client now builds a scheme'd URL and no longer raises:
   ```bash
   cd C:/Users/bottl/AppData/Local/hermes/hermes-agent
   ./venv/Scripts/python -c "from firecrawl import Firecrawl; c=Firecrawl(api_key='test', api_url='https://firecrawl-gateway.nousresearch.com'); print('URL builder OK (no MissingSchema)')"
   ```
2. **Live tool check** — start a fresh Hermes session and run a web search. The
   `web_search`/`web_extract` tools should return results (look for
   `Firecrawl: found N search results` in `logs/agent.log` instead of the WARNING).
3. **`hermes tools`** — the web/Firecrawl provider should report available
   (`check_firecrawl_api_key()` returns True via the gateway path).
4. **Confirm in logs** — `logs/errors.log` should show no new `'NoneType' object has no
   attribute 'status_code'` / `No scheme supplied` entries after the fix.

---

## 5. Watch items — so this doesn't silently break again

1. **OAuth token expiry.** `auth.json` `nous` `access_token` expires hourly
   (`expires_in ≈ 3600s`); refresh is handled automatically by
   `resolve_nous_access_token` on request paths. If refresh ever fails (offline,
   revocation), web tools degrade. This was **not** the cause today but is the other
   common silent-breaker. If cron jobs run web tools unattended, ensure they get a static
   Nous key or trigger a refresh, or they may silently fall back to a different provider.
2. **Gateway env-var scheme discipline.** All `*_GATEWAY_URL` values in `.env` must be
   full origins with scheme (`https://…`). Add a comment line above the var in `.env`
   (or a watch) so a future edit doesn't strip the scheme again.
3. **Watchdog.** Add a low-cost health probe (cron or a morning check) that runs one
   `web_search` and alerts if it errors. This catches both the misconfig class and any
   real upstream outage early, instead of only discovering it mid-run on an expensive
   quant call.
4. **`TOOL_GATEWAY_DOMAIN` correctness.** Keep it as the bare suffix (`nousresearch.com`),
   not a full vendor host, so any future `*_GATEWAY_URL` removal derives a valid URL.

---

## 6. Bottom line for Jason

The expensive quant model did not burn calls because the web tools were "down" at Nous —
they were broken **locally** by a malformed gateway URL in `.env` that lacks `https://`.
Nothing to re-license, no account/billing issue, no Nous outage. Fix the two lines in
`.env`, restart the session, verify with one web search, and add the lightweight watchdog
so a future config slip gets caught before (not during) a paid run.

*All secrets (OAuth tokens, API keys) referenced above are redacted; no credentials are
included in this report.*
