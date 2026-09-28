#!/usr/bin/env python3
"""sentiment_live.py — live HTML panel for the sentiment scanner loop.

A standalone stdlib-only (no third-party deps) HTTP server on port 8099
(127.0.0.1 only) that:

  - Launches sentiment-scanner/main.py in its natural loop mode (no --no-loop)
    as a child process, streaming its stdout/stderr to the browser via SSE.
  - Serves a single-page HTML dashboard at / with auto-scrolling log output.
  - POST /stop kills the child scanner process group cleanly.
  - Auto-opens in the browser (webbrowser.open); on WSL where explorer.exe
    may not reach the browser, prints the URL for manual navigation.

Child env strips inherited PYTHONPATH, PYTHONHOME, and VIRTUAL_ENV so the
shared root .venv's numpy loads correctly — never re-adds them.

Usage:
    python sentiment_live.py [TICKER]
    python sentiment_live.py NVDA
    SET SENTIMENT_TICKER=NVDA && python sentiment_live.py

Runs until the user closes the tab / hits Ctrl-C. When the child ends,
prints a summary of the strongest signals logged.
"""

from __future__ import annotations

import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = "127.0.0.1"
PORT = 8099

SCRIPT_DIR = Path(__file__).resolve().parent
MAIN_PY = SCRIPT_DIR / "main.py"
# Shared root .venv (all suites share it as of 2026-09-14).
SCANNER_VENV_PYTHON = SCRIPT_DIR.parent / ".venv" / "Scripts" / "python.exe"

# Max log lines to keep in memory for the SSE replay buffer.
MAX_BUFFER_LINES = 2000

# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------

# Patterns that indicate a scanner result line worth tracking as a "signal".
# Each tuple: (regex, description, weight for ranking).
_SIGNAL_PATTERNS = [
    # CNS alerts
    (re.compile(r"(CNS:\s*\d+)", re.I), "CNS", 1),
    # Composite signals
    (re.compile(r"\[(?:HIGH|MEDIUM|LOW)\]\s*(.+)", re.I), "COMPOSITE", 2),
    # Alert lines
    (re.compile(r"(🚨|ALERT)", re.I), "ALERT", 3),
    # Error lines
    (re.compile(r"ERROR", re.I), "ERROR", 0),
    # Scanner result lines (GEX, OI, IV, SKEW, PAIN, DISP)
    (re.compile(r"\b(?:GEX|OI|IV|SKEW|PAIN|DISP|EARN|YT)\b.*", re.I), "SCANNER", 1),
    # Deep dive sections
    (re.compile(r"DEEP DIVE", re.I), "DEEP_DIVE", 2),
]


class SignalTracker:
    """Collects and ranks signal lines from scanner output."""

    def __init__(self):
        self._lines: list[tuple[str, str, int]] = []  # (timestamp, line, weight)
        self._lock = threading.Lock()

    def feed(self, line: str) -> None:
        """Check a line against signal patterns; store if it matches."""
        for pattern, tag, weight in _SIGNAL_PATTERNS:
            if pattern.search(line):
                ts = datetime.now().strftime("%H:%M:%S")
                with self._lock:
                    self._lines.append((ts, tag, line.strip(), weight))
                return

    def top_signals(self, n: int = 10) -> list[tuple[str, str, str]]:
        """Return the top-N strongest signal lines as (ts, tag, line)."""
        with self._lock:
            ranked = sorted(self._lines, key=lambda x: x[3], reverse=True)
            return [(ts, tag, line) for ts, tag, line, _w in ranked[:n]]

    def summary(self) -> str:
        """Build a text summary of the strongest signals for console output."""
        top = self.top_signals(15)
        if not top:
            return "No significant signals were logged during this session."
        lines = ["\n" + "=" * 60,
                 "SESSION SUMMARY — Strongest Signals Logged",
                 "=" * 60]
        for ts, tag, line in top:
            lines.append(f"  [{ts}] {line}")
        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------

class ScannerProcess:
    """Manages the sentiment-scanner child process."""

    def __init__(self, ticker: Optional[str]):
        self.ticker = ticker
        self.proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self.lines: list[str] = []
        self.tracker = SignalTracker()
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        env = os.environ.copy()
        # Strip inherited Python env vars so the scanner's own .venv loads
        # its own numpy/site-packages — never re-add them.
        for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(key, None)

        # Determine the Python interpreter.
        if SCANNER_VENV_PYTHON.exists():
            python_exe = str(SCANNER_VENV_PYTHON)
        else:
            python_exe = sys.executable  # fallback (may not have deps)

        cmd = [python_exe, str(MAIN_PY),
               "--skip-sector-prompt", "--skip-report-prompt"]
        if self.ticker:
            cmd.extend(["--universe", self.ticker])

        # Use a process group so we can kill the whole tree (main.py spawns
        # subprocess children for Vol_Suite, sector rotation, etc.).
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            cwd=str(SCRIPT_DIR),
            encoding="utf-8",
            errors="replace",
            # Create a new process group on Windows (CREATE_NEW_PROCESS_GROUP)
            # or on POSIX (start_new_session=True).
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.rstrip("\n\r")
            with self._lock:
                self.lines.append(line)
                if len(self.lines) > MAX_BUFFER_LINES:
                    # Drop the oldest 25% to avoid unbounded growth.
                    del self.lines[: len(self.lines) // 4]
            self.tracker.feed(line)

    def stop(self) -> None:
        """Kill the child process group."""
        if self.proc is None:
            return
        self._stopped = True
        try:
            if os.name == "nt":
                # On Windows, send CTRL_BREAK_EVENT to the process group.
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        # Give it 3 seconds, then force-kill.
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)

    def is_running(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def wait(self) -> None:
        """Block until the child process exits."""
        if self.proc is not None:
            self.proc.wait()
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=5)

    def get_recent_lines(self, since: int = 0) -> list[str]:
        with self._lock:
            return list(self.lines[since:])

    def get_line_count(self) -> int:
        with self._lock:
            return len(self.lines)


# ---------------------------------------------------------------------------
# SSE client manager
# ---------------------------------------------------------------------------

class SSEClients:
    """Thread-safe registry of SSE client queues."""

    def __init__(self):
        self._clients: list[list[str]] = []
        self._lock = threading.Lock()

    def add(self) -> list[str]:
        q: list[str] = []
        with self._lock:
            self._clients.append(q)
        return q

    def remove(self, q: list[str]) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    def broadcast(self, data: str) -> None:
        with self._lock:
            for q in self._clients:
                q.append(data)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

_PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sentiment Scanner — Live</title>
<style>
  :root {
    --bg: #0d1117;
    --fg: #c9d1d9;
    --accent: #58a6ff;
    --green: #3fb950;
    --red: #f85149;
    --yellow: #d29922;
    --border: #30363d;
    --mono: 'SF Mono', 'Cascadia Code', 'Consolas', 'Liberation Mono', monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--fg);
    font-family: var(--mono);
    height: 100vh; display: flex; flex-direction: column;
  }
  #header {
    padding: 10px 16px;
    background: #161b22;
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px;
    flex-shrink: 0;
  }
  #header h1 { font-size: 14px; margin: 0; font-weight: 600; }
  #status {
    font-size: 12px; padding: 2px 8px; border-radius: 4px;
    background: var(--green); color: #000; font-weight: 600;
  }
  #status.stopped { background: var(--red); }
  #stop-btn {
    margin-left: auto;
    background: var(--red); color: #fff; border: none;
    padding: 6px 16px; border-radius: 4px; cursor: pointer;
    font-family: var(--mono); font-size: 12px; font-weight: 600;
  }
  #stop-btn:hover { opacity: 0.85; }
  #stop-btn:disabled { opacity: 0.4; cursor: default; }
  #log {
    flex: 1; overflow-y: auto; padding: 8px 12px;
    font-size: 13px; line-height: 1.5;
    white-space: pre-wrap; word-break: break-word;
  }
  .line { min-height: 1.5em; }
  .line-ALERT { color: var(--red); font-weight: 600; }
  .line-ERROR { color: var(--red); }
  .line-COMPOSITE { color: var(--accent); }
  .line-DEEP_DIVE { color: var(--yellow); font-weight: 600; }
  .line-SCANNER { color: var(--green); }
  #auto-scroll { padding: 4px 12px; font-size: 11px; color: #6e7681; flex-shrink: 0; }
  #auto-scroll label { cursor: pointer; }
</style>
</head>
<body>
<div id="header">
  <h1>📡 Sentiment Scanner</h1>
  <span id="status">RUNNING</span>
  <button id="stop-btn" onclick="stopScanner()">⏹ Stop</button>
</div>
<div id="log"></div>
<div id="auto-scroll">
  <label><input type="checkbox" id="scroll-toggle" checked> Auto-scroll</label>
</div>
<script>
  const logEl = document.getElementById('log');
  const statusEl = document.getElementById('status');
  const stopBtn = document.getElementById('stop-btn');
  const scrollToggle = document.getElementById('scroll-toggle');

  function classify(line) {
    if (/🚨|ALERT/i.test(line)) return 'ALERT';
    if (/ERROR/i.test(line)) return 'ERROR';
    if (/\[(?:HIGH|MEDIUM|LOW)\]/i.test(line)) return 'COMPOSITE';
    if (/DEEP DIVE/i.test(line)) return 'DEEP_DIVE';
    if (/\b(?:GEX|OI|IV|SKEW|PAIN|DISP|EARN|YT)\b/i.test(line)) return 'SCANNER';
    return '';
  }

  const evt = new EventSource('/stream');
  evt.onmessage = function(e) {
    const data = JSON.parse(e.data);
    if (data.lines) {
      for (const line of data.lines) {
        const div = document.createElement('div');
        div.className = 'line' + (classify(line) ? ' line-' + classify(line) : '');
        div.textContent = line;
        logEl.appendChild(div);
      }
      if (scrollToggle.checked) {
        logEl.scrollTop = logEl.scrollHeight;
      }
    }
    if (data.done) {
      statusEl.textContent = 'STOPPED';
      statusEl.className = 'stopped';
      stopBtn.disabled = true;
    }
  };
  evt.onerror = function() {
    statusEl.textContent = 'DISCONNECTED';
    statusEl.className = 'stopped';
  };

  function stopScanner() {
    stopBtn.disabled = true;
    stopBtn.textContent = 'Stopping…';
    fetch('/stop', {method: 'POST'}).then(function() {
      // The SSE stream will deliver done=true when the child exits.
    }).catch(function() {
      stopBtn.disabled = false;
      stopBtn.textContent = '⏹ Stop';
    });
  }
</script>
</body>
</html>"""


class LiveHandler(BaseHTTPRequestHandler):
    scanner: Optional[ScannerProcess] = None
    sse_clients: Optional[SSEClients] = None

    def log_message(self, fmt, *args):
        pass  # suppress default access logging

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_html()
        elif self.path == "/stream":
            self._serve_sse()
        elif self.path == "/status":
            self._serve_status()
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/stop":
            self._handle_stop()
        else:
            self.send_error(404)

    def _serve_html(self):
        body = _PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_status(self):
        data = {
            "running": self.scanner.is_running() if self.scanner else False,
            "line_count": self.scanner.get_line_count() if self.scanner else 0,
        }
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Register as an SSE client.
        queue = self.sse_clients.add()  # type: ignore[union-attr]

        # Send any buffered lines first (replay).
        if self.scanner:
            existing = self.scanner.get_recent_lines()
            if existing:
                self._send_sse_batch(existing)

        # Now poll for new lines and stream them.
        last_index = self.scanner.get_line_count() if self.scanner else 0
        try:
            while True:
                if self.scanner and not self.scanner.is_running():
                    # Send any final lines, then signal done.
                    final = self.scanner.get_recent_lines(since=last_index)
                    if final:
                        self._send_sse_batch(final)
                    self._send_sse_done()
                    break
                new_lines = self.scanner.get_recent_lines(since=last_index) if self.scanner else []
                if new_lines:
                    self._send_sse_batch(new_lines)
                    last_index = self.scanner.get_line_count()
                # Also drain the broadcast queue (for stop notifications).
                while queue:
                    msg = queue.pop(0)
                    self.wfile.write(f"data: {msg}\n\n".encode())
                    self.wfile.flush()
                time.sleep(0.3)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.sse_clients.remove(queue)  # type: ignore[union-attr]

    def _send_sse_batch(self, lines: list[str]) -> None:
        data = json.dumps({"lines": lines})
        self.wfile.write(f"data: {data}\n\n".encode())
        self.wfile.flush()

    def _send_sse_done(self) -> None:
        data = json.dumps({"done": True})
        self.wfile.write(f"data: {data}\n\n".encode())
        self.wfile.flush()

    def _handle_stop(self):
        if self.scanner and self.scanner.is_running():
            self.scanner.stop()
            body = json.dumps({"ok": True, "message": "Scanner stopped."}).encode()
        else:
            body = json.dumps({"ok": False, "message": "Scanner not running."}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _open_browser(url: str) -> None:
    """Try to open the browser; handle WSL gracefully."""
    try:
        webbrowser.open(url)
    except Exception:
        pass
    # On WSL, webbrowser.open may not reach the Windows browser.
    # Try explorer.exe as a fallback.
    if sys.platform.startswith("linux"):
        try:
            subprocess.Popen(["explorer.exe", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (FileNotFoundError, OSError):
            print(f"\n  Open this URL in your browser: {url}")


def main() -> None:
    # Determine the ticker to scan.
    ticker = os.environ.get("SENTIMENT_TICKER", "").strip()
    if not ticker and len(sys.argv) > 1:
        ticker = sys.argv[1].strip()
    # Empty ticker = trending scan (main.py's default behavior).

    # Wire up the shared state.
    LiveHandler.scanner = ScannerProcess(ticker or None)
    LiveHandler.sse_clients = SSEClients()

    # Start the child scanner process.
    print(f"Starting sentiment scanner{' for ' + ticker if ticker else ' (trending)'}...")
    LiveHandler.scanner.start()

    # Start the HTTP server.
    server = ThreadingHTTPServer((HOST, PORT), LiveHandler)
    url = f"http://{HOST}:{PORT}/"
    print(f"\n  Sentiment Live Panel: {url}")
    print(f"  SSE stream:           {url}stream")
    print(f"  POST /stop to end the scanner.\n")

    # Auto-open the browser.
    _open_browser(url)

    # Serve until the child exits or Ctrl-C.
    try:
        # Run the server in a thread so we can also watch the child process.
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        # Give the server a moment to bind before we start health-checking.
        time.sleep(0.5)

        # Wait for the child scanner to exit.
        while LiveHandler.scanner.is_running():
            time.sleep(1)

        print("\n  Scanner process ended.")
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        LiveHandler.scanner.stop()
    finally:
        # Wait for the child to fully exit and collect its output.
        LiveHandler.scanner.wait()

        # Print the signal summary.
        print(LiveHandler.scanner.tracker.summary())

        # Shut down the HTTP server.
        server.shutdown()
        server.server_close()
        print("\n  Panel closed.")


if __name__ == "__main__":
    main()
