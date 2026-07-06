"""Tests for .claude/mcp-servers/gemini-consultant/server.py.

The `mcp` package is stubbed out so the tests run without it and the
tool functions stay plain callables.
"""

import importlib.util
import json
import subprocess
import sys
import time
import types
import urllib.request
from urllib.error import URLError

import pytest
from conftest import REPO_ROOT, fake_gemini

SERVER = REPO_ROOT / ".claude/mcp-servers/gemini-consultant/server.py"


@pytest.fixture
def server(monkeypatch, tmp_path):
    class FakeFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(fn):
                return fn

            return decorator

        def run(self):
            raise AssertionError("mcp.run() must not be called in tests")

    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeFastMCP
    monkeypatch.setitem(sys.modules, "mcp", types.ModuleType("mcp"))
    monkeypatch.setitem(sys.modules, "mcp.server", types.ModuleType("mcp.server"))
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", fastmcp_mod)

    # Keep the module's import-time side effects inside tmp_path.
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda p: p.replace("~", str(tmp_path), 1) if p.startswith("~") else p,
    )
    monkeypatch.setattr("platform.system", lambda: "TestOS")
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key")
    monkeypatch.setenv("GEMINI_PRO_MODEL", "pro-test-model")
    monkeypatch.setenv("GEMINI_FLASH_MODEL", "flash-test-model")

    spec = importlib.util.spec_from_file_location("gemini_consultant_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCallGemini:
    def test_missing_api_key_raises(self, server, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY")
        with pytest.raises(ValueError, match="GEMINI_API_KEY not set"):
            server.call_gemini("question")

    def test_concatenates_all_response_parts(self, server, monkeypatch):
        body = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "foo"}, {"text": "bar"}]}}]}
        ).encode("utf-8")

        class Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp())
        assert server.call_gemini("question") == "foobar"

    def test_retries_with_exponential_backoff(self, server, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            fake_gemini(URLError("down"), URLError("down"), "recovered"),
        )
        assert server.call_gemini("question") == "recovered"
        assert sleeps == [1, 2]

    def test_raises_after_exhausting_retries(self, server, monkeypatch):
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("down")))
        with pytest.raises(URLError):
            server.call_gemini("question")
        assert sleeps == [1, 2, 4]

    def test_uses_requested_model_in_url(self, server, monkeypatch):
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        server.call_gemini("question", model="some-model")
        assert "some-model" in calls[0].full_url


class TestTools:
    def test_consult_gemini_uses_deep_model_and_logs(self, server, monkeypatch):
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini("deep answer", calls=calls)
        )
        result = server.consult_gemini("how should I design this?")
        assert result == "deep answer"
        assert "pro-test-model" in calls[0].full_url
        log_text = open(server.log_file, encoding="utf-8").read()
        assert "[PROMPT]" in log_text
        assert "deep answer" in log_text

    def test_review_gemini_uses_light_model(self, server, monkeypatch):
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini("looks fine", calls=calls)
        )
        assert server.review_gemini("check this plan") == "looks fine"
        assert "flash-test-model" in calls[0].full_url

    def test_consult_gemini_reports_missing_key_as_string(self, server, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY")
        result = server.consult_gemini("anything")
        assert result.startswith("Gemini API error:")

    def test_consult_gemini_reports_api_error_as_string(self, server, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("boom")))
        result = server.consult_gemini("anything")
        assert result.startswith("Gemini API error:")


class TestLogRotation:
    def test_append_log_trims_to_max_lines(self, server, tmp_path):
        log = tmp_path / "rotated.log"
        log.write_text("old\n" * 520, encoding="utf-8")
        server.log_file = str(log)
        server._append_log(["new line\n"])
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == server.MAX_LOG_LINES
        assert lines[-1] == "new line"


class TestNotifyInjection:
    """Caller-controlled title/message must not alter the osascript command."""

    def test_notify_uses_env_indirection_not_interpolation(self, server, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr(subprocess, "run", fake_run)

        malicious_title = 'Title" & do shell script "touch /tmp/pwned'
        malicious_message = 'Msg"\\ $(whoami)'
        server.notify(malicious_title, malicious_message)

        assert calls, "osascript should have been invoked on Darwin"
        cmd, kwargs = calls[0]
        assert cmd[0] == "/usr/bin/osascript"
        script = cmd[cmd.index("-e") + 1]
        # The AppleScript body is a static template; the payload only travels
        # via the environment, never spliced into the script text.
        assert "touch /tmp/pwned" not in script
        assert "whoami" not in script
        assert "printenv CLAUDE_NOTIFY_TITLE" in script
        assert kwargs["env"]["CLAUDE_NOTIFY_TITLE"] == malicious_title
        assert kwargs["env"]["CLAUDE_NOTIFY_MESSAGE"] == malicious_message
