#!/usr/bin/env python3
"""Standalone Tier 2 puller: resolves 2–7 DTE expiry per ticker and calls
seed_data_maker.py with list-arg subprocesses (no shell quoting).
Writes tier2_seed_<ts>/seed_data_<TICKER>_<EXPIRY>_7d.json + tier2_pull_summary.json
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\bottl\FinancialDevelopment")
VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
VOL_SUITE = PROJECT_ROOT / "Vol_Suite"
CACHE_DIR = VOL_SUITE / "_expiry_falsifier_cache"
SEED_DATA_MAKER = VOL_SUITE / "seed_data_maker.py"

TICKERS = [
    "SPY", "QQQ", "AAPL", "NVDA", "AMD",
    "TSLA", "MSFT", "META", "GOOGL", "AMZN",
    "NFLX", "JPM",
]


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def write_probe_script(ticker: str) -> Path:
    src = f"""import os, sys
sys.path.insert(0, r'{PROJECT_ROOT.as_posix()}')
from shared.config import load_env_once
load_env_once()
from shared.thetadata import ThetaDataController
from datetime import datetime, timedelta

td = ThetaDataController()
today = datetime.now().date()
exps = td.list_expirations({ticker!r})
short = []
for e in exps:
    try:
        ed = datetime.strptime(e, "%Y%m%d").date()
        dte = (ed - today).days
        if 2 <= dte <= 7:
            short.append((e, dte))
    except Exception:
        pass
short.sort(key=lambda x: x[1], reverse=True)
print(short[0][0] if short else "")
td.close()
"""
    p = PROJECT_ROOT / f"tier2_probe_{ticker}.py"
    p.write_text(src, encoding="utf-8")
    return p


def probe_expiry(ticker: str) -> str:
    probe_path = write_probe_script(ticker)
    try:
        res = subprocess.run(
            ["env", "-u", "PYTHONPATH", "-u", "VIRTUAL_ENV", str(VENV_PY), str(probe_path)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            env=clean_env(),
        )
        return res.stdout.strip()
    finally:
        probe_path.unlink(missing_ok=True)


def pull_ticker(ticker: str, out_dir: Path, lookback: int = 7) -> dict:
    expiry = probe_expiry(ticker)
    if not expiry:
        return {
            "ticker": ticker,
            "status": "BLOCKED",
            "error": "No 2–7 DTE expiry found",
            "raw_path": None,
            "manifest": None,
        }

    cmd = [
        "env", "-u", "PYTHONPATH", "-u", "VIRTUAL_ENV",
        str(VENV_PY), str(SEED_DATA_MAKER),
        ticker, str(lookback), str(out_dir), expiry,
    ]
    res = subprocess.run(cmd, cwd=str(VOL_SUITE), capture_output=True, text=True, timeout=600, env=clean_env())
    stdout = res.stdout
    stderr = res.stderr
    rc = res.returncode

    expected = out_dir / f"seed_data_{ticker}_{expiry}_{lookback}d.json"
    if not expected.exists():
        return {
            "ticker": ticker,
            "status": "BLOCKED",
            "error": f"seed_data_maker exited {rc}; file not written",
            "command": cmd,
            "stdout": stdout[-2000:],
            "stderr": stderr[-2000:],
            "raw_path": None,
            "manifest": None,
        }

    with open(expected, "r", encoding="utf-8") as f:
        payload = json.load(f)
    manifest = payload.get("manifest", {})
    greeks = payload.get("greeks", [])
    oi = payload.get("oi", [])
    spot = payload.get("spot", [])

    dates = sorted({g["date"] for g in greeks if g.get("date")})
    dtes = []
    for d in dates:
        try:
            dd = datetime.datetime.strptime(d, "%Y%m%d").date()
            ed = datetime.datetime.strptime(expiry, "%Y%m%d").date()
            dtes.append((ed - dd).days)
        except Exception:
            pass
    dte_min = min(dtes) if dtes else None
    dte_max = max(dtes) if dtes else None

    return {
        "ticker": ticker,
        "status": "OK",
        "command": cmd,
        "stdout": stdout[-2000:],
        "stderr": stderr[-2000:],
        "exit_code": rc,
        "raw_path": str(expected),
        "file_size": expected.stat().st_size,
        "sha256": sha256(expected),
        "expiry": expiry,
        "lookback": lookback,
        "manifest": manifest,
        "n_greeks": len(greeks),
        "n_oi": len(oi),
        "n_spot": len(spot),
        "n_dates": len(dates),
        "dte_min": dte_min,
        "dte_max": dte_max,
    }


def main():
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tier2_dir = CACHE_DIR / f"tier2_seed_{ts}"
    tier2_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for ticker in TICKERS:
        print(f"[tier2] pulling {ticker} ...", flush=True)
        try:
            r = pull_ticker(ticker, tier2_dir, lookback=7)
        except Exception as e:
            r = {
                "ticker": ticker,
                "status": "BLOCKED",
                "error": f"Exception: {type(e).__name__}: {e}",
                "raw_path": None,
                "manifest": None,
            }
        results.append(r)
        print(f"[tier2] {ticker} -> {r['status']}", flush=True)
        time.sleep(2)

    summary = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tier2_dir": str(tier2_dir),
        "results": [
            {k: v for k, v in r.items() if k not in ("stdout", "stderr")}
            for r in results
        ],
    }
    out_json = CACHE_DIR / f"tier2_pull_summary_{ts}.json"
    out_json.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"[tier2] summary -> {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
