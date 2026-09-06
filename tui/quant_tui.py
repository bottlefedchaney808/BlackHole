# Quant TUI — terminal client for the FinancialDevelopment widget API.
# Same surfaces as quant.html: GET /api/widgets/catalog, POST /api/widgets/{slug}/run,
# GET /api/widgets/{slug}/state. No business logic lives here.
#
# Run:  .venv/Scripts/python.exe tui/quant_tui.py            (API on 127.0.0.1:8787)
#       FINDEV_TUI_API=http://127.0.0.1:8791 ... quant_tui.py   (custom API)
#
# Keys: up/down select widget · r run · s cached state · t ticker · c clear · q quit
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Static

API = os.environ.get("FINDEV_TUI_API", "http://127.0.0.1:8787").rstrip("/")

REPO = Path(__file__).resolve().parent.parent


def _http(method: str, path: str, body: dict | None = None, timeout: float = 60.0):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def default_scope(ticker: str) -> dict:
    """Ticker plus whatever default_context.json carries (no override of ticker)."""
    scope: dict = {"ticker": ticker.upper()}
    ctx = REPO / "default_context.json"
    if ctx.exists():
        try:
            extra = json.loads(ctx.read_text())
            if isinstance(extra, dict):
                for k, v in extra.items():
                    scope.setdefault(k, v)
        except Exception:
            pass
    return scope


def render_payload(payload) -> str:
    """Bounded terminal rendering: readable head + truncated body, never a 1.6MB dump."""
    if not isinstance(payload, dict):
        return str(payload)[:8000]
    lines: list[str] = []
    status = payload.get("status")
    if status:
        lines.append(f"status: {status}")
    for key in ("slug", "scope", "run_id", "elapsed_s"):
        if payload.get(key) is not None:
            lines.append(f"{key}: {payload[key]}")
    arts = payload.get("artifacts") or []
    for a in arts[:12]:
        if isinstance(a, dict):
            lines.append(f"  [{a.get('kind','?')}] {a.get('path','')}")
    if len(arts) > 12:
        lines.append(f"  … +{len(arts)-12} more artifacts")
    for key in ("error", "message", "detail"):
        if payload.get(key):
            lines.append(f"{key}: {payload[key]}")
    result = payload.get("result")
    body = json.dumps(result, indent=2, default=str) if result is not None \
        else json.dumps({k: v for k, v in payload.items()
                         if k not in ("result", "artifacts")}, indent=2, default=str)
    if len(body) > 20000:
        body = body[:20000] + f"\n… [truncated; {len(body):,} chars total — full payload cached server-side]"
    lines.append(body)
    return "\n".join(lines)[:30000]


class QuantTUI(App):
    TITLE = "Quant TUI — widget console"
    CSS = """
    #main { height: 1fr; }
    #left { width: 34; min-width: 24; border-right: solid $accent; }
    #right { width: 1fr; }
    #output { height: 1fr; border: solid $panel; }
    #status { height: 3; border: solid $panel; padding: 0 1; }
    Input { display: none; height: 3; }
    Input.visible { display: block; }
    """
    BINDINGS = [
        Binding("r", "run", "Run"),
        Binding("s", "state", "State"),
        Binding("t", "ticker", "Ticker"),
        Binding("c", "clear", "Clear"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ticker = "SPY"
        self.catalog: list[dict] = []
        self.selected: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield Input(placeholder="ticker", id="ticker_input")
                yield DataTable(id="catalog")
            with Vertical(id="right"):
                yield Static("ready", id="output", markup=False)
                yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#catalog", DataTable)
        table.cursor_type = "row"
        table.add_columns("widget", "slug")
        self._load_catalog()
        self.set_interval(30.0, self._poll_state)  # live-ish: refresh cached state

    # -- data -------------------------------------------------------------
    def _load_catalog(self) -> None:
        table = self.query_one("#catalog", DataTable)
        table.clear()
        self.catalog = []
        try:
            payload = _http("GET", "/api/widgets/catalog")
            modules = payload if isinstance(payload, list) else payload.get("modules", payload.get("widgets", []))
            for m in modules:
                slug = m.get("slug") or m.get("name") or "?"
                label = m.get("title") or m.get("label") or slug
                self.catalog.append({"slug": slug, "meta": m})
                table.add_row(str(label)[:30], slug)
            self._status(f"catalog: {len(self.catalog)} widgets @ {API}")
        except Exception as e:
            self._status(f"catalog FAILED: {e}")

    def _selected_slug(self) -> str | None:
        table = self.query_one("#catalog", DataTable)
        row = table.cursor_row
        if 0 <= row < len(self.catalog):
            return self.catalog[row]["slug"]
        return None

    def _status(self, text: str) -> None:
        self.last_status = text
        self.query_one("#status", Static).update(text[:500])

    def _out(self, text: str) -> None:
        self.last_output = text
        self.query_one("#output", Static).update(text)

    # -- actions ----------------------------------------------------------
    def action_run(self) -> None:
        slug = self._selected_slug()
        if not slug:
            self._status("no widget selected")
            return
        self._status(f"running {slug} (ticker={self.ticker}) …")
        try:
            result = _http("POST", f"/api/widgets/{slug}/run",
                           {"scope": default_scope(self.ticker), "params": {}})
            self._out(render_payload(result))
            self._status(f"{slug}: done (ticker={self.ticker})")
        except Exception as e:
            self._status(f"{slug}: run FAILED {e}")

    def _state(self, slug: str) -> dict:
        """Cached state for the current ticker (scope key format: 'ticker:SPY')."""
        return _http("GET", f"/api/widgets/{slug}/state?scope=ticker:{self.ticker}")

    def action_state(self) -> None:
        slug = self._selected_slug()
        if not slug:
            self._status("no widget selected")
            return
        try:
            result = self._state(slug)
            self._out(render_payload(result))
            self._status(f"{slug}: cached state (ticker={self.ticker})")
        except Exception as e:
            self._status(f"{slug}: state FAILED {e}")

    def _poll_state(self) -> None:
        slug = self._selected_slug()
        if not slug:
            return
        try:
            result = self._state(slug)
            if isinstance(result, dict) and result:
                self._out(render_payload(result))
                self._status(f"{slug}: auto-refresh (ticker={self.ticker})")
        except Exception:
            pass

    def action_ticker(self) -> None:
        inp = self.query_one("#ticker_input", Input)
        inp.add_class("visible")
        inp.value = self.ticker
        inp.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ticker_input":
            self.ticker = event.value.strip().upper() or "SPY"
            event.input.remove_class("visible")
            self.query_one("#catalog", DataTable).focus()
            self._status(f"ticker set: {self.ticker}")

    def action_clear(self) -> None:
        self._out("")


if __name__ == "__main__":
    QuantTUI().run()
