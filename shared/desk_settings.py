"""desk_settings.py -- small persisted preferences chosen from the dashboard.

Some defaults are the operator's call, not the code's: which jump-diffusion
model is "the" model, for instance. Those used to be env vars, which means a
default could only be changed by restarting the dashboard with a different
environment. This keeps them in one JSON file the dashboard, a CLI run and a
scheduled job all read the same way.

Not the Context Store: that holds computed results keyed by scope. This holds
a handful of global choices. Every read tolerates a missing or corrupt file
(returns the caller's default) -- a bad settings file must never fail a run.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCK = threading.Lock()


def settings_path() -> Path:
    override = os.environ.get("DESK_SETTINGS_PATH", "").strip()
    return (
        Path(override) if override else _REPO_ROOT / "artifacts" / "desk_settings.json"
    )


def load_settings() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def get_setting(key: str, default: Any = None) -> Any:
    value = load_settings().get(key)
    return default if value in (None, "") else value


def set_setting(key: str, value: Any) -> dict[str, Any]:
    """Persist one key atomically; returns the full settings dict after the write."""
    path = settings_path()
    with _LOCK:
        data = load_settings()
        data[key] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".desk_settings.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return data
