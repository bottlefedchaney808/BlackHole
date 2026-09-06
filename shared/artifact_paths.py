"""shared/artifact_paths.py -- portable artifact path helpers.

Every run produces artifact files (PNG charts, JSON manifests, CSVs). Those
paths need to be:
  1. Stored in a machine-independent way (repo-relative POSIX)
  2. Resolved back to absolute for reading/writing on each host

This module owns that translation layer. All paths stored in the archive
(`module_archive.artifact_paths_json`) and in run manifests are repo-relative
POSIX strings; all callers absolutize at read time.

Repo root is derived from __file__ at module load time, so it is stable
per-process (the repo path doesn't change during execution).

Path conventions:
  - Windows: C:\\Users\\bottl\\FinancialDevelopment\\outputs\\r1\\c.png
  - Linux:   /opt/data/FinancialDevelopment/outputs/r1/c.png
  - Stored:  outputs/r1/c.png
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT: Path | None = None


def repo_root() -> Path:
    """Derive the repository root from __file__ (shared/artifact_paths.py).

    The module lives at <repo_root>/shared/artifact_paths.py, so the root
    is two levels up (parent.parent).

    This is computed once at import time and cached, so it stays stable
    during a single run. If the repo is moved on disk, a new process is
    needed to pick up the new path.
    """
    global _REPO_ROOT
    if _REPO_ROOT is None:
        # __file__ is <repo>/shared/artifact_paths.py
        _REPO_ROOT = Path(__file__).resolve().parent.parent
    return _REPO_ROOT


def to_rel(path: str | Path) -> str:
    """Convert an absolute path to a repo-relative POSIX path.

    If the path is under the repo root, strips the root prefix and
    normalizes separators to '/'. If the path is outside the repo
    root (e.g. a temp file, or a legacy Windows path on Linux), returns
    the original path unchanged so callers can decide how to handle it.

    Tolerates Windows-style paths like 'C:\\Users\\bottl\\FinancialDevelopment\\...'
    by stripping everything through the FinancialDevelopment segment.

    This is the writer-side helper: store repo-relative paths in the archive.
    """
    original = str(path)

    # Windows drive-letter path (C:\...): only meaningful if it points into a
    # FinancialDevelopment checkout. Strip through the marker segment and try
    # to resolve against THIS repo root; otherwise leave unchanged below.
    normalized = original.replace('\\', '/')
    if len(normalized) > 1 and normalized[1] == ':':
        parts = normalized[2:].lstrip('/').split('/')
        for i, part in enumerate(parts):
            if part.lower() == 'financialdevelopment':
                candidate = repo_root() / '/'.join(parts[i + 1:])
                try:
                    return str(candidate.relative_to(repo_root())).replace(os.sep, '/')
                except ValueError:
                    pass
        # Drive-letter path that is not a FinancialDevelopment checkout:
        # foreign absolute path — leave unchanged.
        return original

    # Absolute path (POSIX or native Windows): if it is under the repo root,
    # make it relative; otherwise return unchanged so readers can still use it.
    p = Path(original)
    if os.path.isabs(p):
        try:
            rel = p.resolve().relative_to(repo_root())
            return str(rel).replace(os.sep, '/')
        except ValueError:
            return original

    # Relative path: resolve against repo root (not CWD), then re-relativize.
    resolved = (repo_root() / normalized).resolve()
    try:
        rel = resolved.relative_to(repo_root())
        return str(rel).replace(os.sep, '/')
    except ValueError:
        return original


def from_rel(rel_path: str) -> Path:
    """Resolve a repo-relative path to an absolute Path.

    Expecting a POSIX-style path like 'outputs/r1/chart.png', joins it
    with the repo root. If the stored path is already absolute (legacy
    Windows path or outside-root path), returns it as-is after normalizing
    separators.
    """
    # If it looks absolute (starts with / or has a drive letter prefix),
    # normalize and return as-is.
    if rel_path.startswith('/') or (len(rel_path) > 1 and rel_path[1] == ':'):
        normalized = rel_path.replace('\\', '/')
        return Path(normalized)

    # Repo-relative path
    return repo_root() / rel_path.replace('\\', '/')


def resolve_stored(path: str) -> Path | None:
    """Resolve a stored artifact path to an absolute Path.

    Handles three cases:
      1. Already absolute and exists (POSIX or Windows drive-letter, with or
         without the FinancialDevelopment marker) -> use it as-is
      2. Repo-relative POSIX ('outputs/r1/chart.png') -> join with repo root
      3. Missing file in any case -> None

    Returns None if the resolved path doesn't exist (caller decides fallback).
    """
    if not path:
        return None

    # Windows-style drive-letter path pointing into a FinancialDevelopment
    # checkout on ANOTHER machine: strip through the marker and rejoin here.
    normalized = path.replace('\\', '/')
    if len(normalized) > 1 and normalized[1] == ':':
        parts = normalized[2:].lstrip('/').split('/')
        for i, part in enumerate(parts):
            if part.lower() == 'financialdevelopment':
                rest = '/'.join(parts[i + 1:])
                resolved = repo_root() / rest
                return resolved if resolved.exists() else None

    # Absolute path (POSIX or native Windows drive-letter). On the host that
    # produced it, this is a real existing file — use it directly.
    p = Path(path)
    if os.path.isabs(p):
        return p if p.exists() else None

    # Repo-relative: join with repo root (not CWD).
    resolved = from_rel(path)
    return resolved if resolved.exists() else None
