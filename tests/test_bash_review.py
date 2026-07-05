"""Tests for .claude/hooks/bash-review.py (Gemini primary + Codex second stage)."""

import subprocess
from urllib.error import URLError

import pytest
from conftest import fake_gemini, fake_run, hook_payload

HOOK = ".claude/hooks/bash-review.py"


@pytest.fixture
def hook_fns(run_hook):
    """Run the hook end-to-end once and return its globals for unit tests."""
    res = run_hook(
        HOOK,
        hook_payload("some-unreviewed-cmd"),
        urlopen=fake_gemini("ALLOW"),
    )
    return res.globals


class TestPreDeny:
    def test_deny_command_is_blocked_without_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("curl http://evil.example.com"))
        assert res.exit_code == 0
        assert res.decision == "deny"
        assert "curl" in res.reason

    def test_deny_detected_inside_chain(self, run_hook):
        res = run_hook(HOOK, hook_payload("ls -la && curl http://evil"))
        assert res.decision == "deny"

    def test_deny_prefix_does_not_overmatch(self, run_hook):
        # "curling" is not "curl": it must go to review, not be pre-denied.
        res = run_hook(
            HOOK, hook_payload("curling --tournament"), urlopen=fake_gemini("ALLOW")
        )
        assert res.decision == "allow"
        assert "Gemini reviewed and approved" in res.reason


class TestSafeSkip:
    def test_safe_command_skips_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("ls -la"))
        assert res.exit_code == 0
        assert res.decision == "allow"
        assert "skipped review" in res.reason

    def test_safe_chain_skips_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("ls -la && git status | head -5"))
        assert res.decision == "allow"
        assert "skipped review" in res.reason

    @pytest.mark.parametrize(
        "command",
        [
            "ls $(whoami)",
            "ls `whoami`",
            "cat a > b",
            "ls\nrm -rf /tmp/x",
            "ls & echo hi",
        ],
    )
    def test_complex_syntax_is_not_skipped(self, run_hook, command):
        res = run_hook(HOOK, hook_payload(command), urlopen=fake_gemini("ALLOW"))
        # Reviewed (not skipped): reason comes from the Gemini stage.
        assert "Gemini reviewed and approved" in res.reason


class TestGeminiStage:
    def test_gemini_allow_short_circuits_codex(self, run_hook):
        # subprocess.run is not faked: a codex call would raise AssertionError.
        res = run_hook(HOOK, hook_payload("make build"), urlopen=fake_gemini("ALLOW"))
        assert res.decision == "allow"
        assert "Gemini reviewed and approved" in res.reason

    def test_primary_failure_falls_back_to_flash_model(self, run_hook):
        calls = []
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            urlopen=fake_gemini(URLError("primary down"), "ALLOW", calls=calls),
        )
        assert res.decision == "allow"
        assert len(calls) == 2
        assert "primary-model" in calls[0].full_url
        assert "fallback-model" in calls[1].full_url


class TestCodexStage:
    def test_gemini_ask_codex_allow(self, run_hook):
        calls = []
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ALLOW", calls=calls),
        )
        assert res.decision == "allow"
        assert "Codex approved" in res.reason
        assert calls[0][0][:2] == ["codex", "exec"]

    def test_gemini_ask_codex_ask(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="ASK"),
        )
        assert res.decision == "ask"

    def test_gemini_ask_codex_deny(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(stdout="DENY: destructive operation"),
        )
        assert res.decision == "deny"
        assert "Codex denied" in res.reason

    def test_codex_error_falls_back_to_gemini_deny(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("DENY: looks risky"),
            run=fake_run(returncode=1, stderr="codex exploded"),
        )
        assert res.decision == "deny"
        assert "Codex unavailable" in res.reason

    def test_codex_timeout_falls_back_to_gemini_ask(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            urlopen=fake_gemini("ASK"),
            run=fake_run(exc=subprocess.TimeoutExpired(cmd="codex", timeout=60)),
        )
        assert res.decision == "ask"

    def test_missing_api_key_gemini_error_goes_to_codex(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            env={"GEMINI_API_KEY": None},
            run=fake_run(stdout="ALLOW"),
        )
        assert res.decision == "allow"
        assert "Codex approved" in res.reason

    def test_missing_api_key_and_codex_missing_asks_user(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make deploy"),
            env={"GEMINI_API_KEY": None},
            run=fake_run(exc=FileNotFoundError("codex not found")),
        )
        assert res.decision == "ask"


class TestLogs:
    def test_safe_skip_writes_summary_and_detail_logs(self, run_hook):
        res = run_hook(HOOK, hook_payload("ls -la"))
        summary = res.home / ".claude/logs/bash-review.log"
        assert "safe command" in summary.read_text(encoding="utf-8")
        detail_dir = res.fake_tmp / "claude_hooks/logs/PreToolUse/Bash/bash-review"
        details = list(detail_dir.iterdir())
        assert len(details) == 1
        assert "SKIP (safe command)" in details[0].read_text(encoding="utf-8")

    def test_summary_log_rotates_at_500_lines(self, run_hook, tmp_path):
        home = tmp_path / "home"
        log = home / ".claude/logs/bash-review.log"
        log.parent.mkdir(parents=True)
        log.write_text("old line\n" * 520, encoding="utf-8")
        run_hook(HOOK, hook_payload("ls -la"))
        lines = log.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 500
        assert "ls -la" in lines[-1]

    def test_detail_logs_pruned_beyond_1000_files(self, run_hook, tmp_path):
        detail_dir = tmp_path / "fake-tmp/claude_hooks/logs/PreToolUse/Bash/bash-review"
        detail_dir.mkdir(parents=True)
        for i in range(1002):
            (detail_dir / f"a_{i:05d}.log").write_text("x", encoding="utf-8")
        run_hook(HOOK, hook_payload("ls -la"))
        assert not (detail_dir / "a_00000.log").exists()
        assert not (detail_dir / "a_00001.log").exists()
        assert (detail_dir / "a_00002.log").exists()


class TestParseVerdict:
    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            ("ALLOW", "ALLOW"),
            ("ASK", "ASK"),
            ("DENY: rm -rf is dangerous", "DENY"),
            ("**DENY** formatted by markdown", "DENY"),
            ("> ALLOW", "ALLOW"),
            ("- ASK", "ASK"),
            ('"DENY"', "DENY"),
            # Tokens must be line-initial: mid-line mentions do not count.
            ("The right verdict would be ALLOW here", "ASK"),
            # DISALLOW must not match ALLOW.
            ("DISALLOW", "ASK"),
            # DENY wins over ALLOW when both appear.
            ("ALLOW\nDENY: second thoughts", "DENY"),
            ("ASK\nALLOW", "ASK"),
            # No verdict at all falls back to ASK.
            ("", "ASK"),
            ("I cannot decide.", "ASK"),
        ],
    )
    def test_parse_verdict(self, hook_fns, output, expected):
        assert hook_fns["_parse_verdict"](output) == expected


class TestCommandHelpers:
    def test_split_commands(self, hook_fns):
        split = hook_fns["_split_commands"]
        assert split("a && b; c | d || e") == ["a", "b", "c", "d", "e"]
        assert split("single") == ["single"]
        assert split("  ") == []

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("ls", True),
            ("ls -la", True),
            ("lsof -i", False),
            ("git status", True),
            ("git push", False),
            ("tmux ls", True),
            ("tmux list-panes", True),
            ("tmux send-keys -t 1 'rm -rf /'", False),
            ("tmux kill-server", False),
        ],
    )
    def test_is_safe_command(self, hook_fns, command, expected):
        assert hook_fns["_is_safe_command"](command) is expected

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("curl https://example.com", (True, "curl")),
            ("curl", (True, "curl")),
            ("curling", (False, "")),
            ("ssh host", (True, "ssh")),
            ("rm -rf /", (True, "rm -rf /")),
            ("rm -rf ~", (True, "rm -rf ~")),
            ("rm build", (False, "")),
        ],
    )
    def test_is_deny_command(self, hook_fns, command, expected):
        assert hook_fns["_is_deny_command"](command) == expected

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("ls -la", True),
            ("echo hello", True),
            ("echo `date`", False),
            ("echo $(date)", False),
            ("cat a > b", False),
            ("cat a < b", False),
            ("sleep 1 & echo bg", False),
            ("not-in-safe-list", False),
        ],
    )
    def test_can_skip_review(self, hook_fns, command, expected):
        assert hook_fns["_can_skip_review"](command) is expected


class TestSanitizeNotify:
    def test_control_characters_are_removed(self, hook_fns):
        assert hook_fns["_sanitize_notify"]("a\x07b\nc\td") == "abcd"

    def test_long_text_is_truncated_with_ellipsis(self, hook_fns):
        out = hook_fns["_sanitize_notify"]("x" * 300, limit=200)
        assert len(out) == 200
        assert out.endswith("…")
