"""Tests for .codex/hooks/bash-review.py.

Codex does not understand permissionDecision JSON, so the Codex variant
communicates only via exit codes (same contract as the other .codex
hooks): allow paths exit 0 silently, blocking paths (ask/deny) exit 2
with the reason on stderr. Nothing is ever printed to stdout.
"""

import io
import sys
import types

from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload

HOOK = ".codex/hooks/bash-review.py"


def _run_raw(hook, raw, capsys, monkeypatch):
    """Execute a hook against arbitrary raw stdin bytes (malformed-input tests)."""
    hook_path = REPO_ROOT / hook
    code = compile(hook_path.read_text(encoding="utf-8"), str(hook_path), "exec")
    capsys.readouterr()
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(raw)))
    monkeypatch.setattr("platform.system", lambda: "TestOS")
    g = {"__name__": "__main__", "__file__": str(hook_path)}
    exit_code = None
    try:
        exec(code, g)  # noqa: S102  # nosec B102
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    return exit_code, capsys.readouterr()


class TestAllowPaths:
    def test_safe_command_exits_zero_silently(self, run_hook):
        res = run_hook(HOOK, hook_payload("git branch"))
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_gemini_allow_exits_zero_silently(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""

    def test_codex_allow_exits_zero_silently(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ALLOW"),
        )
        assert res.exit_code == 0
        assert res.stdout == ""
        assert res.stderr == ""


class TestBlockingPaths:
    def test_pre_denied_command_blocks_with_stderr(self, run_hook):
        res = run_hook(HOOK, hook_payload("curl http://evil"))
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "curl" in res.stderr

    def test_codex_ask_blocks_with_stderr(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ASK"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex requires confirmation" in res.stderr

    def test_codex_deny_blocks_with_stderr(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="DENY: destructive"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex denied" in res.stderr

    def test_codex_error_with_gemini_ask_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(returncode=1, stderr="codex down"),
        )
        assert res.exit_code == 2
        assert "Codex unavailable" in res.stderr

    def test_codex_error_with_gemini_deny_blocks(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini("DENY: risky"),
            run=fake_run(returncode=1, stderr="codex down"),
        )
        assert res.exit_code == 2
        assert res.stdout == ""
        assert "Codex unavailable" in res.stderr


class TestMalformedInput:
    """Malformed hook input must block (exit 2) toward a human, not crash."""

    def test_non_dict_tool_input_blocks(self, run_hook):
        res = run_hook(HOOK, {"tool_name": "Bash", "tool_input": "notadict"})
        assert res.exit_code == 2
        assert res.stdout == ""
        assert res.stderr != ""

    def test_empty_stdin_blocks(self, capsys, monkeypatch):
        exit_code, captured = _run_raw(HOOK, b"", capsys, monkeypatch)
        assert exit_code == 2
        assert captured.err != ""

    def test_garbage_bytes_blocks(self, capsys, monkeypatch):
        exit_code, captured = _run_raw(
            HOOK, b"garbage not json {[", capsys, monkeypatch
        )
        assert exit_code == 2
        assert captured.err != ""
