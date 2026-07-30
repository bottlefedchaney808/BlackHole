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
