from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from chart_app import profiles
from launch_dock import launch, registry, status, stop, sync
from launch_dock import tune as tune_mod
from launch_dock import spawn as spawn_mod


CHART_PROFILES = sync.CHART_PROFILES
STATIC_PAGE = Path(__file__).resolve().parent / "static" / "index.html"
TAIL_PAGE = Path(__file__).resolve().parent / "static" / "tail.html"
# Only the end of a log is read per poll: a runner appends a line every ~20s
# for as long as it lives, and the page polls every 2s.
_TAIL_BYTES = 64 * 1024


def tail_lines(path: Path, lines: int) -> list[str]:
    """The last `lines` lines of `path`, reading at most _TAIL_BYTES of it."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _TAIL_BYTES))
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace").splitlines()
    if size > _TAIL_BYTES and text:
        text = text[1:]  # the first line was cut by the seek
    return text[-lines:] if lines > 0 else []


def _cardable(ticker: str) -> bool:
    """Can a saved profile's ticker become a card? A Kalshi perp or a stock.

    `*` has no ticker, and an OKX crypto key (BTC-USD, the seed BTC-PERP tunes
    search from) is neither: as a "stock" card it would launch the equity
    scanner on a coin.
    """
    from chart_app.crypto_source import is_crypto

    t = ticker.strip().upper()
    if t in launch.KALSHI:
        return True
    return t not in ("", "*") and not is_crypto(t)


class _DockServer:
    def serve_forever(self) -> None:
        """Bind 127.0.0.1:8792 and block. The only place the dock listens."""
        handler = type("Handler", (_HTTPHandler,), {"dock": self})
        ThreadingHTTPServer(("127.0.0.1", 8792), handler).serve_forever()

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
        cancel_equity: Callable[[str], int] | None = None,
    ) -> None:
        self.cancel_equity = cancel_equity
        self.directory = Path(directory)
        self.card_path = self.directory / "launch_dock.json"
        self.log_dir = self.directory / "launch_dock_logs"
        self.tune_dir = self.directory / "tune_runs"
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

    def _cmdline_for(self, card: dict, fallback: Any) -> str | None:
        """The running pid's real command line when we can read it, else the
        caller's value, else the card's argv. A None result means dead."""
        pid = card.get("pid")
        if pid:
            from launch_dock.real import cmdline as pid_cmdline

            try:
                real = pid_cmdline(int(pid))
            except Exception:
                real = None
            if real:
                return real
        if isinstance(fallback, str):
            return fallback
        if isinstance(fallback, list) and fallback:
            return " ".join(str(a) for a in fallback)
        argv = card.get("argv")
        if argv:
            return " ".join(str(a) for a in argv)
        return None

    def _finish_readback(self, updated: dict) -> dict:
        """Block up to 15s for the runner's session record, then store its books.

        A perp journal that never appears leaves the card `launching`; the
        launch is not reported as success in that case.
        """
        if updated.get("seed") != "perp":
            return updated
        try:
            from launch_dock.real import finish_launch_readback

            return finish_launch_readback(updated, timeout_s=15.0)
        except Exception:
            return updated

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

    def _require_own_profile(self, card: dict[str, Any]) -> None:
        """A sleeve (perp or stock) runs only on the profile saved for its own ticker.

        `profiles.resolve` falls back to `*|5m` and then the shipped defaults,
        so a card whose instrument has no tune -- or is misspelled -- launched
        cleanly on somebody else's in-sample preset.
        """
        ticker = str(card.get("instrument") or "").upper()
        interval = str(card.get("interval") or "15m")
        resolved = profiles.resolve(ticker, interval, path=self.profile_path)
        want = f"{ticker}|{interval}"
        if resolved.get("source") != want:
            raise launch.LaunchRefused(
                f"no saved profile {want}; this card would run {resolved.get('source')} "
                "instead. Tune it (Walk-forward) or change the card's interval."
            )

    def profile_rows(self) -> list[dict[str, Any]]:
        """Every saved profile, flattened for a table, newest first."""
        rows = []
        for key, rec in profiles.load_all(self.profile_path).items():
            metrics = rec.get("metrics") or {}
            ins = metrics.get("in_sample") if isinstance(metrics.get("in_sample"), dict) else metrics
            wf = metrics.get("walk_forward") or {}
            held = wf.get("held") if isinstance(wf, dict) else None
            oos = (held or {}).get("oos_total_return_pct")
            if oos is None and isinstance(metrics.get("oos"), dict):
                oos = metrics["oos"].get("oos_pct")
            cfg = rec.get("config") or {}
            rows.append({
                "key": key,
                "validation": rec.get("validation") or "none",
                "saved_at": rec.get("saved_at"),
                "in_sample_pct": (ins or {}).get("total_return_pct"),
                "oos_pct": oos,
                "shorts": cfg.get("allow_short"),
                "max_units": cfg.get("max_units"),
                "note": rec.get("note") or "",
                "carded": _cardable(key.partition("|")[0]),
                "used_by": [
                    c["id"] for c in self.registry["cards"]
                    if f"{str(c.get('instrument') or '').upper()}|{c.get('interval') or '15m'}" == key
                ],
            })
        rows.sort(key=lambda r: r["saved_at"] or "", reverse=True)
        return rows

    def _pid_alive(self, pid: Any) -> bool | None:
        if not pid or self.alive is None:
            return None
        return bool(self.alive(int(pid)))

    def start_tune(self, card_id: str, shorts: str, seed_from: str) -> dict[str, Any]:
        """Spawn one walk-forward for a card. One at a time per card: two
        would race on the same report file, and the rule is never to fan out
        billed pulls."""
        card = self._find_card(card_id)
        job = card.get("tune") or {}
        if self._pid_alive(job.get("pid")):
            raise launch.LaunchRefused(f"a walk-forward is already running for {card_id}")
        self.tune_dir.mkdir(parents=True, exist_ok=True)
        report = tune_mod.report_path(self.tune_dir, card_id, shorts)
        argv = tune_mod.tune_argv(card, shorts=shorts, seed_from=seed_from, report=report)
        if self.popen is None:
            return {"argv": argv, "spawned": False}
        report.unlink(missing_ok=True)  # a stale report must not read as this run's
        process = self.popen(
            argv,
            creationflags=launch.DETACH,
            cwd=launch.BLACKHOLE,
            stdin=spawn_mod.DEVNULL,
            log_name=f"{card_id}.tune",
        )
        card["tune"] = {"pid": process.pid, "shorts": shorts, "seed_from": seed_from}
        self._save_registry()
        return {"argv": argv, "spawned": True, "pid": process.pid}

    def tune_status(self, card_id: str) -> dict[str, Any]:
        card = self._find_card(card_id)
        job = card.get("tune") or {}
        return {
            "id": card_id,
            "job": job,
            "running": bool(self._pid_alive(job.get("pid"))),
            "results": {
                shorts: tune_mod.read_summary(
                    tune_mod.report_path(self.tune_dir, card_id, shorts)
                )
                for shorts in tune_mod.SHORTS
            },
        }

    def adopt(self, card_id: str, shorts: str) -> dict[str, Any]:
        """Write an ADOPT's candidate as the card's walk_forward profile."""
        card = self._find_card(card_id)
        summary = tune_mod.read_summary(tune_mod.report_path(self.tune_dir, card_id, shorts))
        if not summary or "error" in summary:
            raise ValueError("no finished walk-forward for that choice")
        ticker = str(card["instrument"])
        interval = str(card.get("interval") or "5m")
        if (summary.get("ticker"), summary.get("interval")) != (ticker, interval):
            raise ValueError("report is for a different ticker/interval than the card")
        existing = profiles.resolve(ticker, interval, path=self.profile_path)
        record = tune_mod.adoption(summary, existing=existing)
        backup = tune_mod.backup_store(self.profile_path, self.directory / "profile_backups")
        body = {"ticker": ticker, "interval": interval, **record}
        # Through the chart when it is up, so it redraws with the new profile
        # now rather than at the next bar; straight to the store otherwise.
        post = self._chart_post_for_save()
        if post is not None:
            result = post(CHART_PROFILES, body)
            if not result.get("ok"):
                raise sync.ChartSaveError(str(result.get("error") or "chart save failed"))
            saved = result["saved"]
        else:
            saved = profiles.save(ticker, interval, path=self.profile_path, **record)
        return {"saved": saved, "backup": str(backup) if backup else None}

    def tail(self, card_id: str, lines: int, kind: str = "") -> dict[str, Any]:
        """One card's own runner output, plus whether its pid is still alive.

        A card launched before per-card logs has none; say so rather than
        showing the shared launch.log, whose lines carry no ticker.
        """
        card = self._find_card(card_id)
        if kind == "tune":
            log = self.log_dir / f"{card_id}.tune.log"
            pid = (card.get("tune") or {}).get("pid")
        else:
            log = self.log_dir / f"{card_id}.log"
            pid = card.get("pid")
        alive = None
        if pid and self.alive is not None:
            alive = bool(self.alive(int(pid)))
        state = card.get("state")
        if kind == "tune":
            state = "running" if alive else "finished"
        return {
            "id": card_id,
            "kind": kind,
            "state": state,
            "pid": pid,
            "alive": alive,
            "log": str(log),
            "exists": log.exists(),
            "lines": tail_lines(log, max(1, min(lines, 2000))),
        }

    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        body = body or {}
        url = urlsplit(path)
        path = url.path
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and path == "/":
                return 200, {"html": STATIC_PAGE.read_text(encoding="utf-8")}

            if method == "GET" and path == "/tail":
                return 200, {"html": TAIL_PAGE.read_text(encoding="utf-8")}

            if method == "GET" and path == "/api/tail":
                return 200, self.tail(
                    str(query["id"]), int(query.get("lines", 300)), query.get("kind", "")
                )

            if method == "GET" and path == "/api/tune":
                return 200, self.tune_status(str(query["id"]))

            if method == "POST" and path == "/api/tune":
                return 200, self.start_tune(
                    str(body["id"]),
                    str(body.get("shorts") or "keep"),
                    str(body.get("seed_from") or ""),
                )

            if method == "POST" and path == "/api/adopt":
                return 200, self.adopt(str(body["id"]), str(body["shorts"]))

            if method == "GET" and path == "/api/cards":
                cards = self._resolved_cards()
                return 200, {
                    "seeds": self.registry["seeds"],
                    "cards": cards,
                    "registry": self.registry,
                }

            if method == "GET" and path == "/api/instruments":
                # Stock copy targets are the tickers with a saved chart_app profile:
                # a stock sleeve is only worth copying onto a name that has been
                # tuned. `*` wildcards and Kalshi names (GOLD is the perp here, not
                # Barrick) are not stocks.
                stocks = sorted({
                    key.partition("|")[0].upper()
                    for key in profiles.load_all(self.profile_path)
                } - {"*"} - set(launch.KALSHI))
                return 200, {
                    "perp": sorted(launch.KALSHI),
                    "stocks": stocks,
                    "intervals": list(registry.INTERVALS),
                }

            if method == "GET" and path == "/api/profiles":
                return 200, {"profiles": self.profile_rows()}

            if method == "POST" and path == "/api/copy":
                card = registry.copy_card(
                    self.registry,
                    str(body["source_id"]),
                    str(body.get("new_id") or ""),
                    str(body["instrument"]),
                    known=launch.KALSHI,
                )
                self._save_registry()
                return 200, {"card": card}

            if method == "POST" and path == "/api/card-from-profile":
                if not _cardable(str(body["key"]).partition("|")[0]):
                    raise ValueError(f"{body['key']} is not a perp or a stock; it cannot be a card")
                card = registry.card_from_profile(self.registry, str(body["key"]), launch.KALSHI)
                self._save_registry()
                return 200, {"card": card}

            if method == "POST" and path == "/api/delete":
                self._resolved_cards()  # reap dead pids first so a dead "running" card can go
                registry.delete_card(self.registry, str(body["id"]))
                self._save_registry()
                return 200, {"deleted": str(body["id"])}

            if method == "POST" and path == "/api/card":
                self._resolved_cards()
                card = registry.update_card(
                    self.registry, str(body["id"]), dict(body.get("fields") or {})
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
                    self._require_own_profile(card)
                    argv = launch.perp_argv(
                        card,
                        self.registry["seeds"]["perp"],
                        cards=self.registry["cards"],
                    )
                elif card.get("seed") == "stocks":
                    self._require_own_profile(card)
                    argv = launch.stocks_argv(card, cards=self.registry["cards"])
                elif card.get("seed") == "event_desk":
                    argv = launch.event_desk_argv()
                else:
                    raise ValueError(f"unknown seed {card.get('seed')}")
                card["argv"] = argv
                if self.popen is None:
                    card["state"] = "launching"
                    self._save_registry()
                    return 200, {"argv": argv, "spawned": False, "card": card}
                if card.get("seed") == "perp":
                    launch.refuse_duplicate_runner(str(card.get("instrument") or ""))
                cwd = launch.EVENT_DESK if card.get("seed") == "event_desk" else launch.BLACKHOLE
                updated = spawn_mod.spawn(card, argv, popen=self.popen, cwd=cwd)
                self.registry["cards"] = [
                    updated if item.get("id") == updated.get("id") else item
                    for item in self.registry["cards"]
                ]
                self._save_registry()
                updated = self._finish_readback(updated)
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
                    cmdline=self._cmdline_for(card, body.get("cmdline")),
                    cancel=self.cancel,
                    kill=self.kill,
                    cancel_equity=self.cancel_equity,
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
    cancel_equity: Callable[[str], int] | None = None,
) -> _DockServer:
    return _DockServer(
        Path(directory),
        cancel_equity=cancel_equity,
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
        try:
            self.wfile.write(raw)
        except (ConnectionAbortedError, BrokenPipeError, OSError):
            return

    def do_GET(self) -> None:  # noqa: N802
        code, payload = self.dock.handle("GET", self.path)
        if urlsplit(self.path).path in ("/", "/tail") and code == 200:
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


def _detached_popen(argv, log_name=None, **kw):
    import subprocess

    log_dir = ARTIFACTS / "launch_dock_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handle = open(log_dir / f"{log_name or 'launch'}.log", "ab", buffering=0)
    return subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT, **kw)


def serve(directory: str | Path = ARTIFACTS) -> None:
    from launch_dock.real import build_dock

    build_dock().serve_forever()


if __name__ == "__main__":
    serve()
