"""
Active-path import guard for the current Options_Suite layout.

Two jobs:
  (a) Prove every active-path module imports cleanly through the package.
  (b) Prove importing the active path does not transitively pull in the
      migrated-only legacy module names that remain intentionally absent
      from the current repo.

The migrated tree also quarantined fetch_options.py and chain_evaluation.py,
but the current repo still ships those modules and main.py imports
chain_evaluation on demand, so they are not part of this repo's quarantine
contract.
"""

import importlib
import sys

LEGACY_MODULES = [
    "_legacy_vanna_volga",
    "_legacy_vv_implement",
    "async_HSivSIM",
    "models_params",
    "sabr_params_dict",
    "tools",
]

ACTIVE_MODULES = [
    "Options_Suite.american_binomial",
    "Options_Suite.MC",
    "Options_Suite.barone_adesi_whaley",
    "Options_Suite.MCHestonLSM",
    "Options_Suite.SABRModel",
    "Options_Suite.VannaVolga",
    "Options_Suite.bruteforceimpliedvol",
    "Options_Suite.NewtonRaphsonIV",
    "Options_Suite.vol_manager",
]

# Options_Suite.main is a CLI entrypoint that exits at import time when
# ThetaData credentials are absent, so the active import regression only
# guards the secret-free library surface above.


def test_active_import_guard_avoids_credential_gated_entrypoint():
    """The import guard must stay secret-free and avoid CLI credential gates."""
    assert "Options_Suite.main" not in ACTIVE_MODULES


def test_active_modules_import_cleanly():
    """The active package path must import without raising."""
    for name in ACTIVE_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - surface a clear failure
            raise AssertionError(
                f"ACTIVE module '{name}' failed to import: {type(exc).__name__}: {exc}"
            ) from exc


def test_active_path_does_not_import_migrated_legacy_modules():
    """The active path must not leak migrated-only legacy module names."""
    for name in ACTIVE_MODULES:
        importlib.import_module(name)

    leaked = [module for module in LEGACY_MODULES if module in sys.modules]
    assert not leaked, (
        "Active path transitively imported migrated-only legacy module(s): "
        f"{leaked}. These names are intentionally absent from the current "
        "repo and must stay off the active import path."
    )
