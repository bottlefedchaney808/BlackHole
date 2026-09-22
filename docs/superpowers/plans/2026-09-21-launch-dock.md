# Launch Dock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local dock on `127.0.0.1:8792` that collects stocks, perp, and event-desk cards, syncs algo knobs with the chart preset file, and stop/launches existing runners without a new order path.

**Architecture:** `chart_app.profiles` stays the only algo store, with a cross-process lock and atomic replace. `launch_dock` owns cards, seed knobs, argv, and pid state in `artifacts/launch_dock.json`. The running sleeve is not patched to re-read. A save after launch marks a perp card stale. Stop then Launch applies it.

**Tech Stack:** Python 3.11, stdlib (`msvcrt` lock, `http.server`, `urllib`), pytest, existing `chart_app.profiles` and `PerpsClient.cancel_resting`. No new dependencies.

## Global Constraints

- Bind `127.0.0.1:8792` only. Not `0.0.0.0`. Not `:8791`. Not `:8787`.
- Stocks and perp interpreter: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe`.
- Event desk: `py -3.11 event_desk/churn.py watch`, cwd `E:/BlackHole_Investments/BlackHole/Event_Desk`. Never the BlackHole venv. Missing `py -3.11` fails the launch and names that fact.
- Algo file: `E:/BlackHole_Investments/BlackHole/artifacts/chart_app_profiles.json`. Card file: `E:/BlackHole_Investments/BlackHole/artifacts/launch_dock.json`.
- Lock file: `artifacts/chart_app_profiles.lock`. Temp file: `chart_app_profiles.json.tmp`, then `os.replace`.
- Chart up: `POST /api/profiles` with `ticker` and `interval` in the body. Do not also write the file. `ok: false` does not fall back to a direct write.
- Chart down: `profiles.save` on the absolute path.
- A knob change stores `validation: in_sample`. The pad cannot store `walk_forward` or `permutation`.
- Copy changes the instrument only. It does not copy leverage, `live`, `pnl_since`, `base_capital`, or the profile record.
- Standing leverage: `BTC-PERP` 6, `XRP-PERP` 2, anything else including `GOLD` is null. Null leverage refuses live and dry-run.
- Perp seed `cap_dollars: null` refuses launch. `hours: 0` means until stopped and is allowed.
- New copy is dry-run. It does not inherit `--live`.
- Relaunch passes stored `--pnl-since` and `--base-capital`. The dock never invents now.
- Perp stop: command line must contain `run_live_perp` and this `--ticker`, then `cancel_resting` for that Kalshi ticker only, then kill. Never `cancel_all`. Cancel failure does not kill.
- Dead live pid still calls `cancel_resting`, then marks stopped. Startup does not cancel.
- Wrong command line: state `unknown`, no cancel, no kill.
- Stale is perp-only, and only for that card's own `ticker|interval`.
- Pid poll every 2 seconds marks an exited pid `stopped` and does not cancel.
- Two cards with the same seed and instrument do not both run. One event-desk watch globally.
- Detach flags: `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW`.
- Dock does not send orders, does not edit `event_desk/config.py`, does not re-read inside `run_live_perp.py`.

---

### Task 1: Locked atomic profile save

**Files:**
- Modify: `chart_app/profiles.py`
- Test: `chart_app/tests/test_profiles.py`

**Interfaces:**
- Consumes: existing `profiles.save` / `profiles.delete` / `profiles.load_all`
- Produces: same signatures. `save` and `delete` take a cross-process lock and replace the file atomically. Lock timeout raises `TimeoutError`.

- [ ] **Step 1: Write the failing test**

Append to `chart_app/tests/test_profiles.py`:

```python
def test_save_replaces_atomically_and_keeps_both_keys(store, tmp_path):
    profiles.save("SPY", "15m", elmo={"liq_window": 3}, path=store)
    profiles.save("NVDA", "15m", elmo={"liq_window": 9}, path=store)
    data = json.loads(store.read_text(encoding="utf-8"))
    assert data["SPY|15m"]["elmo"]["liq_window"] == 3
    assert data["NVDA|15m"]["elmo"]["liq_window"] == 9
    assert not store.with_name("profiles.json.tmp").exists()


def test_delete_uses_the_same_replace(store):
    profiles.save("SPY", "15m", elmo={"liq_window": 3}, path=store)
    assert profiles.delete("SPY", "15m", path=store) is True
    assert "SPY|15m" not in json.loads(store.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest chart_app/tests/test_profiles.py::test_save_replaces_atomically_and_keeps_both_keys -q`

Expected: FAIL. The tmp-name assertion fails because save still uses `write_text` on the destination and never creates `profiles.json.tmp`. That failure is the signal to add the replace. If the assertion on keys passes and only the tmp assertion fails, that is the expected RED.

- [ ] **Step 3: Write minimal implementation**

In `chart_app/profiles.py`, add a lock and replace helper, and use it from `save` and `delete`. Keep every existing field and the validation check.

```python
import time

class ProfileLockTimeout(TimeoutError):
    pass


def _lock_path(path: Path) -> Path:
    return path.with_name("chart_app_profiles.lock") if path.name == "chart_app_profiles.json" else path.with_name(path.name + ".lock")


def _replace_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


@contextmanager
def _file_lock(path: Path, timeout: float = 5.0):
    lock = _lock_path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock, "a+b")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ProfileLockTimeout(f"lock timeout: {lock}")
                time.sleep(0.05)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()
```

Add `from contextlib import contextmanager` if it is not already imported. Wrap the read-modify-write in `save` and `delete`:

```python
def save(..., path: Path | None = None) -> dict[str, Any]:
    if validation not in VALIDATIONS:
        raise ValueError(f"validation must be one of {VALIDATIONS}, got {validation!r}")
    p = path or store_path()
    with _file_lock(p):
        data = load_all(p)
        record = { ... existing record dict ... }
        data[_key(ticker, interval)] = record
        _replace_json(p, data)
    return record


def delete(...) -> bool:
    p = path or store_path()
    with _file_lock(p):
        data = load_all(p)
        if _key(ticker, interval) not in data:
            return False
        del data[_key(ticker, interval)]
        _replace_json(p, data)
    return True
```

Do not change `load_all` or `resolve`.

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest chart_app/tests/test_profiles.py -q`

Expected: PASS, including the existing six tests.

- [ ] **Step 5: Commit**

```bash
git add chart_app/profiles.py chart_app/tests/test_profiles.py
git commit -m "fix(profiles): lock and replace the preset file"
```

---

### Task 2: Card registry and copy rules

**Files:**
- Create: `launch_dock/__init__.py`
- Create: `launch_dock/registry.py`
- Create: `launch_dock/tests/test_registry.py`

**Interfaces:**
- Consumes: nothing from Task 1
- Produces:
  - `EMPTY = {"seeds": {"perp": {"hours": 0.0, "cap_dollars": None}, "stocks": {}, "event_desk": {}}, "cards": []}`
  - `STANDING_LEVERAGE = {"BTC-PERP": 6.0, "XRP-PERP": 2.0}`
  - `load(path: Path) -> dict`
  - `save(data: dict, path: Path) -> None`
  - `copy_card(data: dict, source_id: str, new_id: str, instrument: str) -> dict` returns the new card and appends it

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
import json
import pytest
from launch_dock.registry import EMPTY, STANDING_LEVERAGE, copy_card, load, save


def test_copy_keeps_lane_and_drops_books(tmp_path: Path):
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "perp-btc",
        "seed": "perp",
        "instrument": "BTC-PERP",
        "interval": "15m",
        "leverage": 6.0,
        "live": True,
        "subaccount": 0,
        "pnl_since": "2026-09-20T00:00:00+00:00",
        "base_capital": 150.0,
        "pid": 99,
        "argv": ["run_live_perp"],
        "running_saved_at": "2026-09-20T00:00:00+00:00",
        "state": "running",
    })
    card = copy_card(data, "perp-btc", "perp-xrp", "XRP-PERP")
    assert card["seed"] == "perp"
    assert card["instrument"] == "XRP-PERP"
    assert card["leverage"] == STANDING_LEVERAGE["XRP-PERP"]
    assert card["live"] is False
    assert card["pnl_since"] == ""
    assert card["base_capital"] is None
    assert card["pid"] is None
    assert card["state"] == "stopped"
    assert "config" not in card and "elmo" not in card


def test_gold_copy_has_null_leverage(tmp_path: Path):
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
        "interval": "15m", "leverage": 6.0, "live": True, "subaccount": 0,
        "pnl_since": "", "base_capital": None, "pid": None, "argv": [],
        "running_saved_at": None, "state": "stopped",
    })
    card = copy_card(data, "perp-btc", "perp-gold", "GOLD")
    assert card["leverage"] is None
    assert card["live"] is False


def test_event_desk_copy_refused():
    data = json.loads(json.dumps(EMPTY))
    data["cards"].append({
        "id": "desk", "seed": "event_desk", "instrument": "",
        "interval": "15m", "leverage": None, "live": False, "subaccount": 0,
        "pnl_since": "", "base_capital": None, "pid": None, "argv": [],
        "running_saved_at": None, "state": "stopped",
    })
    with pytest.raises(ValueError, match="event_desk"):
        copy_card(data, "desk", "desk-2", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_registry.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock`.

- [ ] **Step 3: Write minimal implementation**

`launch_dock/__init__.py` is empty.

`launch_dock/registry.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EMPTY: dict[str, Any] = {
    "seeds": {
        "perp": {"hours": 0.0, "cap_dollars": None},
        "stocks": {},
        "event_desk": {},
    },
    "cards": [],
}

STANDING_LEVERAGE = {"BTC-PERP": 6.0, "XRP-PERP": 2.0}


def load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    data = json.loads(json.dumps(EMPTY))
    if isinstance(raw, dict):
        seeds = raw.get("seeds") if isinstance(raw.get("seeds"), dict) else {}
        data["seeds"]["perp"].update(seeds.get("perp") or {})
        data["cards"] = list(raw.get("cards") or [])
    return data


def save(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def copy_card(data: dict[str, Any], source_id: str, new_id: str, instrument: str) -> dict[str, Any]:
    source = next((c for c in data["cards"] if c.get("id") == source_id), None)
    if source is None:
        raise ValueError(f"no card {source_id}")
    if source.get("seed") == "event_desk":
        raise ValueError("event_desk cards cannot be copied")
    if any(c.get("id") == new_id for c in data["cards"]):
        raise ValueError(f"id taken {new_id}")
    inst = instrument.upper()
    card = {
        "id": new_id,
        "seed": source["seed"],
        "instrument": inst,
        "interval": source.get("interval") or "15m",
        "leverage": STANDING_LEVERAGE.get(inst) if source["seed"] == "perp" else None,
        "live": False,
        "subaccount": 0,
        "pnl_since": "",
        "base_capital": None,
        "pid": None,
        "argv": [],
        "running_saved_at": None,
        "state": "stopped",
    }
    data["cards"].append(card)
    return card
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_registry.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/__init__.py launch_dock/registry.py launch_dock/tests/test_registry.py
git commit -m "feat(launch-dock): card copy stays in lane"
```

---

### Task 3: Chart-or-file preset sync

**Files:**
- Create: `launch_dock/sync.py`
- Create: `launch_dock/tests/test_sync.py`

**Interfaces:**
- Consumes: `chart_app.profiles.save`
- Produces:
  - `ChartSaveError`
  - `save_preset(ticker, interval, *, config, elmo, capital, note="", path, chart_post, existing) -> dict`
  - `chart_post(url, body) -> dict` is injected. Tests pass a fake. Production passes a urllib POST to `http://127.0.0.1:8791/api/profiles`.

- [ ] **Step 1: Write the failing test**

```python
import json
from chart_app import profiles
from launch_dock.sync import ChartSaveError, save_preset
import pytest


def test_chart_down_writes_the_file(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    rec = save_preset(
        "NVDA", "15m", config={"entry_long": 30}, elmo={}, capital=None,
        path=path, chart_post=None, existing=None,
    )
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["NVDA|15m"]["config"]["entry_long"] == 30
    assert rec["validation"] == "in_sample"


def test_chart_up_posts_and_does_not_write(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    seen = {}

    def post(url, body):
        seen["url"] = url
        seen["body"] = body
        return {"ok": True, "saved": {"ticker": "NVDA", "validation": "in_sample"}}

    save_preset(
        "NVDA", "15m", config={"entry_long": 30}, elmo={"liq_window": 7},
        capital=1000.0, path=path, chart_post=post, existing=None,
    )
    assert seen["url"] == "http://127.0.0.1:8791/api/profiles"
    assert seen["body"]["ticker"] == "NVDA"
    assert seen["body"]["interval"] == "15m"
    assert not path.exists()


def test_post_failure_does_not_direct_write(tmp_path):
    path = tmp_path / "chart_app_profiles.json"

    def post(url, body):
        return {"ok": False, "error": "bad"}

    with pytest.raises(ChartSaveError, match="bad"):
        save_preset(
            "NVDA", "15m", config={}, elmo={}, capital=None,
            path=path, chart_post=post, existing=None,
        )
    assert not path.exists()


def test_unchanged_save_keeps_walk_forward(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    profiles.save("NVDA", "15m", elmo={"liq_window": 7}, validation="walk_forward", path=path)
    existing = profiles.resolve("NVDA", "15m", path=path)
    rec = save_preset(
        "NVDA", "15m", config={}, elmo={"liq_window": 7}, capital=None,
        path=path, chart_post=None, existing=existing,
    )
    assert rec["validation"] == "walk_forward"


def test_changed_knob_downgrades_to_in_sample(tmp_path):
    path = tmp_path / "chart_app_profiles.json"
    profiles.save("NVDA", "15m", elmo={"liq_window": 7}, validation="walk_forward", path=path)
    existing = profiles.resolve("NVDA", "15m", path=path)
    rec = save_preset(
        "NVDA", "15m", config={}, elmo={"liq_window": 8}, capital=None,
        path=path, chart_post=None, existing=existing,
    )
    assert rec["validation"] == "in_sample"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_sync.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.sync`.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from chart_app import profiles

CHART_PROFILES = "http://127.0.0.1:8791/api/profiles"


class ChartSaveError(RuntimeError):
    pass


def _changed(existing: dict | None, config: dict, elmo: dict, capital: float | None) -> bool:
    if not existing or existing.get("source") == "defaults":
        return True
    return (
        dict(existing.get("config") or {}) != dict(config)
        or dict(existing.get("elmo") or {}) != dict(elmo)
        or existing.get("capital") != capital
    )


def save_preset(
    ticker: str,
    interval: str,
    *,
    config: dict[str, Any],
    elmo: dict[str, Any],
    capital: float | None,
    note: str = "",
    path: Path,
    chart_post: Callable[[str, dict], dict] | None,
    existing: dict | None,
) -> dict[str, Any]:
    validation = "in_sample"
    if existing and not _changed(existing, config, elmo, capital):
        stored = existing.get("validation") or "in_sample"
        if stored in profiles.VALIDATIONS:
            validation = stored
    body = {
        "ticker": ticker,
        "interval": interval,
        "config": dict(config),
        "elmo": dict(elmo),
        "capital": capital,
        "validation": validation,
        "metrics": {},
        "note": note,
    }
    if chart_post is not None:
        result = chart_post(CHART_PROFILES, body)
        if not result.get("ok"):
            raise ChartSaveError(str(result.get("error") or "chart save failed"))
        return result["saved"]
    return profiles.save(
        ticker, interval, config=config, elmo=elmo, capital=capital,
        validation=validation, note=note, path=path,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_sync.py chart_app/tests/test_profiles.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/sync.py launch_dock/tests/test_sync.py
git commit -m "feat(launch-dock): save presets through the chart when it is up"
```

---

### Task 4: Launch argv and refuse rules

**Files:**
- Create: `launch_dock/launch.py`
- Create: `launch_dock/tests/test_launch.py`

**Interfaces:**
- Consumes: card dict and seed dict from Task 2
- Produces:
  - `LaunchRefused(RuntimeError)`
  - `perp_argv(card, seed) -> list[str]`
  - `stocks_argv(card) -> list[str]`
  - `event_desk_argv() -> list[str]`
  - `KALSHI = {"BTC-PERP": "KXBTCPERP", "XRP-PERP": "KXXRPPERP", "GOLD": "KXGOLDPERP"}`
  - `DETACH` int flags
  - `apply_session(card, session: dict) -> dict` writes `pnl_since`, `base_capital`, `running_saved_at`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from launch_dock.launch import LaunchRefused, apply_session, event_desk_argv, perp_argv, stocks_argv

PY = r"E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe"


def _perp(**over):
    card = {
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "15m",
        "leverage": 6.0, "live": False, "subaccount": 0, "pnl_since": "",
        "base_capital": None, "state": "stopped",
    }
    card.update(over)
    return card


def test_perp_argv_uses_seed_cap_and_card_leverage():
    argv = perp_argv(_perp(), {"hours": 0.0, "cap_dollars": 100.0})
    assert argv[0] == PY
    assert argv[1:3] == ["-m", "chart_app.run_live_perp"]
    assert "--cap-dollars" in argv and "100.0" in argv
    assert "--leverage" in argv and "6.0" in argv
    assert "--live" not in argv
    assert "--pnl-since" not in argv


def test_relaunch_passes_stored_books():
    argv = perp_argv(
        _perp(pnl_since="2026-09-20T01:02:03+00:00", base_capital=150.0, live=True),
        {"hours": 0.0, "cap_dollars": 100.0},
    )
    assert "2026-09-20T01:02:03+00:00" in argv
    assert "150.0" in argv
    assert "--live" in argv


def test_null_cap_and_null_leverage_refuse():
    with pytest.raises(LaunchRefused, match="cap_dollars"):
        perp_argv(_perp(), {"hours": 0.0, "cap_dollars": None})
    with pytest.raises(LaunchRefused, match="leverage"):
        perp_argv(_perp(leverage=None), {"hours": 0.0, "cap_dollars": 100.0})


def test_second_running_card_refused():
    cards = [
        {"id": "a", "seed": "perp", "instrument": "BTC-PERP", "state": "running"},
        {"id": "b", "seed": "perp", "instrument": "BTC-PERP", "state": "stopped"},
    ]
    with pytest.raises(LaunchRefused, match="already running"):
        perp_argv(_perp(id="b"), {"hours": 0.0, "cap_dollars": 100.0}, cards=cards)


def test_stocks_and_event_desk_commands():
    argv = stocks_argv({"instrument": "nvda"})
    assert argv[:3] == [PY, "-m", "Direction.whale_scanner"]
    assert argv[-1] == "NVDA"
    desk = event_desk_argv()
    assert desk == ["py", "-3.11", "event_desk/churn.py", "watch"]


def test_apply_session_stores_runner_values():
    card = _perp()
    out = apply_session(card, {
        "pnl_since": "2026-09-21T12:00:00+00:00",
        "equity": 80.0,
    }, saved_at="2026-09-21T11:00:00+00:00")
    assert out["pnl_since"] == "2026-09-21T12:00:00+00:00"
    assert out["base_capital"] == 80.0
    assert out["running_saved_at"] == "2026-09-21T11:00:00+00:00"
    assert out["state"] == "running"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_launch.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.launch`.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

import os
from typing import Any

PY = r"E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe"
BLACKHOLE = r"E:/BlackHole_Investments/BlackHole"
EVENT_DESK = r"E:/BlackHole_Investments/BlackHole/Event_Desk"
KALSHI = {"BTC-PERP": "KXBTCPERP", "XRP-PERP": "KXXRPPERP", "GOLD": "KXGOLDPERP"}
DETACH = 0x00000200 | 0x00000008 | 0x08000000  # new group, detached, no window


class LaunchRefused(RuntimeError):
    pass


def _busy(cards: list[dict], card: dict) -> None:
    for other in cards:
        if other.get("id") == card.get("id"):
            continue
        if other.get("state") not in ("running", "launching"):
            continue
        if card["seed"] == "event_desk" and other.get("seed") == "event_desk":
            raise LaunchRefused("event_desk watch already running")
        if other.get("seed") == card.get("seed") and other.get("instrument") == card.get("instrument"):
            raise LaunchRefused(f"{card['instrument']} already running")


def perp_argv(card: dict, seed: dict, cards: list[dict] | None = None) -> list[str]:
    if cards:
        _busy(cards, card)
    cap = seed.get("cap_dollars")
    if not cap or float(cap) <= 0:
        raise LaunchRefused("perp seed cap_dollars is unset")
    if card.get("leverage") is None:
        raise LaunchRefused("leverage is unset")
    if card["instrument"].upper() not in KALSHI:
        raise LaunchRefused(f"no Kalshi map for {card['instrument']}")
    hours = float(seed.get("hours") or 0.0)
    argv = [
        PY, "-m", "chart_app.run_live_perp",
        "--ticker", card["instrument"].upper(),
        "--interval", card.get("interval") or "15m",
        "--cap-dollars", str(float(cap)),
        "--leverage", str(float(card["leverage"])),
        "--hours", str(hours),
        "--subaccount", str(int(card.get("subaccount") or 0)),
    ]
    if card.get("pnl_since"):
        argv += ["--pnl-since", card["pnl_since"]]
        if card.get("base_capital") is None:
            raise LaunchRefused("pnl_since set but base_capital missing")
        argv += ["--base-capital", str(float(card["base_capital"]))]
    if card.get("live"):
        argv.append("--live")
    return argv


def stocks_argv(card: dict) -> list[str]:
    return [PY, "-m", "Direction.whale_scanner", card["instrument"].upper()]


def event_desk_argv() -> list[str]:
    return ["py", "-3.11", "event_desk/churn.py", "watch"]


def apply_session(card: dict, session: dict, *, saved_at: str | None) -> dict:
    card = dict(card)
    card["pnl_since"] = session["pnl_since"]
    card["base_capital"] = float(session["equity"])
    card["running_saved_at"] = saved_at
    card["state"] = "running"
    return card
```

`os` is imported for the later spawn task. Leave it.

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_launch.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/launch.py launch_dock/tests/test_launch.py
git commit -m "feat(launch-dock): build runner argv without sending orders"
```

---

### Task 5: Perp stop

**Files:**
- Create: `launch_dock/stop.py`
- Create: `launch_dock/tests/test_stop.py`

**Interfaces:**
- Consumes: `KALSHI` from `launch_dock.launch`
- Produces: `stop_card(card, *, cmdline: str | None, cancel, kill) -> dict`
  - `cancel(kalshi_ticker: str) -> int` raises on failure
  - `kill(pid: int) -> None`
  - `cmdline is None` means the pid is already dead

- [ ] **Step 1: Write the failing test**

```python
import pytest
from launch_dock.stop import stop_card


def _card(**over):
    card = {
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP",
        "live": True, "pid": 42, "state": "running",
    }
    card.update(over)
    return card


def test_stop_cancels_that_ticker_then_kills():
    calls = []

    def cancel(ticker):
        calls.append(("cancel", ticker))
        return 1

    def kill(pid):
        calls.append(("kill", pid))

    out = stop_card(
        _card(),
        cmdline="python -m chart_app.run_live_perp --ticker BTC-PERP --live",
        cancel=cancel, kill=kill,
    )
    assert calls == [("cancel", "KXBTCPERP"), ("kill", 42)]
    assert out["state"] == "stopped"
    assert out["pid"] is None


def test_cancel_failure_does_not_kill():
    killed = []

    def cancel(ticker):
        raise RuntimeError("exchange down")

    out = stop_card(
        _card(),
        cmdline="python -m chart_app.run_live_perp --ticker BTC-PERP",
        cancel=cancel, kill=lambda pid: killed.append(pid),
    )
    assert killed == []
    assert out["state"] == "running"
    assert "exchange down" in out["error"]


def test_dead_live_pid_still_cancels_and_does_not_kill():
    calls = []
    out = stop_card(
        _card(),
        cmdline=None,
        cancel=lambda ticker: calls.append(ticker) or 0,
        kill=lambda pid: calls.append("kill"),
    )
    assert calls == ["KXBTCPERP"]
    assert out["state"] == "stopped"


def test_wrong_command_line_is_unknown():
    calls = []
    out = stop_card(
        _card(),
        cmdline="python something_else.py",
        cancel=lambda ticker: calls.append(ticker),
        kill=lambda pid: calls.append("kill"),
    )
    assert calls == []
    assert out["state"] == "unknown"


def test_event_desk_stop_does_not_cancel():
    calls = []
    out = stop_card(
        {"id": "desk", "seed": "event_desk", "instrument": "", "live": False, "pid": 7, "state": "running"},
        cmdline="py -3.11 event_desk/churn.py watch",
        cancel=lambda ticker: calls.append(ticker),
        kill=lambda pid: calls.append(pid),
    )
    assert calls == [7]
    assert out["state"] == "stopped"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_stop.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.stop`.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from launch_dock.launch import KALSHI


def stop_card(card: dict, *, cmdline: str | None, cancel, kill) -> dict:
    out = dict(card)
    seed = card.get("seed")
    if seed == "perp":
        if cmdline is not None:
            if "run_live_perp" not in cmdline or f"--ticker {card['instrument']}" not in cmdline:
                out["state"] = "unknown"
                return out
        if card.get("live"):
            try:
                cancel(KALSHI[card["instrument"].upper()])
            except Exception as exc:
                out["error"] = str(exc)
                out["state"] = "running"
                return out
        if cmdline is None:
            out["state"] = "stopped"
            out["pid"] = None
            return out
        kill(card["pid"])
        out["state"] = "stopped"
        out["pid"] = None
        return out
    if cmdline is None:
        out["state"] = "stopped"
        out["pid"] = None
        return out
    expected = "whale_scanner" if seed == "stocks" else "event_desk/churn.py watch"
    if expected not in (cmdline or ""):
        out["state"] = "unknown"
        return out
    kill(card["pid"])
    out["state"] = "stopped"
    out["pid"] = None
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_stop.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/stop.py launch_dock/tests/test_stop.py
git commit -m "feat(launch-dock): cancel that ticker before killing a perp"
```

---

### Task 6: Stale flag and pid reap

**Files:**
- Create: `launch_dock/status.py`
- Create: `launch_dock/tests/test_status.py`

**Interfaces:**
- Consumes: card dict
- Produces:
  - `mark_stale(card, resolved_saved_at: str | None) -> dict`
  - `reap(card, alive: bool) -> dict` — exited pid becomes `stopped`, no cancel

- [ ] **Step 1: Write the failing test**

```python
from launch_dock.status import mark_stale, reap


def test_perp_stale_when_own_saved_at_moves():
    card = {"seed": "perp", "state": "running", "running_saved_at": "T1", "argv": ["x"]}
    out = mark_stale(card, "T2")
    assert out["state"] == "stale"
    assert out["argv"] == ["x"]


def test_stocks_never_stale():
    card = {"seed": "stocks", "state": "running", "running_saved_at": "T1"}
    assert mark_stale(card, "T2")["state"] == "running"


def test_reap_marks_stopped_without_touching_orders():
    out = reap({"seed": "stocks", "state": "running", "pid": 3}, alive=False)
    assert out["state"] == "stopped"
    assert out["pid"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_status.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.status`.

- [ ] **Step 3: Write minimal implementation**

```python
def mark_stale(card: dict, resolved_saved_at: str | None) -> dict:
    out = dict(card)
    if out.get("seed") != "perp" or out.get("state") != "running":
        return out
    if resolved_saved_at and resolved_saved_at != out.get("running_saved_at"):
        out["state"] = "stale"
    return out


def reap(card: dict, alive: bool) -> dict:
    out = dict(card)
    if alive or not out.get("pid"):
        return out
    out["state"] = "stopped"
    out["pid"] = None
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_status.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/status.py launch_dock/tests/test_status.py
git commit -m "feat(launch-dock): stale is perp-only, reap does not cancel"
```

---

### Task 7: Local page

**Files:**
- Create: `launch_dock/server.py`
- Create: `launch_dock/static/index.html`
- Create: `launch_dock/tests/test_server.py`

**Interfaces:**
- Consumes: `registry.load/save/copy_card`, `sync.save_preset`, `launch.perp_argv/stocks_argv/event_desk_argv`, `stop.stop_card`, `status.mark_stale/reap`
- Produces: `create_server(directory)` bound by `__main__` to `127.0.0.1:8792`
  - `GET /` the page
  - `GET /api/cards` registry plus resolved preset source for each card
  - `POST /api/copy` body `{source_id, new_id, instrument}`
  - `POST /api/preset` body `{ticker, interval, config, elmo, capital}`
  - `POST /api/seed` body `{hours, cap_dollars}` for the perp seed only
  - `POST /api/launch` body `{id}` returns the argv and sets `state: launching`. This task does not spawn. Spawning is the operator step after the argv tests are green, and it must use `DETACH` plus the cwd from Task 4. Do not add a spawn in this task.
  - `POST /api/stop` body `{id, cmdline}` calls `stop_card` with injected cancel/kill left unimplemented in this task's handler: the handler calls `stop.stop_card` only when the test passes fakes through `create_server(cancel=..., kill=...)`. Default cancel/kill raise `RuntimeError("no process control in unit test")` so a test that forgets the fake fails closed.

- [ ] **Step 1: Write the failing test**

```python
import json
from launch_dock.server import create_server


def test_copy_and_seed_round_trip(tmp_path):
    httpd = create_server(tmp_path)
    httpd.registry["cards"].append({
        "id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "15m",
        "leverage": 6.0, "live": True, "subaccount": 0, "pnl_since": "T",
        "base_capital": 10.0, "pid": None, "argv": [], "running_saved_at": None,
        "state": "stopped",
    })
    status, body = httpd.handle("POST", "/api/copy", {"source_id": "perp-btc", "new_id": "perp-xrp", "instrument": "XRP-PERP"})
    assert status == 200
    assert body["card"]["leverage"] == 2.0
    assert body["card"]["live"] is False
    status, body = httpd.handle("POST", "/api/seed", {"hours": 4.0, "cap_dollars": 80.0})
    assert status == 200
    assert httpd.registry["seeds"]["perp"]["cap_dollars"] == 80.0
    status, body = httpd.handle("POST", "/api/launch", {"id": "perp-xrp"})
    assert status == 200
    assert "--leverage" in body["argv"] and "2.0" in body["argv"]
    assert "--live" not in body["argv"]
    assert body["spawned"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_server.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.server`.

- [ ] **Step 3: Write minimal implementation**

`create_server` returns an object with `handle(method, path, body) -> tuple[int, dict]` and `registry`. `GET /` reads `launch_dock/static/index.html`. The HTML is one file, no CDN, and it posts to those five routes. Buttons: Copy, Save seed, Save preset, Launch, Stop. Launch response with `spawned: false` is shown as the argv. Stop posts `{id, cmdline}` and displays `state` and `error`.

The page does not call Kalshi. The page does not contain a walk-forward control.

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests -q`

Expected: PASS. Then run `chart_app/tests/test_profiles.py -q`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/server.py launch_dock/static/index.html launch_dock/tests/test_server.py
git commit -m "feat(launch-dock): local page lists cards and builds argv"
```

Spawning stays out of the page handler. Task 8 adds it behind an injected `Popen` so the tests never start a runner.

---

### Task 8: Detached spawn, journal readback, poll

**Files:**
- Create: `launch_dock/spawn.py`
- Create: `launch_dock/tests/test_spawn.py`

**Interfaces:**
- Consumes: `perp_argv`, `apply_session`, `DETACH`, `mark_stale`, `reap`
- Produces:
  - `spawn(card, argv, *, popen, cwd) -> dict` sets `pid`, `argv`, `state: launching`. Passes `creationflags=DETACH`, `cwd`, `stdin=DEVNULL`.
  - `finish_from_journal(card, journal_text: str, saved_at: str | None) -> dict` reads the first JSON line whose `kind` is `session`. Missing session leaves `state: launching`.
  - `poll(cards, *, alive, saved_at_for) -> list[dict]` reaps dead pids and marks perp stale. It does not cancel.

- [ ] **Step 1: Write the failing test**

```python
from launch_dock.launch import DETACH
from launch_dock.spawn import finish_from_journal, poll, spawn


def test_spawn_detaches_and_does_not_invent_books():
    seen = {}

    class Proc:
        pid = 77

    def popen(argv, **kw):
        seen["argv"] = argv
        seen["flags"] = kw["creationflags"]
        seen["cwd"] = kw["cwd"]
        assert kw["stdin"] is not None
        return Proc()

    card = {"id": "perp-btc", "instrument": "BTC-PERP", "state": "stopped", "pnl_since": "", "base_capital": None}
    out = spawn(card, ["python", "-m", "chart_app.run_live_perp"], popen=popen, cwd=r"E:/BlackHole_Investments/BlackHole")
    assert out["pid"] == 77
    assert out["state"] == "launching"
    assert seen["flags"] == DETACH
    assert out["pnl_since"] == ""


def test_journal_session_is_stored_not_invented():
    text = '{"kind":"session","pnl_since":"2026-09-21T12:00:00+00:00","equity":80.0}\n'
    out = finish_from_journal(
        {"id": "perp-btc", "state": "launching", "pnl_since": "", "base_capital": None},
        text,
        saved_at="2026-09-21T11:00:00+00:00",
    )
    assert out["pnl_since"] == "2026-09-21T12:00:00+00:00"
    assert out["base_capital"] == 80.0
    assert out["state"] == "running"


def test_missing_session_stays_launching():
    out = finish_from_journal({"state": "launching", "pnl_since": ""}, "", saved_at=None)
    assert out["state"] == "launching"


def test_poll_reaps_and_stales_without_cancel():
    cards = [
        {"id": "scan", "seed": "stocks", "state": "running", "pid": 3, "running_saved_at": "T1"},
        {"id": "perp-btc", "seed": "perp", "instrument": "BTC-PERP", "interval": "15m",
         "state": "running", "pid": 9, "running_saved_at": "T1", "argv": ["x"]},
    ]
    out = poll(cards, alive=lambda pid: pid == 9, saved_at_for=lambda card: "T2")
    assert out[0]["state"] == "stopped" and out[0]["pid"] is None
    assert out[1]["state"] == "stale" and out[1]["argv"] == ["x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests/test_spawn.py -q`

Expected: FAIL with `ModuleNotFoundError: launch_dock.spawn`.

- [ ] **Step 3: Write minimal implementation**

`spawn` calls the injected `popen` and copies `pid` and `argv` onto the card. `finish_from_journal` splits lines, `json.loads` each, and on `kind == "session"` calls `apply_session`. `poll` calls `reap` then `mark_stale`. Neither function imports `PerpsClient` or calls `subprocess` itself. The `__main__` block in `server.py` is the only place that binds a real `subprocess.Popen` with `DETACH`, and this task does not add that block. Wiring the real Popen is a one-line follow-up after these tests pass, still with no new order path.

- [ ] **Step 4: Run test to verify it passes**

Run: `E:/BlackHole_Investments/BlackHole/.venv/Scripts/python.exe -m pytest launch_dock/tests -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add launch_dock/spawn.py launch_dock/tests/test_spawn.py
git commit -m "feat(launch-dock): detach, read the session journal, poll pids"
```

## Self-review

- Spec coverage: lock (Task 1), copy (Task 2), chart sync (Task 3), argv refuses (Task 4), cancel-then-kill (Task 5), stale and reap (Task 6), page (Task 7), detach plus journal readback plus poll (Task 8).
- No hot reload of `run_live_perp.py`. No `cancel_all`. No `config.py` editor. No cron seed. No test spawns a real runner.
- Task 8's real `Popen` binding is one line in `__main__` after the fake tests pass. It is not a new runner.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-21-launch-dock.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
