from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from chart_app import profiles
from launch_dock import launch, registry, status, stop, sync
from launch_dock import spawn as spawn_mod


CHART_PROFILES = sync.CHART_PROFILES
STATIC_PAGE = Path(__file__).resolve().parent / "static" / "index.html"


class _DockServer:
    def __init__(
        self,
        directory: Path,
        *,
        cancel: Callable[[str], int] | None = None,
        kill: Callable[[int], None] | None = None,
        chart_post: Callable[[str, dict], dict] | None = None,
        chart_probe: Callable[[], bool] | None = None,
        alive: Callable[[int], bool] | None = None,
        popen: Callable | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.card_path = self.directory / "launch_dock.json"
        self.profile_path = self.directory / "chart_app_profiles.json"
        self.registry = registry.load(self.card_path)
        self.cancel = cancel or self._no_process_control
        self.kill = kill or self._no_process_control
        self.chart_post = chart_post
        self.chart_probe = chart_probe
        self.alive = alive
        self.popen = popen

    @staticmethod
    def _no_process_control(_value: Any) -> None:
        raise RuntimeError("no process control in unit test")

    def _save_registry(self) -> None:
        registry.save(self.registry, self.card_path)

    def _find_card(self, card_id: str) -> dict[str, Any]:
        for card in self.registry["cards"]:
            if card.get("id") == card_id:
                return card
        raise ValueError(f"no card {card_id}")

    def _resolved_cards(self) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        changed = False
        for card in self.registry["cards"]:
            resolved = profiles.resolve(
                card.get("instrument") or None,
                card.get("interval") or "15m",
                path=self.profile_path,
            )
            enriched = dict(card)
            if self.alive is not None and card.get("pid") is not None:
                enriched = status.reap(enriched, self.alive(int(card["pid"])))
            enriched = status.mark_stale(enriched, resolved.get("saved_at"))
            enriched["preset"] = resolved
            enriched["preset_source"] = resolved.get("source")
            cards.append(enriched)
            changed = changed or enriched != card
        if changed:
            self.registry["cards"] = [
                {key: value for key, value in card.items() if key not in ("preset", "preset_source")}
                for card in cards
            ]
            self._save_registry()
        return cards

    @staticmethod
    def _json_request(url: str, *, method: str = "GET", body: dict | None = None) -> dict:
        encoded = None
        headers = {}
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=encoded, headers=headers, method=method)
        try:
            with urlopen(request, timeout=2.0) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return value if isinstance(value, dict) else {"ok": False, "error": "invalid chart response"}

    def _chart_post_for_save(self) -> Callable[[str, dict], dict] | None:
        if self.chart_post is not None:
            return self.chart_post
        if self.chart_probe is not None:
            chart_up = self.chart_probe()
        else:
            chart_up = self._json_request(CHART_PROFILES).get("ok", True)
        if not chart_up:
            return None
        return lambda url, body: self._json_request(url, method="POST", body=body)

    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        body = body or {}
        try:
            if method == "GET" and path == "/":
                return 200, {"html": STATIC_PAGE.read_text(encoding="utf-8")}

            if method == "GET" and path == "/api/cards":
                cards = self._resolved_cards()
                return 200, {
                    "seeds": self.registry["seeds"],
                    "cards": cards,
                    "registry": self.registry,
                }

            if method == "POST" and path == "/api/copy":
                card = registry.copy_card(
                    self.registry,
                    str(body["source_id"]),
                    str(body["new_id"]),
                    str(body["instrument"]),
                )
                self._save_registry()
                return 200, {"card": card}

            if method == "POST" and path == "/api/seed":
                hours = float(body["hours"])
                cap = body.get("cap_dollars")
                cap_dollars = None if cap is None else float(cap)
                seed = self.registry["seeds"]["perp"]
                seed.update(hours=hours, cap_dollars=cap_dollars)
                self._save_registry()
                return 200, {"seed": seed}

            if method == "POST" and path == "/api/preset":
                ticker = str(body["ticker"])
                interval = str(body["interval"])
                existing = profiles.resolve(ticker, interval, path=self.profile_path)
                saved = sync.save_preset(
                    ticker,
                    interval,
                    config=dict(body.get("config") or {}),
                    elmo=dict(body.get("elmo") or {}),
                    capital=(
                        None
                        if body.get("capital") is None
                        else float(body["capital"])
                    ),
                    note=str(body.get("note") or ""),
                    path=self.profile_path,
                    chart_post=self._chart_post_for_save(),
                    existing=existing,
                )
                return 200, {"saved": saved}

            if method == "POST" and path == "/api/launch":
                card = self._find_card(str(body["id"]))
                launch._busy(self.registry["cards"], card)
                if card.get("seed") == "perp":
                    argv = launch.perp_argv(
                        card,
                        self.registry["seeds"]["perp"],
                        cards=self.registry["cards"],
                    )
                elif card.get("seed") == "stocks":
                    argv = launch.stocks_argv(card)
                elif card.get("seed") == "event_desk":
                    argv = launch.event_desk_argv()
                else:
                    raise ValueError(f"unknown seed {card.get('seed')}")
                card["argv"] = argv
                if self.popen is None:
                    card["state"] = "launching"
                    self._save_registry()
                    return 200, {"argv": argv, "spawned": False, "card": card}
                cwd = launch.EVENT_DESK if card.get("seed") == "event_desk" else launch.BLACKHOLE
                updated = spawn_mod.spawn(card, argv, popen=self.popen, cwd=cwd)
                self.registry["cards"] = [
                    updated if item.get("id") == updated.get("id") else item
                    for item in self.registry["cards"]
                ]
                self._save_registry()
                return 200, {"argv": argv, "spawned": True, "card": updated}

            if method == "POST" and path == "/api/stop":
                card = self._find_card(str(body["id"]))
                updated = stop.stop_card(
                    card,
                    cmdline=body.get("cmdline"),
                    cancel=self.cancel,
                    kill=self.kill,
                )
                self.registry["cards"] = [
                    updated if item.get("id") == updated.get("id") else item
                    for item in self.registry["cards"]
                ]
                self._save_registry()
                return 200, {"card": updated, "state": updated.get("state"), "error": updated.get("error")}

            return 404, {"error": "not found"}
        except (KeyError, TypeError, ValueError, launch.LaunchRefused, sync.ChartSaveError) as exc:
            return 400, {"error": str(exc)}


def create_server(
    directory: str | Path,
    *,
    cancel: Callable[[str], int] | None = None,
    kill: Callable[[int], None] | None = None,
    chart_post: Callable[[str, dict], dict] | None = None,
    chart_probe: Callable[[], bool] | None = None,
    alive: Callable[[int], bool] | None = None,
    popen: Callable | None = None,
) -> _DockServer:
    return _DockServer(
        Path(directory),
        cancel=cancel,
        kill=kill,
        chart_post=chart_post,
        chart_probe=chart_probe,
        alive=alive,
        popen=popen,
    )


class _HTTPHandler(BaseHTTPRequestHandler):
    dock: _DockServer

    def _respond(self, status_code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        code, payload = self.dock.handle("GET", self.path)
        if self.path == "/" and code == 200:
            raw = payload["html"].encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self._respond(code, payload)

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._respond(400, {"error": "invalid JSON"})
            return
        code, payload = self.dock.handle("POST", self.path, body)
        self._respond(code, payload)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


ARTIFACTS = Path(r"E:/BlackHole_Investments/BlackHole/artifacts")


def _detached_popen(argv, **kw):
    import subprocess

    log_dir = ARTIFACTS / "launch_dock_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handle = open(log_dir / "launch.log", "ab", buffering=0)
    return subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT, **kw)


def serve(directory: str | Path = ARTIFACTS) -> None:
    dock = create_server(directory, popen=_detached_popen)

    class Handler(_HTTPHandler):
        pass

    Handler.dock = dock
    HTTPServer(("127.0.0.1", 8792), Handler).serve_forever()


if __name__ == "__main__":
    serve()
