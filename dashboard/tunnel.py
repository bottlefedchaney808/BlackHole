"""dashboard/tunnel.py

In-memory manager for a single Cloudflare quick tunnel (`cloudflared tunnel
--url <target>`), used by the dashboard's Share feature to expose the
localhost-only dashboard temporarily via a random `*.trycloudflare.com` URL.

Exactly one tunnel runs at a time, guarded by an `asyncio.Lock`. State lives
only in the running process (no persistence) and is lost on dashboard
restart, which is the desired behavior: a share link should not silently
survive a dashboard bounce.

See CLAUDE.md's "Dashboard and Tools" section: the dashboard has no auth,
so sharing it exposes swap data and lets anyone trigger orchestrator runs.
This module only manages the tunnel subprocess; the caller (routes, added
in a later task) is responsible for any additional gating.
"""

import asyncio
import re
import shutil
from datetime import UTC, datetime

_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_START_TIMEOUT_SECONDS = 10
_STOP_TIMEOUT_SECONDS = 5
_STDERR_TAIL_LINES = 20


class TunnelUnavailable(Exception):
    """Raised when the `cloudflared` binary is not on PATH."""


class TunnelStartError(Exception):
    """Raised when the tunnel process exits before ever emitting a URL."""


class TunnelManager:
    """Owns the lifecycle of a single `cloudflared` quick-tunnel subprocess."""

    def __init__(self, target_url: str = "http://127.0.0.1:8787"):
        self._target_url = target_url
        self._lock = asyncio.Lock()
        self._proc = None
        self._reader_task = None
        self._url = None
        self._state = "idle"
        self._started_at = None
        self._last_error = None

    def is_available(self) -> bool:
        return shutil.which("cloudflared") is not None

    async def start(self) -> dict:
        if not self.is_available():
            raise TunnelUnavailable("cloudflared is not installed or not on PATH")

        async with self._lock:
            if self._state == "running" and self._proc is not None:
                return self.status()

            self._url = None
            self._last_error = None
            self._state = "connecting"
            self._started_at = datetime.now(UTC).isoformat()

            self._proc = await asyncio.create_subprocess_exec(
                "cloudflared",
                "tunnel",
                "--url",
                self._target_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            found_url = asyncio.Event()
            tail: list[str] = []

            async def read_stderr():
                while True:
                    line = await self._proc.stderr.readline()
                    if not line:
                        break
                    decoded = line.decode(errors="replace")
                    tail.append(decoded)
                    if len(tail) > _STDERR_TAIL_LINES:
                        tail.pop(0)
                    match = _URL_PATTERN.search(decoded)
                    if match and self._url is None:
                        self._url = match.group(0)
                        found_url.set()

            self._reader_task = asyncio.create_task(read_stderr())

            # Race a URL appearing against stderr closing (EOF, our proxy for the
            # process exiting) rather than `proc.wait()` directly: the latter can
            # resolve for reasons unrelated to stderr having produced its URL line
            # yet, so EOF-on-stderr is the more faithful "did it exit" signal here.
            wait_url = asyncio.create_task(found_url.wait())
            done, pending = await asyncio.wait(
                {wait_url, self._reader_task},
                timeout=_START_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if self._url is not None:
                self._state = "running"
                return self.status()

            if self._reader_task in done:
                await self._proc.wait()
                self._state = "stopped"
                self._last_error = (
                    self._last_error
                    or "".join(tail).strip()
                    or (
                        f"cloudflared exited with code {self._proc.returncode} "
                        "before emitting a tunnel URL"
                    )
                )
                if self._proc.returncode is None:
                    self._proc.kill()
                error = self._last_error
                self._proc = None
                raise TunnelStartError(error)

            # Timeout elapsed, process still alive, no URL yet: leave as "connecting".
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            if self._proc is None:
                self._state = "idle"
                return self.status()

            if self._reader_task is not None:
                self._reader_task.cancel()
                self._reader_task = None

            proc = self._proc
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_STOP_TIMEOUT_SECONDS)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()

            self._proc = None
            self._url = None
            self._state = "idle"
            return self.status()

    def status(self) -> dict:
        if self._proc is not None and self._proc.returncode is not None:
            self._state = "stopped"
            self._url = None
            if self._last_error is None:
                self._last_error = (
                    f"cloudflared exited unexpectedly with code {self._proc.returncode}"
                )
            self._proc = None

        return {
            "running": self._state == "running",
            "url": self._url,
            "state": self._state,
            "started_at": self._started_at,
            "pid": self._proc.pid if self._proc is not None else None,
            "last_error": self._last_error,
        }

    async def shutdown(self) -> None:
        if self._proc is not None or self._reader_task is not None:
            await self.stop()
