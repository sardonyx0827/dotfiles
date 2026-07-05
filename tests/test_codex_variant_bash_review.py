"""Tests for .codex/hooks/bash-review.py.

Codex does not understand permissionDecision JSON, so the Codex variant
communicates only via exit codes (same contract as the other .codex
hooks): allow paths exit 0 silently, blocking paths (ask/deny) exit 2
with the reason on stderr. Nothing is ever printed to stdout.
"""

from conftest import fake_gemini, fake_run, hook_payload

HOOK = ".codex/hooks/bash-review.py"


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
