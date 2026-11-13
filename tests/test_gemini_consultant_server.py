"""Tests for .claude/mcp-servers/gemini-consultant/server.py.

The `mcp` package is stubbed out so the tests run without it and the
tool functions stay plain callables (the stub decorator returns the function
unchanged, so the tools keep their real sync/async nature).
"""

import asyncio
import http.client
import importlib.util
import inspect
import io
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from urllib.error import URLError

import pytest
from conftest import REPO_ROOT, fake_gemini

SERVER = REPO_ROOT / ".claude/mcp-servers/gemini-consultant/server.py"

# Captured at import: several tests patch time.sleep, while the slow-call
# stand-ins below need a REAL blocking sleep to model a stuck HTTP call.
_REAL_SLEEP = time.sleep


async def drive_tool(server, name, question):
    """Call an MCP tool the way FastMCP does: await it if it is a coroutine.

    FastMCP awaits an async tool and calls a sync one directly. Mirroring that
    keeps the pre-existing tests valid for either shape; the dedicated tests
    below are what pin the tools to `async def`.
    """
    result = getattr(server, name)(question)
    if inspect.isawaitable(result):
        result = await result
    return result


def call_tool(server, name, question):
    """Run an MCP tool to completion from a synchronous test."""
    return asyncio.run(drive_tool(server, name, question))


def call_tool_with_ticker(server, name, question):
    """Run a tool while a ticker coroutine records event-loop wake-ups.

    Returns (result, ticks_during_the_call, elapsed_seconds). A tool that
    blocks the loop leaves the ticker starved: it cannot run until the call
    returns, so ~0 ticks land inside the call window.
    """

    async def _run():
        ticks = []

        async def ticker():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.01)

        task = asyncio.ensure_future(ticker())
        await asyncio.sleep(0)  # let the ticker take its first tick
        started = time.monotonic()
        result = await drive_tool(server, name, question)
        finished = time.monotonic()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        during = [t for t in ticks if started < t < finished]
        return result, len(during), finished - started

    return asyncio.run(_run())


@pytest.fixture
def load_server(monkeypatch, tmp_path):
    """Factory loading server.py (or a copy reached by another path) as a module."""

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

    def _load(path=SERVER):
        spec = importlib.util.spec_from_file_location("gemini_consultant_server", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture
def server(load_server):
    return load_server()


class TestCallGemini:
    def test_missing_api_key_raises(self, server, monkeypatch):
        # GeminiKeyError, deliberately NOT a ValueError subclass: key problems,
        # json.JSONDecodeError and putheader's value-carrying ValueError must
        # not share one `except ValueError` arm, which would make the
        # JSONDecodeError entry below it dead and leak the key. The distinct
        # type is what lets the three be told apart, so pin the type.
        monkeypatch.delenv("GEMINI_API_KEY")
        with pytest.raises(server.GeminiKeyError, match="GEMINI_API_KEY not set"):
            server.call_gemini("question")
        assert not issubclass(server.GeminiKeyError, ValueError)

    def test_blank_api_key_is_treated_as_missing(self, server, monkeypatch):
        # strip() makes a whitespace-only key indistinguishable from unset,
        # which is the honest reading of a `GEMINI_API_KEY=` line in a .env.
        monkeypatch.setenv("GEMINI_API_KEY", "   \r\n")
        with pytest.raises(server.GeminiKeyError, match="GEMINI_API_KEY not set"):
            server.call_gemini("question")

    def test_json_decode_error_is_not_reported_as_a_key_problem(
        self, server, monkeypatch
    ):
        # A 200 with a non-JSON body (proxy / captive portal) must not be
        # announced as an API-key problem ("APIキー...").
        class Resp:
            def read(self):
                return b"<html>captive portal</html>"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp())
        notices = []
        monkeypatch.setattr(server, "notify", lambda t, m, s=5: notices.append(m))
        result = call_tool(server, "consult_gemini", "anything")
        assert result.startswith("Gemini API error:")
        assert not any("APIキー" in m for m in notices), (
            f"a JSON parse failure was announced as a key problem: {notices}"
        )

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
        # No sleep after the FINAL failed attempt: waiting 4s before giving
        # up delays the error report without ever retrying again.
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("down")))
        with pytest.raises(URLError):
            server.call_gemini("question")
        assert sleeps == [1, 2]

    def test_uses_requested_model_in_url(self, server, monkeypatch):
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        server.call_gemini("question", model="some-model")
        assert "some-model" in calls[0].full_url


class TestDefaults:
    def test_default_models_are_the_gemini_4_ids(self, load_server, monkeypatch):
        # consult_gemini stands in for the advisor (CLAUDE.md), so its default is
        # the top Gemini 4 model. The *-latest aliases are not documented to move
        # to Gemini 4, so the defaults name concrete ids.
        monkeypatch.delenv("GEMINI_PRO_MODEL")
        monkeypatch.delenv("GEMINI_FLASH_MODEL")
        server = load_server()
        assert server.DEEP_MODEL == "gemini-4-carbon"
        assert server.LIGHT_MODEL == "gemini-4-hydrogen"

    def test_each_request_waits_up_to_180_seconds(self, server, monkeypatch):
        # A long Carbon answer takes longer than 90 s (41 s for 2.6K output
        # tokens was measured), so one attempt may wait 180 s.
        timeouts = []
        inner = fake_gemini("ok")

        def _urlopen(req, timeout=None):
            timeouts.append(timeout)
            return inner(req, timeout)

        monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
        assert server.call_gemini("question", model="some-model") == "ok"
        assert timeouts == [180]


class TestTools:
    def test_consult_gemini_uses_deep_model_and_logs(self, server, monkeypatch):
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini("deep answer", calls=calls)
        )
        result = call_tool(server, "consult_gemini", "how should I design this?")
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
        assert call_tool(server, "review_gemini", "check this plan") == "looks fine"
        assert "flash-test-model" in calls[0].full_url

    def test_consult_gemini_reports_missing_key_as_string(self, server, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY")
        result = call_tool(server, "consult_gemini", "anything")
        assert result.startswith("Gemini API error:")

    def test_consult_gemini_reports_api_error_as_string(self, server, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("boom")))
        result = call_tool(server, "consult_gemini", "anything")
        assert result.startswith("Gemini API error:")

    def test_review_gemini_reports_missing_key_as_string(self, server, monkeypatch):
        # GeminiKeyError branch of review_gemini (mirror of consult_gemini's).
        monkeypatch.delenv("GEMINI_API_KEY")
        result = call_tool(server, "review_gemini", "anything")
        assert result.startswith("Gemini API error:")

    def test_review_gemini_reports_api_error_as_string(self, server, monkeypatch):
        # API-exception branch of review_gemini (mirror of consult_gemini's).
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("boom")))
        result = call_tool(server, "review_gemini", "anything")
        assert result.startswith("Gemini API error:")


SENTINEL_KEY = "SENTINELKEYVALUEMUSTNEVERAPPEAR"


class TestApiKeyNeverLeaks:
    """The credential must not reach the caller or the log, on any path.

    `http.client.putheader` refuses a header value containing CR/LF and raises
    `ValueError("Invalid header value %r" % value)` -- with the RAW value in the
    message. `call_gemini`'s inner handler does not catch ValueError, so one
    reaches the tools' `except ValueError` clause; an arm that formatted
    `str(e)` there would put it in BOTH the returned string and
    `_log_quietly`: the key would sit in the conversation transcript and on
    disk in ~/.claude/logs. Two layers block that independently:
    `_reject_unusable_api_key` refuses the value up front, and the last-resort
    arm reports only the exception type and withholds the message.

    `call_gemini` strips the key via `.strip()` before anything else, and only
    THEN hands it to `_reject_unusable_api_key`, which rejects embedded CR/LF
    and non-latin-1 characters without ever revealing the value. That ordering
    splits "illegal" into two shapes that behave nothing alike:

    - A CR/LF INTERIOR to the value survives strip() and reaches the guard,
      which raises before a `Request` is ever built -- no socket is involved,
      which is why those cases run under `no_network` rather than a mocked
      `urlopen`.
    - A CR/LF at the EDGE of the value is removed BY strip() before the guard
      ever sees it. The result is a legitimate key and the call is expected to
      SUCCEED -- so those cases are tested as tolerated, with `urlopen` mocked
      the same way every passing-case test elsewhere in this file mocks it.
      Treating an edge case as though it must also be "rejected" would assert
      against correct behavior; treating it as "offline because no socket is
      involved" would silently let it dial the real API instead.
    """

    # Shapes strip() CANNOT remove. Split at 8 so a fragment of the sentinel
    # survives on either side of the break for the leak assertion below.
    _INTERIOR = [
        ("interior newline", SENTINEL_KEY[:8] + "\n" + SENTINEL_KEY[8:]),
        ("interior CR", SENTINEL_KEY[:8] + "\r" + SENTINEL_KEY[8:]),
        ("interior CRLF", SENTINEL_KEY[:8] + "\r\n" + SENTINEL_KEY[8:]),
    ]

    # Shapes strip() DOES remove. The realistic trigger: a `.env` written with
    # CRLF endings leaves a trailing "\r" on the value.
    _TRAILING = [
        ("trailing CR (CRLF .env)", SENTINEL_KEY + "\r"),
        ("trailing LF", SENTINEL_KEY + "\n"),
    ]

    @pytest.mark.parametrize(("label", "key"), _INTERIOR)
    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_interior_newline_key_is_rejected_and_never_reaches_the_network(
        self, server, monkeypatch, no_network, label, key, tool
    ):
        monkeypatch.setenv("GEMINI_API_KEY", key)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        result = call_tool(server, tool, f"prompt for {label}")
        log_text = open(server.log_file, encoding="utf-8").read()

        # Naming the guard's OWN wording -- not just "no leak" -- is what tells
        # "the guard rejected this" apart from "something else happened to not
        # leak it". A leak-only assertion would stay green even with
        # `_reject_unusable_api_key` deleted: the unguarded path (raw value ->
        # putheader -> ValueError) is caught by the tools' last-resort
        # `except ValueError` arm, which drops the message too, so it would
        # ALSO produce a leak-free string -- just not this one.
        assert result.startswith("Gemini API error:"), result
        assert "GEMINI_API_KEY" in result, (
            f"{tool} did not name the variable ({label}): {result!r}"
        )
        assert "illegal in an HTTP header" in result, (
            f"{tool} did not report the guard's own reason ({label}): {result!r}"
        )
        assert "illegal in an HTTP header" in log_text, (
            f"the guard's reason did not reach the log ({label}): {log_text!r}"
        )

        # Check FRAGMENTS, not the whole key: an interior newline splits the
        # value, so asserting on the contiguous sentinel would pass vacuously
        # for exactly the case that is hardest to fix.
        for sink, text in (("returned to the caller", result), ("the log", log_text)):
            for fragment in (SENTINEL_KEY[:8], SENTINEL_KEY[-8:]):
                assert fragment not in text, (
                    f"{tool} leaked the API key into {sink} ({label}): {text!r}"
                )

        assert no_network == [], (
            f"{tool} reached the network with an unusable key ({label}): {no_network}"
        )

    @pytest.mark.parametrize(("label", "key"), _TRAILING)
    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_trailing_newline_key_is_stripped_and_still_authenticates(
        self, server, monkeypatch, label, key, tool
    ):
        # strip() is what removes the CRLF-.env trigger in the first place, so
        # this shape must keep WORKING, not merely fail to leak -- trading a
        # leak for an outage would be no fix at all. Asserting the header
        # carries the bare sentinel (no trailing CR/LF) is the
        # security-relevant check here: it proves the newline was gone BEFORE
        # the header was ever built, rather than "happened not to matter".
        calls = []
        monkeypatch.setenv("GEMINI_API_KEY", key)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))

        result = call_tool(server, tool, f"prompt for {label}")

        assert result == "ok", f"{tool} treated a tolerable key as an error ({label})"
        assert calls[0].get_header("X-goog-api-key") == SENTINEL_KEY, (
            f"the stripped key did not reach the request header ({label})"
        )

    def test_non_latin1_key_is_rejected_without_quoting_it(self, server, monkeypatch):
        # putheader encodes header values as latin-1, so a key carrying (say) a
        # full-width character raises UnicodeEncodeError -- whose message names
        # the offending character. Reject it here instead, and do not chain the
        # original (`from None`) so nothing derived from the value escapes.
        monkeypatch.setenv("GEMINI_API_KEY", "キー" + SENTINEL_KEY)
        with pytest.raises(server.GeminiKeyError) as excinfo:
            server.call_gemini("question")
        assert SENTINEL_KEY not in str(excinfo.value)
        assert excinfo.value.__cause__ is None, (
            "chaining re-exposes the value through the original exception"
        )

    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_unexpected_value_error_is_reported_without_its_message(
        self, server, monkeypatch, tool
    ):
        # Backstop for anything that still reaches putheader-shaped territory:
        # the last-resort ValueError arm must name the TYPE and drop the text.
        def boom(prompt, **kwargs):
            raise ValueError(f"Invalid header value b'{SENTINEL_KEY}'")

        monkeypatch.setattr(server, "call_gemini", boom)
        result = call_tool(server, tool, "anything")
        assert SENTINEL_KEY not in result, result
        assert "ValueError" in result
        log_text = open(server.log_file, encoding="utf-8").read()
        assert SENTINEL_KEY not in log_text

    def test_surrounding_whitespace_in_the_key_is_tolerated(self, server, monkeypatch):
        # Stripping is what removes the CRLF-.env trigger entirely, so pin that
        # a padded key still authenticates rather than merely failing quietly.
        calls = []
        monkeypatch.setenv("GEMINI_API_KEY", "  padded-key\r\n")
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        assert server.call_gemini("hi") == "ok"
        assert calls[0].get_header("X-goog-api-key") == "padded-key"


class TestTruncatedResponses:
    """A response cut short by the token budget must not read as a finished one.

    `call_gemini` sends a maxOutputTokens budget, so joining `parts` without
    looking at `finishReason` would return a MAX_TOKENS truncation as if it
    were the whole answer. For a design-consultation tool that is the worst
    shape of wrong: the caller acts on half an argument believing it complete.
    """

    def _resp(self, monkeypatch, body):
        class Resp:
            def read(self):
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: Resp())

    def test_max_tokens_truncation_is_flagged(self, server, monkeypatch):
        self._resp(
            monkeypatch,
            {
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {"parts": [{"text": "Here are the steps: 1) sta"}]},
                    }
                ]
            },
        )
        result = call_tool(server, "consult_gemini", "anything")
        assert "Here are the steps: 1) sta" in result, (
            "the partial text is still useful"
        )
        assert "MAX_TOKENS" in result or "truncat" in result.lower(), (
            f"truncation was not disclosed to the caller: {result!r}"
        )

    def test_safety_block_is_not_silent_empty(self, server, monkeypatch):
        self._resp(
            monkeypatch,
            {"candidates": [{"finishReason": "SAFETY", "content": {}}]},
        )
        result = call_tool(server, "consult_gemini", "anything")
        assert result.strip(), "a SAFETY block returned an empty string"
        assert "SAFETY" in result

    def test_empty_candidates_list_does_not_index_error(self, server, monkeypatch):
        # `.get("candidates", [{}])` does not defend a key that is PRESENT and
        # empty: [0] would raise IndexError and surface as an opaque
        # "list index out of range".
        self._resp(
            monkeypatch,
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
        )
        result = call_tool(server, "consult_gemini", "anything")
        assert "list index out of range" not in result, (
            f"raw IndexError leaked to the caller: {result!r}"
        )
        assert result.strip()

    def test_no_candidates_and_no_block_reason_still_says_something(
        self, server, monkeypatch
    ):
        # The shape with neither candidates nor promptFeedback: still must not
        # come back as an empty string the caller reads as "Gemini had nothing
        # to say".
        self._resp(monkeypatch, {})
        result = call_tool(server, "consult_gemini", "anything")
        assert result.strip()

    def test_stop_with_no_text_is_not_a_silent_empty_string(self, server, monkeypatch):
        # A "successful" finish that carries no text is the same failure shape
        # as the SAFETY branch: the caller gets "" and reads it as "Gemini had
        # nothing to say" rather than "something went wrong". finishReason alone
        # cannot be the test -- the emptiness has to be.
        self._resp(
            monkeypatch,
            {"candidates": [{"finishReason": "STOP", "content": {}}]},
        )
        result = call_tool(server, "consult_gemini", "anything")
        assert result.strip(), "a STOP with no parts returned an empty string"

    def test_normal_stop_is_returned_verbatim(self, server, monkeypatch):
        # The disclosure must not fire on the happy path: a STOP finish is a
        # complete answer and gets no annotation.
        self._resp(
            monkeypatch,
            {
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": "done"}]}}
                ]
            },
        )
        assert call_tool(server, "consult_gemini", "anything") == "done"


class TestLogRotation:
    def test_append_log_trims_to_max_lines(self, server, tmp_path):
        log = tmp_path / "rotated.log"
        log.write_text("old\n" * 520, encoding="utf-8")
        server.log_file = str(log)
        server._append_log(["new line\n"])
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == server.MAX_LOG_LINES
        assert lines[-1] == "new line"

    def test_a_failed_rotation_leaves_the_log_intact(
        self, server, tmp_path, monkeypatch
    ):
        # Rotation must write the trimmed content to a temp file and swap it
        # in with os.replace, matching the atomic pattern in
        # _bash_review_common.py's append_and_rotate, rather than truncating in
        # place via open(log_file, "w"): a crash mid-write would leave the log
        # empty for good, and concurrent writers (Claude + Codex sessions)
        # could race on the same truncate. So a failure there must leave the
        # previous log content untouched.
        log = tmp_path / "rotated.log"
        before = "old\n" * 520
        log.write_text(before, encoding="utf-8")
        server.log_file = str(log)

        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(server.os, "replace", boom)
        with pytest.raises(OSError, match="disk full"):
            server._append_log(["new line\n"])

        assert log.read_text(encoding="utf-8") == before + "new line\n"

    def test_rotation_leaves_no_temp_files_behind(self, server, tmp_path):
        # Own subdirectory so the module's own ~/.claude/logs bootstrap
        # (created at import time by the `server` fixture) isn't mistaken
        # for a leftover.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        log = log_dir / "rotated.log"
        log.write_text("old\n" * 520, encoding="utf-8")
        server.log_file = str(log)
        server._append_log(["new line\n"])
        leftovers = sorted(p.name for p in log_dir.iterdir() if p.name != "rotated.log")
        assert leftovers == [], f"rotation left temp files behind: {leftovers}"


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


class TestLoggingNeverCostsTheResponse:
    """A response is already paid for by the time it is written to the log."""

    @staticmethod
    def _fill_log_past_rotation(server):
        os.makedirs(os.path.dirname(server.log_file), exist_ok=True)
        with open(server.log_file, "w", encoding="utf-8") as f:
            f.writelines("x\n" for _ in range(server.MAX_LOG_LINES + 5))

    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_rotation_failure_still_returns_the_response(
        self, server, monkeypatch, tool
    ):
        # _append_log re-raises OSError after cleaning up its temp file, and
        # neither except clause catches a bare OSError -- URLError is a SUBCLASS
        # of OSError, not its parent -- so an unguarded failed rotation would
        # propagate straight out of the tool and discard a successful answer.
        def boom(*_a, **_k):
            raise OSError("no space left on device")

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))
        self._fill_log_past_rotation(server)
        monkeypatch.setattr(os, "replace", boom)
        assert call_tool(server, tool, "question") == "the answer"

    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_rotation_failure_on_the_error_path_still_returns_the_message(
        self, server, monkeypatch, tool
    ):
        # The error path logs too, so the same OSError could replace a tidy
        # "Gemini API error: ..." string with a crash.
        def boom(*_a, **_k):
            raise OSError("no space left on device")

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("down")))
        monkeypatch.setattr(time, "sleep", lambda _s: None)
        self._fill_log_past_rotation(server)
        monkeypatch.setattr(os, "replace", boom)
        assert "Gemini API error" in call_tool(server, tool, "question")

    @pytest.mark.parametrize(
        "exc",
        [
            OSError("no space left on device"),
            UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates not allowed"),
        ],
        ids=["oserror", "unicodeencodeerror"],
    )
    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_any_log_failure_still_returns_the_response(
        self, server, monkeypatch, tool, exc
    ):
        # Suppressing only OSError would leave the same bug reachable by another
        # type: a lone surrogate survives json.loads but cannot be encoded as
        # UTF-8, so writing the log raises UnicodeEncodeError -- a ValueError
        # subclass, which the tools' `except ValueError` arm would report as an
        # error while discarding the answer. Logging is a side effect; it never
        # costs the response, whatever it fails with.
        def boom(*_a, **_k):
            raise exc

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))
        monkeypatch.setattr(server, "_append_log", boom)
        assert call_tool(server, tool, "question") == "the answer"


class TestUpstreamFailures:
    """Non-2xx statuses, truncated bodies and odd payload shapes.

    Catching only (URLError, TimeoutError) would be wrong twice over. HTTPError
    *is* a URLError, so a 400 / 403 / 404 -- which answers identically every
    time -- would burn all three attempts and 3s of backoff, and the upstream
    `error.message` that names the cause would never be read. http.client's
    IncompleteRead is neither, so a connection dropped mid-body would escape the
    tool entirely. scripts/gemini_api.py states the policy the server is
    aligned with here: retry only what may still succeed, quote the upstream
    message, and always hand the caller a string.
    """

    @staticmethod
    def _http_error(code, body=b"{}"):
        return urllib.error.HTTPError(
            "https://example.invalid", code, "reason", {}, io.BytesIO(body)
        )

    @staticmethod
    def _response(body):
        class Resp:
            def read(self):
                return json.dumps(body).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return lambda req, timeout: Resp()

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_a_permanent_status_fails_fast_and_quotes_the_upstream_message(
        self, server, monkeypatch, status
    ):
        sleeps, calls = [], []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        body = json.dumps({"error": {"message": "model X is not found"}}).encode()
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            fake_gemini(self._http_error(status, body), calls=calls),
        )
        result = call_tool(server, "consult_gemini", "anything")
        assert result.startswith("Gemini API error:")
        assert str(status) in result
        assert "model X is not found" in result
        assert len(calls) == 1, "a permanent status must not burn the retries"
        assert sleeps == []

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    def test_a_transient_status_is_retried(self, server, monkeypatch, status):
        sleeps = []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            fake_gemini(self._http_error(status), "recovered"),
        )
        assert server.call_gemini("question") == "recovered"
        assert sleeps == [1]

    def test_an_exhausted_transient_status_reports_the_status(
        self, server, monkeypatch
    ):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini(self._http_error(503))
        )
        result = call_tool(server, "review_gemini", "anything")
        assert result.startswith("Gemini API error:")
        assert "503" in result

    def test_a_truncated_body_is_retried_then_reported_as_a_string(
        self, server, monkeypatch
    ):
        sleeps, calls = [], []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            fake_gemini(http.client.IncompleteRead(b"{", 400), calls=calls),
        )
        result = call_tool(server, "review_gemini", "anything")
        assert result.startswith("Gemini API error:")
        assert len(calls) == 3
        assert sleeps == [1, 2]

    def test_a_truncated_body_then_success_recovers(self, server, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            fake_gemini(http.client.IncompleteRead(b"{", 400), "recovered"),
        )
        assert server.call_gemini("question") == "recovered"

    # A socket error raised while the body is being read is NOT wrapped in
    # URLError (urllib only wraps errors from the connect phase), so naming
    # (URLError, TimeoutError, HTTPException) let a reset connection escape
    # without a retry, a log line or a notification. scripts/gemini_api.py
    # catches OSError for the same reason; the server matches it.
    _SOCKET_ERRORS = [
        ConnectionResetError(54, "Connection reset by peer"),
        ssl.SSLError("record layer failure"),
        OSError("network is unreachable"),
    ]

    @pytest.mark.parametrize("err", _SOCKET_ERRORS, ids=type)
    def test_a_socket_error_mid_body_is_retried(self, server, monkeypatch, err):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(err, "recovered"))
        assert server.call_gemini("question") == "recovered"

    @pytest.mark.parametrize("err", _SOCKET_ERRORS, ids=type)
    @pytest.mark.parametrize("tool", ["consult_gemini", "review_gemini"])
    def test_an_exhausted_socket_error_is_reported_as_a_string(
        self, server, monkeypatch, err, tool
    ):
        sleeps, calls = [], []
        monkeypatch.setattr(time, "sleep", sleeps.append)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(err, calls=calls))
        result = call_tool(server, tool, "anything")
        assert result.startswith("Gemini API error:")
        assert len(calls) == 3
        assert sleeps == [1, 2]

    @pytest.mark.parametrize(
        "body",
        [
            {"candidates": [{"content": {"parts": ["oops"]}, "finishReason": "STOP"}]},
            {
                "candidates": [
                    {"content": {"parts": [{"text": None}]}, "finishReason": "STOP"}
                ]
            },
            {"candidates": [{"content": "not a dict", "finishReason": "STOP"}]},
            {"candidates": [None]},
            {"candidates": "not a list"},
            {"candidates": {"0": "not a list either"}},
            {"promptFeedback": "not a dict"},
            ["not", "a", "dict"],
        ],
    )
    def test_a_malformed_payload_returns_a_message_instead_of_raising(
        self, server, monkeypatch, body
    ):
        monkeypatch.setattr(urllib.request, "urlopen", self._response(body))
        result = call_tool(server, "consult_gemini", "anything")
        assert isinstance(result, str)
        assert result.startswith("[Gemini"), result


TOOLS = ["consult_gemini", "review_gemini"]
SLOW = 0.3


def _slow_urlopen(delay, *replies):
    """A urlopen fake that really blocks for `delay` seconds first."""
    inner = fake_gemini(*replies)

    def _urlopen(req, timeout=None):
        _REAL_SLEEP(delay)
        return inner(req, timeout)

    return _urlopen


class TestToolsDoNotBlockTheEventLoop:
    """FastMCP calls a sync tool directly on the event loop.

    A Gemini call can take minutes (3 attempts x 180 s plus backoff), and while
    it runs a blocking tool leaves the server unable to read a cancellation, a
    ping, or a second tool call. The tools must be coroutines that push every
    blocking step (HTTP, backoff sleep, log write, notification) to a thread.
    """

    @pytest.mark.parametrize("tool", TOOLS)
    def test_tool_is_a_coroutine_function(self, server, tool):
        assert inspect.iscoroutinefunction(getattr(server, tool)), (
            f"{tool} must be `async def` so FastMCP awaits it instead of "
            "running it on the event loop"
        )

    @pytest.mark.parametrize("tool", TOOLS)
    def test_loop_keeps_ticking_during_a_slow_gemini_call(
        self, server, monkeypatch, tool
    ):
        monkeypatch.setattr(
            urllib.request, "urlopen", _slow_urlopen(SLOW, "slow answer")
        )
        result, ticks, elapsed = call_tool_with_ticker(server, tool, "question")
        assert result == "slow answer"
        assert elapsed >= SLOW, "the slow path did not actually run"
        assert ticks >= 10, (
            f"the event loop was starved during the Gemini call: only {ticks} "
            f"ticks in {elapsed:.2f}s (a free loop manages ~{int(elapsed / 0.01)})"
        )

    @pytest.mark.parametrize("tool", TOOLS)
    def test_loop_keeps_ticking_during_the_retry_backoff_sleep(
        self, server, monkeypatch, tool
    ):
        # The backoff is a time.sleep inside call_gemini: it must be off the
        # loop too, not just the HTTP request.
        monkeypatch.setattr(time, "sleep", lambda s: _REAL_SLEEP(SLOW))
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini(URLError("down"), "recovered")
        )
        result, ticks, elapsed = call_tool_with_ticker(server, tool, "question")
        assert result == "recovered"
        assert elapsed >= SLOW, "the backoff sleep did not actually run"
        assert ticks >= 10, (
            f"the event loop was starved during the backoff sleep: only {ticks} "
            f"ticks in {elapsed:.2f}s"
        )

    @pytest.mark.parametrize("tool", TOOLS)
    def test_loop_keeps_ticking_while_the_notification_blocks(
        self, server, monkeypatch, tool
    ):
        # notify() runs osascript / notify-send with a multi-second timeout.
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("answer"))
        monkeypatch.setattr(server, "notify", lambda t, m, s=5: _REAL_SLEEP(SLOW))
        result, ticks, elapsed = call_tool_with_ticker(server, tool, "question")
        assert result == "answer"
        assert elapsed >= SLOW, "the slow notification did not actually run"
        assert ticks >= 10, (
            f"the event loop was starved by the notification: only {ticks} "
            f"ticks in {elapsed:.2f}s"
        )

    def test_two_tool_calls_make_progress_concurrently(self, server, monkeypatch):
        delay = 0.4
        monkeypatch.setattr(urllib.request, "urlopen", _slow_urlopen(delay, "answer"))

        async def both():
            started = time.monotonic()
            results = await asyncio.gather(
                drive_tool(server, "consult_gemini", "first"),
                drive_tool(server, "review_gemini", "second"),
            )
            return results, time.monotonic() - started

        results, elapsed = asyncio.run(both())
        assert results == ["answer", "answer"]
        # Serialised on the loop this takes 2 x delay; overlapped, about 1 x.
        assert elapsed < 1.75 * delay, (
            f"two slow tool calls ran back to back ({elapsed:.2f}s for "
            f"2 x {delay}s of work)"
        )


# Obviously fake, built by concatenation so no literal ever looks like a real
# token. Shaped to match the scanner: ghp_ + 36 alphanumerics, AKIA + 16.
FAKE_GITHUB_TOKEN = "ghp_" + "FAKE0123" * 4 + "FAKE"
FAKE_AWS_KEY = "AKIA" + "FAKE0123" * 2
CONTEXT_MARKER = "UNRELATED-CONTEXT-MARKER"

CREDENTIAL_CASES = [
    pytest.param(FAKE_GITHUB_TOKEN, FAKE_GITHUB_TOKEN, "GitHub token", id="bare"),
    pytest.param(
        f"{CONTEXT_MARKER}: why does this call fail?\n\n{FAKE_GITHUB_TOKEN}\n\nthanks",
        FAKE_GITHUB_TOKEN,
        "GitHub token",
        id="github-in-prose",
    ),
    pytest.param(
        f"{CONTEXT_MARKER}: is it ok that {FAKE_AWS_KEY} is in the config?",
        FAKE_AWS_KEY,
        "AWS access key",
        id="aws-in-prose",
    ),
]


def _fragments(secret):
    """The value and pieces of it: a leak can be partial."""
    return [secret, secret[:8], secret[-8:]]


def _fake_hooks_dir(tmp_path, source):
    """A stand-in hooks dir; source=None leaves it without the shared module."""
    hooks = tmp_path / "fake-hooks"
    hooks.mkdir(exist_ok=True)
    if source is not None:
        (hooks / "_bash_review_common.py").write_text(source, encoding="utf-8")
    return str(hooks)


class TestSecretScanBeforeSend:
    """Text sent to Google is scanned first, with the repo's own scan_secrets.

    Every other path that ships text to an external model (the bash-review
    hook, the editors via scripts/secret_scan.py) refuses a credential before
    sending it. "Credential found" and "scanner unavailable" both stop the
    send: an unverifiable payload is not a clean one.
    """

    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize(("question", "secret", "label"), CREDENTIAL_CASES)
    def test_a_credential_blocks_the_send_and_names_only_its_category(
        self, server, monkeypatch, tool, question, secret, label
    ):
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini("never fetched", calls=calls)
        )
        result = call_tool(server, tool, question)

        assert calls == [], f"{tool} sent a credential to Gemini"
        assert label in result, result
        assert "remove" in result.lower(), result
        assert "retry" in result.lower(), result
        for fragment in _fragments(secret):
            assert fragment not in result, f"the value leaked into the reply: {result}"

    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize(("question", "secret", "label"), CREDENTIAL_CASES)
    def test_a_blocked_question_leaves_no_trace_in_the_log_or_notifications(
        self, server, monkeypatch, tool, question, secret, label
    ):
        notices = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("never fetched"))
        monkeypatch.setattr(server, "notify", lambda t, m, s=5: notices.append(m))
        call_tool(server, tool, question)

        log_text = ""
        if os.path.exists(server.log_file):
            log_text = open(server.log_file, encoding="utf-8").read()
        sinks = {"the log": log_text, "a notification": "\n".join(notices)}
        for sink, text in sinks.items():
            for piece in [*_fragments(secret), CONTEXT_MARKER]:
                assert piece not in text, f"{piece!r} reached {sink}: {text!r}"
        # The fact and the label are the only things the log may record.
        assert label in log_text, f"the block was not recorded: {log_text!r}"

    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_clean_question_is_still_sent(self, server, monkeypatch, tool):
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        assert call_tool(server, tool, "token refresh logic: is it safe?") == "ok"
        assert len(calls) == 1

    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize(
        "source",
        [
            None,
            "raise RuntimeError('cannot load')\n",
            "x = 1\n",
            "def scan_secrets(command, tool_input):\n    raise RuntimeError('boom')\n",
            "def scan_secrets(command, tool_input):\n    return None\n",
        ],
        ids=["missing", "import-raises", "no-scan_secrets", "scan-raises", "bad-shape"],
    )
    def test_an_unavailable_scanner_fails_closed(
        self, server, monkeypatch, tmp_path, tool, source
    ):
        calls = []
        monkeypatch.setattr(
            urllib.request, "urlopen", fake_gemini("never fetched", calls=calls)
        )
        monkeypatch.setattr(
            server, "_HOOKS_DIR", _fake_hooks_dir(tmp_path, source), raising=False
        )
        # Unscanned text may well contain a secret: it must be neither sent
        # nor echoed nor logged.
        question = f"{CONTEXT_MARKER} {FAKE_GITHUB_TOKEN}"
        result = call_tool(server, tool, question)

        assert calls == [], f"{tool} sent unscanned text to Gemini"
        assert "secret scan is unavailable" in result, result
        log_text = ""
        if os.path.exists(server.log_file):
            log_text = open(server.log_file, encoding="utf-8").read()
        for sink, text in {"the reply": result, "the log": log_text}.items():
            for piece in [*_fragments(FAKE_GITHUB_TOKEN), CONTEXT_MARKER]:
                assert piece not in text, f"{piece!r} reached {sink}: {text!r}"

    @pytest.mark.parametrize("tool", TOOLS)
    def test_the_verdict_of_scan_secrets_from_the_hooks_dir_is_obeyed(
        self, server, monkeypatch, tmp_path, tool
    ):
        # Proves the tool delegates to the shared scan_secrets with the
        # caller's question (not to a private copy of the patterns).
        source = (
            "def scan_secrets(command, tool_input):\n"
            "    return ('TRIGGER' in command, 'fake category')\n"
        )
        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        monkeypatch.setattr(
            server, "_HOOKS_DIR", _fake_hooks_dir(tmp_path, source), raising=False
        )

        blocked = call_tool(server, tool, "contains TRIGGER here")
        assert "fake category" in blocked
        assert calls == []
        assert call_tool(server, tool, "harmless") == "ok"
        assert len(calls) == 1

    @pytest.mark.parametrize("tool", TOOLS)
    def test_the_scanner_is_found_through_a_symlinked_install(
        self, load_server, monkeypatch, tmp_path, tool
    ):
        # ~/.claude/mcp-servers is a symlink into the repo. Resolving the hooks
        # dir from abspath(__file__) lands in ~/.claude/hooks (which need not
        # exist); realpath lands in the repo's .claude/hooks.
        claude_dir = tmp_path / "installed" / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "mcp-servers").symlink_to(SERVER.parent.parent)
        linked = claude_dir / "mcp-servers" / "gemini-consultant" / "server.py"
        server = load_server(linked)

        calls = []
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("ok", calls=calls))
        blocked = call_tool(server, tool, f"check {FAKE_GITHUB_TOKEN}")
        assert "GitHub token" in blocked, blocked
        assert calls == []
        assert call_tool(server, tool, "a clean question") == "ok"


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TestPrivateLog:
    """The log holds full prompts and answers: owner-only, whatever the umask."""

    @pytest.fixture
    def open_umask(self):
        previous = os.umask(0)
        try:
            yield
        finally:
            os.umask(previous)

    def test_log_directory_is_created_0700(self, load_server, open_umask):
        server = load_server()
        mode = _mode(os.path.dirname(server.log_file))
        assert mode == 0o700, oct(mode)

    @pytest.mark.parametrize("tool", TOOLS)
    def test_log_file_is_created_0600(self, server, monkeypatch, open_umask, tool):
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("answer"))
        call_tool(server, tool, "question")
        mode = _mode(server.log_file)
        assert mode == 0o600, oct(mode)

    def test_a_removed_log_directory_is_recreated_0700(
        self, server, monkeypatch, open_umask
    ):
        shutil.rmtree(os.path.dirname(server.log_file))
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("answer"))
        call_tool(server, "consult_gemini", "question")
        assert _mode(os.path.dirname(server.log_file)) == 0o700
        assert _mode(server.log_file) == 0o600

    @pytest.mark.parametrize("preexisting", [1, 520], ids=["append", "rotate"])
    def test_loose_existing_log_and_directory_are_tightened_on_the_next_write(
        self, server, monkeypatch, preexisting
    ):
        log_dir = os.path.dirname(server.log_file)
        os.chmod(log_dir, 0o755)  # nosec B103  # deliberately loose
        with open(server.log_file, "w", encoding="utf-8") as f:
            f.write("previous entry\n" * preexisting)
        os.chmod(server.log_file, 0o644)

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("fresh answer"))
        call_tool(server, "consult_gemini", "question")

        assert _mode(log_dir) == 0o700, oct(_mode(log_dir))
        assert _mode(server.log_file) == 0o600, oct(_mode(server.log_file))
        text = open(server.log_file, encoding="utf-8").read()
        assert "previous entry" in text, "tightening must not discard old entries"
        assert "fresh answer" in text

    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_failed_tightening_never_costs_the_response(
        self, server, monkeypatch, tool
    ):
        log_dir = os.path.dirname(server.log_file)
        os.chmod(log_dir, 0o755)  # nosec B103  # deliberately loose
        with open(server.log_file, "w", encoding="utf-8") as f:
            f.write("previous entry\n")
        os.chmod(server.log_file, 0o644)

        def denied(*_a, **_k):
            raise PermissionError("operation not permitted")

        monkeypatch.setattr(os, "chmod", denied)
        monkeypatch.setattr(os, "fchmod", denied, raising=False)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))
        assert call_tool(server, tool, "question") == "the answer"


# One entry as log_entry() writes it: separator, header, prompt, response, separator.
_LOG_ENTRY = re.compile(
    r"-{60}\n\[[^\]]+\] \w+\s*\| (\w+)\n\[PROMPT\]\n(.*?)\n\n\[RESPONSE\]\n(.*?)\n-{60}\n",
    re.DOTALL,
)


def _echo_urlopen(delay):
    """A urlopen fake answering each request with a large reply tagged like it."""
    filler = "dolor sit amet " * 2000  # ~30 KB on one line, scanner-clean

    class Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _urlopen(req, timeout=None):
        _REAL_SLEEP(delay)
        prompt = json.loads(req.data)["contents"][0]["parts"][0]["text"]
        tag = re.search(r"Q(\d+)-START", prompt).group(1)
        text = f"R{tag}-START {filler} R{tag}-END"
        body = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        return Resp(json.dumps(body).encode("utf-8"))

    return _urlopen


class TestConcurrentLogging:
    """The tools run in worker threads, so _append_log runs concurrently.

    Entries larger than the stdio buffer are written in several write(2) calls
    and interleave, and the read / rewrite / os.replace rotation is a lost
    update when two threads rotate at once. Both corrupt the log silently.
    """

    N = 10
    FILLER = "lorem ipsum dolor sit amet " * 1200  # ~32 KB on one line

    @pytest.mark.parametrize("preexisting", [0, 495], ids=["interleave", "rotate"])
    def test_concurrent_calls_leave_every_entry_whole(
        self, server, monkeypatch, preexisting
    ):
        if preexisting:
            with open(server.log_file, "w", encoding="utf-8") as f:
                f.write("old entry\n" * preexisting)
        monkeypatch.setattr(urllib.request, "urlopen", _echo_urlopen(0.05))

        async def everything_at_once():
            return await asyncio.gather(
                *(
                    drive_tool(
                        server,
                        TOOLS[i % 2],
                        f"Q{i:02d}-START {self.FILLER} Q{i:02d}-END",
                    )
                    for i in range(self.N)
                )
            )

        asyncio.run(everything_at_once())

        text = open(server.log_file, encoding="utf-8").read()
        entries = _LOG_ENTRY.findall(text)
        assert len(entries) == self.N, (
            f"expected {self.N} whole entries, found {len(entries)}"
        )
        seen = set()
        for _tool, prompt, response in entries:
            tags = re.findall(r"Q(\d+)-START", prompt)
            assert len(tags) == 1, f"foreign data inside one prompt: {tags}"
            tag = tags[0]
            assert prompt.endswith(f"Q{tag}-END"), "a prompt was cut or interleaved"
            assert re.findall(r"R(\d+)-START", response) == [tag], (
                "a response is not the one that belongs to its prompt"
            )
            assert response.endswith(f"R{tag}-END"), "a response was cut or interleaved"
            seen.add(tag)
        assert seen == {f"{i:02d}" for i in range(self.N)}


class TestLogPathHardening:
    """The log is only written where it is safe: a private directory we own."""

    @staticmethod
    def _pretend(monkeypatch, directory, *, uid=None, mode=None):
        """Make stat()/lstat() report `directory` with another owner / mode."""
        real = {"stat": os.stat, "lstat": os.lstat}

        def wrap(name):
            def wrapper(path, *args, **kwargs):
                result = real[name](path, *args, **kwargs)
                try:
                    same = os.fspath(path) == directory
                except TypeError:
                    same = False
                if not same:
                    return result
                values = list(tuple(result))
                if uid is not None:
                    values[4] = uid
                if mode is not None:
                    values[0] = (values[0] & ~0o7777) | mode
                return os.stat_result(tuple(values))

            return wrapper

        monkeypatch.setattr(os, "stat", wrap("stat"))
        monkeypatch.setattr(os, "lstat", wrap("lstat"))

    @pytest.mark.skipif(
        not hasattr(os, "O_NOFOLLOW"), reason="platform has no O_NOFOLLOW"
    )
    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_symlink_at_the_log_path_is_not_followed(
        self, server, monkeypatch, tmp_path, tool
    ):
        target = tmp_path / "elsewhere.txt"
        target.write_text("original\n", encoding="utf-8")
        if os.path.lexists(server.log_file):
            os.remove(server.log_file)
        os.symlink(target, server.log_file)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))

        assert call_tool(server, tool, "question") == "the answer"
        assert target.read_text(encoding="utf-8") == "original\n", (
            "the log write followed the symlink and landed in another file"
        )
        assert os.path.islink(server.log_file)

    def test_the_log_file_is_tightened_through_its_descriptor(
        self, server, monkeypatch
    ):
        # chmod(path) re-resolves the name, so a path swapped in between open
        # and chmod would be tightened instead of the file that was opened.
        with open(server.log_file, "w", encoding="utf-8") as f:
            f.write("previous entry\n")
        os.chmod(server.log_file, 0o644)
        real_chmod = os.chmod

        def chmod_by_path_fails(path, *args, **kwargs):
            if os.fspath(path) == server.log_file:
                raise PermissionError("chmod by path is not allowed here")
            return real_chmod(path, *args, **kwargs)

        monkeypatch.setattr(os, "chmod", chmod_by_path_fails)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("fresh answer"))
        call_tool(server, "consult_gemini", "question")

        assert _mode(server.log_file) == 0o600, oct(_mode(server.log_file))
        text = open(server.log_file, encoding="utf-8").read()
        assert "previous entry" in text and "fresh answer" in text

    @pytest.mark.skipif(
        not hasattr(os, "geteuid"), reason="no POSIX ownership on this platform"
    )
    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_log_directory_owned_by_someone_else_is_not_written_to(
        self, server, monkeypatch, tool
    ):
        directory = os.path.dirname(server.log_file)
        self._pretend(monkeypatch, directory, uid=os.geteuid() + 1)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))

        assert call_tool(server, tool, "question") == "the answer"
        assert not os.path.exists(server.log_file), (
            "the prompt was written into a directory owned by another user"
        )

    @pytest.mark.skipif(
        not hasattr(os, "geteuid"), reason="no POSIX modes on this platform"
    )
    @pytest.mark.parametrize("tool", TOOLS)
    def test_a_log_directory_left_group_writable_is_not_written_to(
        self, server, monkeypatch, tool
    ):
        # The tightening attempt "succeeds" but the directory still reports a
        # group-writable mode (a filesystem that ignores chmod): other users
        # could swap the file, so nothing is written.
        directory = os.path.dirname(server.log_file)
        self._pretend(monkeypatch, directory, mode=0o775)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))

        assert call_tool(server, tool, "question") == "the answer"
        assert not os.path.exists(server.log_file), (
            "the prompt was written into a group-writable directory"
        )


class TestCancellation:
    """A cancelled caller must not leave the worker retrying in the background."""

    @pytest.mark.parametrize("tool", TOOLS)
    @pytest.mark.parametrize("cancel_during", ["request", "backoff"])
    def test_a_cancelled_call_makes_no_further_request(
        self, server, monkeypatch, tool, cancel_during
    ):
        calls, sleeps, notices = [], [], []
        monkeypatch.setattr(server, "notify", lambda t, m, s=5: notices.append(m))
        inner = fake_gemini(URLError("down"), URLError("down"), "ok", calls=calls)

        def urlopen(req, timeout=None):
            if cancel_during == "request" and not calls:
                _REAL_SLEEP(SLOW)  # the first attempt is slow, then fails
            return inner(req, timeout)

        def sleep(seconds):
            sleeps.append(seconds)
            if cancel_during == "backoff":
                _REAL_SLEEP(SLOW)

        monkeypatch.setattr(urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(time, "sleep", sleep)

        async def scenario():
            task = asyncio.ensure_future(drive_tool(server, tool, "question"))
            await asyncio.sleep(SLOW / 3)  # the attempt / the backoff is under way
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        # asyncio.run() joins the executor on the way out, so the worker has
        # finished (or kept retrying) by the time it returns.
        asyncio.run(scenario())

        assert len(calls) == 1, f"{len(calls)} requests made; a cancelled call retried"
        assert sleeps == ([] if cancel_during == "request" else [1])
        assert notices == [], "a cancelled call still announced a result"
