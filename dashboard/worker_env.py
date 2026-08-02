"""worker_env.py

Task 8 of docs/superpowers/plans/2026-08-01-quant-console.md: builds the
explicit environment allowlist every worker subprocess (Task 9's dispatched
`claude -p` processes for the `interpret`/`investigate`/`explain` actions)
is launched with, instead of the full inherited `os.environ`.

This is the single most load-bearing constraint in the whole quant-console
plan (Global Constraints; spec Security section, findings R2-F1/R3-F2).
`subprocess.Popen` inherits the complete parent environment unless `env=`
is explicitly overridden, and this dashboard process's environment is
*structurally guaranteed* to contain `DASHBOARD_API_KEY` (`dashboard/auth.py`
reads it on every request) plus, once Task 1's `shared.config.load_env_once()`
runs, every credential in the root `.env` (`THETADATA_CF_ACCESS_CLIENT_ID`/
`_SECRET`, etc). A worker subprocess -- one of which (`explain`) is granted
live network egress -- must never see any of that: a prompt-injected job
reading residual environment state and shipping it out over `WebFetch` is
exactly the exfiltration path this module exists to close.

`build_worker_env()` is therefore an **allowlist, not a blocklist**: it only
ever copies a small, fixed set of keys out of the *current* `os.environ` into
a brand-new dict. Every other variable -- named here or not, present before
or added later by `load_env_once()` -- is omitted by construction, never
filtered after the fact.
"""

import os

# The complete allowlist (spec Security section): PATH so the subprocess can
# resolve executables, HOME/USERPROFILE (whichever this OS actually sets) so
# it can resolve a home directory, TEMP for scratch files. Nothing else --
# in particular, no Anthropic/Claude credential: `claude -p` is expected to
# authenticate via the operator's own existing Claude Code session/
# credentials, configured outside this repo, not via a repo-defined env var.
_ALLOWED_KEYS = ("PATH", "HOME", "USERPROFILE", "TEMP")


def build_worker_env() -> dict:
    """Return a fresh dict containing only the allowlisted environment
    variables, sourced from the current ``os.environ``.

    Only ``PATH``, ``HOME``/``USERPROFILE`` (whichever is set on this OS),
    and ``TEMP`` are copied through. ``DASHBOARD_API_KEY``,
    ``THETADATA_CF_ACCESS_CLIENT_ID``/``_SECRET``, ``SWAPS_DB_PATH``, and
    every other variable in ``os.environ`` -- including anything
    ``shared.config.load_env_once()`` added -- are excluded by construction:
    this function never starts from a copy of ``os.environ`` and subtracts
    from it, it only ever adds the named keys, one at a time, if present.

    A key missing from ``os.environ`` (e.g. no ``HOME`` on Windows, no
    ``USERPROFILE`` on POSIX) is simply omitted from the result -- never
    included as ``None``/empty, and never raises.
    """
    return {key: os.environ[key] for key in _ALLOWED_KEYS if key in os.environ}
