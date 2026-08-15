"""Shared validation primitives for acquisition provenance boundaries."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_source_hashes(value: Any) -> tuple[str, ...]:
    """Return normalized source hashes or reject malformed provenance."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("source_hashes must be a non-empty list of SHA-256 hashes")
    hashes = tuple(str(item).lower() for item in value)
    if any(not SHA256_RE.fullmatch(item) for item in hashes):
        raise ValueError("source_hashes must contain 64-character hexadecimal SHA-256 hashes")
    return hashes


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a manifest without permitting non-standard JSON numbers."""
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def normalize_hashes(value: Iterable[str]) -> tuple[str, ...]:
    """Normalize an iterable after validating each source hash."""
    return validate_source_hashes(tuple(value))


__all__ = ["SHA256_RE", "canonical_json_bytes", "normalize_hashes", "validate_source_hashes"]
