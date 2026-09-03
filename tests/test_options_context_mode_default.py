"""Test for Phase 5 of Modularization Overhaul: default Leisen-Reimer selection
in Options_Suite context mode when no 'modules' key present (byte-identical default).
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_opts = REPO_ROOT / "Options_Suite"
if str(_opts) not in sys.path:
    sys.path.insert(0, str(_opts))

# Options_Suite/main.py uses flat cwd-relative imports (`from config import
# PricingConfig`). When the full tests/ dir is collected, another test module
# (sentiment-scanner's config) can already own `sys.modules["config"]`, and
# _opts leading sys.path doesn't help — the stale entry wins. Purge the flat
# names so they re-resolve against Options_Suite (same failure class as the
# spec_from_file_location pattern used by Tools/tools/price_dist_tool.py).
for _flat in ("config", "market_data", "vol_manager", "american_binomial", "MC",
              "VannaVolga", "NewtonRaphsonIV", "bruteforceimpliedvol",
              "SABRModel", "barone_adesi_whaley"):
    sys.modules.pop(_flat, None)
# Earlier-collected test modules can leave sentiment-scanner/ at sys.path[0];
# put Options_Suite back at the front so the flat names resolve HERE.
while str(_opts) in sys.path:
    sys.path.remove(str(_opts))
sys.path.insert(0, str(_opts))

import Options_Suite.main as main_mod


def test_options_context_mode_default_selects_leisen_reimer_only(monkeypatch, tmp_path):
    """run_context_mode(context without modules key) still selects Leisen-Reimer only."""
    # mocks to avoid network / live deps
    class FakeMD:
        def fetch_spot_price(self, t): return 100.0
        def fetch_risk_free_rate(self): return 0.05
        def fetch_dividend_yield(self, t): return 0.01
        def validate_strike(self, *a, **k): return {"closest": 100.0}

    class FakeVol:
        def get_sigma(self, *a, **k): return 0.2

    monkeypatch.setattr(main_mod, "MarketDataController", lambda: FakeMD())
    monkeypatch.setattr(main_mod, "VolManager", lambda: FakeVol())
    monkeypatch.setattr(main_mod, "leisen_reimer_american_price", lambda *a, **k: 5.0)
    monkeypatch.setattr(main_mod, "lr_all_greeks", lambda *a, **k: {"delta": 0.5})

    ctx = {
        "focus": {
            "ticker": "SPY",
            "option_type": "call",
            "target_years": 1.0,
        }
    }
    # deliberately no "modules" key -> should default to leisen only
    ctxf = tmp_path / "ctx.json"
    ctxf.write_text(json.dumps(ctx))
    outf = tmp_path / "out.json"

    rc = main_mod.run_context_mode(str(ctxf), str(outf), no_interactive=True)
    assert rc == 0

    payload = json.loads(outf.read_text())
    assert payload.get("method") == "LeisenReimer"
    assert payload.get("status") == "ok"
    # ensure no other models unless explicitly asked
    assert "other_models" not in payload or not payload["other_models"]


def test_options_context_mode_with_modules_uses_registry(monkeypatch, tmp_path):
    """Explicit modules key triggers registry path (Phase 5)."""
    class FakeMD:
        def fetch_spot_price(self, t): return 100.0
        def fetch_risk_free_rate(self): return 0.05
        def fetch_dividend_yield(self, t): return 0.01
        def validate_strike(self, *a, **k): return {"closest": 100.0}

    class FakeVol:
        def get_sigma(self, *a, **k): return 0.2

    monkeypatch.setattr(main_mod, "MarketDataController", lambda: FakeMD())
    monkeypatch.setattr(main_mod, "VolManager", lambda: FakeVol())
    monkeypatch.setattr(main_mod, "leisen_reimer_american_price", lambda *a, **k: 5.0)
    monkeypatch.setattr(main_mod, "lr_all_greeks", lambda *a, **k: {"delta": 0.5})

    ctx = {
        "focus": {"ticker": "SPY", "option_type": "call", "target_years": 1.0},
        "modules": ["leisen_reimer", "crr"],
    }
    ctxf = tmp_path / "ctx.json"
    ctxf.write_text(json.dumps(ctx))
    outf = tmp_path / "out.json"

    rc = main_mod.run_context_mode(str(ctxf), str(outf), no_interactive=True)
    assert rc == 0
    payload = json.loads(outf.read_text())
    assert payload.get("method") == "LeisenReimer"
    # registry path exercised (other_models may be added depending on resolve success)
