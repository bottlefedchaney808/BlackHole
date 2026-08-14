# Hermes Local Settings Audit — 2026-08-13

**Audited host:** `C:\Users\bottl\AppData\Local\hermes` (Windows-native, Jason migrated off WSL 2026-08-12)
**Audit agent:** CODER / settings-audit subagent
**Audit focus:** silent-break misconfigurations in **local settings** (`.env`, `config.yaml`, `auth.json`, MCP servers, cron) — the same class as the schemeless-gateway-URL bug (FIRECRAWL_GATEWAY_URL was `firecrawl-gateway.nousresearch.com`, missing `https://`, which made the Firecrawl SDK build `/v2/search` with no scheme and fail before touching the network). That bug was **already fixed and verified** prior to this audit; this report confirms it and sweeps for the rest.

> **Redaction notice:** all API keys, tokens, passwords, and project secrets are redacted (`<redacted>` or shown as length/prefix only). No secret values appear in this report.

---

## 1. Settings checked — status table

| # | Setting / file | Value (redacted) | Status |
|---|---|---|---|
| 1 | `.env` → `FIRECRAWL_GATEWAY_URL` | `https://firecrawl-gateway.nousresearch.com` | **FIXED** (was schemeless; now full origin w/ scheme) |
| 2 | `.env` → `TOOL_GATEWAY_DOMAIN` | `nousresearch.com` | **FIXED** (was full host `firecrawl-gateway.nousresearch.com`; must be bare suffix) |
| 3 | `.env` → `NOUS_BASE_URL` | `https://inference-api.nousresearch.com/v1` | **OK** (scheme present) |
| 4 | `.env` → `HASS_URL` | `http://homeassistant.local:8123` | **OK** (http fine for LAN) |
| 5 | `.env` → `PHOTON_DASHBOARD_HOST` | `https://app.photon.codes` | **OK** (scheme present) |
| 6 | `.env` scheme-discipline comments | added above vars 1–2 | **FIXED** (this audit) |
| 7 | `config.yaml` → `web.use_gateway` | `true` | **OK** |
| 8 | `config.yaml` → `web.backend` / `web.extract_backend` | `firecrawl` / `firecrawl` | **OK** (consistent) |
| 9 | `config.yaml` → `browser.use_gateway` | `true` | **OK** |
| 10 | `config.yaml` → `model.provider` / `base_url` | `nous` / `https://inference-api.nousresearch.com/v1` | **OK** (scheme present) |
| 11 | `config.yaml` → `terminal.cwd` | `C:\Users\bottl` | **OK** (correct Windows path) |
| 12 | `config.yaml` → `model.api_key` / `providers.nous.api_key` | `sk-…` (`<redacted>`) | **PROPOSED** (see §2.3) |
| 13 | `auth.json` → `providers.nous` | OAuth device_code, expires `2026-08-13T09:55:56Z`, refresh_token present | **OK** (near-expiry but auto-refresh; valid) |
| 14 | `auth.json` → openrouter credential | last_status **exhausted**, error `402 … requires more credits` | **BROKEN-UNFIXED** (see §2.2) |
| 15 | `auth.json` → photon / robinhood | valid, robinhood token expires `2026-08-21` (~8d) | **OK** |
| 16 | `config.yaml` → `mcp_servers.reddit` | cmd `…\node\reddit-mcp-server.cmd` (exists), `REDDIT_PASSWORD` in env | **OK** (path valid) + **PROPOSED** hygiene (§2.4) |
| 17 | `config.yaml` → `mcp_servers.robinhood` | `url: https://agent.robinhood.com/mcp/trading`, auth oauth, token valid | **OK** |
| 18 | `config.yaml` → `mcp_servers.tradingview` | cmd `…\node\node.exe` + `…\FinancialDevelopment\tradingview-mcp\src\server.js` (exists) | **OK** (relocation correct) |
| 19 | `config.yaml` → tradingview `TV_CDP_PORT` | `19222` | **PROPOSED** (see §2.5) |
| 20 | Cron `quant-bridge-watchdog` | script `quant_bridge_watchdog_cron.py`, last `ok` | **OK** |
| 21 | Cron `powerhour-prep` | script `powerhour_prep_cron.py` (Windows wrapper), last run errored **before** wrapper existed | **FIXED-in-config / UNVERIFIED** (see §2.6) |
| 22 | Cron `rh-position-monitor`, `photon-gateway-watchdog`, `web-search-watchdog` | `.py` scripts, last `ok` / pending | **OK** |
| 23 | Gateway process (pid 13616) | running; webhook + photon `connected`; sidecar on `127.0.0.1:8789` | **OK** |
| 24 | Stale WSL cwd in active session | runtime cwd `/home/bottl/Financial_Development` missing → terminal fell back to `/` | **BROKEN-UNFIXED** (session-scoped, see §2.1) |

---

## 2. Confirmed issues + fix applied / proposed

### 2.1 STALE WSL CWD — active session only (BROKEN-UNFIXED / resolves on new session)
- **Evidence:** `logs/errors.log`:
  - `agent.runtime_cwd: configured working directory does not exist: /home/bottl/Financial_Development`
  - `LocalEnvironment cwd '/home/bottl/Financial_Development' is missing on disk; falling back to '/'`
  - seen for session `20260813_025029_53c5cd`.
- **Root cause:** the **running session** was created with a pre-migration (WSL-era) working-directory override. This is **NOT** a `config.yaml` problem — `terminal.cwd` is correctly `C:\Users\bottl`. It is a session-scoped value baked into the live session's runtime.
- **Fix:** none applied (cannot mutate a running session safely). **Resolves automatically** when that session is abandoned / a new session starts. **Action for Jason:** do not resume the stale session; start fresh.
- **Status:** BROKEN-UNFIXED (transient, self-heals on new session).

### 2.2 OpenRouter credential exhausted (BROKEN-UNFIXED — billing, non-blocking)
- **Evidence:** `auth.json` → `credential_pool.openrouter[0]`: `last_status=exhausted`, `last_error_code=402`, message *"This request requires more credits … you requested up to 65536 tokens but can only afford 36975 … add more credits."*
- **Impact:** any tool/model routed through OpenRouter (e.g. a `fallback_model` configured to `openrouter`) silently fails with 402. **Not blocking today** because the active provider is `nous` (OAuth) and `fallback_model` is commented out in `config.yaml`.
- **Fix applied:** none (requires user action — add credits at openrouter.ai, or remove the exhausted key). **Proposed:** either top-up credits or delete `OPENROUTER_API_KEY` from `.env` so it cannot be selected as a fallback.
- **Status:** BROKEN-UNFIXED (non-blocking).

### 2.3 Redundant static `sk-…` API keys alongside OAuth (PROPOSED)
- `config.yaml` `model.api_key` and `providers.nous.api_key` hold a static `sk-…` key, while `auth.json` shows `active_provider=nous` via OAuth device_code (with refresh_token). The OAuth path is authoritative; the static key is redundant/legacy.
- **Proposed (not applied):** remove the static keys from `config.yaml` once you confirm OAuth-only is intended. Keeping them is harmless (OAuth wins) but is a credential-hygiene smell.

### 2.4 `REDDIT_PASSWORD` plaintext in `config.yaml` (PROPOSED hygiene)
- `mcp_servers.reddit.env.REDDIT_PASSWORD` is stored in plaintext in `config.yaml`. Hermes convention is secrets in `.env`, settings in `config.yaml`.
- **Proposed (not applied):** move the password to `.env` and reference it (or rely on the MCP env filter). Harmless to leave, but it's a secret in a settings file.

### 2.5 tradingview `TV_CDP_PORT=19222` may be stale (PROPOSED — verify)
- **Context:** `19222` was the **WSL bridge** port (`start_tvc_proxy.ps1` / `tvc_proxy.py` forward `19222 → 9222`). On **Windows-native**, TradingView launches with `--remote-debugging-port=9222` directly (`launch_windows_tv_cdp.ps1` default port `9222`; `connection.js` default `9222`). No bridge is referenced in the current config; nothing is listening on 9222 or 19222 right now (TV not running).
- **Proposed (not applied — ambiguous):** if Jason runs TradingView **without** the `tvc_proxy` bridge on Windows-native, change `TV_CDP_PORT` to `9222`. If he still runs the proxy bridge, keep `19222`. **Needs a live TradingView to confirm.**
- **Status:** PROPOSED.

### 2.6 Cron `powerhour-prep` WSL failure — FIXED-in-config, needs one successful run to confirm (UNVERIFIED)
- **Evidence:** `cron/jobs.json` job `0866dbb60d0e` (`powerhour-prep`) shows `last_status=error`, `last_error` = *"Windows Subsystem for Linux has no installed distributions"* — a WSL invocation, from the run on **08-12 13:00** (before the Windows wrapper existed).
- **Current config:** the job's `script` is now **`powerhour_prep_cron.py`** — the Windows-native wrapper (created 08-12 14:25) that calls the project venv python directly and never routes via WSL. **No active cron job references a `.sh`/WSL script** (all five enabled jobs are `.py`).
- **Fix applied (prior, confirmed in config):** pointing the job at the `.py` wrapper. **Not yet verified** — next run is 08-13 13:00 CDT.
- **Status:** FIXED-in-config / UNVERIFIED.

### 2.7 `.env` scheme-discipline comments (FIXED — this audit)
Comments added above the gateway vars (see §5 for exact text):
- Above `TOOL_GATEWAY_DOMAIN`: bare-suffix rule.
- Above `FIRECRAWL_GATEWAY_URL`: must-include-`https://` rule with the Firecrawl failure explanation.

---

## 3. Still broken / unverified

- **§2.1** — stale WSL cwd in the **active** session (`20260813_025029_53c5cd`) → terminal falls back to `/` for that session. Resolves on new session. **Not config-fixable live.**
- **§2.2** — OpenRouter API key out of credits (402). Non-blocking today (nous is primary); will fail if anything routes via OpenRouter.
- **§2.6** — `powerhour-prep` cron fix not yet proven by a successful run (next 08-13 13:00 CDT).
- **tradingview `TV_CDP_PORT`** — unverifiable without TradingView running.

---

## 4. What I changed

| File | Change | Backup |
|---|---|---|
| `.env` | Added scheme-discipline comment blocks above `FIRECRAWL_GATEWAY_URL` and `TOOL_GATEWAY_DOMAIN`. **No values changed** (verified byte-identical for all URL/domain/host values vs backup). | `.env.bak_20260813_audit` (fresh; prior `.env.bak_20260813` from the original fix also present) |
| `config.yaml` | **No changes.** | `config.yaml.bak.20260813_audit` (created defensively; not needed) |

- **Net config changes:** 0 value changes. Only comments added to `.env`.
- All prior fixes (FIRECRAWL scheme, TOOL_GATEWAY_DOMAIN bare suffix) were already applied before this audit and are confirmed correct.

---

## 5. Scheme-discipline comments added (exact text)

Above `TOOL_GATEWAY_DOMAIN`:
```
# NOTE: TOOL_GATEWAY_DOMAIN must be the BARE suffix only (e.g. nousresearch.com), NOT a full origin.
# The client appends it to build gateway URLs. Do NOT add https:// or a path here.
```

Above `FIRECRAWL_GATEWAY_URL`:
```
# NOTE: gateway URLs MUST include the https:// scheme — a bare hostname silently breaks the tool client
# (the Firecrawl SDK built schemeless '/v2/search' URLs and failed before touching the network).
# Keep this as a FULL origin with scheme: https://firecrawl-gateway.nousresearch.com
```

---

## 6. Reference commands / evidence used (read-only)

- `hermes config get web.use_gateway` → `true`; `model.provider` → `nous`; `web.backend` → `firecrawl`; `hermes config check` → no missing core sections.
- `auth.json` / `shared/nous_auth.json` parsed for expiry + pool state (redacted).
- `cron/jobs.json` reviewed for script references, last_status, last_error.
- `netstat` confirmed gateway (13616) ↔ photon sidecar (8789) established; webhook+photon `connected`, `needs_attention=false`.
- MCP paths verified on disk: `node\reddit-mcp-server.cmd`, `node\node.exe`, `tradingview-mcp\src\server.js`, `FinancialDevelopment\.venv\Scripts\python.exe` all exist.
- `robinshood` MCP token valid to `2026-08-21`; nous OAuth valid to `2026-08-13T09:55:56Z` with refresh_token.
