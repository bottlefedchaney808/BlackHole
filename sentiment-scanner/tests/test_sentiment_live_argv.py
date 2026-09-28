from pathlib import Path
import importlib.util


def _load():
    p = Path(__file__).resolve().parent.parent / "sentiment_live.py"
    spec = importlib.util.spec_from_file_location("sentiment_live_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_popen(captured):
    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd

        class P:
            stdout = iter(())

            def poll(self):
                return 0

        return P()

    return fake_popen


def _stub_thread(*a, **k):
    return type("T", (), {"start": lambda self: None, "join": lambda self, timeout=None: None})()


def test_cmd_without_ticker_has_no_positional(monkeypatch):
    live = _load()
    captured = {}

    monkeypatch.setattr(live.subprocess, "Popen", _fake_popen(captured))
    monkeypatch.setattr(live.threading, "Thread", _stub_thread)
    proc = live.ScannerProcess(None)
    proc.start()
    cmd = captured["cmd"]
    assert "--universe" not in cmd
    assert cmd[-1] in ("--skip-report-prompt", "--skip-sector-prompt")
    assert not cmd[-1].isalpha() or cmd[-1].startswith("--")
    last_skip = max(cmd.index("--skip-sector-prompt"), cmd.index("--skip-report-prompt"))
    assert all(tok.startswith("--") for tok in cmd[last_skip + 1:])


def test_cmd_with_ticker_uses_universe_flag(monkeypatch):
    live = _load()
    captured = {}

    monkeypatch.setattr(live.subprocess, "Popen", _fake_popen(captured))
    monkeypatch.setattr(live.threading, "Thread", _stub_thread)
    proc = live.ScannerProcess("NVDA")
    proc.start()
    cmd = captured["cmd"]
    assert "--universe" in cmd
    assert cmd[cmd.index("--universe") + 1] == "NVDA"
    assert "NVDA" not in cmd[: cmd.index("--universe")]
