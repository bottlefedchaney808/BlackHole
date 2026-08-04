"""Dashboard request helpers.

This dashboard is localhost-only, single-user, and already keeps its real
secrets (ThetaData credentials) in plaintext in the root `.env`/`shared/config.py`
-- there is no unauthenticated network exposure for an HTTP-level API key to
protect against, so the API-key auth layer that used to gate `/run/*` and the
worker-dispatch routes (`verify_api_key`, `require_dispatch_configured`) has
been removed. Don't expose this dashboard beyond localhost without adding
real auth back first (see CLAUDE.md).
"""

from fastapi import Request


def get_client_ip(request: Request) -> str:
    """Extract client IP from request.

    Handles X-Forwarded-For header (for proxied requests) and direct client.host.
    """
    # Check for X-Forwarded-For header (proxy, Cloudflare, etc.)
    forwarded_for = request.headers.get("X-Forwarded-For", "").split(",")
    if forwarded_for and forwarded_for[0].strip():
        return forwarded_for[0].strip()
    # Fallback to direct client IP
    return request.client.host if request.client else "unknown"
