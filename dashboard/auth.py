"""Dashboard authentication & rate limiting.

Provides API key validation and rate limiting for orchestrator endpoints.
"""

import os
from typing import Optional
from fastapi import Depends, HTTPException, Request, status


# Load API key from environment (defaults to development key if not set).
# Production deployment should set DASHBOARD_API_KEY in .env or deployment config.
DEFAULT_API_KEY = "dev-key-change-in-production"
API_KEY = os.environ.get("DASHBOARD_API_KEY", DEFAULT_API_KEY)


async def verify_api_key(request: Request) -> str:
    """Verify API key from Authorization header.

    Authorization header format: 'Bearer <key>' or just '<key>'.
    Returns the key if valid, raises 401 if missing/invalid.
    """
    auth_header = request.headers.get("Authorization", "").strip()

    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Use 'Authorization: Bearer <key>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Handle both 'Bearer <key>' and bare '<key>' formats
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        key = parts[1]
    elif len(parts) == 1:
        key = parts[0]
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization format. Use 'Authorization: Bearer <key>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return key


async def require_dispatch_configured(request: Request) -> None:
    """Fail closed for the two worker-dispatch routes (Quant Console Phase 2:
    POST /runs/{run_id}/dispatch/{action}, GET /runs/{run_id}/dispatch/{job_id})
    when DASHBOARD_API_KEY is unset or left at its public, hardcoded default.

    Dispatch's blast radius (an LLM-driven subprocess with filesystem write
    access and, for `explain`, live network egress) is materially worse than
    run-triggering's, which already tolerates the default key via
    verify_api_key. This is a *separate*, additional dependency layered on
    top of verify_api_key for those two routes only -- it is not a
    replacement for it and is not applied to any pre-existing route
    (`/run/{suite_or_unified}`, `/runs/{run_id}`, `/quant`, etc.).

    Evaluated per-request, as a normal FastAPI dependency (re-reading the
    module-level API_KEY on every call) -- deliberately NOT a check that runs
    once at import/process-startup time. dashboard/app.py has no
    router/sub-app split (every route is registered directly on `app`), so a
    startup-time check that raises/exits would take down the entire,
    already-working dashboard process (swap browser, run-trigger, poll, etc.)
    over a misconfiguration that only matters for these two dispatch routes.
    """
    if not API_KEY or API_KEY == DEFAULT_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "dispatch disabled: DASHBOARD_API_KEY not configured. "
                "Set DASHBOARD_API_KEY in the root .env to a non-default "
                "value to enable worker dispatch."
            ),
        )


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
