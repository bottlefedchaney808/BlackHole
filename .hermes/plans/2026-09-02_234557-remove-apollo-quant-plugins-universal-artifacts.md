# Remove Apollo + Quant Plugins; Build Universal Hermes Interactive Artifacts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** plan-only; NO code changed.
**Author:** Hermes Agent (grounded in live repo/tool inspection, 2026-09-02).
**Goal:** Delete the two unused Hermes desktop plugins (ApolloHermes, Quant Command Center) and their entire private backend stack, then build `C:/Users/bottl/hermes-artifacts` — a repo-agnostic, git-versioned framework of interactive single-file HTML artifacts that replace the static mockups in `C:/Users/bottl/Claude/Artifacts`.

**Architecture:** Removal is three layers — desktop plugins (JS, hot-reloaded by the app), one Hermes backend plugin (Python, config-registered), and the FinDev repo's `quant_bridge:8765` FastAPI stack it talked to (worker broker + synthesis + 6 test modules + a watchdog cron). The artifacts framework is a fresh git repo with ONE universal data contract (`data.json` schema → cards/tables/charts), ONE renderer shell (vanilla JS + Chart.js CDN, fully inlined into each `index.html` so it renders anywhere with zero sibling-file fetches), and per-artifact stdlib-only collector functions that read any repo read-only. `python tools/refresh.py <id> --repo <any-repo>` is the universal refresh path.

**Tech Stack:** Python 3.11 stdlib only (sqlite3, json, argparse — no new deps); vanilla ESM-free JS + Chart.js 4.5.0 CDN; git. FinDev root venv (`.venv/Scripts/python.exe`) runs the tests.

---

## Grounding (every file inspected to write this)

| Read | Contributes |
|---|---|
| `desktop-plugins/apollohermes/plugin.js` (188 lines) | Apollo plugin: `/apollo` route + sidebar nav + webview of `https://apollohermes.fun`, `persist:apollohermes` partition |
| `desktop-plugins/quant-command-center/plugin.js` (504 lines) | Quant plugin: `/quant` route + sidebar 'Quant' + tabs; fetches `http://127.0.0.1:8765` (`/api/modules`, …) |
| `plugins/quant-command-center/dashboard/plugin_api.py` + `manifest.json` | Hermes BACKEND plugin (config-registered): adapter proxying the worker broker on 8765 |
| `AppData/Local/hermes/config.yaml` (lines ~250–262) + 4 profile configs | `quant-command-center` listed in `plugins.enabled` in root + `quant`, `coder`, `trading-strategist`, `expert---cem-karsan` profiles |
| `FinancialDevelopment/quant_bridge.py` (28.8 KB), `quant_bridge.bat/.sh`, `worker_broker.py` (23 KB), `quant_synthesis.py` (14.2 KB), `quant_interpret.py` (8.6 KB) | The bridge stack — sole consumers of port 8765 |
| `tests/test_quant_bridge.py`, `test_worker_broker.py`, `test_quant_synthesis.py`, `test_personal_bot_handoff.py`, `test_worker_contracts.py`, `test_integration_quant_orchestrator.py` | Bridge-only tests; the integration test even statically `node --check`s the plugin.js |
| `shared/worker_contracts.py` | Consumed ONLY by `quant_synthesis.py` + 2 of those tests → dies with them |
| `scripts/burst_checkpoint.sh` | `run_sentiment()` runs `tests/test_quant_bridge.py` + `test_worker_broker.py` → must be edited |
| `personal-bot/cron/jobs.json` | `quant-bridge-watchdog` (id `d24612a20cfa`) exists solely to keep the bridge up → remove |
| `$LOCALAPPDATA/hermes/run/quant-bridge-worker.token` | Broker secret → delete (never archive) |
| `netstat` | Bridge was LIVE at plan time: `127.0.0.1:8765 LISTENING pid 7108` (re-derive PID at execution) |
| `dashboard/app.py` grep + `dashboard/tests/` listing | Worker-dispatch stack (`worker_env.py`, `worker_worktree.py`, `job_object.py`, `/runs/{id}/dispatch`) is independent of the bridge — **kept per Jason** |
| All 6 `C:/Users/bottl/Claude/Artifacts/*/index.html` + meta blocks | Static Cowork mockups with placeholder data; two load Chart.js CDN; none fetch live data |
| Real data sources verified: `Vol_Suite/vol_suite_result.json`, `orchestrator_output/<ts>/`, `sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json`, `VaR_Tools_Simulations/var_context_run_latest.json` (517 B, fresh 2026-09-02), `Options_Suite/REPORT.md`, `swaps.db` (346 GB, live WAL) | What the collectors will actually read |

---

## Current context / assumptions

1. **"Quant dashboard" = the Hermes desktop plugin + its backend**, per Jason's correction — NOT the FinDev `dashboard/` FastAPI app, which stays.
2. **Jason still dispatches LLM workers from the FinDev dashboard** — `dashboard/worker_env.py`, `worker_worktree.py`, `job_object.py`, `POST /runs/{run_id}/dispatch/*`, and their tests (`test_dispatch.py`, `test_dispatch_poll.py`, `test_job_object.py`, `test_live_ws.py`, `test_worker_env.py`, `test_worker_worktree.py`) are **PRESERVED, untouched**.
3. The bridge on 8765 is currently running and will be running at execution start — the plan kills it by its LISTENING pid (never a guessed/wrapper pid — known FinDev stale-process trap).
4. "Apollo" text hits in `trading_journal/*.md` are about Apollo Global Management (Nvidia financing news) — unrelated prose, not touched.
5. `C:/Users/bottl/hermes-artifacts` does not exist yet (verified). New git repo, local only (remote TBD — open question).
6. Existing artifacts in `C:/Users/bottl/Claude/Artifacts/` stay untouched as read-only originals until each conversion lands; the new repo gets converted copies.
7. All 6 artifacts convert in this pass (Jason's choice), mechanical once the first lands.
8. House rules that bind execution: FinDev commit subjects use the `scripts/hooks/commit-msg` allowlist (`chore|fix|test|docs|refactor|improve|data`, optional `(scope):`); never break the single-dashboard-process rule; `env -u PYTHONPATH -u PYTHONHOME` before the project venv.

---

## Global constraints

- No new Python dependencies anywhere (stdlib only for the framework).
- Every `terminal` pytest/venv command in FinDev runs through `env -u PYTHONPATH -u PYTHONHOME`.
- Never touch `swaps.db` write-side; collectors open it `mode=ro`, and count via `MAX(rowid)` (index hit) — never `COUNT(*)` on `swap_trades` (full scan of 346 GB).
- The inline-JS constraint: `assets/hermes-artifact.js` must never contain the literal string `</script>`.
- Artifact charts: vivid, full-opacity bars (Jason's standing chart preference); palette is the shell's fixed 6-color set.
- Deletion of Hermes-side (non-git) files is preceded by an archive copy into `FinancialDevelopment/_trash/2026-09-02-plugin-removal/` (git-ignored dir) — EXCEPT the token file, which is a secret and is deleted outright.

---

## Phase 0 — Baseline snapshot (read-only)

### Task 0.1: Record pre-removal state

- [ ] Run from `C:/Users/bottl/FinancialDevelopment`:

```bash
git status --short | head -5
netstat -ano | grep ":8765" | grep LISTENING
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest tests/ -q --collect-only 2>&1 | tail -2
ls "$LOCALAPPDATA/hermes/desktop-plugins/"
```

Expected: a dirty-tree line count (note it), one `127.0.0.1:8765 ... LISTENING <pid>` line (write the pid down), `N tests collected` (write N down), and exactly two plugin dirs (`apollohermes`, `quant-command-center`).

- [ ] Record the numbers in the task ledger (`.superpowers/sdd/` or the working notes file in use that day). These are the before-values for Phase 3 verification.

---

## Phase 1 — Stop the running bridge

### Task 1.1: Kill the LISTENING pid on 8765

- [ ] Using the pid recorded in Task 0.1 (example shows `7108` — substitute the fresh one):

```bash
cmd //c "taskkill /PID 7108 /T /F"
netstat -ano | grep ":8765" | grep LISTENING; echo "exit=$?"
```

Expected: taskkill reports SUCCESS; the netstat line prints nothing and `exit=1` (grep found nothing). If a NEW pid reappears within ~30s, the `quant-bridge-watchdog` cron re-launched it — proceed to Task 3.5 (cron removal) first, kill again, then continue.

---

## Phase 2 — FinDev repo removals (git-tracked; commit after each task)

### Task 2.1: Remove the bridge modules

- [ ] Run:

```bash
cd "C:/Users/bottl/FinancialDevelopment"
git rm quant_bridge.py quant_bridge.bat quant_bridge.sh worker_broker.py quant_synthesis.py quant_interpret.py
```

Expected: `rm 'quant_bridge.py'` … six removals staged.

### Task 2.2: Remove the bridge-only tests + the orphaned contracts module

- [ ] Confirm zero remaining consumers first:

```bash
grep -r -l "worker_contracts" --include="*.py" . | grep -v ".venv\|.worktrees\|__pycache__\|_trash"
```

Expected: only `quant_synthesis.py` (staged-deleted in 2.1) and the two test files being removed now. If anything ELSE appears, stop and re-scope before deleting.

- [ ] Run:

```bash
git rm tests/test_quant_bridge.py tests/test_worker_broker.py tests/test_quant_synthesis.py \
       tests/test_personal_bot_handoff.py tests/test_worker_contracts.py \
       tests/test_integration_quant_orchestrator.py shared/worker_contracts.py
```

Expected: seven removals staged.

### Task 2.3: Fix `scripts/burst_checkpoint.sh`

- [ ] Apply this exact edit (replace the bridge test lines inside `run_sentiment()`):

```bash
```

```python
# old (lines inside run_sentiment()):
    env -u PYTHONPATH -u VIRTUAL_ENV PYTHONPATH="." "$PY" -m pytest \
        tests/test_quant_bridge.py \
        tests/test_worker_broker.py \
        --import-mode=importlib -q --no-header -p no:cacheprovider || FAILED=1

# new — delete that entire env -u ... || FAILED=1 block, leaving run_sentiment()
# with only the sentiment-scanner pytest block above it.
```

- [ ] Verify the file still parses and the vol slice is untouched:

```bash
bash -n scripts/burst_checkpoint.sh && echo OK
grep -c "test_quant_bridge\|test_worker_broker" scripts/burst_checkpoint.sh
```

Expected: `OK` and `0`.

### Task 2.4: Reference sweep (docs, guides, CI, k8s)

- [ ] Run:

```bash
grep -r -n "quant_bridge\|quant-bridge\|worker_broker\|quant_synthesis\|quant_interpret" \
  CLAUDE.md AGENTS.md docs/guides .github k8s Procfile requirements.txt system 2>/dev/null \
  | grep -v "docs/archive\|docs/superpowers/plans\|docs/superpowers/specs"
```

Expected: zero hits (historical `docs/archive|plans|specs` are preserved as history). For any hit that DOES appear in a *live* doc, delete the sentence/section referencing the removed stack, then:

```bash
git add -u && git commit -m "docs: drop quant-bridge references from live docs"
```

### Task 2.5: Test + commit the removal

- [ ] Run the affected slices and the full `tests/` collection:

```bash
bash scripts/burst_checkpoint.sh sentiment
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest tests/ -q --collect-only 2>&1 | tail -2
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest tests/ -q -x
```

Expected: burst checkpoint `OK`; collected count = Task 0.1's N minus the test functions that lived in the six deleted files (count them: `grep -c "^def test" tests/test_quant_bridge.py tests/test_worker_broker.py tests/test_quant_synthesis.py tests/test_personal_bot_handoff.py tests/test_worker_contracts.py tests/test_integration_quant_orchestrator.py` **before** this phase if you want the exact delta — otherwise the -x run passing is the gate); `pytest tests/ -q -x` exits 0 with zero failures.

- [ ] Commit:

```bash
git add -u
git commit -m "chore(quant-console): remove quant bridge stack — quant_bridge/worker_broker/synthesis/interpret + 6 test modules + worker_contracts"
```

Expected: commit lands; `scripts/hooks/commit-msg` accepts the `chore(scope):` subject.

---

## Phase 3 — Hermes-side removals (non-git; archive first)

### Task 3.1: Archive + delete the two desktop plugins

- [ ] Run:

```bash
mkdir -p "C:/Users/bottl/FinancialDevelopment/_trash/2026-09-02-plugin-removal"
mv "$LOCALAPPDATA/hermes/desktop-plugins/apollohermes" \
   "$LOCALAPPDATA/hermes/desktop-plugins/quant-command-center" \
   "C:/Users/bottl/FinancialDevelopment/_trash/2026-09-02-plugin-removal/"
ls "$LOCALAPPDATA/hermes/desktop-plugins/"
```

Expected: the desktop-plugins dir listing no longer contains either folder.

### Task 3.2: Archive + delete the Hermes backend plugin

- [ ] Run:

```bash
mv "$LOCALAPPDATA/hermes/plugins/quant-command-center" \
   "C:/Users/bottl/FinancialDevelopment/_trash/2026-09-02-plugin-removal/backend-quant-command-center"
ls "$LOCALAPPDATA/hermes/plugins/"
```

Expected: listing shows only `hermes-achievements` and `zai-tool-stream`.

### Task 3.3: De-register from config.yaml (root + 4 profiles)

- [ ] Show each occurrence with context, then delete just the `    - quant-command-center` list line (keep YAML indentation of neighbours intact):

```bash
for f in "$LOCALAPPDATA/hermes/config.yaml" \
         "$LOCALAPPDATA/hermes/profiles/quant/config.yaml" \
         "$LOCALAPPDATA/hermes/profiles/coder/config.yaml" \
         "$LOCALAPPDATA/hermes/profiles/trading-strategist/config.yaml" \
         "$LOCALAPPDATA/hermes/profiles/expert---cem-karsan/config.yaml"; do
  grep -n -B1 -A1 "quant-command-center" "$f" && echo "== $f"
done
```

Expected: exactly one hit per file, under `plugins: enabled:`.

- [ ] Edit each of the 5 files with `patch`-style precision: remove only the `- quant-command-center` line. Verify each file still parses as YAML:

```bash
for f in ...; do "$LOCALAPPDATA/hermes/../../Python311/python.exe" -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" "$f" && echo "$f OK"; done
```

(If that interpreter path is wrong on this host, use `C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -c "import yaml,sys; yaml.safe_load(open(sys.argv[1],encoding='utf-8'))" <file>` — the project venv has pydantic/yaml transitively; if yaml is missing entirely, visually diff instead. Gate: file opens, list intact.)

Expected: each file `OK` (or visually intact), and the final grep shows zero `quant-command-center` lines anywhere.

### Task 3.4: Delete the broker token (secret — no archive)

- [ ] Run:

```bash
rm "$LOCALAPPDATA/hermes/run/quant-bridge-worker.token"
ls "$LOCALAPPDATA/hermes/run/"
```

Expected: the run dir no longer lists `quant-bridge-worker.token`.

### Task 3.5: Remove the `quant-bridge-watchdog` cron job

- [ ] Delete job id `d24612a20cfa` from the **personal-bot** profile via the Hermes cron system (cronjob_manage / the profile's `/cron`), NOT by hand-editing `jobs.json` while the gateway is live (it gets overwritten).
- [ ] Verify:

```bash
grep -c "quant-bridge-watchdog" "$LOCALAPPDATA/hermes/profiles/personal-bot/cron/jobs.json"
```

Expected: `0`. If still 1 after deletion, the gateway hadn't flushed — wait 60s and re-check; escalate to a gateway restart only if it persists.

### Task 3.6: Post-removal verification (definition of done for removal)

- [ ] `netstat -ano | grep ":8765" | grep LISTENING` → prints nothing.
- [ ] In the Hermes desktop app: ⌘K/Ctrl+K → **Reload desktop plugins**. Expected: no error toast; the sidebar shows neither **Apollo** nor **Quant**; navigating to `/apollo` or `/quant` renders nothing.
- [ ] `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -c "import quant_bridge"` from the repo root → `ModuleNotFoundError: No module named 'quant_bridge'` (expected).
- [ ] `pytest tests/ -q -x` still exits 0 (already run in 2.5; re-run if any Phase 3 step surprised you).

---

## Phase 4 — `hermes-artifacts` framework (TDD)

New repo: `C:/Users/bottl/hermes-artifacts/`. Layout:

```
hermes-artifacts/
  README.md
  assets/
    hermes-artifact.css
    hermes-artifact.js
  tools/
    refresh.py
    collectors/
      __init__.py
      var_sim_collector.py
      findev_swaps_collector.py
      vol_suite_collector.py
      sentiment_collector.py
      options_models_collector.py
      dev_roadmap_collector.py
  artifacts/
    <artifact-id>/
      artifact.json      # manifest: id, title, collector, repo, description
      template.html      # marker skeleton
      data.json          # written by refresh (canonical payload)
      index.html         # written by refresh (fully self-contained)
  tests/
    test_refresh.py
```

**Core design (locked):** `refresh.py` stamps `{schema_version, artifact_id, title, generated_at}` onto a collector's payload, validates it, writes `data.json`, and builds `index.html` by inlining CSS + JS + the JSON payload between markers — one self-contained file, no sibling fetches, renders in the Hermes `::preview` frame, a browser, or anywhere. Chart.js stays CDN (only external dep; shell degrades gracefully offline). Adding an artifact for ANY repo = new folder + one collector module.

### Task 4.1: Init the repo

- [ ] Run:

```bash
mkdir -p /c/Users/bottl/hermes-artifacts/{assets,tools/collectors,tests}
cd /c/Users/bottl/hermes-artifacts
git init -b main
printf '__pycache__/\n*.pyc\n' > .gitignore
git add .gitignore && git commit -m "chore: init hermes-artifacts"
```

Expected: `Initialized empty Git repository`, first commit lands.

### Task 4.2: Write the failing validator/injection tests

- [ ] Create `tests/test_refresh.py`:

```python
"""Network-free unit tests for the Hermes Interactive Artifacts refresh tool."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import refresh  # noqa: E402

VALID = {
    "schema_version": 1,
    "artifact_id": "demo",
    "title": "Demo",
    "generated_at": "2026-09-02 23:00 UTC",
    "summary": [{"label": "rows", "value": "42"}],
    "tables": [{"id": "t", "title": "T", "columns": ["a", "b"], "rows": [["1", "x"]]}],
    "charts": [{"id": "c", "title": "C", "type": "bar",
                "labels": ["a"], "series": [{"label": "s", "data": [1]}]}],
    "notes": [],
}

TEMPLATE = """<!DOCTYPE html>
<html><head><title>T</title>
<!--HERMES-ARTIFACT:CSS-->
<!--/HERMES-ARTIFACT:CSS-->
</head><body><div id="app"></div>
<!--HERMES-ARTIFACT:DATA-->
<!--/HERMES-ARTIFACT:DATA-->
<!--HERMES-ARTIFACT:JS-->
<!--/HERMES-ARTIFACT:JS-->
</body></html>
"""


def test_validate_accepts_valid_payload():
    assert refresh.validate_payload(VALID) == []


def test_validate_rejects_bad_chart_type():
    bad = dict(VALID, charts=[{"id": "c", "title": "C", "type": "pie",
                               "labels": [], "series": []}])
    assert any("type" in e for e in refresh.validate_payload(bad))


def test_validate_rejects_row_wider_than_columns():
    bad = json.loads(json.dumps(VALID))
    bad["tables"][0]["rows"] = [["1", "x", "extra"]]
    assert refresh.validate_payload(bad)


def test_validate_rejects_bad_schema_version():
    bad = dict(VALID, schema_version=2)
    assert refresh.validate_payload(bad)


def test_build_index_inlines_assets_and_data(tmp_path):
    tpl = tmp_path / "template.html"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "hermes-artifact.css").write_text("body{}", encoding="utf-8")
    (assets / "hermes-artifact.js").write_text("window.HermesArtifact={};", encoding="utf-8")
    html = refresh.build_index(tpl, VALID, assets)
    assert "<style>" in html and "body{}" in html
    assert 'id="hermes-artifact-data"' in html
    embedded = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded) == VALID
    assert "window.HermesArtifact" in html


def test_build_index_escapes_closing_tag_in_data(tmp_path):
    tpl = tmp_path / "template.html"
    tpl.write_text(TEMPLATE, encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "hermes-artifact.css").write_text("", encoding="utf-8")
    (assets / "hermes-artifact.js").write_text("", encoding="utf-8")
    payload = dict(VALID, notes=["evil </script> string"])
    html = refresh.build_index(tpl, payload, assets)
    assert html.count("</script>") == 1  # only the real closer after the JSON


def test_build_index_rejects_missing_marker(tmp_path):
    tpl = tmp_path / "template.html"
    tpl.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError):
        refresh.build_index(tpl, VALID, tmp_path)


def test_cli_list(tmp_path, capsys):
    (tmp_path / "artifacts" / "a").mkdir(parents=True)
    assert refresh.main(["--list"], root=tmp_path) == 0
    assert "a" in capsys.readouterr().out


def test_json_to_payload_flattens_scalars():
    import collectors
    payload = collectors.json_to_payload({"spot": 6650.5, "flags": ["a", "b"]})
    assert payload["summary"][0]["label"] == "spot"
    assert ["spot", "6650.5"] in payload["tables"][0]["rows"]
```

- [ ] Run: `env -u PYTHONPATH -u PYTHONHOME C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests -q`
  Expected: FAIL — `ModuleNotFoundError: No module named 'refresh'`.

### Task 4.3: Implement `tools/refresh.py` minimal (validator + injection + CLI)

- [ ] Create `tools/refresh.py`:

```python
#!/usr/bin/env python3
"""Hermes Interactive Artifacts — refresh tool.

Refresh one artifact or all: writes data.json and rebuilds a fully
self-contained index.html (CSS/JS/data inlined; Chart.js stays CDN).
Collectors are stdlib-only and read their source repo read-only.

Usage:
  python tools/refresh.py --list
  python tools/refresh.py var-simulations-digest
  python tools/refresh.py --all
  python tools/refresh.py var-simulations-digest --repo C:/path/to/other/repo
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import collectors  # noqa: E402

for _mod in sorted(p.name for p in (ROOT / "tools" / "collectors").glob("*_collector.py")):
    __import__(f"collectors.{_mod[:-3]}")

CSS_MARKER = ("<!--HERMES-ARTIFACT:CSS-->", "<!--/HERMES-ARTIFACT:CSS-->")
DATA_MARKER = ("<!--HERMES-ARTIFACT:DATA-->", "<!--/HERMES-ARTIFACT:DATA-->")
JS_MARKER = ("<!--HERMES-ARTIFACT:JS-->", "<!--/HERMES-ARTIFACT:JS-->")
CHART_TYPES = {"line", "bar", "doughnut"}


def validate_payload(p: dict) -> list[str]:
    """Return a list of human-readable errors; empty list = valid."""
    errors: list[str] = []

    def need(cond, msg):
        if not cond:
            errors.append(msg)

    need(isinstance(p, dict), "payload must be an object")
    if not isinstance(p, dict):
        return errors
    need(p.get("schema_version") == 1, "schema_version must be 1")
    for key in ("artifact_id", "title", "generated_at"):
        need(isinstance(p.get(key), str), f"{key} must be a string")
    for name in ("summary", "tables", "charts", "notes"):
        need(isinstance(p.get(name, []), list), f"{name} must be a list")
    for i, c in enumerate(p.get("summary", [])):
        need(isinstance(c, dict) and "label" in c and "value" in c,
             f"summary[{i}] needs label+value")
    for i, t in enumerate(p.get("tables", [])):
        need(isinstance(t, dict) and isinstance(t.get("title"), str),
             f"tables[{i}] needs a string title")
        cols = t.get("columns", []) if isinstance(t, dict) else []
        need(isinstance(cols, list) and all(isinstance(c, str) for c in cols),
             f"tables[{i}].columns must be list[str]")
        need(isinstance(t.get("rows"), list), f"tables[{i}].rows must be a list")
        for j, r in enumerate(t.get("rows", [])):
            need(isinstance(r, list) and len(r) <= len(cols),
                 f"tables[{i}].rows[{j}] must have <= {len(cols)} cells")
    for i, c in enumerate(p.get("charts", [])):
        need(isinstance(c, dict) and c.get("type") in CHART_TYPES,
             f"charts[{i}].type must be one of {sorted(CHART_TYPES)}")
        need(isinstance(c.get("labels"), list), f"charts[{i}].labels must be a list")
        series = c.get("series", []) if isinstance(c, dict) else []
        need(isinstance(series, list) and all(
            isinstance(s, dict) and isinstance(s.get("data"), list) for s in series),
            f"charts[{i}].series must be a list of {{label, data}}")
    return errors


def _inject(html: str, marker: tuple[str, str], content: str) -> str:
    start, end = marker
    if start not in html or end not in html:
        raise ValueError(f"marker {start!r} missing from template")
    head = html.split(start, 1)[0] + start
    tail = html.split(end, 1)[1]
    return head + "\n" + content + "\n" + end + tail


def build_index(template: Path, payload: dict, assets_dir: Path) -> str:
    html = template.read_text(encoding="utf-8")
    css = (assets_dir / "hermes-artifact.css").read_text(encoding="utf-8")
    js = (assets_dir / "hermes-artifact.js").read_text(encoding="utf-8")
    assert "</script>" not in js, "hermes-artifact.js must not contain </script>"
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = _inject(html, CSS_MARKER, "<style>\n" + css + "\n</style>")
    html = _inject(html, DATA_MARKER,
                   '<script id="hermes-artifact-data" type="application/json">\n'
                   + data + "\n</script>")
    html = _inject(html, JS_MARKER, "<script>\n" + js + "\n</script>")
    return html


def refresh_one(artifact_id: str, repo_override: str | None = None,
                root: Path = ROOT) -> dict:
    artifact_dir = root / "artifacts" / artifact_id
    manifest = json.loads((artifact_dir / "artifact.json").read_text(encoding="utf-8"))
    collector_name = manifest["collector"]
    if collector_name not in collectors.COLLECTORS:
        raise SystemExit(f"unknown collector {collector_name!r} for {artifact_id}")
    repo = Path(repo_override or manifest["repo"])
    payload = collectors.COLLECTORS[collector_name](repo)
    payload.update({
        "schema_version": 1,
        "artifact_id": artifact_id,
        "title": manifest.get("title", artifact_id),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    errors = validate_payload(payload)
    if errors:
        raise SystemExit(f"{artifact_id}: invalid payload:\n"
                         + "\n".join("  - " + e for e in errors))
    (artifact_dir / "data.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = build_index(artifact_dir / "template.html", payload, root / "assets")
    (artifact_dir / "index.html").write_text(html, encoding="utf-8")
    return payload


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description="Refresh Hermes interactive artifacts")
    ap.add_argument("artifact", nargs="?", help="artifact id (folder under artifacts/)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--repo", help="override the source repo path for this refresh")
    args = ap.parse_args(argv)
    ids = sorted(p.name for p in (root / "artifacts").iterdir() if p.is_dir())
    if args.list:
        print("\n".join(ids))
        return 0
    if args.all:
        for aid in ids:
            refresh_one(aid, root=root)
            print(f"refreshed {aid}")
        return 0
    if not args.artifact:
        ap.error("give an artifact id, --all, or --list")
    refresh_one(args.artifact, args.repo, root=root)
    print(f"refreshed {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run the tests again — validator/injection/CLI tests pass; the `json_to_payload` test still fails (registry not written yet):

```bash
env -u PYTHONPATH -u PYTHONHOME C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests -q
```

Expected: `8 passed, 1 failed` (the collectors test).

### Task 4.4: Collector registry + generic JSON adapter

- [ ] Create `tools/collectors/__init__.py`:

```python
"""Collector registry for Hermes Interactive Artifacts.

A collector is `collect(repo_path: Path) -> dict` returning a payload body
WITHOUT the envelope fields (schema_version / artifact_id / title /
generated_at) — refresh.py stamps those. Include a "source" dict and, when
useful, "notes": [str].
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

COLLECTORS: dict[str, Callable[[Path], dict]] = {}


def register(name: str) -> Callable:
    def deco(fn):
        COLLECTORS[name] = fn
        return fn
    return deco


def json_to_payload(raw: dict) -> dict:
    """Generic adapter: flatten a nested JSON blob into cards + one table."""
    rows: list[list[str]] = []

    def walk(prefix: str, obj) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(f"{prefix}{k}." if prefix else f"{k}.", v)
        elif isinstance(obj, list):
            rows.append([prefix.rstrip("."), f"list[{len(obj)}]"])
        else:
            rows.append([prefix.rstrip("."), str(obj)])

    walk("", raw)
    return {
        "summary": [{"label": r[0], "value": r[1]} for r in rows[:8]],
        "tables": [{"id": "fields", "title": "Fields",
                    "columns": ["field", "value"], "rows": rows}],
        "charts": [],
        "notes": [],
    }
```

- [ ] Run the suite: expected `9 passed`. Commit:

```bash
git add -A && git commit -m "feat: refresh tool — schema validator, marker injection, collector registry"
```

### Task 4.5: The shell assets (CSS + JS)

- [ ] Create `assets/hermes-artifact.css`:

```css
/* Hermes Interactive Artifacts — universal shell styles.
   Uses Hermes ::preview theme vars when present; var() fallbacks keep it
   readable standalone in any browser. Never hardcode frame backgrounds. */
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px;
  color: var(--foreground, #1a1a1a);
  background: var(--card, transparent);
  font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
h1 { font-size: 20px; margin: 0 0 2px; }
.ha-sub { color: var(--muted-foreground, #6b6b6b); font-size: 12px; margin-bottom: 16px; }
.ha-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 16px; }
.ha-card { border: 1px solid var(--border, #e8e6e0); border-radius: 10px; padding: 12px 14px; }
.ha-card .label { font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted-foreground, #8a8a8a); }
.ha-card .value { font-size: 20px; font-weight: 650; margin-top: 3px; }
.ha-card.tone-ok .value { color: #1a7f37; }
.ha-card.tone-bad .value { color: #c0341d; }
.ha-chart { border: 1px solid var(--border, #e8e6e0); border-radius: 10px;
  padding: 12px; margin-bottom: 16px; }
.ha-chart h3 { font-size: 14px; margin: 0 0 8px; }
.ha-chart .box { position: relative; height: 280px; }
.ha-tablewrap { border: 1px solid var(--border, #e8e6e0); border-radius: 10px;
  padding: 10px 12px 14px; margin-bottom: 16px; overflow-x: auto; }
.ha-tablewrap h3 { font-size: 14px; margin: 2px 0 8px; }
.ha-filter { width: 220px; padding: 4px 8px; margin-bottom: 8px;
  border: 1px solid var(--border, #ccc); border-radius: 6px;
  background: transparent; color: inherit; font: inherit; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th, td { text-align: left; padding: 6px 9px; white-space: nowrap;
  border-bottom: 1px solid var(--border, #efece5); }
th { cursor: pointer; color: var(--muted-foreground, #6b6b6b);
  font-size: 11px; text-transform: uppercase; }
tr:last-child td { border-bottom: none; }
.ha-notes { color: var(--muted-foreground, #6b6b6b); font-size: 12px; }
.ha-notes li { margin-bottom: 4px; }
```

- [ ] Create `assets/hermes-artifact.js` (NOTE: contains zero `</script>` sequences — build_index asserts this):

```js
/* Hermes Interactive Artifacts — universal renderer.
   Reads the inline JSON payload written by tools/refresh.py and renders
   summary cards, charts (Chart.js if the CDN is reachable), and
   filterable/sortable tables. No build step, no fetches, no framework. */
(function () {
  'use strict';
  /* Vivid, full-opacity palette per Jason's chart preferences. */
  var PALETTE = ['#3b82f6', '#ef4444', '#22c55e', '#eab308', '#a855f7', '#06b6d4'];

  function h(tag, attrs) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      if (arguments[i]) node.appendChild(arguments[i]);
    }
    return node;
  }

  function payload() {
    return JSON.parse(document.getElementById('hermes-artifact-data').textContent);
  }

  function renderCards(d) {
    var wrap = h('div', { class: 'ha-cards' });
    (d.summary || []).forEach(function (c) {
      wrap.appendChild(h('div', { class: 'ha-card' + (c.tone ? ' tone-' + c.tone : '') },
        h('div', { class: 'label', text: c.label }),
        h('div', { class: 'value', text: String(c.value) })));
    });
    return wrap;
  }

  function renderChart(chart) {
    var box = h('div', { class: 'ha-chart' }, h('h3', { text: chart.title }));
    if (typeof Chart === 'undefined') {
      box.appendChild(h('div', { class: 'ha-notes',
        text: 'Chart.js CDN unreachable — values remain in tables/notes.' }));
      return box;
    }
    var canvas = h('canvas');
    box.appendChild(h('div', { class: 'box' }, canvas));
    var datasets = (chart.series || []).map(function (s, i) {
      return {
        label: s.label,
        data: s.data,
        backgroundColor: PALETTE[i % PALETTE.length],
        borderColor: PALETTE[i % PALETTE.length],
        borderWidth: 2, tension: 0.35, pointRadius: 2,
        fill: chart.type === 'line'
      };
    });
    new Chart(canvas.getContext('2d'), {
      type: chart.type === 'doughnut' ? 'doughnut'
        : (chart.type === 'bar' ? 'bar' : 'line'),
      data: { labels: chart.labels || [], datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { position: 'top', labels: { boxWidth: 12 } } }
      }
    });
    return box;
  }

  function renderTable(table) {
    var wrap = h('div', { class: 'ha-tablewrap' }, h('h3', { text: table.title }));
    var filter = h('input', { class: 'ha-filter', placeholder: 'Filter…' });
    var tbl = h('table');
    var headRow = h('tr');
    var thead = h('thead'), tbody = h('tbody');
    (table.columns || []).forEach(function (c) {
      headRow.appendChild(h('th', { text: c }));
    });
    thead.appendChild(headRow);
    tbl.appendChild(thead);
    tbl.appendChild(tbody);
    var rows = table.rows || [];

    function fill(list) {
      tbody.textContent = '';
      list.forEach(function (r) {
        var tr = h('tr');
        (table.columns || []).forEach(function (_, i) {
          tr.appendChild(h('td', { text: r[i] == null ? '' : String(r[i]) }));
        });
        tbody.appendChild(tr);
      });
    }
    fill(rows);
    filter.addEventListener('input', function () {
      var q = filter.value.toLowerCase();
      fill(rows.filter(function (r) {
        return r.some(function (cell) {
          return String(cell == null ? '' : cell).toLowerCase().indexOf(q) !== -1;
        });
      }));
    });
    var sortDir = {};
    headRow.addEventListener('click', function (e) {
      var idx = Array.prototype.indexOf.call(headRow.children, e.target);
      if (idx < 0) return;
      sortDir[idx] = !sortDir[idx];
      fill(rows.slice().sort(function (a, b) {
        var x = a[idx], y = b[idx];
        var nx = parseFloat(x), ny = parseFloat(y);
        var cmp = (!isNaN(nx) && !isNaN(ny)) ? nx - ny
          : String(x).localeCompare(String(y));
        return sortDir[idx] ? cmp : -cmp;
      }));
    });
    wrap.appendChild(filter);
    wrap.appendChild(tbl);
    return wrap;
  }

  function render(root) {
    var d = payload();
    root.textContent = '';
    root.appendChild(h('h1', { text: d.title || d.artifact_id }));
    root.appendChild(h('div', { class: 'ha-sub', text: 'Generated '
      + d.generated_at + (d.source && d.source.note ? ' · ' + d.source.note : '') }));
    if ((d.summary || []).length) root.appendChild(renderCards(d));
    (d.charts || []).forEach(function (c) { root.appendChild(renderChart(c)); });
    (d.tables || []).forEach(function (t) { root.appendChild(renderTable(t)); });
    if ((d.notes || []).length) {
      var ul = h('ul', { class: 'ha-notes' });
      d.notes.forEach(function (n) { ul.appendChild(h('li', { text: n })); });
      root.appendChild(ul);
    }
  }

  window.HermesArtifact = { render: render };
  document.addEventListener('DOMContentLoaded', function () {
    render(document.getElementById('app'));
  });
})();
```

### Task 4.6: Universal template + first artifact end-to-end (`var-simulations-digest`)

- [ ] Create `artifacts/var-simulations-digest/template.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>VaR Simulations Digest</title>
<!--HERMES-ARTIFACT:CSS-->
<!--/HERMES-ARTIFACT:CSS-->
</head>
<body>
<div id="app"></div>
<!--HERMES-ARTIFACT:DATA-->
<!--/HERMES-ARTIFACT:DATA-->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" crossorigin="anonymous"></script>
<!--HERMES-ARTIFACT:JS-->
<!--/HERMES-ARTIFACT:JS-->
</body>
</html>
```

- [ ] Create `artifacts/var-simulations-digest/artifact.json`:

```json
{
  "id": "var-simulations-digest",
  "title": "VaR Simulations Digest",
  "collector": "var-sim",
  "repo": "C:/Users/bottl/FinancialDevelopment",
  "description": "Latest VaR context run (Historical / MC / GARCH metrics) from VaR_Tools_Simulations/var_context_run_latest.json.",
  "refresh": "ask Hermes to 'refresh var-simulations-digest'"
}
```

- [ ] Create `tools/collectors/var_sim_collector.py`:

```python
"""VaR Simulations Digest — reads VaR_Tools_Simulations/var_context_run_latest.json."""
from __future__ import annotations

import json
from pathlib import Path

from collectors import json_to_payload, register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")


@register("var-sim")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    src = repo_path / "VaR_Tools_Simulations" / "var_context_run_latest.json"
    if not src.exists():
        return {
            "summary": [{"label": "source", "value": "missing", "tone": "bad"}],
            "tables": [], "charts": [],
            "notes": [f"{src} not found — run the VaR context mode first."],
            "source": {"repo": str(repo_path), "collector": "var-sim",
                       "note": "var_context_run_latest.json"},
        }
    payload = json_to_payload(json.loads(src.read_text(encoding="utf-8")))
    payload["source"] = {"repo": str(repo_path), "collector": "var-sim",
                         "note": "var_context_run_latest.json"}
    return payload
```

- [ ] Refresh and verify:

```bash
cd /c/Users/bottl/hermes-artifacts
env -u PYTHONPATH -u PYTHONHOME C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe tools/refresh.py var-simulations-digest
ls -la artifacts/var-simulations-digest/
```

Expected: `refreshed var-simulations-digest`; `data.json` + `index.html` exist; `index.html` contains `id="hermes-artifact-data"` and inlined `<style>`/`<script>` blocks.

- [ ] Visual gate: deliver `::preview{file="C:/Users/bottl/hermes-artifacts/artifacts/var-simulations-digest/index.html"}` in chat. Expected: title, generated stamp, summary cards from the real 517-byte JSON, sortable/filterable Fields table. **Jason approves the look before the remaining 5 convert.**

- [ ] Commit:

```bash
git add -A && git commit -m "feat: artifact shell (css/js) + var-sim collector + first converted artifact"
```

---

## Phase 5 — Convert the remaining 5 artifacts (mechanical per Task 4.6)

Each follows the identical loop: `artifact.json` + `template.html` (copy Task 4.6's template, change `<title>`) + collector module + `refresh.py <id>` + `::preview` visual check + commit. Common template diff for each:

```bash
cd /c/Users/bottl/hermes-artifacts
mkdir -p artifacts/<id>
# copy template, edit <title>, write artifact.json + collector, then:
env -u PYTHONPATH -u PYTHONHOME C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe tools/refresh.py <id>
git add -A && git commit -m "feat: convert <id> to universal shell"
```

### Task 5.1: `vol-suite-dashboard`

- `artifact.json`: `"collector": "vol-suite"`.
- Create `tools/collectors/vol_suite_collector.py`:

```python
"""Vol Suite Dashboard — vol_suite_result.json + latest orchestrator_output runs."""
from __future__ import annotations

import json
from pathlib import Path

from collectors import json_to_payload, register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")


@register("vol-suite")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    notes, cards, tables = [], [], []
    result = repo_path / "Vol_Suite" / "vol_suite_result.json"
    if result.exists():
        payload = json_to_payload(json.loads(result.read_text(encoding="utf-8")))
        cards += payload["summary"]
        tables += payload["tables"]
    else:
        notes.append(f"{result} not found — run Vol_Suite first.")
    runs_dir = repo_path / "orchestrator_output"
    if runs_dir.exists():
        runs = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())
        if runs:
            cards.insert(0, {"label": "latest unified run", "value": runs[-1]})
        tables.append({"id": "runs", "title": "Recent unified runs",
                       "columns": ["run"], "rows": [[r] for r in runs[-10:]]})
    return {"summary": cards[:8], "tables": tables, "charts": [], "notes": notes,
            "source": {"repo": str(repo_path), "collector": "vol-suite",
                       "note": "vol_suite_result.json + orchestrator_output/"}}
```

### Task 5.2: `financial-dev-dashboard`

- `artifact.json`: `"collector": "findev-swaps"`.
- Create `tools/collectors/findev_swaps_collector.py` (schema-agnostic `SELECT *` — no guessed column names):

```python
"""FinDev swaps snapshot — read-only peek at swaps.db (WAL-safe, index-only count)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from collectors import register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")


def _recent_rows(con: sqlite3.Connection, table: str, limit: int = 5) -> dict:
    cur = con.execute(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT {limit}")
    cols = [d[0] for d in cur.description]
    return {"id": table, "title": table, "columns": cols,
            "rows": [list(r) for r in cur.fetchall()]}


@register("findev-swaps")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    db = repo_path / "swaps.db"
    if not db.exists():
        return {"summary": [{"label": "swaps.db", "value": "missing", "tone": "bad"}],
                "tables": [], "charts": [], "notes": [f"{db} not found"],
                "source": {"repo": str(repo_path), "collector": "findev-swaps",
                           "note": "swaps.db"}}
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        max_row = con.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM swap_trades").fetchone()[0]
        runs_count = con.execute(
            "SELECT COUNT(*) FROM orchestrator_runs").fetchone()[0]
        scrape = _recent_rows(con, "scrape_log")
    finally:
        con.close()
    return {
        "summary": [
            {"label": "swap_trades (max rowid)", "value": f"{max_row:,}"},
            {"label": "orchestrator_runs", "value": str(runs_count)},
            {"label": "db size", "value": f"{db.stat().st_size / (1 << 30):.0f} GB"},
        ],
        "tables": [scrape],
        "charts": [],
        "notes": ["Read-only snapshot. Row count via MAX(rowid) — a COUNT(*) "
                  "on the 346 GB swap_trades table is a full scan and is avoided."],
        "source": {"repo": str(repo_path), "collector": "findev-swaps",
                   "note": "swaps.db"},
    }
```

### Task 5.3: `sentiment-scanner-board`

- `artifact.json`: `"collector": "sentiment"`.
- Create `tools/collectors/sentiment_collector.py`:

```python
"""Sentiment Scanner Board — highlighted ticker packs manifest."""
from __future__ import annotations

import json
from pathlib import Path

from collectors import json_to_payload, register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")


@register("sentiment")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    src = (repo_path / "sentiment-scanner/data/exports"
                      / "highlighted_ticker_packs/latest_manifest.json")
    if not src.exists():
        return {"summary": [{"label": "manifest", "value": "missing", "tone": "bad"}],
                "tables": [], "charts": [], "notes": [f"{src} not found"],
                "source": {"repo": str(repo_path), "collector": "sentiment",
                           "note": "latest_manifest.json"}}
    payload = json_to_payload(json.loads(src.read_text(encoding="utf-8")))
    payload["source"] = {"repo": str(repo_path), "collector": "sentiment",
                         "note": "latest_manifest.json"}
    return payload
```

(Implementation note: the module-level `MANIFEST` line above was left out on purpose — delete it; only `collect()` is used. Keep the file to exactly the imports, `DEFAULT_REPO`, and `collect`.)

### Task 5.4: `options-model-tracker` — **CONDITIONAL**

- Depends on `Options_Suite/REPORT.md` actually containing parseable markdown tables. First: `head -40 Options_Suite/REPORT.md`. If tables exist, ship the collector below; if the file is prose-only, ship a stub payload with `notes: ["REPORT.md has no tables — collector needs a data source decision"]` and flag to Jason instead of inventing data.
- Create `tools/collectors/options_models_collector.py`:

```python
"""Options Model Tracker — markdown tables from Options_Suite/REPORT.md."""
from __future__ import annotations

from pathlib import Path

from collectors import register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")


def _md_tables(text: str) -> list[dict]:
    lines = text.splitlines()
    tables, i = [], 0
    while i < len(lines):
        is_row = lines[i].strip().startswith("|")
        sep = (i + 1 < len(lines)
               and not set(lines[i + 1].replace("|", "")
                              .replace("-", "").replace(" ", "").strip()))
        if is_row and sep:
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip()
                             for c in lines[j].strip().strip("|").split("|")])
                j += 1
            tables.append({"id": f"md{len(tables)}",
                           "title": f"REPORT.md table {len(tables) + 1}",
                           "columns": header, "rows": rows})
            i = j
        i += 1
    return tables


@register("options-models")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    report = repo_path / "Options_Suite" / "REPORT.md"
    if not report.exists():
        return {"summary": [{"label": "REPORT.md", "value": "missing", "tone": "bad"}],
                "tables": [], "charts": [], "notes": [f"{report} not found"],
                "source": {"repo": str(repo_path), "collector": "options-models",
                           "note": "REPORT.md"}}
    tables = _md_tables(report.read_text(encoding="utf-8", errors="replace"))
    return {"summary": [{"label": "tables found", "value": str(len(tables))}],
            "tables": tables, "charts": [],
            "notes": ["Parsed from Options_Suite/REPORT.md markdown tables."],
            "source": {"repo": str(repo_path), "collector": "options-models",
                       "note": "REPORT.md"}}
```

### Task 5.5: `dev-knowledge-roadmap` — the universal showcase

- `artifact.json`: `"collector": "dev-roadmap"` — and note in `description` that pointing `--repo` at ANY repo works.
- Create `tools/collectors/dev_roadmap_collector.py`:

```python
"""Dev Knowledge Roadmap — newest markdown knowledge in ANY repo (mtime-ranked)."""
from __future__ import annotations

import time
from pathlib import Path

from collectors import register

DEFAULT_REPO = Path("C:/Users/bottl/FinancialDevelopment")
SCAN_DIRS = ("trading_journal", "docs", ".hermes/plans")


@register("dev-roadmap")
def collect(repo_path: Path = DEFAULT_REPO) -> dict:
    md: list[Path] = []
    for sub in SCAN_DIRS:
        base = repo_path / sub
        if base.exists():
            md += [p for p in base.rglob("*.md") if p.is_file()]
    md.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    for p in md[:20]:
        head = ""
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("# "):
                    head = line[2:].strip()
                    break
        except OSError:
            head = "(unreadable)"
        st = p.stat()
        rows.append([p.relative_to(repo_path).as_posix(),
                     time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                     f"{st.st_size:,} B", head])
    return {"summary": [{"label": "tracked markdown", "value": str(len(md))}],
            "tables": [{"id": "recent", "title": "Newest notes",
                        "columns": ["file", "mtime", "size", "title"], "rows": rows}],
            "charts": [],
            "notes": ["Top 20 newest .md across trading_journal/, docs/, "
                      ".hermes/plans/ of the source repo."],
            "source": {"repo": str(repo_path), "collector": "dev-roadmap",
                       "note": "mtime-ranked"}}
```

- Universal-path demo (the payoff): `python tools/refresh.py dev-knowledge-roadmap --repo C:/Users/bottl/obsidian-vault` → expected: `refreshed dev-knowledge-roadmap` with the vault's notes listed. Re-run with the default repo afterwards so data.json points at FinDev.

### Task 5.6: Refresh all + README + final commit

- [ ] `python tools/refresh.py --all` → expected: `refreshed <id>` × 6, exit 0.
- [ ] Write `README.md`: what the framework is, the data contract (schema summary), how to add an artifact (3 steps: folder + artifact.json + collector), how to refresh (per-id / `--all` / `--repo` override), the `::preview{file="..."}` chat-render convention, and the "ask Hermes to refresh <artifact>" phrasing.
- [ ] `pytest tests -q` → all pass. Commit: `docs: README + all six artifacts converted`.

---

## Phase 6 — Acceptance verification (definition of done)

- [ ] Removal: `netstat -ano | grep ":8765" | grep LISTENING` → empty; sidebar has no Apollo/Quant after Reload desktop plugins; `grep -r -c "quant-command-center" $LOCALAPPDATA/hermes/config.yaml` → 0; `pytest tests/ -q -x` in FinDev → 0 failures; `git -C C:/Users/bottl/FinancialDevelopment log --oneline -5` shows the removal commits.
- [ ] Framework: `pytest tests -q` in `hermes-artifacts` → all pass; `ls artifacts/*/data.json | wc -l` → `6`; every `index.html` renders via `::preview` (spot-check ≥ 2: `var-simulations-digest`, `financial-dev-dashboard`).
- [ ] Universality: `refresh.py dev-knowledge-roadmap --repo <obsidian-vault>` produced vault data (done in 5.5, re-verify output non-empty).
- [ ] Restore path documented: `_trash/2026-09-02-plugin-removal/` holds both desktop plugins + the backend plugin; git history holds the FinDev files.

---

## Scope taxonomy

**SELECTED (this plan):** Tasks 0–6 as written — plugin/backend/bridge removal, watchdog cron removal, burst_checkpoint fix, the `hermes-artifacts` framework, all 6 conversions.

**CONDITIONAL:** Task 5.4 (`options-model-tracker`) — gated on `Options_Suite/REPORT.md` containing parseable tables; stub-with-note + escalate if not. Task 3.3 YAML validation step — fall back to visual diff if no yaml parser is importable.

**PRESERVED (do NOT touch):** Everything in `dashboard/` — especially `worker_env.py`, `worker_worktree.py`, `job_object.py`, dispatch routes, `quant_modules.py`, `quant_alerts.py`, `migrations/004`, and their tests (Jason still dispatches workers from the dashboard). `scripts/quant_alert_check.py`. Historical docs (`docs/archive/`, `docs/superpowers/`, `Vol_Suite/docs/**`). `aggregate_unified.py` (no bridge coupling — verified by grep). All `.worktrees/**` copies (stale snapshots, out of scope). The originals under `C:/Users/bottl/Claude/Artifacts/`.

**EXCLUDED (not now):** A git remote/push for `hermes-artifacts` (Jason hasn't named one). The stray `%LOCALAPPDATA%/` junk dir in the FinDev root (unrelated; hygiene-pass candidate). Deleting the `persist:apollohermes` webview partition storage (cosmetic disk savings; can be found later under the Electron partition dir if wanted).

---

## Risks, tradeoffs, open questions

**Risks**
1. **Watchdog resurrection:** `quant-bridge-watchdog` may restart the bridge between Task 1.1 and Task 3.5 — symptom (new pid on 8765) and cure (do 3.5 first, kill again) are written into Task 1.1.
2. **Config corruption:** editing 5 YAML files by hand — mitigated by context-greps + parse check; a broken config.yaml would disable plugin loading wholesale, so verify with the app right after (Task 3.6).
3. **Cron jobs.json write race:** hand-editing while the gateway lives gets overwritten — hence Task 3.5 goes through the cron system, not the file.
4. **`swaps.db` reads on a live 346 GB WAL db:** all collector queries are either index hits (`MAX(rowid)`) or tiny-table reads; `mode=ro` guarantees no write-side lock. Still, if the scrape is mid-INSERT the reader may wait up to `timeout=30` — acceptable.
5. **REPORT.md format unknown (5.4):** plan stubs honestly instead of fabricating model data — a fabricated tracker would be worse than an empty one (house rule: never promise what isn't verified).
6. **`::preview` + CDN:** Chart.js needs network; offline render degrades to tables/notes with an explicit note rather than blank boxes.
7. **Old plugin sessions in state.db:** Apollo's webview cookies may linger in partition storage — cosmetic, listed under EXCLUDED.

**Tradeoffs**
- Single self-contained `index.html` per artifact (inlined CSS/JS/data) trades a few KB duplication for zero fetch-path risk and total portability — right call for a chat-rendered artifact.
- One universal schema (cards/tables/charts) trades expressiveness for consistency; anything that doesn't fit becomes a table row or a note. YAGNI until a real artifact needs more.
- stdlib-only validation (hand-rolled ~60 lines) instead of a `jsonschema` dependency — smaller install surface, no venv coupling; the test suite pins the behavior.

**Open questions (non-blocking)**
1. Git remote for `hermes-artifacts` — private GitHub repo under the existing account? (Needed only before the first push.)
2. Should the five Hermes profiles keep any memory of the removed Quant pane (e.g. profile SOUL.md mentions)? Quick grep during execution; deletions only if trivially safe.
3. Does Jason want the old `C:/Users/bottl/Claude/Artifacts/` originals kept forever as reference, or deleted once all 6 conversions are approved?
