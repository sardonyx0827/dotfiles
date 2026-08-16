"""Tests for scripts/gemini_api.py — the shared Gemini REST client CLI.

Both editors reach Gemini through this helper instead of through the `gemini`
CLI, so it is the single place where the request shape, the retry policy and
the "is this reply usable?" decision live. This pins the CLI contract:

    exit 0  -> a usable reply on stdout, nothing on stderr
    exit 1  -> no usable reply; one line on stderr saying why
    exit 2  -> GEMINI_API_KEY is not set

The case worth the most attention is the one that arrives as HTTP 200:
`finishReason: "MAX_TOKENS"` comes back alongside TRUNCATED text, and the
callers splice the reply over a selection or a whole buffer. Returning it would
apply an answer written for a fragment to the entire range, so it has to read
as a failure — see TestExtractText.
"""

import io
import json
import os
import subprocess
import sys
import urllib.error

import gemini_api
import pytest
from conftest import REPO_ROOT

HELPER = REPO_ROOT / "scripts" / "gemini_api.py"


class _Response:
    """The subset of a urlopen() result the helper touches."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_body(text="hi", finish_reason="STOP"):
    candidate = {"content": {"parts": [{"text": text}]}}
    if finish_reason is not None:
        candidate["finishReason"] = finish_reason
    return {"candidates": [candidate]}


def _http_error(code, body=b"{}"):
    return urllib.error.HTTPError(
        "https://example.invalid", code, "boom", {}, io.BytesIO(body)
    )


def _fake_urlopen(*replies, calls=None):
    """Yield each reply in turn; the last one repeats. Exceptions are raised."""
    queue = list(replies)

    def _open(req, timeout=None):
        if calls is not None:
            calls.append((req, timeout))
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, bytes):
            return _Response(reply)
        return _Response(json.dumps(reply).encode("utf-8"))

    return _open


class TestResolveModel:
    def test_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "from-env")
        assert gemini_api._resolve_model("explicit") == "explicit"

    def test_env_is_the_fallback(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "from-env")
        assert gemini_api._resolve_model(None) == "from-env"

    def test_default_when_nothing_is_set(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert gemini_api._resolve_model(None) == gemini_api.DEFAULT_MODEL

    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_a_blank_value_counts_as_unset(self, monkeypatch, blank):
        """`export GEMINI_MODEL=` is a shell accident, not a model id.

        Letting it through produces a 404 that reads as though the API itself
        were broken, which is the least actionable failure available.
        """
        monkeypatch.setenv("GEMINI_MODEL", blank)
        assert gemini_api._resolve_model(None) == gemini_api.DEFAULT_MODEL
        assert gemini_api._resolve_model(blank) == gemini_api.DEFAULT_MODEL

    def test_surrounding_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        assert gemini_api._resolve_model("  gemini-flash-latest \n") == (
            "gemini-flash-latest"
        )

    @pytest.mark.parametrize(
        "bad", ["../../v1/models/other", "model with space", "model:generateContent"]
    )
    def test_a_model_id_that_could_reshape_the_url_is_refused(self, monkeypatch, bad):
        """The id is interpolated into the request PATH.

        $GEMINI_MODEL is user-set, and nothing downstream re-checks the URL, so
        this regex is the only thing standing between a stray `../` and a
        request aimed somewhere other than generateContent.
        """
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        with pytest.raises(gemini_api.GeminiError, match="invalid model id"):
            gemini_api._resolve_model(bad)


class TestBuildRequest:
    def build(self):
        return gemini_api.build_request("m-1", "SYSTEM", "PAYLOAD", "secret-key")

    def test_the_url_targets_generate_content_for_the_model(self):
        assert self.build().full_url == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "m-1:generateContent"
        )

    def test_the_key_travels_in_a_header_not_the_query_string(self):
        req = self.build()
        # urllib title-cases header names it is given.
        assert req.get_header("X-goog-api-key") == "secret-key"
        assert "secret-key" not in req.full_url

    def test_the_instruction_and_the_payload_stay_separate(self):
        """Mirrors the CLI split this replaced (`-p INSTRUCTION` plus stdin).

        Collapsing them into one blob would quietly change what every prompt in
        ai/prompt.lua means, since each is written as an instruction ABOUT the
        text rather than as part of it.
        """
        body = json.loads(self.build().data.decode("utf-8"))
        assert body["systemInstruction"]["parts"][0]["text"] == "SYSTEM"
        assert body["contents"][0]["parts"][0]["text"] == "PAYLOAD"

    @pytest.mark.parametrize(
        "model", ["gemini-flash-lite-latest", "gemini-2.5-flash-lite", "models.v1_2-x"]
    )
    def test_a_dotted_or_underscored_model_id_reaches_the_url_intact(self, model):
        """MODEL_RE admits `.` and `_`; real ids use them.

        `gemini-2.5-flash-lite` is a live id today. A regex tightened to
        hyphens-and-letters would reject it, and the symptom would be "invalid
        model id" for a model that exists -- so pin the permitted shapes here
        rather than only the rejected ones.
        """
        req = gemini_api.build_request(model, "S", "P", "k")
        assert req.full_url.endswith(f"/models/{model}:generateContent")

    def test_no_generation_config_is_sent(self):
        """Both omissions are load-bearing; see build_request's docstring.

        `maxOutputTokens` would manufacture the MAX_TOKENS truncation this
        helper has to refuse, and `thinkingLevel: minimal` is a measured HTTP
        400 on every model except the default — which $GEMINI_MODEL can change.
        """
        assert "generationConfig" not in json.loads(self.build().data.decode("utf-8"))


class TestExtractText:
    def test_a_plain_reply_comes_back(self):
        assert gemini_api.extract_text(_ok_body("hello")) == "hello"

    def test_multiple_parts_are_concatenated(self):
        body = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "a"}, {"text": "b"}]},
                }
            ]
        }
        assert gemini_api.extract_text(body) == "ab"

    def test_thought_parts_are_dropped(self):
        """Reasoning is not the answer, and the answer replaces the buffer."""
        body = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "thinking out loud", "thought": True},
                            {"text": "answer"},
                        ]
                    },
                }
            ]
        }
        assert gemini_api.extract_text(body) == "answer"

    def test_a_missing_finish_reason_is_not_treated_as_a_failure(self):
        assert gemini_api.extract_text(_ok_body("hi", finish_reason=None)) == "hi"

    def test_max_tokens_is_a_failure_even_though_http_said_200(self):
        """The whole reason this function exists rather than a `.get()` chain.

        The API returns success with truncated text. Handing that back would
        splice a reply written for a fragment over the entire selection — the
        same harm run_cli's ARG_MAX guard refuses in the outbound direction.
        """
        body = _ok_body("half an ans", finish_reason="MAX_TOKENS")
        with pytest.raises(gemini_api.GeminiError) as exc:
            gemini_api.extract_text(body)
        assert "MAX_TOKENS" in str(exc.value)
        assert "half an ans" not in str(exc.value), "the partial reply must not leak"

    def test_any_other_unfinished_reason_is_a_failure_and_is_named(self):
        with pytest.raises(gemini_api.GeminiError, match="SAFETY"):
            gemini_api.extract_text(_ok_body("x", finish_reason="SAFETY"))

    def test_a_blocked_prompt_reports_the_block_reason(self):
        body = {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        with pytest.raises(gemini_api.GeminiError, match="blockReason=SAFETY"):
            gemini_api.extract_text(body)

    def test_no_candidates_without_a_block_reason(self):
        with pytest.raises(gemini_api.GeminiError, match="no candidates"):
            gemini_api.extract_text({"candidates": []})

    def test_an_error_body_is_reported_verbatim(self):
        body = {"error": {"message": "API key not valid"}}
        with pytest.raises(gemini_api.GeminiError, match="API key not valid"):
            gemini_api.extract_text(body)

    def test_an_empty_reply_is_a_failure(self):
        with pytest.raises(gemini_api.GeminiError, match="empty response"):
            gemini_api.extract_text(_ok_body("   "))

    @pytest.mark.parametrize(
        "body", ["not a dict", {"candidates": [42]}, {"candidates": "nope"}]
    )
    def test_an_unexpected_shape_never_raises_a_type_error(self, body):
        """A surprise from upstream must arrive as a readable line, not a stack.

        The editors print stderr into the diff/report window, so a TypeError
        traceback would be what the user reads instead of a reason.
        """
        with pytest.raises(gemini_api.GeminiError):
            gemini_api.extract_text(body)

    def test_a_missing_content_block_is_an_empty_reply(self):
        with pytest.raises(gemini_api.GeminiError, match="empty response"):
            gemini_api.extract_text({"candidates": [{"finishReason": "STOP"}]})


class TestErrorDetail:
    def test_the_google_error_message_is_preferred(self):
        raw = json.dumps({"error": {"code": 404, "message": "model not found"}})
        assert gemini_api._error_detail(raw) == "model not found"

    def test_a_non_json_body_falls_back_to_the_body(self):
        assert gemini_api._error_detail("  <html>502</html>  ") == "<html>502</html>"

    def test_a_json_body_without_an_error_field_falls_back(self):
        assert gemini_api._error_detail('{"x": 1}') == '{"x": 1}'

    def test_a_long_message_is_truncated(self):
        raw = json.dumps({"error": {"message": "y" * 5000}})
        assert len(gemini_api._error_detail(raw)) == gemini_api.MAX_DETAIL


class TestRequestGenerate:
    def call(self, monkeypatch, *replies, calls=None, attempts=3):
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen(*replies, calls=calls)
        )
        return gemini_api.request_generate(
            "m",
            "sys",
            "payload",
            "key",
            attempts=attempts,
            sleep=lambda _s: None,
        )

    def test_a_successful_call_returns_the_decoded_body(self, monkeypatch):
        assert self.call(monkeypatch, _ok_body("hi")) == _ok_body("hi")

    def test_the_request_is_rebuilt_each_attempt(self, monkeypatch):
        """urllib consumes a Request; reusing one silently sends no body."""
        calls = []
        self.call(monkeypatch, _http_error(503), _ok_body("hi"), calls=calls)
        assert len(calls) == 2
        first, second = calls[0][0], calls[1][0]
        assert first is not second
        assert first.data == second.data

    @pytest.mark.parametrize("status", sorted(gemini_api.RETRYABLE_STATUS))
    def test_transient_statuses_are_retried(self, monkeypatch, status):
        calls = []
        body = self.call(monkeypatch, _http_error(status), _ok_body("hi"), calls=calls)
        assert body == _ok_body("hi")
        assert len(calls) == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    def test_a_permanent_status_is_reported_without_burning_the_retries(
        self, monkeypatch, status
    ):
        """A bad key or a bad model answers the same way every time.

        Retrying it only delays the one message that tells the reader what to
        fix, behind two rounds of backoff.
        """
        calls = []
        body = json.dumps({"error": {"message": "nope"}}).encode()
        with pytest.raises(gemini_api.GeminiError) as exc:
            self.call(monkeypatch, _http_error(status, body), calls=calls)
        assert len(calls) == 1
        assert f"HTTP {status}" in str(exc.value)
        assert "nope" in str(exc.value)

    def test_the_reported_reason_is_the_last_attempt_not_the_first(self, monkeypatch):
        """`last` must be overwritten every round, not set once.

        Each attempt can fail differently (503, then 429, then a dead socket),
        and reporting the first would describe a condition that has since
        changed. Same-status retries cannot tell the two apart.
        """
        calls = []
        with pytest.raises(gemini_api.GeminiError) as exc:
            self.call(
                monkeypatch,
                _http_error(503),
                _http_error(429),
                urllib.error.URLError("connection reset"),
                calls=calls,
            )
        assert len(calls) == 3
        assert "connection reset" in str(exc.value)
        assert "503" not in str(exc.value)

    def test_a_transport_failure_is_retried_then_reported(self, monkeypatch):
        calls = []
        with pytest.raises(gemini_api.GeminiError, match="URLError"):
            self.call(monkeypatch, urllib.error.URLError("down"), calls=calls)
        assert len(calls) == 3

    def test_a_timeout_is_retried_like_any_other_transport_failure(self, monkeypatch):
        calls = []
        body = self.call(monkeypatch, TimeoutError("slow"), _ok_body("hi"), calls=calls)
        assert body == _ok_body("hi")
        assert len(calls) == 2

    def test_a_non_json_body_is_not_retried(self, monkeypatch):
        """A broken endpoint answers the same question the same way."""
        calls = []
        with pytest.raises(gemini_api.GeminiError, match="invalid JSON"):
            self.call(monkeypatch, b"<html>", calls=calls)
        assert len(calls) == 1

    def test_the_backoff_grows_and_is_not_slept_after_the_last_attempt(
        self, monkeypatch
    ):
        slept = []
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen(urllib.error.URLError("down"))
        )
        with pytest.raises(gemini_api.GeminiError):
            gemini_api.request_generate(
                "m", "s", "p", "k", attempts=3, sleep=slept.append
            )
        assert slept == [1, 2], "one sleep per gap, none after the final attempt"


class TestMain:
    def run(self, monkeypatch, capsys, argv, stdin="payload", **env):
        for key, value in {"GEMINI_API_KEY": "test-key", **env}.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode("utf-8")))
        )
        rc = gemini_api.main(argv)
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    def test_a_reply_goes_to_stdout_and_exits_zero(self, monkeypatch, capsys):
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(_ok_body("done")))
        rc, out, err = self.run(monkeypatch, capsys, ["--system", "S"])
        assert (rc, out, err) == (0, "done\n", "")

    def test_a_reply_that_already_ends_in_a_newline_is_not_doubled(
        self, monkeypatch, capsys
    ):
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(_ok_body("done\n")))
        rc, out, _ = self.run(monkeypatch, capsys, ["--system", "S"])
        assert (rc, out) == (0, "done\n")

    def test_a_missing_api_key_exits_2_and_names_the_variable(
        self, monkeypatch, capsys
    ):
        """Split from exit 1 because it is the one the reader can act on.

        A GUI-launched editor that never inherited the shell environment lands
        here, and "GEMINI_API_KEY is not set" is a far better report than a
        network error would be.
        """
        rc, out, err = self.run(
            monkeypatch, capsys, ["--system", "S"], GEMINI_API_KEY=None
        )
        assert rc == 2
        assert out == ""
        assert "GEMINI_API_KEY" in err

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_api_key_is_treated_as_missing(self, monkeypatch, capsys, blank):
        rc, _, err = self.run(
            monkeypatch, capsys, ["--system", "S"], GEMINI_API_KEY=blank
        )
        assert rc == 2
        assert "GEMINI_API_KEY" in err

    def test_the_api_key_is_never_echoed(self, monkeypatch, capsys):
        """It is read from the environment precisely so it stays out of sight."""
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(_ok_body("ok")))
        rc, out, err = self.run(
            monkeypatch, capsys, ["--system", "S"], GEMINI_API_KEY="super-secret"
        )
        assert rc == 0
        assert "super-secret" not in out + err

    def test_an_empty_payload_is_refused_before_the_request(self, monkeypatch, capsys):
        def explode(*_a, **_k):
            raise AssertionError("no request should be made for an empty payload")

        monkeypatch.setattr("urllib.request.urlopen", explode)
        rc, out, err = self.run(monkeypatch, capsys, ["--system", "S"], stdin="  \n")
        assert (rc, out) == (1, "")
        assert "empty payload" in err

    def test_a_failure_reports_one_line_on_stderr_and_nothing_on_stdout(
        self, monkeypatch, capsys
    ):
        """The editors splice stdout into a buffer; a diagnostic must not land
        there. cli_failure_reason quotes stderr instead."""
        body = json.dumps({"error": {"message": "API key not valid"}}).encode()
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen(_http_error(403, body))
        )
        rc, out, err = self.run(monkeypatch, capsys, ["--system", "S"])
        assert (rc, out) == (1, "")
        assert err.strip().count("\n") == 0
        assert "API key not valid" in err

    def test_an_unreadable_stdin_exits_1_rather_than_crashing(
        self, monkeypatch, capsys
    ):
        class Unreadable:
            def read(self):
                raise OSError("stdin went away")

        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr("sys.stdin", type("S", (), {"buffer": Unreadable()})())
        rc = gemini_api.main(["--system", "S"])
        captured = capsys.readouterr()
        assert (rc, captured.out) == (1, "")
        assert "i/o error" in captured.err
        assert "stdin went away" in captured.err

    def test_an_unforeseen_exception_still_arrives_as_one_line(
        self, monkeypatch, capsys
    ):
        """The contract is "one line on stderr", with no exceptions.

        The editors render stderr straight into a diff or report window, so a
        traceback there is what the user reads instead of a reason. This is the
        backstop for a failure mode that does not exist yet rather than one that
        has been seen -- which is exactly when it is cheap to install.
        """

        def boom(*_a, **_k):
            raise RuntimeError("something nobody planned for")

        monkeypatch.setattr(gemini_api, "extract_text", boom)
        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(_ok_body("ok")))
        rc, out, err = self.run(monkeypatch, capsys, ["--system", "S"])
        assert (rc, out) == (1, "")
        assert err.strip().count("\n") == 0
        assert "RuntimeError" in err
        assert "something nobody planned for" in err

    def test_the_model_reaches_the_request(self, monkeypatch, capsys):
        calls = []
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen(_ok_body("ok"), calls=calls)
        )
        rc, _, _ = self.run(
            monkeypatch, capsys, ["--system", "S", "--model", "gemini-flash-latest"]
        )
        assert rc == 0
        assert calls[0][0].full_url.endswith("/gemini-flash-latest:generateContent")

    def test_undecodable_payload_bytes_are_sent_rather_than_refused(
        self, monkeypatch, capsys
    ):
        """Same call scripts/secret_scan.py makes: decode with replacement.

        A buffer that is mostly text with a stray byte in it is still a
        question worth answering; refusing would be the more surprising answer.
        """
        calls = []
        monkeypatch.setattr(
            "urllib.request.urlopen", _fake_urlopen(_ok_body("ok"), calls=calls)
        )
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(
            "sys.stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe print(1)"))
        )
        assert gemini_api.main(["--system", "S"]) == 0
        sent = json.loads(calls[0][0].data.decode("utf-8"))
        assert "print(1)" in sent["contents"][0]["parts"][0]["text"]


class TestCliSubprocess:
    """The shape the editors actually invoke: `python3 gemini_api.py --system ...`
    with the payload on stdin and the key only in the environment."""

    def _run(self, *args, stdin="payload", env=None):
        return subprocess.run(  # noqa: S603
            [sys.executable, str(HELPER), *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            env=env if env is not None else {"PATH": os.environ["PATH"]},
        )

    def test_a_missing_key_exits_2_without_touching_the_network(self):
        r = self._run("--system", "S")
        assert r.returncode == 2
        assert r.stdout == ""
        assert "GEMINI_API_KEY" in r.stderr

    def test_the_system_flag_is_required(self):
        r = self._run()
        assert r.returncode == 2, r.stderr
        assert "--system" in r.stderr

    def test_a_non_ascii_reply_survives_an_ascii_output_encoding(self):
        """The reply is routinely Japanese; the locale must not eat it.

        ai/prompt.lua's buffer-check and hint prompts both say "Reply in
        Japanese", so stdout is non-ASCII on the paths that matter most. Going
        through sys.stdout's text layer raises UnicodeEncodeError under an
        encoding that cannot carry it -- reachable via PYTHONIOENCODING, or via
        a C locale on any system without C.UTF-8 for PEP 538 to coerce to -- and
        the reply this call has already been billed for is lost to a traceback.

        Has to run in a real subprocess: the failure IS the interaction between
        the process's stdout encoding and the write, and an in-process capsys
        replaces exactly the object under test. urlopen is stubbed in the child,
        so no request is made.

        Note stderr would NOT show this. Python gives stderr
        errors="backslashreplace", so a text-layer write there mangles the text
        instead of raising -- which is why this drives the reply path.
        """
        driver = (
            "import json, sys, urllib.request\n"
            f"sys.path.insert(0, {str(REPO_ROOT / 'scripts')!r})\n"
            "import gemini_api\n"
            "class R:\n"
            "    def read(self):\n"
            "        return json.dumps({'candidates': [{'finishReason': 'STOP',\n"
            "            'content': {'parts': [{'text': '日本語の応答'}]}}]}).encode()\n"
            "    def __enter__(self): return self\n"
            "    def __exit__(self, *a): return False\n"
            "urllib.request.urlopen = lambda *a, **k: R()\n"
            "raise SystemExit(gemini_api.main(['--system', 'S']))\n"
        )
        r = subprocess.run(  # noqa: S603
            [sys.executable, "-c", driver],
            input=b"payload",
            capture_output=True,
            timeout=30,
            env={
                "PATH": os.environ["PATH"],
                "GEMINI_API_KEY": "k",
                "PYTHONIOENCODING": "ascii",
            },
        )
        assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
        assert r.stdout.decode("utf-8") == "日本語の応答\n"

    def test_a_bad_model_id_is_refused_before_any_request(self):
        r = self._run(
            "--system",
            "S",
            "--model",
            "../evil",
            env={"PATH": os.environ["PATH"], "GEMINI_API_KEY": "k"},
        )
        assert r.returncode == 1
        assert r.stdout == ""
        assert "invalid model id" in r.stderr
