# PHClient 2.0.1 — beta install

v2 is ready. Your current 1.x client can't identify itself to the API and it's also hitting a bunch of routes that were deleted in 2.0 (~8k 404s/day, mostly deleted per-strike history endpoints + a small MOCK-ticker loop we can also kill). 2.0 fixes both. Your existing CF Access token keeps working; only the client changes.

## Install

Everything's behind Cloudflare Access with your existing Auth0 login. Log in through a browser first, then download the wheel + verify:

```bash
# 1) Log in (opens Auth0 in your browser once; sets a cookie good for 24h)
open https://install.potatohedge.com/releases/v2.0.1/manifest.json

# 2) Download the wheel + its sha256
curl -O https://install.potatohedge.com/releases/v2.0.1/artifacts/potatohedge-2.0.1-py3-none-any.whl
curl https://install.potatohedge.com/releases/v2.0.1/checksums/potatohedge-2.0.1-py3-none-any.whl.sha256 | sha256sum -c

# 3) Install (Python >= 3.10; pyyaml + httpx + pydantic pulled transitively)
pip install ./potatohedge-2.0.1-py3-none-any.whl
```

For scripted / agent installs: the CF Access cookie your browser gets can be extracted and reused; or you can use the service token previously provided.

## Configure

v2 has no positional-args constructor — build a `ClientConfig`. Use your existing PH_API access token in each capability, keep `caller_id="zinko"`:

```python
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

_TOKEN = {
    "CF-Access-Client-Id":     "<YOUR-PH-API-CF-ACCESS-CLIENT-ID>.access",
    "CF-Access-Client-Secret": "<YOUR-PH-API-CF-ACCESS-CLIENT-SECRET>",
}

config = ClientConfig(
    base_url="https://api.potatohedge.com",
    credentials={
        "market.read":     Credential(headers=dict(_TOKEN)),
        "options.read":    Credential(headers=dict(_TOKEN)),
        "dealer.read":     Credential(headers=dict(_TOKEN)),
        "volatility.read": Credential(headers=dict(_TOKEN)),
    },
    caller_id="zinko",
    client_version="2",
)
client = PHClient(config)
```

Full capability list: `dealer.read`, `flow.read`, `market.read`, `news.read`, `options.read`, `recipes.read`, `regsho.read`, `signals.read`, `support_resistance.read`, `volatility.read`.

## Docs (same auth as the wheel)

Release metadata + artifacts:

- **`https://install.potatohedge.com/releases/v2.0.1/manifest.json`** — release manifest (version, source_commit, python_requirement, ph_api_contract_fingerprint, artifact sha)
- **`https://install.potatohedge.com/releases/v2.0.1/checksums/`** — sha256 sidecars for the wheel + sdist + manifest
- **`https://install.potatohedge.com/releases/v2.0.1/artifacts/`** — wheel + sdist

Docs (byte-identical to what your agent would get in a tarball; point it at these):

- **`https://install.potatohedge.com/releases/v2.0.1/README.md`** — package-level README
- **`https://install.potatohedge.com/releases/v2.0.1/CHANGELOG.md`** — 2.0.1 + 2.0.0 release notes
- **`https://install.potatohedge.com/releases/v2.0.1/docs/CLIENT_REFERENCE.md`** — canonical SDK method reference (start here)
- **`https://install.potatohedge.com/releases/v2.0.1/docs/API_REFERENCE.md`** — REST endpoint reference
- **`https://install.potatohedge.com/releases/v2.0.1/docs/AGENT_QUICKSTART.md`** — agent-oriented setup
- **`https://install.potatohedge.com/releases/v2.0.1/docs/AGENT_COOKBOOK.md`** — worked recipes
- **`https://install.potatohedge.com/releases/v2.0.1/docs/llms.txt`** — llms.txt-format capability/method summary
- **`https://install.potatohedge.com/releases/v2.0.1/docs/v1-to-v2-migration-guide.md`** — 1→2 migration reference
- **`https://install.potatohedge.com/releases/v2.0.1/examples/PHClient_demo.ipynb`** — v2-only Jupyter demo

## What changed 1.x → 2.x (top hits)

- Per-strike history methods (`/api/theta/hist/option/all_greeks/...`, `.../open_interest/...`) were **deleted in 2.0** and have been 404'ing you for a while. Use the bulk chain variants — one call per chain, not one per strike. Full mapping in the migration guide.
- Errors are typed (`PHClientError` hierarchy) instead of raw `requests`/`httpx` exceptions.
- Responses are typed `ResponseEnvelope`s — call `.data` for the payload.
- After you cut over: the MOCK-ticker loop (~140 404s/day on `/api/theta/hist/stock/eod/MOCK`) is separate and probably unintended — kill it too.

## Expectations

- This is a hand-onboarded private beta. Nothing about your PH_API access token or CF Access setup changes.
- If something you used in 1.x has no obvious v2 equivalent, tell me what you were calling and I'll map it.
