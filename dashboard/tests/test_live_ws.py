"""test_live_ws.py

Covers Task 13 of docs/superpowers/plans/2026-08-01-quant-console.md:
`WS /suites/{suite}/live`, the log-tail websocket route for a suite's
continuous (loop-mode) process -- concretely, sentiment-scanner run without
`--no-loop`, per the design spec's Phase 3 section and this repo's own
CLAUDE.md.

Design note (see dashboard/app.py's LIVE_LOG_DIR docstring for the full
version): the brief for this task says to reuse "the same way job logs
already are redirected to a file elsewhere in this codebase." That file-
based convention does not actually exist anywhere in this codebase today --
orchestrator.run_suite/run_unified and the dispatch worker launch path
(job_object.run_with_job_object, Task 10) both capture subprocess stdout
in-memory via subprocess.PIPE + .communicate(), never to a file (confirmed
by search before writing this route). This task therefore defines the
smallest new convention consistent with its own two-file scope (no new
launcher module): one rolling log file per suite name under
dashboard.app.LIVE_LOG_DIR. These tests exercise only the tailing side of
that convention -- they never launch a real continuous process; that
launcher is out of Task 13's scope.
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


@pytest.fixture(autouse=True)
def _isolate_live_state(tmp_path, monkeypatch):
    """Point LIVE_LOG_DIR at a disposable tmp_path directory and give each
    test a clean _LIVE_WRITERS slate -- mirrors test_dispatch.py's
    _isolate_dispatch_state fixture for the analogous in-memory registry.
    """
    monkeypatch.setattr(dashboard_app, 'LIVE_LOG_DIR', str(tmp_path))
    saved_writers = dict(dashboard_app._LIVE_WRITERS)
    dashboard_app._LIVE_WRITERS.clear()
    yield
    dashboard_app._LIVE_WRITERS.clear()
    dashboard_app._LIVE_WRITERS.update(saved_writers)


def _log_path(tmp_path, suite='sentiment'):
    return tmp_path / f'{suite}.log'


def test_receives_lines_appended_after_connecting(tmp_path):
    log_path = _log_path(tmp_path)
    log_path.write_text('', encoding='utf-8')

    with client.websocket_connect('/suites/sentiment/live') as ws:
        # Give the server's tail loop a moment to open the file and seek to
        # its end before we append -- otherwise the append could race the
        # server's initial seek(0, SEEK_END) and get skipped.
        time.sleep(0.3)

        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('scan started for AAPL\n')
            f.flush()
        assert ws.receive_text() == 'scan started for AAPL'

        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('found 3 contested narratives\n')
            f.flush()
        assert ws.receive_text() == 'found 3 contested narratives'


def test_receives_lines_once_file_is_created(tmp_path):
    """The route must not require the log file to exist at connect time --
    a client can connect slightly before the writer creates it."""
    with client.websocket_connect('/suites/sentiment/live') as ws:
        time.sleep(0.3)
        log_path = _log_path(tmp_path)
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('server just started\n')
            f.flush()
        assert ws.receive_text() == 'server just started'


def test_clean_disconnect_on_client_close(tmp_path):
    log_path = _log_path(tmp_path)
    log_path.write_text('', encoding='utf-8')

    # Exiting the `with` block below sends a close frame. If the server's
    # route didn't handle the disconnect cleanly (e.g. hung in its polling
    # loop, or raised past the disconnect), this test would hang or error.
    with client.websocket_connect('/suites/sentiment/live') as ws:
        time.sleep(0.2)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('one line before closing\n')
            f.flush()
        assert ws.receive_text() == 'one line before closing'


def test_clean_disconnect_when_writer_process_exited(tmp_path):
    log_path = _log_path(tmp_path)
    log_path.write_text('last line before the process died\n', encoding='utf-8')

    dead_proc = MagicMock()
    dead_proc.poll.return_value = 1  # non-None == already exited
    dashboard_app._LIVE_WRITERS['sentiment'] = dead_proc

    # The server notices the writer is gone and closes on its own, fast
    # enough (nothing to drain) that TestClient's own connect/disconnect
    # bookkeeping can surface the resulting exception at connect time
    # rather than at this first receive_text() call -- same pattern as
    # test_unknown_suite_closes_immediately below, so wrap the whole
    # connection, not just the interior receive.
    with pytest.raises(Exception):
        with client.websocket_connect('/suites/sentiment/live') as ws:
            ws.receive_text()


def test_drains_remaining_lines_before_closing_on_writer_exit(tmp_path):
    """A writer can write its last line and exit before this route's next
    poll tick -- that line must still reach the client, not be dropped."""
    log_path = _log_path(tmp_path)
    log_path.write_text('', encoding='utf-8')

    # As above (test_clean_disconnect_when_writer_process_exited), the
    # server-initiated close here can surface as an exception either from
    # the second receive_text() below or from this whole block's own
    # __exit__ (TestClient's teardown racing the server's own close) --
    # wrap the connection, not just the interior call.
    with pytest.raises(Exception):
        with client.websocket_connect('/suites/sentiment/live') as ws:
            time.sleep(0.2)
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write('final line written just before exit\n')
                f.flush()

            dead_proc = MagicMock()
            dead_proc.poll.return_value = 0
            dashboard_app._LIVE_WRITERS['sentiment'] = dead_proc

            assert ws.receive_text() == 'final line written just before exit'
            ws.receive_text()


def test_unknown_suite_closes_immediately(tmp_path):
    with pytest.raises(Exception):
        with client.websocket_connect('/suites/not-a-real-suite/live') as ws:
            ws.receive_text()
