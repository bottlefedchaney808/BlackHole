"""Shared disk cache for all Financial_Development suites.

Usage:
    from shared.cache import load, save, clear

    data = load("mytag")
    if data is None:
        data = expensive_fetch()
        save("mytag", data)
"""
import json
import hashlib
import os
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".shared_cache"


def _cache_key(tag: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(tag.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def load(tag: str):
    """Return cached data for *tag*, or None if missing / corrupt."""
    p = _cache_key(tag)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def save(tag: str, data):
    """Write *data* (JSON-serialisable) under *tag*.  No-ops on empty data."""
    if not data:
        return
    try:
        _cache_key(tag).write_text(json.dumps(data))
    except Exception:
        pass


def clear():
    """Wipe entire .shared_cache directory."""
    import shutil
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)


def get_path(tag: str) -> str:
    """Return the on-disk path for *tag* without creating the file."""
    return str(_cache_key(tag))