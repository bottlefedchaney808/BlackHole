#!/usr/bin/env python3
"""verify_pot_token.py

Standalone check for the YouTube PO-token setup (bgutil-ytdlp-pot-provider).

Run this AFTER starting the bgutil server (node build\\main.js or the Docker
container), from the sentiment-scanner venv:

    cd C:\\Users\\bottl\\FinancialDevelopment\\sentiment-scanner
    .venv\\Scripts\\activate.ps1
    python verify_pot_token.py

It checks FOUR things, in order, and tells you plainly which one failed:
  0. Is the 'bgutil:http' PO token PROVIDER actually registered inside
     yt-dlp's plugin system? (This is checked BEFORE any network call --
     it answers "is the plugin installed/discoverable at all", independent
     of whether the bgutil server process happens to be running.)
  1. Is the bgutil server reachable at all (http://127.0.0.1:4416/ping)?
  2. Does yt-dlp actually attach a PO token (pot=...) to a real video's
     automatic-caption URL once fetch_pot is forced to "always"? (Confirmed
     via source read: yt-dlp bakes pot= into the URL synchronously inside
     extract_info(download=False) itself -- there's no later download-time
     step that adds it, so checking the URL string right after extraction is
     the correct place to check, not a premature check. Runs with
     youtube:pot_trace=true so the real per-request provider attempt/
     success/reject lines are visible -- plain verbose=True alone only
     shows the one-time provider *registration* banner, not those lines.)
  3. Can that caption URL actually be downloaded (the real end-to-end test --
     this is the part that was failing with HTTP 429 before the fix)?

UPDATE -- confirmed via live reproduction: once you get past steps 0/1 (the
provider IS registered and the server IS reachable), step 2's real
historical failure mode was NOT a problem with fetch_pot policy or with the
pot_trace key. It was that yt-dlp's default player_client -- ANDROID_VR --
is not one of the clients bgutil:http's PO-token provider supports. Running
with youtube:pot_trace=true and watching the real per-request trace lines
showed the explicit rejection:

  PO Token Provider "bgutil:http" rejected this request, trying next
  available provider. Reason: Client "ANDROID_VR" is not supported by
  bgutil:http. Supported clients: WEB, MWEB, TVHTML5, WEB_EMBEDDED_PLAYER,
  WEB_CREATOR, WEB_REMIX, TVHTML5_SIMPLY, TVHTML5_SIMPLY_EMBEDDED_PLAYER

So fetch_pot=always alone only forces the ATTEMPT -- it does not make the
attempt succeed if it's made on behalf of an unsupported client. The actual
fix is forcing youtube:player_client=web,android_vr via extractor_args (see
extract_caption_url() below and scanner/youtube.py's _pot_extractor_args()):
querying the `web` client gets a Subs PO token bgutil:http will actually
issue, while keeping `android_vr` in the list preserves real-format
resolution without needing a token. This was confirmed by standing up our
own bgutil server (v1.3.1) and a fresh yt-dlp venv in a Linux sandbox and
watching pot= appear on the caption URL only once player_client included
`web`.

Step 0 exists because of a bug found in the wild: yt-dlp will happily run
with fetch_pot=always set and a healthy bgutil server, and STILL never
attach a pot= param, if the bgutil-ytdlp-pot-provider package isn't actually
importable as `yt_dlp_plugins.extractor.getpot_bgutil_http` from this
Python's site-packages (e.g. it's listed in requirements.txt but
`pip install -r requirements.txt` was never (re)run against this venv, or
was run against a different Python than the one executing this script).
fetch_pot policy gating and provider registration are two independent
failure points -- this script now checks both.

No FinancialDevelopment-specific imports are needed -- this only depends on
yt-dlp and requests, so it can be run in isolation to debug the PO-token
pipeline without touching the rest of the scanner.
"""
import sys
import urllib.request
import urllib.error

POT_SERVER_URL = "http://127.0.0.1:4416"
# Overridable via `python verify_pot_token.py <video_id>` to test one specific
# video. Otherwise the script tries a short list of long-form, heavily-viewed
# music videos below IN ORDER and uses whichever is the first to actually
# report English automatic captions -- "Me at the zoo" (the previous default)
# turned out to be too short to have any, which is a property of the video,
# not a sign of anything broken. Repeatedly testing the SAME video ID from
# the SAME IP can also trigger YouTube's abuse throttling on that specific
# (IP, video) pair independent of whether the PO token setup is correct, so
# trying several spreads that risk around instead of hammering one target.
_CANDIDATE_VIDEO_IDS = ["9bZkp7q19f0", "kJQP7kiw5Fk", "dQw4w9WgXcQ"]
TEST_VIDEO_ID = sys.argv[1] if len(sys.argv) > 1 else None


def check_provider_registered() -> bool:
    """Definitive, network-independent check: did yt-dlp actually import and
    register the bgutil HTTP PO-token provider plugin?

    This runs BEFORE any YoutubeDL() config/network call. If this fails, no
    amount of fetch_pot policy tweaking or bgutil server uptime will ever
    produce a pot= param -- there's simply no provider registered to call.
    """
    print("[0/3] Checking whether yt-dlp registered the 'bgutil:http' PO "
          "token provider ...")
    try:
        import yt_dlp  # noqa: F401
        print(f"      yt-dlp version: {yt_dlp.version.__version__} "
              f"(module: {yt_dlp.__file__})")
    except ImportError:
        print("      FAILED -- yt-dlp isn't installed in this environment.")
        print("      Run: pip install -r requirements.txt")
        return False

    try:
        from yt_dlp.extractor.youtube.pot._registry import _pot_providers
    except ImportError as e:
        print(f"      FAILED -- couldn't import yt-dlp's PO-token provider "
              f"registry ({e!r}). This yt-dlp version may predate the "
              f"provider-plugin framework -- you need a recent yt-dlp "
              f"(2024.09+ added the provider framework; this codebase pins "
              f"yt-dlp with no upper bound in requirements.txt).")
        return False

    # Force yt-dlp to actually run its plugin-discovery/import step (this
    # normally happens lazily on first YoutubeDL() construction).
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}):
        pass

    registered = sorted(_pot_providers.value.keys())
    print(f"      Registered PO token providers: {registered or '(none)'}")

    if any('bgutilhttp' in name.lower() or 'bguhttp' in name.lower()
           for name in registered) or 'BgUtilHTTP' in registered:
        print("      OK -- BgUtilHTTP provider is registered.")
        return True

    print()
    print("      FAILED -- the bgutil HTTP provider is NOT registered, even")
    print("      though bgutil-ytdlp-pot-provider may be listed in")
    print("      requirements.txt. This means the package isn't actually")
    print("      importable as yt_dlp_plugins.extractor.getpot_bgutil_http")
    print("      from THIS Python interpreter's site-packages. Fix:")
    print(f"        {sys.executable} -m pip install bgutil-ytdlp-pot-provider")
    print("      Then re-run this script -- step [0/3] should list")
    print("      'BgUtilHTTP' above.")
    try:
        import yt_dlp_plugins.extractor.getpot_bgutil_http as _m  # noqa: F401
        print(f"      (Note: the module DID import fine from {_m.__file__} -- "
              "if step 0 still fails after that import succeeds, the issue "
              "is in provider registration itself, not discovery. Please "
              "report this.)")
    except ImportError as e:
        print(f"      (Confirms: 'import yt_dlp_plugins.extractor."
              f"getpot_bgutil_http' fails with {e!r} in this interpreter.)")
    return False


def check_server_reachable() -> bool:
    print(f"[1/3] Checking bgutil server at {POT_SERVER_URL}/ping ...")
    try:
        with urllib.request.urlopen(f"{POT_SERVER_URL}/ping", timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"      OK -- server responded: {body.strip()[:200]}")
            return True
    except Exception as e:
        print(f"      FAILED -- {e!r}")
        print()
        print("      The bgutil server isn't reachable. Start it first:")
        print("        cd <wherever you cloned bgutil-ytdlp-pot-provider>\\server")
        print("        node build\\main.js")
        print("      ...and leave that window running, then re-run this script.")
        return False


def extract_caption_url(video_id: str):
    print(f"[2/3] Extracting caption info for test video {video_id} "
          f"(verbose=True + youtube:pot_trace=true so yt-dlp's PO-token "
          f"provider attempt/success/reject lines are visible below -- see "
          f"note in scanner/youtube.py's _pot_extractor_args() docstring: "
          f"plain verbose=True alone only raises the pot provider logger to "
          f"DEBUG, one level less verbose than the TRACE level its actual "
          f"per-request attempt/rejection messages are gated behind, so "
          f"pot_trace=true is required here to see anything beyond the "
          f"one-time provider *registration* banner) ...")
    print("-" * 60)
    try:
        import yt_dlp
    except ImportError:
        print("      FAILED -- yt-dlp isn't installed in this environment.")
        print("      Run: pip install -r requirements.txt")
        return None, None

    ydl_config = {
        "verbose": True,
        "quiet": False,
        "no_warnings": False,
        "skip_download": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        # Avoids a hard ExtractorError("Requested format is not available")
        # if format/JS-signature resolution fails for one of the queried
        # player_clients -- we only need auto-captions here
        # (skip_download=True), so a missing real format must not be fatal.
        "ignore_no_formats_error": True,
        "extractor_args": {
            "youtubepot-bgutilhttp": {"base_url": [POT_SERVER_URL]},
            "youtube": {
                "fetch_pot": ["always"],
                "pot_trace": ["true"],
                # THE FIX: yt-dlp's default player_client (ANDROID_VR) is
                # rejected outright by bgutil:http's PO-token provider (it
                # only supports WEB, MWEB, TVHTML5, WEB_EMBEDDED_PLAYER,
                # WEB_CREATOR, WEB_REMIX, TVHTML5_SIMPLY,
                # TVHTML5_SIMPLY_EMBEDDED_PLAYER -- confirmed via a live
                # pot_trace run). Querying 'web' too gets a token bgutil:http
                # will actually issue; keeping 'android_vr' preserves real
                # format availability without needing a token.
                "player_client": ["web", "android_vr"],
            },
        },
    }

    ydl = yt_dlp.YoutubeDL(ydl_config)
    try:
        info = ydl.extract_info(video_id, download=False)
    except Exception as e:
        print("-" * 60)
        print(f"      FAILED -- yt-dlp raised an exception during extraction: {e!r}")
        ydl.close()
        return None, None
    print("-" * 60)

    auto_captions = (info or {}).get("automatic_captions", {}) or {}
    en_captions = auto_captions.get("en", [])
    json3 = next((c for c in en_captions if c.get("ext") == "json3"), None)

    if not json3:
        print(f"      No English json3 automatic captions found for {video_id} "
              f"(this is a property of the video, not necessarily a bug -- some "
              f"videos don't have auto-captions, e.g. very short ones).")
        ydl.close()
        return None, None

    url = json3["url"]
    has_pot = "pot=" in url
    print(f"      Caption URL found. Contains 'pot=' parameter: {has_pot}")
    print()
    print("      This 'pot=' check is a valid pass/fail signal -- confirmed by")
    print("      direct yt-dlp source read (yt_dlp/extractor/youtube/_video.py,")
    print("      _extract_formats_and_subtitles/process_language()): the pot")
    print("      param is baked into automatic_captions[...]['url'] synchronously")
    print("      during extract_info(download=False) itself, NOT later during a")
    print("      separate download/write-subtitle step. So checking the URL")
    print("      string right after extract_info() is architecturally correct,")
    print("      not a premature check -- yt-dlp never adds pot= after the fact.")
    if not has_pot:
        print()
        print("      No PO token was attached to the URL. This means yt-dlp DID")
        print("      NOT successfully retrieve one from any registered provider")
        print("      for this request, even though fetch_pot=always was set (which")
        print("      only forces the ATTEMPT, not success). Scroll up through the")
        print("      '[debug] [youtube] [pot:...]' lines above -- with pot_trace=true")
        print("      you should now see explicit 'Attempting to fetch a PO Token")
        print("      from ... provider' / 'PO Token response from ...' / rejection")
        print("      lines that a plain verbose=True run WOULD NOT show (they are")
        print("      gated behind TRACE, one level more verbose than DEBUG). Also")
        print("      check the bgutil server's own console window for errors, and")
        print("      re-check step [0/3] above -- if the provider isn't registered,")
        print("      this is expected regardless of fetch_pot policy.")
        ydl.close()
        return None, None
    if not has_pot:
        ydl.close()
        return None, None
    return ydl, url


def find_working_caption_url():
    """Try TEST_VIDEO_ID if explicitly given; otherwise walk _CANDIDATE_VIDEO_IDS
    in order and use whichever is the first one that actually has English
    automatic captions, instead of failing the whole run because one
    arbitrarily-chosen test video happens not to have any.
    """
    candidates = [TEST_VIDEO_ID] if TEST_VIDEO_ID else _CANDIDATE_VIDEO_IDS
    for i, vid in enumerate(candidates):
        ydl, url = extract_caption_url(vid)
        if url:
            return ydl, url
        if i < len(candidates) - 1:
            print(f"      Trying next candidate video ({candidates[i + 1]}) ...")
            print()
    return None, None


def fetch_caption(ydl, url: str) -> bool:
    """Download the caption payload the SAME WAY scanner/youtube.py's
    _fetch_caption_payload() actually does it in production: through the
    yt-dlp instance's own impersonation-capable urlopen(), not a plain
    urllib request. A plain urllib.request.urlopen() call here would fail
    with HTTP 429 regardless of whether the pot= token is valid, because
    YouTube blocks non-impersonating clients outright -- that mismatch
    previously made this step a false negative even when the real scanner
    code would have succeeded.
    """
    print("[3/3] Attempting to download the caption payload (the real test, "
          "via yt-dlp's impersonation-capable urlopen -- same code path "
          "scanner/youtube.py uses in production) ...")
    try:
        from yt_dlp.networking.common import Request
        from yt_dlp.networking.impersonate import ImpersonateTarget
        req = Request(url, extensions={"impersonate": ImpersonateTarget()})
        resp = ydl.urlopen(req)
        data = resp.read()
        print(f"      SUCCESS -- downloaded {len(data)} bytes of caption data.")
        return True
    except urllib.error.HTTPError as e:
        print(f"      FAILED -- HTTP {e.code} {e.reason}")
        if e.code == 429:
            print()
            print("      Still getting 429 even with a pot= token attached AND")
            print("      browser impersonation on the download itself. Check the")
            print("      bgutil server console for errors, and consider whether")
            print("      this network/IP is being rate-limited independent of the")
            print("      token (e.g. too many recent attempts from this address).")
        return False
    except Exception as e:
        print(f"      FAILED -- {e!r}")
        return False
    finally:
        ydl.close()


def main() -> int:
    print("=" * 60)
    print("YouTube PO-token setup verification")
    print("=" * 60)

    if not check_provider_registered():
        return 1

    print()
    if not check_server_reachable():
        return 1

    print()
    ydl, url = find_working_caption_url()
    if not url:
        print()
        print("      All candidate videos failed to yield captions/download "
              "successfully -- see output above for the specific reason each one failed.")
        return 1

    print()
    ok = fetch_caption(ydl, url)

    print()
    print("=" * 60)
    if ok:
        print("ALL CHECKS PASSED. YouTube captions should now work in the scanner.")
    else:
        print("Setup is incomplete -- see the FAILED step above for what to fix.")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
