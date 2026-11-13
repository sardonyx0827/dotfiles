"""Regression tests for the bash-review gate hardening.

Each class pins one audited defect of the shared decision module
(.claude/hooks/_bash_review_common.py) or of the two entry points. They live
apart from test_bash_review.py because they were written together, from one
audit, and read best side by side.
"""

import http.client
import json
import ssl
import sys
import time
import urllib.error

import pytest
from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload

HOOK = ".claude/hooks/bash-review.py"
CODEX_HOOK = ".codex/hooks/bash-review.py"

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _bash_review_common as _common  # noqa: E402

# Built at runtime so no literal in this file looks like a live credential.
FAKE_GITHUB_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"
FAKE_OPAQUE_VALUE = "Zq8vK2mN" + "5xR7tY1w"


@pytest.fixture(autouse=True)
def _keep_chmod_inside_the_sandbox(request, monkeypatch):
    """Same guard as test_bash_review.py: run_hook does not remap lstat/chmod."""
    if "run_hook" in request.fixturenames:
        monkeypatch.setattr(_common, "_tighten_dir", lambda path: None)


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def gemini_bodies(*replies, calls=None):
    """urlopen() fake that returns each reply as the raw response body.

    A dict or list is JSON-encoded, bytes are sent as they are, and an
    Exception instance is raised. The last reply repeats.
    """
    queue = list(replies)

    def _urlopen(req, timeout=None):
        if calls is not None:
            calls.append(req)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, bytes):
            return _Response(reply)
        return _Response(json.dumps(reply).encode("utf-8"))

    return _urlopen


def text_body(*parts, finish="STOP", **extra):
    candidate = {"content": {"parts": [dict(p) for p in parts]}}
    if finish is not None:
        candidate["finishReason"] = finish
    return {"candidates": [candidate], **extra}


def review(monkeypatch, *replies, key="test-api-key"):
    calls: list = []
    monkeypatch.setattr("urllib.request.urlopen", gemini_bodies(*replies, calls=calls))
    verdict, output = _common.run_gemini_review("prompt", key, "primary", "fallback")
    return verdict, output, calls


class TestGrepOperandsAfterDoubleDash:
    """`--` ends the options, not the operands: a directory after it is still a
    recursive, hidden-file-reading search under the harness's grep."""

    @pytest.mark.parametrize(
        "command",
        [
            "grep foo -- .",
            "grep -n -- '^[A-Z_]*=' src",
            "grep -- foo sub/",
            "grep -e foo -- src",
            "grep -i -- foo docs notes.txt",
            "grep -- foo -r",  # after --, -r is a file operand, not a flag
        ],
    )
    def test_directory_operand_after_double_dash_is_reviewed(self, command):
        assert _common._can_skip_review(command) is False

    @pytest.mark.parametrize(
        "command",
        [
            "grep -- -r file.txt",  # after --, -r is the pattern
            "grep foo -- a.txt b.md",
            "grep -e foo -- a.txt",
            "grep -- foo",  # pattern only: reads stdin
        ],
    )
    def test_regular_file_operands_after_double_dash_stay_safe(self, command):
        assert _common._can_skip_review(command) is True


class TestParseVerdictStrictAllow:
    """ALLOW is the one verdict that runs a command unseen, so it must stand
    alone on its line. DENY and ASK stay permissive: they fail safe."""

    @pytest.mark.parametrize(
        "output",
        [
            "ALLOW is not appropriate here: the command pushes to production",
            "- ALLOW would be wrong: it deletes the branch",
            "ALLOW this only after checking the remote",
            "ALLOW: but the target directory is unverified",
            "ALLOW, although the script was not inspected",
            "ALLOWING the push would be a mistake",
            "ALLOW..",
        ],
    )
    def test_allow_followed_by_anything_else_is_not_an_allow(self, output):
        assert _common._parse_verdict(output) == "ASK"

    @pytest.mark.parametrize(
        "output",
        [
            "ALLOW",
            "  ALLOW  ",
            '"ALLOW"',
            "**ALLOW**",
            "`ALLOW`",
            "> ALLOW",
            "ALLOW.",
            "ALLOW。",
            "Looks read-only.\nALLOW",
            "ALLOW\r\n",
        ],
    )
    def test_standalone_allow_is_accepted(self, output):
        assert _common._parse_verdict(output) == "ALLOW"

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("DENY: rm -rf is dangerous", "DENY"),
            ("DENY because it exfiltrates", "DENY"),
            ("ASK the user first", "ASK"),
            ("ALLOW is wrong here\nDENY: pushes to main", "DENY"),
            ("ALLOW\nDENY: second thoughts", "DENY"),
            ("ASK is not needed\nALLOW", "ASK"),
        ],
    )
    def test_deny_and_ask_stay_permissive(self, output, expected):
        assert _common._parse_verdict(output) == expected


class TestGeminiResponseShapes:
    def test_all_text_parts_are_read(self, monkeypatch):
        verdict, _, _ = review(
            monkeypatch, text_body({"text": "ALLOW"}, {"text": "\nDENY: exfil"})
        )
        assert verdict == "DENY"

    def test_a_verdict_split_across_parts_is_joined(self, monkeypatch):
        verdict, _, _ = review(monkeypatch, text_body({"text": "AL"}, {"text": "LOW"}))
        assert verdict == "ALLOW"

    def test_thought_parts_are_not_a_verdict(self, monkeypatch):
        verdict, _, _ = review(
            monkeypatch,
            text_body(
                {"text": "DENY would be my first guess", "thought": True},
                {"text": "ALLOW"},
            ),
        )
        assert verdict == "ALLOW"

    def test_a_response_without_a_finish_reason_is_read_as_before(self, monkeypatch):
        verdict, _, _ = review(monkeypatch, text_body({"text": "ALLOW"}, finish=None))
        assert verdict == "ALLOW"

    @pytest.mark.parametrize(
        "body",
        [
            {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}},
            {"candidates": [{"finishReason": "SAFETY"}]},
            {"candidates": [{"finishReason": "RECITATION", "content": {"parts": []}}]},
        ],
    )
    def test_a_filter_block_asks_the_human_and_names_the_cause(self, monkeypatch, body):
        # Whoever controls the command text can trip a content filter, so a
        # block must not become ERROR ("no opinion"), which Codex alone resolves.
        verdict, output, calls = review(monkeypatch, body)
        assert verdict == "ASK"
        assert len(calls) == 1  # no retry on the fallback model either
        assert "declined" in output

    @pytest.mark.parametrize(
        "body",
        [
            text_body({"text": "ALLOW"}, finish="SAFETY"),
            text_body({"text": "ALLOW"}, finish="SOME_FUTURE_REASON"),
            text_body({"text": "ALLOW"}, promptFeedback={"blockReason": "OTHER"}),
            # Cut at the output limit: a half-written answer is not an approval.
            text_body({"text": "ALLOW"}, finish="MAX_TOKENS"),
        ],
    )
    def test_an_unfinished_answer_never_yields_allow(self, monkeypatch, body):
        verdict, _, _ = review(monkeypatch, body)
        assert verdict == "ASK"

    def test_an_unfinished_deny_is_still_a_deny(self, monkeypatch):
        verdict, _, _ = review(
            monkeypatch,
            text_body({"text": "DENY: the reason was cut he"}, finish="MAX_TOKENS"),
        )
        assert verdict == "DENY"

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"candidates": []},
            {"candidates": None},
            {"candidates": [None]},
            {"candidates": [{"content": None}]},
            {"candidates": [{"content": {"parts": None}}]},
            {"candidates": [{"content": {"parts": [{"text": ""}]}}]},
            {"candidates": [{"content": {"parts": [{"text": 7}]}}]},
            {"candidates": [{"content": {"parts": ["not-a-dict"]}}]},
            text_body({"text": "ALLOW", "thought": True}),
            ["not", "an", "object"],
        ],
    )
    def test_an_answer_without_a_verdict_asks_and_is_not_an_error(
        self, monkeypatch, body
    ):
        # ERROR means "Gemini unavailable", which a lone Codex ALLOW resolves.
        # An answer that arrived but says nothing keeps the human in the loop.
        verdict, output, calls = review(monkeypatch, body)
        assert verdict == "ASK"
        assert len(calls) == 1
        assert "no verdict" in output

    @pytest.mark.parametrize(
        "reply",
        [
            http.client.IncompleteRead(b""),
            http.client.RemoteDisconnected("closed"),
            ssl.SSLError("handshake"),
            OSError("connection reset"),
            urllib.error.URLError("dns"),
            b"not json",
        ],
    )
    def test_transport_and_decoding_failures_are_recoverable(self, monkeypatch, reply):
        verdict, _, calls = review(monkeypatch, reply)
        assert verdict == "ERROR"
        assert len(calls) == 2  # primary, then the fallback model

    def test_a_transport_failure_is_rescued_by_the_fallback(self, monkeypatch):
        verdict, _, calls = review(
            monkeypatch, http.client.IncompleteRead(b""), text_body({"text": "ALLOW"})
        )
        assert verdict == "ALLOW"
        assert len(calls) == 2


class TestGeminiApiKeyNeverLeaks:
    @pytest.mark.parametrize(
        "key", ["ab\rcd", "ab\ncd", "ab\x00cd", "ab\x7fcd", "キー"]
    )
    def test_an_unusable_key_is_an_error_without_a_request(self, monkeypatch, key):
        verdict, output, calls = review(
            monkeypatch, text_body({"text": "ALLOW"}), key=key
        )
        assert verdict == "ERROR"
        assert calls == []
        assert key not in output

    def test_surrounding_whitespace_is_stripped_at_the_entry_point(self, run_hook):
        calls: list = []
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ALLOW", calls=calls),
            env={"GEMINI_API_KEY": "test-api-key\r\n"},
        )
        assert res.decision == "allow"
        assert calls[0].get_header("X-goog-api-key") == "test-api-key"

    def test_codex_variant_strips_the_key_too(self, run_hook):
        calls: list = []
        res = run_hook(
            CODEX_HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ALLOW", calls=calls),
            env={"GEMINI_API_KEY": "test-api-key\r\n"},
        )
        assert res.exit_code == 0
        assert calls[0].get_header("X-goog-api-key") == "test-api-key"

    def test_a_key_with_an_inner_newline_never_reaches_the_reason(self, run_hook):
        # urlopen is not faked: a request would raise. Gemini is ERROR, so the
        # (faked) Codex verdict decides, and the key appears nowhere.
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            run=fake_run(stdout="ASK"),
            env={"GEMINI_API_KEY": "sentinel\rkey"},
        )
        assert res.decision == "ask"
        assert "sentinel" not in res.stdout


class TestHookErrorDoesNotEchoExceptionText:
    """An unexpected exception may carry anything in its message (http.client
    quotes the header value it rejected). Only its type and place are reported."""

    def test_claude_entry_reports_the_type_and_place_only(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(exc=RuntimeError("SENTINEL-SECRET")),
        )
        assert res.decision == "ask"
        assert "SENTINEL-SECRET" not in res.stdout
        assert "RuntimeError" in res.reason
        assert "_bash_review_common.py:" in res.reason

    def test_codex_entry_reports_the_type_and_place_only(self, run_hook):
        res = run_hook(
            CODEX_HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(exc=RuntimeError("SENTINEL-SECRET")),
        )
        assert res.exit_code == 2
        assert "SENTINEL-SECRET" not in res.stderr
        assert "RuntimeError" in res.stderr

    @pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
    def test_a_value_error_from_the_transport_is_not_quoted(self, run_hook, hook):
        boom = ValueError("Invalid header value b'SENTINEL-KEY\\r'")
        res = run_hook(hook, hook_payload("make build"), urlopen=fake_gemini(boom))
        assert "SENTINEL-KEY" not in res.stdout + res.stderr
        assert "ValueError" in res.stdout + res.stderr
        if hook == HOOK:
            assert res.decision == "ask"
        else:
            assert res.exit_code == 2


class TestCodexAllowlistGaps:
    @staticmethod
    def _reason(command, **kwargs):
        subs = _common._split_commands(command)
        return _common.codex_flag_ask_reason(subs, command, **kwargs)

    @pytest.mark.parametrize(
        "command",
        [
            # the environment is changed by another command, not by a prefix
            "export CODEX_HOME=/tmp/evil; codex exec -s read-only 'x'",
            "export CODEX_HOME=/tmp/evil && codex exec -s read-only 'x'",
            "CODEX_HOME=/tmp/evil; codex exec -s read-only 'x'",
            "declare -x CODEX_HOME=/tmp/evil; codex exec -s read-only 'x'",
            "typeset -x CODEX_HOME=/tmp/evil; codex exec -s read-only 'x'",
            "set -a; codex exec -s read-only 'x'",
            "set -o allexport\ncodex exec -s read-only 'x'",
            "export CODEX_HOME=/tmp/evil\ncodex exec -s read-only 'x'",
            # `--` ends the flags, not the shell's expansions and redirects
            'codex exec -s read-only -- "$(cat notes.txt)"',
            "codex exec -s read-only -- `cat notes.txt`",
            "codex exec -s read-only -- {a,-s} danger-full-access",
            "codex exec -s read-only -- - < /p",
            # stdin is read even when a prompt is given on argv
            "codex exec -s read-only 'review this' < prompt.md",
            "codex exec -s read-only 'review this' 0<&3",
            "cat prompt.md | codex exec -s read-only 'review this'",
            "cat prompt.md |& codex exec -s read-only 'review this'",
            "codex exec -s read-only 'review' <<< \"$(cat prompt.md)\"",
        ],
    )
    def test_forces_ask(self, command):
        assert self._reason(command)

    @pytest.mark.parametrize(
        "command",
        [
            "export EDITOR_AI_ONESHOT=1; codex exec -s read-only 'x'",
            "PYTHONPATH=. python3 t.py && codex exec -s read-only 'x'",
            "set -e; codex exec -s read-only 'x'",
            "set -euo pipefail; codex exec -s read-only 'x'",
            "cd /r && codex exec -s read-only 'x'",
            "codex exec -s read-only -- 'a plain prompt'",
            "python3 secret_scan.py < /p && codex exec -s read-only 'review' < /p",
            "codex exec -s read-only 'review' <<< 'inline text'",
            # a pipe elsewhere in the command does not feed codex
            "ls | wc -l; codex exec -s read-only 'x'",
            "codex exec -s read-only 'x' | tee /tmp/out.txt",
            # stderr folded into stdout is not an input
            "codex exec -s read-only 'x' > /tmp/out.txt 2>&1",
            # the export sits in text the shell never runs
            "git commit -m \"$(cat <<'EOF'\nfix: x\n\nexport CODEX_HOME=/x; codex exec "
            'is now confirmed\nEOF\n)"',
        ],
    )
    def test_does_not_force_ask(self, command):
        assert self._reason(command) == ""


class TestCodexReasonCarriesNoRawTokens:
    @staticmethod
    def _reason(command, **kwargs):
        subs = _common._split_commands(command)
        return _common.codex_flag_ask_reason(subs, command, **kwargs)

    @pytest.mark.parametrize(
        ("command", "raw"),
        [
            ("codex 'use the staging key to call | the api'", "staging key"),
            (f"codex {FAKE_OPAQUE_VALUE}", FAKE_OPAQUE_VALUE),
            ("codex exec -s 'a | b' x", "a | b"),
            (f"codex exec -s {FAKE_OPAQUE_VALUE} x", FAKE_OPAQUE_VALUE),
            ("codex exec -c 'we ird | key=1' x", "we ird"),
            (f"codex exec -c {FAKE_OPAQUE_VALUE}=1 x", FAKE_OPAQUE_VALUE),
            ("codex exec '--odd | flag' x", "odd | flag"),
            (f"T{FAKE_OPAQUE_VALUE}=1 codex exec -s read-only x", FAKE_OPAQUE_VALUE),
        ],
    )
    def test_a_token_that_is_not_a_plain_name_is_not_quoted(self, command, raw):
        reason = self._reason(command)
        assert reason
        assert raw not in reason
        assert "|" not in reason
        assert "\n" not in reason

    @pytest.mark.parametrize(
        ("command", "shown"),
        [
            ("codex exec --add-dir /etc x", "--add-dir"),
            ("codex exec -p evil x", "'-p'"),
            ("codex exec -s danger-full-access x", "danger-full-access"),
            ("codex exec -c approval_policy=never x", "approval_policy"),
            ("codex exec -c 'mcp_servers.x.command=\"sh\"' x", "mcp_servers.x.command"),
            ("codex login", "login"),
            ("CODEX_HOME=/x codex exec -s read-only x", "CODEX_HOME"),
        ],
    )
    def test_a_plain_name_is_still_named(self, command, shown):
        assert shown in self._reason(command)

    def test_redact_replaces_the_reason_with_a_fixed_sentence(self):
        command = f"codex exec -s {FAKE_GITHUB_TOKEN} x"
        reason = self._reason(command, redact=True)
        assert reason
        assert FAKE_GITHUB_TOKEN not in reason
        assert reason == self._reason("codex login", redact=True)

    @pytest.mark.parametrize(
        "command",
        [
            "codex exec -s read-only 'x'",
            "codex --version",
            "echo codex exec --add-dir /etc",
        ],
    )
    def test_redact_never_turns_an_allowed_shape_into_an_ask(self, command):
        assert self._reason(command, redact=True) == ""

    def test_claude_entry_keeps_a_detected_credential_out_of_reason_and_log(
        self, run_hook
    ):
        res = run_hook(
            HOOK, hook_payload(f"codex 'call the api with {FAKE_GITHUB_TOKEN}'")
        )
        assert res.decision == "ask"
        assert "codex" in res.reason
        assert FAKE_GITHUB_TOKEN not in res.stdout
        log = (res.home / ".claude" / "logs" / "bash-review.log").read_text()
        assert "ASK" in log
        assert FAKE_GITHUB_TOKEN not in log

    def test_codex_entry_keeps_a_detected_credential_out_of_stderr_and_log(
        self, run_hook
    ):
        res = run_hook(CODEX_HOOK, hook_payload(f"codex exec -s {FAKE_GITHUB_TOKEN} x"))
        assert res.exit_code == 2
        assert "codex" in res.stderr
        assert FAKE_GITHUB_TOKEN not in res.stderr
        logs = [p for p in res.home.rglob("*.log") if p.is_file()]
        assert logs
        assert all(FAKE_GITHUB_TOKEN not in p.read_text() for p in logs)


class TestSecretScanGhSecretBody:
    @pytest.mark.parametrize(
        "command",
        [
            f"gh secret set MY_TOKEN --body '{FAKE_OPAQUE_VALUE}'",
            f"gh secret set DEPLOY_HOOK --body {FAKE_OPAQUE_VALUE}",
            f"gh secret set DEPLOY_HOOK -b {FAKE_OPAQUE_VALUE}",
            f"gh secret set DEPLOY_HOOK --body={FAKE_OPAQUE_VALUE}",
            f"gh secret set DEPLOY_HOOK -b{FAKE_OPAQUE_VALUE}",
            # flags before the name, and between the name and --body
            f"gh secret set --body {FAKE_OPAQUE_VALUE} DEPLOY_HOOK",
            f'gh secret set DEPLOY_HOOK --repo o/r --body "{FAKE_OPAQUE_VALUE}"',
            # a quoted value with spaces is one value
            "gh secret set DEPLOY_HOOK --body 'four words of it'",
            # glued with =, a leading dash cannot be the next flag
            f"gh secret set DEPLOY_HOOK --body=-{FAKE_OPAQUE_VALUE}",
        ],
    )
    def test_a_literal_body_is_a_credential(self, command):
        found, label = _common.scan_secrets(command, {"command": command})
        assert found is True
        assert label
        assert FAKE_OPAQUE_VALUE not in label

    @pytest.mark.parametrize(
        "command",
        [
            'gh secret set DEPLOY_HOOK --body "$DEPLOY_HOOK"',
            "gh secret set DEPLOY_HOOK --body $DEPLOY_HOOK",
            'gh secret set DEPLOY_HOOK --body="$DEPLOY_HOOK"',
            "gh secret set DEPLOY_HOOK --body --repo o/r",
            "gh secret set DEPLOY_HOOK < value.txt",
            # prose that documents the shape, with a placeholder for the value
            "git commit -m 'docs: gh secret set NAME --body VALUE is now scanned'",
            "echo 'usage: gh secret set NAME -b VALUE'",
            "gh secret list --repo o/r",
            "gh pr create --body 'Fixes the deploy hook handling'",
            "gh issue comment 12 -b 'looks good to me overall'",
        ],
    )
    def test_references_and_other_gh_commands_are_not(self, command):
        assert _common.scan_secrets(command, {"command": command}) == (False, "")


class TestSummaryLogIsOneLinePerDecision:
    MARK = "⏎"

    @staticmethod
    def _write(tmp_path, command, reason):
        path = tmp_path / "bash-review.log"
        _common.log_summary(str(path), command, "ASK", "codex", reason)
        return path.read_text(encoding="utf-8")

    def test_a_multi_line_command_cannot_forge_a_second_entry(self, tmp_path):
        forged = "echo hi\n[2026-11-11 10:00:00] ALLOW | pre      | x"
        text = self._write(tmp_path, forged, "gemini=ASK, codex=ASK")
        assert text.count("\n") == 1
        assert text.endswith("| gemini=ASK, codex=ASK\n")
        assert f"echo hi{self.MARK}[2026-11-11" in text

    def test_carriage_returns_are_replaced_too(self, tmp_path):
        text = self._write(tmp_path, "echo a\r\necho b", "r")
        assert "\r" not in text
        assert text.count("\n") == 1
        assert f"echo a{self.MARK}{self.MARK}echo b" in text

    def test_the_replacement_is_one_character_for_one(self, tmp_path):
        # The reader matches "first 80 characters + ..." against the detail log,
        # so the mark must not change how many characters a command has.
        command = "a\n" * 60
        text = self._write(tmp_path, command, "r")
        logged = text.split(" | ")[2]
        assert logged == (self.MARK.join(["a"] * 40) + self.MARK) + "..."
        assert len(logged) == 80 + 3

    def test_the_reason_cannot_move_the_last_separator(self, tmp_path):
        text = self._write(tmp_path, "codex x", "first | second\nthird")
        assert text.count("\n") == 1
        # decision | stage | command | reason -- and nothing after the reason.
        assert text.rstrip("\n").split(" | ")[3:] == [f"first / second{self.MARK}third"]


class TestNothingThatAskedBeforeAllowsNow:
    """Inputs an independent review found had moved from ask to allow (or to
    ERROR, which a lone Codex ALLOW resolves) in the first cut of this change."""

    @pytest.mark.parametrize("output", ["ALLOW_", "_ALLOW_", "ALLOW_OK", "ALLOW*x"])
    def test_allow_keeps_its_word_boundary(self, output):
        assert _common._parse_verdict(output) == "ASK"

    def test_a_run_of_decoration_does_not_make_the_parser_quadratic(self):
        hostile = "ALLOW" + "*" * 200_000 + "X"
        started = time.monotonic()
        assert _common._parse_verdict(hostile) == "ASK"
        assert time.monotonic() - started < 1.0

    @pytest.mark.parametrize(
        "parts",
        [
            [None, {"text": "ALLOW"}],
            ["ALLOW", {"text": "ALLOW"}],
            [{"text": 7}, {"text": "ALLOW"}],
            [{"inlineData": {"data": "x"}}, {"text": "ALLOW"}],
            [{"text": "ALLOW"}, None],
        ],
    )
    def test_a_malformed_part_beside_an_allow_is_not_an_allow(self, monkeypatch, parts):
        body = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
        verdict, output, calls = review(monkeypatch, body)
        assert verdict == "ASK"
        assert len(calls) == 1
        assert "malformed" in output

    def test_a_malformed_part_does_not_hide_a_deny(self, monkeypatch):
        body = {"candidates": [{"content": {"parts": [None, {"text": "DENY: x"}]}}]}
        verdict, _, _ = review(monkeypatch, body)
        assert verdict == "DENY"

    def test_an_unspecified_finish_reason_is_not_a_finished_answer(self, monkeypatch):
        verdict, _, _ = review(
            monkeypatch,
            text_body({"text": "ALLOW"}, finish="FINISH_REASON_UNSPECIFIED"),
        )
        assert verdict == "ASK"

    def test_a_body_that_is_not_utf8_asks_and_is_not_an_error(self, monkeypatch):
        verdict, output, calls = review(monkeypatch, b"\xff\xfe not utf-8")
        assert verdict == "ASK"
        assert len(calls) == 1
        assert "no verdict" in output


class TestCodexAllowlistGapsSecondPass:
    @staticmethod
    def _reason(command):
        subs = _common._split_commands(command)
        return _common.codex_flag_ask_reason(subs, command)

    @pytest.mark.parametrize(
        "command",
        [
            # the exporting builtin behind a wrapper
            "command export CODEX_HOME=/tmp/evil; codex exec -s read-only x",
            "builtin export CODEX_HOME=/tmp/evil; codex exec -s read-only x",
            "FOO=1 export CODEX_HOME=/tmp/evil; codex exec -s read-only x",
            # code whose effect on the environment cannot be read
            'eval "export CODEX_HOME=/tmp/evil"; codex exec -s read-only x',
            "source ./env.sh && codex exec -s read-only x",
            ". ./env.sh; codex exec -s read-only x",
            # a pipe into codex across a line continuation
            "cat prompt.md | \\\ncodex exec -s read-only 'review this'",
            "cat prompt.md |\\\n  codex exec -s read-only 'review this'",
        ],
    )
    def test_forces_ask(self, command):
        assert self._reason(command)

    @pytest.mark.parametrize(
        "command",
        [
            # printing variables changes nothing
            "declare -p PATH; codex exec -s read-only 'x'",
            "export -p; codex exec -s read-only 'x'",
            "typeset -p HOME && codex exec -s read-only 'x'",
        ],
    )
    def test_does_not_force_ask(self, command):
        assert self._reason(command) == ""


def test_overlapping_separators_in_a_reason_leave_none_behind(tmp_path):
    path = tmp_path / "bash-review.log"
    _common.log_summary(str(path), "codex x", "ASK", "codex", "first | | second")
    line = path.read_text(encoding="utf-8").rstrip("\n")
    assert len(line.split(" | ")) == 4, line


class TestStdinRulesAreIndependent:
    """Every `< FILE` must have been scanned (no exemption, not even /dev/null),
    a heredoc is checked whatever else is redirected, and a redirect on another
    descriptor is not stdin."""

    @staticmethod
    def _reason(command):
        subs = _common._split_commands(command)
        return _common.codex_flag_ask_reason(subs, command)

    @pytest.mark.parametrize(
        "command",
        [
            # the heredoc is the later redirect, so it is what codex reads
            "codex exec -s read-only - < /dev/null <<EOF\n$(cat /etc/hosts)\nEOF",
            "codex exec -s read-only - 3< /dev/null <<EOF\n$(cat /etc/hosts)\nEOF",
            # fd 3 is not stdin: the pipe still feeds the prompt
            "cat prompt.md | codex exec -s read-only - 3< /dev/null",
            "codex exec -s read-only - 3< /dev/null",
            "codex exec -s read-only - 3< prompt.md",
            # a second, unscanned file beside /dev/null
            "codex exec -s read-only - < /dev/null < prompt.md",
            # /dev/null gets no exemption: the tokens carry no quoting, so a quoted
            # argument that merely looks like the redirect would earn it too
            "codex exec -s read-only 'x' < /dev/null",
            "codex exec -s read-only - < /dev/null",
            "cat prompt.md | codex exec -s read-only '</dev/null'",
            # stdin re-pointed with an output operator
            "codex exec -s read-only - < /dev/null 0>&3",
            "codex exec -s read-only 'x' 0>&3",
            # a duplicated descriptor cannot be traced to a file
            "codex exec -s read-only 'x' 0<&3",
            "codex exec -s read-only - <&3",
        ],
    )
    def test_forces_ask(self, command):
        assert self._reason(command)

    @pytest.mark.parametrize(
        "command",
        [
            "codex exec -s read-only 'x' 2>&1",
            "codex exec -s read-only 'x' > /tmp/out.txt 2> /tmp/err.txt",
            "codex exec -s read-only - <<EOF\nplain text\nEOF",
            "python3 secret_scan.py < /p && codex exec -s read-only - < /p 2> /tmp/e",
        ],
    )
    def test_does_not_force_ask(self, command):
        assert self._reason(command) == ""


class TestSecretScanGhSecretBodyStaysOnItsLine:
    @pytest.mark.parametrize(
        "command",
        [
            # the flag belongs to the next command, on the next line
            "gh secret set FOO\ngh issue comment 3 -b 'thanks for the report'",
            "gh secret set FOO < v.txt\ngh pr create --body 'adds the deploy hook'",
            # a quoted value built from a variable is a reference
            'gh secret set DEPLOY_HOOK --body "prefix-$DEPLOY_HOOK"',
            'gh secret set DEPLOY_HOOK --body="${DEPLOY_HOOK}-suffix"',
        ],
    )
    def test_not_a_credential(self, command):
        assert _common.scan_secrets(command, {"command": command}) == (False, "")


@pytest.mark.parametrize(
    "line",
    [
        " " * 20_000 + " ALLOW  x",
        " " * 20_000 + "X",
        "*" * 20_000 + " " * 20_000 + "x",
        "> " * 10_000 + "ALLOW x",
    ],
)
def test_the_verdict_parser_is_linear_in_leading_padding(line):
    started = time.monotonic()
    assert _common._parse_verdict(line) == "ASK"
    assert time.monotonic() - started < 0.5


# ---------------------------------------------------------------------------
# Bash writes to the harness's own configuration (settings / hooks / mods / the
# Codex hooks and home config / the instruction files read into every session:
# CLAUDE.md and its rules, Codex AGENTS.md, Gemini GEMINI.md) are asked
# deterministically, before any model is consulted. A single Gemini ALLOW must not
# be enough to let a command rewrite the gate that reviews it. Reads of the same
# paths stay on their usual path: the check recognises write FORMS, not mentions.
# ---------------------------------------------------------------------------

HARNESS_REDIRECT_CASES = [
    "echo '{}' > .claude/settings.json",
    "echo x>>.claude/settings.local.json",
    "printf x >| .codex/hooks/a.sh",
    "ls &> .claude/hooks/log",
    "make 2>.claude/CLAUDE.md",
    "true;>.claude/mods/x",
    "echo x >& .claude/settings.json",
    'echo x > "$HOME/.codex/config.toml"',
    "echo x > ${HOME}/.codex/auth.json",
    "echo x > ~/.codex/c.toml",
    "echo x > ~/.claude/settings.json",
    "echo x > ./.claude/hooks/x.py",
    "echo x > .Claude/Settings.json",  # case-insensitive filesystems
    "echo x > .claude/hooks/*.py",
    "echo x > ~/.cl*/settings.json",
    "echo x > ~/.cl*/hooks/x",
    "echo x > $D/settings.json",
    "cat > .claude/hooks/n.py <<'EOF'\nprint(1)\nEOF",
    "cat x > \\\n  .claude/settings.json",  # line continuation between `>` and target
    # instruction files read into every session (Claude rules, Codex, Gemini)
    "echo x >> .claude/rules/security.md",
    "echo x > ~/.claude/rules/new.md",
    "echo x > .codex/AGENTS.md",
    "echo x >> ~/.gemini/GEMINI.md",
    "echo x > .claude/rules/*.md",
    # expansions that can land on one: these go through the dynamic check, not the
    # literal path pattern, so each pins one name in it
    "echo x > ~/.cl*/rules/x",
    "echo x > ~/.co*/AGENTS.md",
    "echo x > ~/.gem*/GEMINI.md",
    "rm ~/.gemini/*",
    "echo 'x > .claude/settings.json",  # unparsable: conservative fallback
]

HARNESS_WRITER_CASES = [
    "tee -a .claude/settings.json",
    "echo x | tee .claude/hooks/x.py",
    "rm .claude/hooks/x.py",
    "rm -rf .claude",
    "truncate -s 0 .claude/CLAUDE.md",
    "touch .codex/hooks/x",
    "chmod +x .claude/hooks/x.sh",
    "mv .claude/hooks/a.py /tmp/",
    "cp /tmp/x .claude/settings.json",
    "cp /tmp/settings.json ~/.claude/",
    "cp -t .claude/hooks a b",
    "cp -rt .claude/hooks a b",
    "cp -at .claude/hooks a b",
    "cp -t.claude/hooks a",
    "install -Dt .claude/hooks a",
    "install --target-directory=.claude/mods x",
    "ln --target-directory .claude/hooks x",
    "rsync -a s/ ~/.codex/",
    "ln -sf /tmp/e .claude/hooks/x.py",
    "sed -i 's/a/b/' .claude/settings.json",
    "sed -Ei 's/a/b/' .claude/settings.json",
    "sed -i.bak s/a/b/ .claude/hooks/x.sh",
    "sed -i '' s/a/b/ .claude/hooks/x.sh",
    "gsed --in-place s/a/b/ .codex/hooks/x",
    "perl -pi -e 's/a/b/' .claude/hooks/x.py",
    "env FOO=1 timeout 5 rm .claude/hooks/x",
    "sed -i 's/a/b/' .claude/rules/git-workflow.md",
    "rm .claude/rules/security.md",
    "cp /tmp/x .codex/AGENTS.md",
    "cp /tmp/GEMINI.md ~/.gemini/",
    "dd if=/dev/zero of=.claude/settings.json",
]

# Shell code is itself a command line, so the same write forms are found inside it.
HARNESS_SHELL_CODE_CASES = [
    "bash -c 'echo x > .claude/hooks/a'",
    'sh -c "sed -i s/a/b/ .claude/settings.json"',
    # multi-line code strings (masked out of the live text) are read from the raw form
    "bash -c 'echo hi\nrm .claude/hooks/x'",
    'bash -c "echo hi\necho x > .claude/settings.json"',
    # the code is the word after the flag carrying `c`, not every non-flag word
    "bash -o pipefail -c 'rm .claude/hooks/x'",
    "bash -O extglob -c 'rm .claude/hooks/x'",
    # positional arguments after the code word are the code's $0 / $1 / $@
    "bash -c 'rm \"$1\"' _ .claude/hooks/x",
    "sh -c 'rm \"$@\"' sh .claude/hooks/x",
    "xargs sh -c 'rm \"$@\"' _ .claude/hooks/x",
    "bash -c 'echo hi\nrm .claude/hooks/x' _ arg",  # multi-line code with positionals
    'eval "rm .claude/hooks/x"',
    "awk '{print > \".claude/settings.json\"}' in",
    "bash <<'EOF'\nrm .claude/hooks/x.py\nEOF",
    "source /dev/stdin <<'EOF'\ntouch .claude/hooks/x\nEOF",
]

# Code in another language cannot be told apart statically (read or write?), and
# replaying the real log showed these were almost always hook-source analysis. They
# are NOT asked here; they stay on the two-model AND gate, which they reach as
# inline code / stdin interpreters.
HARNESS_FOREIGN_CODE_CASES = [
    "python3 -c \"open('.claude/settings.json','w')\"",
    "node -e \"fs.rmSync('.codex/hooks')\"",
    "python3 - <<'EOF'\nimport pathlib\npathlib.Path('.claude/settings.json').write_text('{}')\nEOF",
    "python3 -c '\nimport pathlib\npathlib.Path(\".claude/hooks/x.py\").unlink()\n'",
    "python3 -c \"json.load(open('.claude/settings.json'))\"",
]

HARNESS_NOT_A_WRITE_CASES = [
    # reads
    "sed -n 1,5p .claude/hooks/x.py",
    "git diff -- .claude/settings.json",
    "cat .claude/settings.json | jq .",
    "cat < .claude/settings.json | jq .",
    "cat .claude/settings.json > /tmp/x",
    "cp .claude/settings.json /tmp/x",
    "ln -s .claude/hooks/x.py /tmp/l",
    # a positional argument handed to code that only reads it
    "bash -c 'cat \"$1\"' _ .claude/settings.json",
    # with -t, every operand is a source and only the -t value is the destination
    "cp -t /tmp .claude/settings.json",
    "cp --target-directory=/tmp/bk .claude/settings.json",
    "cp -t/tmp/x .claude/hooks/a.py",
    # reference / value arguments are read, not written
    "touch -r .claude/settings.json /tmp/x",
    "perl -Mstrict -ne 'print' .claude/hooks/x.py",
    # a glob under an unprotected .claude subdirectory cannot expand to a protected path
    "rm -f .claude/skills/x/__pycache__/*.pyc",
    "rm -f .codex/agents/*.toml",
    # `hooks` / `mods` outside the harness (React hooks, git hooks)
    "echo x > src/hooks/*.ts",
    "rm -f src/hooks/*.test.ts",
    "cp tpl src/hooks/use$NAME.ts",
    'tee "$(git rev-parse --git-dir)/hooks/pre-commit"',
    "rsync -t .claude/hooks/ /tmp/h/",
    "cat .claude/settings.json | python3 -c 'import json,sys; json.load(sys.stdin)'",
    # the protected path is a sed/awk script word or awk input, not a target
    "sed -i 's#.claude/hooks#x#' README.md",
    "sed -i -e 's#.claude/hooks#x#' README.md",
    "awk '{print $1}' .claude/settings.json",
    "awk -f p.awk .claude/settings.json",
    # running (not writing) a hook, or sourcing it
    "python3 .claude/hooks/bash-review.py < p.json",
    "source .claude/hooks/_hook_common.sh && hook_log x",
    "diff <(git show HEAD:.claude/settings.json) .claude/settings.json",
    # fd duplication and harmless targets
    "echo x 2>&1",
    "echo x >&2",
    "echo x > /dev/null",
    'echo x > "$OUT"',
    # look-alikes outside the protected set
    "echo x > .claude/agents/x.md",
    "echo x > .claude/hooks-old/x",
    "echo x > .claude/settings.json.bak",
    "echo x > .codex/config.toml",
    "echo x > .claude/rules-old/x.md",
    "echo x > .codex/AGENTS.md.bak",
    "echo x > .gemini/GEMINI.md.bak",
    "rm src/rules/*.ts",
    "echo x > $D/AGENTS.md",  # a variable-held AGENTS.md is usually a project's own
    "echo x > .codex/agents/planner.toml",
    "echo x > AGENTS.md",  # a project's own AGENTS.md at the repo root
    "echo x > GEMINI.md",
    "echo x > docs/rules/x.md",
    "sed -n 1,5p .claude/rules/security.md",
    "cat .codex/AGENTS.md | wc -l",
    "{ ls; }",
    "echo {a,b}",
    "ls # > .claude/settings.json",
    # data that merely mentions a write (commit messages, docs)
    "git commit -m \"$(cat <<'EOF'\nrm .claude/hooks/old.py\nEOF\n)\"",
    "git commit -F - <<'EOF'\ntee .claude/settings.json\nEOF",
    'git commit -m "fix\n\nrm .claude/hooks/x.py gone"',
    "cat > notes.md <<'EOF'\necho x > .claude/settings.json\nEOF",
    # a python heredoc is not read as shell even though it is code on stdin
    "python3 - <<'EOF'\nprint(a > \".claude/settings.json\")\nEOF",
]

# Accepted residuals, documented as known gaps in hooks/README.md. Pinned so that
# closing one later is a visible, deliberate edit to this list.
HARNESS_WRITE_RESIDUAL_CASES = [
    "cd .claude/hooks && echo x > a.py",
    "git checkout -- .claude/hooks/x.py",
    "tar -C .claude/hooks -xf a.tar",
    "x=.claude/settings.json; tee $x",
    "echo x > $D/hooks/x",
    # shell code that arrives on stdin as a single word (they reach the AND gate as
    # stdin interpreters)
    "echo 'rm .claude/hooks/x' | sh",
    "printf 'rm .claude/hooks/x' | bash",
    "bash <<< 'rm .claude/hooks/x'",
]


def _harness_reason(command: str) -> str:
    return _common.harness_write_ask_reason(_common._split_commands(command), command)


class TestHarnessConfigWriteAsks:
    @pytest.mark.parametrize(
        "command",
        HARNESS_REDIRECT_CASES + HARNESS_WRITER_CASES + HARNESS_SHELL_CODE_CASES,
    )
    def test_write_forms_are_asked(self, command):
        assert _harness_reason(command) != "", command

    @pytest.mark.parametrize("command", HARNESS_FOREIGN_CODE_CASES)
    def test_foreign_code_is_left_to_the_dual_review_gate(self, command):
        sub = _common._split_commands(command)
        assert _harness_reason(command) == "", command
        assert _common.classify_high_risk(sub, command) != "", command

    def test_shell_code_recursion_is_bounded(self):
        # Three levels of `sh -c` nesting: the third is not unwrapped (it still
        # reaches the AND gate as `sh -c`), so a pathological nesting cannot make
        # the deterministic tier recurse without bound.
        nested = 'sh -c "sh -c \'sh -c \\"echo x > .claude/settings.json\\"\'"'
        assert _harness_reason(nested) == ""
        sub = _common._split_commands(nested)
        assert _common.classify_high_risk(sub, nested) != ""

    @pytest.mark.parametrize("command", HARNESS_NOT_A_WRITE_CASES)
    def test_reads_and_inert_text_are_not_asked(self, command):
        assert _harness_reason(command) == "", command

    @pytest.mark.parametrize("command", HARNESS_WRITE_RESIDUAL_CASES)
    def test_residual_forms_are_pinned(self, command):
        assert _harness_reason(command) == "", (
            f"{command!r} is now asked -- if that is intentional, move it out of "
            "HARNESS_WRITE_RESIDUAL_CASES and drop it from the README's known gaps"
        )

    def test_absolute_home_codex_path_is_recognised(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/someone")
        command = "echo x > /Users/someone/.codex/config.toml"
        assert _harness_reason(command) != ""

    def test_reason_is_fixed_vocabulary(self):
        # The reason reaches the dialog and the summary log; it must not carry
        # command text (a credential on the same line would leak through it).
        reason = _harness_reason(f"echo {FAKE_GITHUB_TOKEN} > .claude/settings.json")
        assert FAKE_GITHUB_TOKEN not in reason
        assert reason.endswith("— confirm manually")

    def test_claude_entry_asks_without_consulting_a_model(self, run_hook):
        # urlopen / run are not faked: a model call would raise, so an "ask" here
        # proves the decision was made deterministically.
        res = run_hook(HOOK, hook_payload("echo x > .claude/settings.json"))
        assert res.decision == "ask"
        # The exact phrase, not just "harness": a crash in the hook also ends in
        # "ask" (fail-closed) with an error reason that may contain the word.
        assert "writes to the agent harness configuration" in res.reason
        assert "hook error" not in res.reason

    def test_codex_entry_blocks_without_consulting_a_model(self, run_hook):
        res = run_hook(CODEX_HOOK, hook_payload("sed -i s/a/b/ .claude/settings.json"))
        assert res.exit_code == 2
        assert "writes to the agent harness configuration" in res.stderr
        assert "hook error" not in res.stderr
        assert res.stdout == ""

    @pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
    @pytest.mark.parametrize(
        "command",
        [
            "sed -n 1,5p .claude/hooks/x.py | wc -l",
            "git commit -F - <<'EOF'\ntee .claude/settings.json\nEOF",
        ],
    )
    def test_reads_keep_their_usual_path(self, run_hook, hook, command):
        res = run_hook(hook, hook_payload(command), urlopen=fake_gemini("ALLOW"))
        if hook == HOOK:
            assert res.decision == "allow", (res.decision, res.reason)
        else:
            assert res.exit_code == 0, res.stderr

    def test_deny_still_wins_over_the_harness_ask(self, run_hook):
        res = run_hook(HOOK, hook_payload("curl x > .claude/settings.json"))
        assert res.decision == "deny"

    def test_secret_on_the_line_is_redacted_from_the_reason(self, run_hook):
        res = run_hook(
            HOOK, hook_payload(f"echo {FAKE_GITHUB_TOKEN} > .claude/settings.json")
        )
        assert res.decision == "ask"
        assert FAKE_GITHUB_TOKEN not in res.reason
        assert FAKE_GITHUB_TOKEN not in res.stderr
