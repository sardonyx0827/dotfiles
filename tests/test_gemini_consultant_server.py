"""Tests for .claude/mcp-servers/gemini-consultant/server.py.

The `mcp` package is stubbed out so the tests run without it and the
tool functions stay plain callables.
"""

import http.client
import importlib.util
import io
import json
import os
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
        # GeminiKeyError, deliberately NOT a ValueError subclass: the tools used
        # to route key problems, json.JSONDecodeError and putheader's
        # value-carrying ValueError through one `except ValueError` arm. That
        # made the JSONDecodeError entry below it dead and leaked the key. The
        # distinct type is what lets the three be told apart, so pin the type.
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
        # A 200 with a non-JSON body (proxy / captive portal) used to be caught
        # by `except ValueError` and announced as "APIキー未設定".
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
        result = server.consult_gemini("anything")
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

    def test_review_gemini_reports_missing_key_as_string(self, server, monkeypatch):
        # ValueError branch of review_gemini (mirror of consult_gemini's).
        monkeypatch.delenv("GEMINI_API_KEY")
        result = server.review_gemini("anything")
        assert result.startswith("Gemini API error:")

    def test_review_gemini_reports_api_error_as_string(self, server, monkeypatch):
        # API-exception branch of review_gemini (mirror of consult_gemini's).
        monkeypatch.setattr(time, "sleep", lambda s: None)
        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini(URLError("boom")))
        result = server.review_gemini("anything")
        assert result.startswith("Gemini API error:")


SENTINEL_KEY = "SENTINELKEYVALUEMUSTNEVERAPPEAR"


class TestApiKeyNeverLeaks:
    """The credential must not reach the caller or the log, on any path.

    `http.client.putheader` refuses a header value containing CR/LF and raises
    `ValueError("Invalid header value %r" % value)` -- with the RAW value in the
    message. `call_gemini`'s inner handler only catches (URLError, TimeoutError),
    so that ValueError reaches the tools' `except ValueError` clause, which puts
    `str(e)` into BOTH the returned string and `_log_quietly`. The key then sits
    in the conversation transcript and on disk in ~/.claude/logs.

    `call_gemini` strips the key before anything else (server.py:302), and only
    THEN hands it to `_reject_unusable_api_key` (server.py:308). That ordering
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
      involved" would silently let it dial the real API instead, which is
      exactly the bug this class used to have.
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

        result = getattr(server, tool)(f"prompt for {label}")
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

        result = getattr(server, tool)(f"prompt for {label}")

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
        result = getattr(server, tool)("anything")
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

    `call_gemini` sends maxOutputTokens=8192 and then joins `parts` without ever
    looking at `finishReason`, so a MAX_TOKENS truncation is returned as if it
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
        result = server.consult_gemini("anything")
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
        result = server.consult_gemini("anything")
        assert result.strip(), "a SAFETY block returned an empty string"
        assert "SAFETY" in result

    def test_empty_candidates_list_does_not_index_error(self, server, monkeypatch):
        # `.get("candidates", [{}])` does not defend a key that is PRESENT and
        # empty, so [0] raised IndexError and surfaced as an opaque
        # "list index out of range".
        self._resp(
            monkeypatch,
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
        )
        result = server.consult_gemini("anything")
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
        result = server.consult_gemini("anything")
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
        result = server.consult_gemini("anything")
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
        assert server.consult_gemini("anything") == "done"


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
        # Regression guard: the old rotation path truncated the log in place
        # via open(log_file, "w"), so a crash mid-write left it empty for
        # good and concurrent writers (Claude + Codex sessions) could race on
        # the same truncate. The fix must write the trimmed content to a
        # temp file and swap it in with os.replace, matching the atomic
        # pattern in _bash_review_common.py's append_and_rotate -- so a
        # failure there must leave the previous log content untouched.
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
        # of OSError, not its parent -- so a failed rotation propagated straight
        # out of the tool and discarded a successful answer.
        def boom(*_a, **_k):
            raise OSError("no space left on device")

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))
        self._fill_log_past_rotation(server)
        monkeypatch.setattr(os, "replace", boom)
        assert getattr(server, tool)("question") == "the answer"

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
        assert "Gemini API error" in getattr(server, tool)("question")

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
        # Suppressing only OSError left the same bug reachable by another type:
        # a lone surrogate survives json.loads but cannot be encoded as UTF-8,
        # so writing the log raises UnicodeEncodeError -- a ValueError subclass,
        # which the first except clause then reports as "APIキー未設定" while
        # discarding the answer. Logging is a side effect; it never costs the
        # response, whatever it fails with.
        def boom(*_a, **_k):
            raise exc

        monkeypatch.setattr(urllib.request, "urlopen", fake_gemini("the answer"))
        monkeypatch.setattr(server, "_append_log", boom)
        assert getattr(server, tool)("question") == "the answer"


class TestUpstreamFailures:
    """Non-2xx statuses, truncated bodies and odd payload shapes.

    call_gemini used to catch only (URLError, TimeoutError). HTTPError *is* a
    URLError, so a 400 / 403 / 404 -- which answers identically every time --
    burned all three attempts and 3s of backoff, and the upstream
    `error.message` that names the cause was never read. http.client's
    IncompleteRead is neither, so a connection dropped mid-body escaped the
    tool entirely. scripts/gemini_api.py already states the policy the server
    is aligned with here: retry only what may still succeed, quote the
    upstream message, and always hand the caller a string.
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
        result = server.consult_gemini("anything")
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
        result = server.review_gemini("anything")
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
        result = server.review_gemini("anything")
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
        result = server.consult_gemini("anything")
        assert isinstance(result, str)
        assert result.startswith("[Gemini"), result
