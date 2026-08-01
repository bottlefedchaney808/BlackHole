# Sentiment Scanner

Real-time sentiment scanner: contested-narrative detection (StockTwits, Reddit,
YouTube transcripts) cross-referenced with options flow (ThetaData) and swap
activity (CME SDR).

## Setup

```
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell
pip install -r requirements.txt
```

## YouTube transcript scanner — PO token server (bgutil-ytdlp-pot-provider)

`scanner/youtube.py` uses yt-dlp to search YouTube and pull automatic-caption
transcripts. YouTube's `web` client requires a Proof-of-Origin (PO) token for
subtitle/caption requests — this is a YouTube requirement independent of
browser/TLS impersonation (curl_cffi), and without it caption fetches keep
failing with HTTP 429/403 even when impersonation itself is working. See
yt-dlp's own [PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)
for background.

The fix is [`bgutil-ytdlp-pot-provider`](https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
the yt-dlp-maintainer-endorsed PO token provider. It has two parts:

1. **Python plugin** — the `bgutil-ytdlp-pot-provider` pip package (already in
   `requirements.txt`, installed alongside yt-dlp). Once installed, yt-dlp's
   plugin system auto-discovers it; `scanner/youtube.py` also passes
   `extractor_args={"youtubepot-bgutilhttp": {"base_url": [...]}}` explicitly
   so it knows where the token server lives.
2. **A local token-generation server** — a small HTTP service (default port
   `4416`) that the plugin talks to. This is **not** something this sandbox
   can run persistently, so **you need to start it yourself** on your actual
   Windows machine. Pick one of the two options below.

### Option A — Docker (simplest)

```powershell
docker run --name bgutil-provider -d --init -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider
```

- Port `4416` is the server's default; `-p 4416:4416` publishes it to the
  same port on the host. If you want a different host port, e.g. `8080`, use
  `-p 8080:4416` and set `YTDLP_POT_SERVER_URL=http://127.0.0.1:8080` (see
  below).
- The image has Node.js and Deno flavors; `:latest` (used above) defaults to
  Node.js.

### Option B — Node.js (no Docker)

Requires Node.js >= 20 and git.

```powershell
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider\server
npm ci
npx tsc
node build\main.js
```

This runs the server in the foreground on port `4416` by default. Pass
`--port 8080` to `node build\main.js` to use a different port (and set
`YTDLP_POT_SERVER_URL` to match — see below). Leave this running in its own
terminal/window while you use the scanner (or run it as a scheduled
task/service if you want it always available).

### Pointing the scanner at the server

By default `scanner/youtube.py` looks for the server at
`http://127.0.0.1:4416`. If you run it on a different host or port, set:

```powershell
$env:YTDLP_POT_SERVER_URL = "http://127.0.0.1:8080"
```

before launching the scanner.

### Verifying it's working

```powershell
curl http://127.0.0.1:4416/ping
```

should return a small JSON response (e.g. containing a `version` field). You
can also just re-run the scanner: if the server isn't reachable, you'll see a
single one-time warning in the logs ("bgutil PO-token server not reachable at
... — YouTube transcript fetches ... will be skipped until it's running")
with the exact start commands above. Once the server is up, that warning
stops appearing and YouTube transcript-based sentiment starts flowing again.
If the server is simply not running, the scanner treats this as an expected,
non-fatal condition — the same way it treats "no API key configured"
elsewhere — and continues scanning the other sources (StockTwits, Reddit,
options/swap data) normally.
