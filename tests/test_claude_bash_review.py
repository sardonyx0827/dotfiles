"""Tests for .claude/hooks/claude-bash-review.py (claude CLI review)."""

import pytest
from conftest import fake_run, hook_payload

HOOK = ".claude/hooks/claude-bash-review.py"


class TestSafeSkip:
    def test_safe_command_skips_review(self, run_hook):
        res = run_hook(HOOK, hook_payload("git log --oneline"))
        assert res.exit_code == 0
        assert res.decision == "allow"
        assert "skipped Claude review" in res.reason

    def test_unsafe_subcommand_in_chain_is_reviewed(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("ls && make deploy"),
            run=fake_run(stdout="ALLOW"),
        )
        assert "Claude reviewed and approved" in res.reason

    @pytest.mark.parametrize(
        "command",
        [
            "ls $(whoami)",
            "ls `whoami`",
            "cat a > b",
            "ls & echo hi",
        ],
    )
    def test_complex_syntax_is_not_skipped(self, run_hook, command):
        # 先頭が安全コマンド名でも、コマンド置換 / リダイレクト / バックグラウンド
        # 実行などの複雑構文を含む場合はスキップせず必ずレビューへ回す
        # (safe コマンド判定だけに頼るとレビューを迂回できてしまうため)。
        res = run_hook(HOOK, hook_payload(command), run=fake_run(stdout="ALLOW"))
        assert "Claude reviewed and approved" in res.reason


class TestVerdictMapping:
    def test_allow(self, run_hook):
        calls = []
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            run=fake_run(stdout="ALLOW", calls=calls),
        )
        assert res.decision == "allow"
        assert calls[0][0][0] == "claude"

    def test_ask(self, run_hook):
        res = run_hook(HOOK, hook_payload("make build"), run=fake_run(stdout="ASK"))
        assert res.decision == "ask"
        assert "Claude requires confirmation" in res.reason

    def test_deny(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            run=fake_run(stdout="DENY: too risky"),
        )
        assert res.decision == "deny"
        assert "DENY: too risky" in res.reason


class TestErrors:
    def test_nonzero_exit_asks_user(self, run_hook):
        res = run_hook(
            HOOK,
            hook_payload("make build"),
            run=fake_run(returncode=1, stderr="claude crashed"),
        )
        assert res.exit_code == 0
        assert res.decision == "ask"
        assert "Error during review" in res.reason


class TestMalformedInput:
    def test_non_dict_tool_input_asks(self, run_hook):
        res = run_hook(HOOK, {"tool_name": "Bash", "tool_input": "notadict"})
        assert res.exit_code == 0
        assert res.decision == "ask"

    def test_non_dict_payload_asks(self, run_hook):
        res = run_hook(HOOK, "not-a-hook-object")
        assert res.exit_code == 0
        assert res.decision == "ask"
