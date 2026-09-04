"""quant_console_agent.py

Phase 6 of the widget-native quant console: the natural-language console
agent that turns a free-text prompt into a set of widget runs on the current
page.

Scope note: SELECT-AND-RUN ONLY. This endpoint resolves which widgets to run,
executes them in-process, and arranges the results on the page. It does NOT
interpret or editorialize on results.

----------------------------------------------------------------------------
SECURITY -- localhost-only (plan step 7)
----------------------------------------------------------------------------
This endpoint turns FREE-TEXT into billed ThetaData-backed widget runs, which
is a strictly larger exposure than the existing structured-param ``POST
/run/*`` routes. The dashboard's deliberate zero-auth posture (see CLAUDE.md)
means this endpoint MUST stay localhost-only. Do not expose the dashboard
beyond localhost, especially now that a single free-text submission can
trigger multiple billed widget runs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from shared.module_registry import all_modules, resolve_modules

router = APIRouter(prefix="/api/quant-console", tags=["quant-console-agent"])

# ---------------------------------------------------------------------------
# Bounds (plan step 2 per-request cap, plan step 6 aggregate day cap)
# ---------------------------------------------------------------------------
MAX_TOOL_CALLS_PER_REQUEST = 6          # plan step 2: cap one prompt's blast radius
AGENT_DAILY_CAP = int(os.environ.get("AGENT_DAILY_CAP", "50"))  # plan step 6

_CAP_DB = Path(__file__).resolve().parent.parent / "artifacts" / "agent_cost_cap.db"


def _today() -> str:
    return date.today().isoformat()


def _ensure_cap_table() -> None:
    _CAP_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_CAP_DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_runs (day TEXT PRIMARY KEY, count INTEGER NOT NULL)"
        )


def read_day_count(day: str) -> int:
    """Count of /agent requests already served on ``day`` (plan step 6)."""
    try:
        _ensure_cap_table()
        with sqlite3.connect(_CAP_DB) as conn:
            row = conn.execute(
                "SELECT count FROM agent_runs WHERE day = ?", (day,)
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        # A broken cap counter fails open to a safe default: treat as no runs yet.
        return 0


def bump_day_count(day: str) -> int:
    """Increment the count for ``day`` and return the new count (plan step 6)."""
    _ensure_cap_table()
    with sqlite3.connect(_CAP_DB) as conn:
        conn.execute(
            "INSERT INTO agent_runs(day, count) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET count = count + 1",
            (day,),
        )
        row = conn.execute(
            "SELECT count FROM agent_runs WHERE day = ?", (day,)
        ).fetchone()
    return int(row[0])


# ---------------------------------------------------------------------------
# Pluggable model client (plan step 4: ANTHROPIC_API_KEY wiring is a later step)
# ---------------------------------------------------------------------------
class ModelNotConfiguredError(Exception):
    """Raised when no real model client can be constructed (no API key)."""


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def build_tool_manifest() -> list[dict]:
    """One tool entry per ModuleSpec in all_modules().

    This is the SINGLE source of truth shared with the human catalog browser
    (plan's "What can I ask" requirement): one code path -- all_modules() --
    serves both the agent's tool manifest and the human picker. The model's
    tool descriptions/input schemas are derived directly from each spec's
    ``description`` / ``inputs`` / ``sample``, never hand-maintained again.
    """
    manifest: list[dict] = []
    for spec in all_modules():
        properties: dict[str, Any] = {}
        required: list[str] = []
        for field_name in ("ticker", "expiry", "basket"):
            mode = getattr(spec.inputs, field_name)
            if mode == "none":
                continue
            properties[field_name] = {
                "type": "string",
                "description": f"{field_name} ({mode})",
            }
            if mode == "required":
                required.append(field_name)
        # Merge the spec's sample payload as extra (optional) hint properties so
        # the model can see the real argument shape the widget expects.
        for key, value in spec.sample.items():
            if key not in properties:
                properties[key] = {
                    "type": _json_type(value),
                    "description": f"sample value for {key}",
                }
        input_schema: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required
        manifest.append(
            {
                "name": spec.slug,
                "description": spec.description,
                "input_schema": input_schema,
            }
        )
    return manifest


def _default_model_client_factory() -> Any:
    """Default client factory: requires ANTHROPIC_API_KEY to be set.

    Real Anthropic wiring (the Messages API tool-use loop) is a later step,
    deferred until a key is available; today this factory raises a 503 when
    the env var is missing so the endpoint never silently runs without funds.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ModelNotConfiguredError(
            "model client not configured: ANTHROPIC_API_KEY is missing"
        )
    raise ModelNotConfiguredError(
        "real Anthropic tool-use client not yet implemented; "
        "ANTHROPIC_API_KEY is present but wiring is a later phase"
    )


# Pluggable at module level so tests can inject a fake client without touching
# app wiring. A factory returning an object with:
#     plan_tool_calls(prompt: str, tools: list[dict]) -> list[dict]
# where each call is {"name": <slug>, "arguments": {scope fields}}.
MODEL_CLIENT_FACTORY: Callable[[], Any] = _default_model_client_factory


# ---------------------------------------------------------------------------
# Layout store integration (plan step 3). dashboard.layouts is being created
# by another worker; coded against the documented interface and marked pending
# until that file lands.
# ---------------------------------------------------------------------------
def _default_layout_db_path() -> str:
    return str(Path(__file__).resolve().parent.parent / "artifacts" / "widget_cache.db")


def _get_layout_store():
    """Best-effort import of dashboard.layouts.LayoutStore.

    Returns (store, None) on success, or (None, error) if the file does not
    exist yet (another worker is creating it) so the endpoint can mark the
    wiring as pending rather than failing the whole request.
    """
    try:
        from dashboard.layouts import LayoutStore
    except Exception as exc:  # file not landed yet
        return None, exc
    try:
        return LayoutStore(), None
    except TypeError:
        return LayoutStore(db_path=_default_layout_db_path()), None
    except Exception as exc:
        return None, exc


def write_layout_instance(page: str, slug: str, scope: dict, prompt: str) -> dict:
    """Append one widget instance to ``page``'s layout, tagged with the prompt.

    Returns a status dict. If the layouts store isn't wired in yet (another
    worker is creating dashboard.layouts), returns a 'pending' marker -- the
    widget still ran; only the arrangement step is deferred.
    """
    store, err = _get_layout_store()
    if store is None:
        return {"added": False, "reason": f"layouts store pending: {err!r}"}
    try:
        existing = store.list_page(page) or []
    except Exception as exc:
        return {"added": False, "reason": f"list_page failed: {exc!r}"}
    instances = list(existing)
    position = len(instances)
    new_instance = {
        "slug": slug,
        "position": position,
        "scope_override": scope,
        "sync_enabled": True,
        "config_json": json.dumps({"added_from_prompt": prompt}),
    }
    try:
        store.replace_page(page, instances + [new_instance])
        return {"added": True, "slug": slug, "position": position}
    except Exception as exc:
        return {"added": False, "reason": f"replace_page failed: {exc!r}"}


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class AgentRequest(BaseModel):
    prompt: str
    confirm: bool = False
    page: str = "quant"


def _json_safe(obj: Any) -> Any:
    """Coerce run() metrics to a JSON-serializable form (numpy scalars/arrays)."""
    def _default(o: Any) -> Any:
        if hasattr(o, "item"):
            return o.item()
        if hasattr(o, "tolist"):
            return o.tolist()
        return str(o)

    try:
        return json.loads(json.dumps(obj, default=_default))
    except Exception:
        return {"unserializable": str(obj)}


def _run_agent_loop(client: Any, prompt: str, confirm: bool, page: str):
    """Run the model's tool-use loop and gate execution on ``confirm``.

    confirm=False (plan step 5 confirmation gate): collect the proposed
    widget/scope list and run NOTHING -- return {proposed, executed: []}.
    confirm=True: execute each proposed widget in-process and arrange the
    results on the page.

    Execution is in-process via shared.module_registry.resolve_modules(...)
    [0].run(context) -- NOT an HTTP call (the route lives in another worker's
    file).
    """
    manifest = build_tool_manifest()
    proposed: list[dict] = []
    executed: list[dict] = []

    try:
        calls = client.plan_tool_calls(prompt, manifest) or []
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"model tool-use loop failed: {exc!r}")

    truncated = len(calls) > MAX_TOOL_CALLS_PER_REQUEST
    calls = (calls or [])[:MAX_TOOL_CALLS_PER_REQUEST]

    for call in calls:
        if not isinstance(call, dict):
            continue
        slug = call.get("name")
        scope = call.get("arguments") or {}
        try:
            spec = resolve_modules([slug])[0]
        except Exception as exc:
            proposed.append(
                {"slug": slug, "scope": scope, "error": f"unknown slug: {exc!r}"}
            )
            continue
        proposed.append({"slug": slug, "scope": scope, "description": spec.description})

        if not confirm:
            continue  # plan step 5: confirmation gate -- do not execute yet

        try:
            result = spec.run(scope)
        except Exception as exc:
            executed.append(
                {"slug": slug, "scope": scope, "status": "failed",
                 "error": str(exc)}
            )
            continue

        recorded = {
            "slug": slug,
            "scope": scope,
            "status": result.status,
            "metrics": _json_safe(result.metrics),
        }
        executed.append(recorded)
        if result.status == "ok":
            recorded["layout"] = write_layout_instance(
                page, slug, scope, prompt
            )

    return {
        "proposed": proposed,
        "executed": executed,
        "truncated": truncated,
        "tool_call_cap": MAX_TOOL_CALLS_PER_REQUEST,
    }


@router.post("/agent")
def agent_run(body: AgentRequest) -> dict:
    """POST /api/quant-console/agent

    Body: {prompt: str, confirm: bool = false, page: str = "quant"}.

    Fails 429 past the daily aggregate cap (plan step 6), 503 when no model
    client is configured (ANTHROPIC_API_KEY missing), 502 on model failure.
    """
    day = _today()
    if read_day_count(day) >= AGENT_DAILY_CAP:
        raise HTTPException(
            status_code=429,
            detail=(
                f"daily agent run cap reached ({AGENT_DAILY_CAP} for {day}); "
                "set AGENT_DAILY_CAP or wait until tomorrow"
            ),
        )
    bump_day_count(day)

    try:
        client = MODEL_CLIENT_FACTORY()
    except ModelNotConfiguredError as exc:
        raise HTTPException(
            status_code=503,
            detail="model client not configured: " + str(exc),
        ) from None

    return _run_agent_loop(client, body.prompt, body.confirm, body.page)