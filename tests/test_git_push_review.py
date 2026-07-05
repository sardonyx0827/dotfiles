"""Tests for git-push-review.sh (.claude JSON-ask variant, .codex exit-2 variant)."""

import json

from conftest import REPO_ROOT

CLAUDE_HOOK = REPO_ROOT / ".claude/hooks/git-push-review.sh"
CODEX_HOOK = REPO_ROOT / ".codex/hooks/git-push-review.sh"


def payload(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}})


class TestClaudeVariant:
    def test_non_push_command_passes_through(self, shell_env, git_repo):
        res = shell_env.run(CLAUDE_HOOK, stdin=payload("git status"), cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_push_requires_confirmation_with_summary(self, shell_env, git_repo):
        res = shell_env.run(
            CLAUDE_HOOK, stdin=payload("git push origin main"), cwd=git_repo
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "git push detected" in reason
        assert "branch: main" in reason
        assert "initial commit" in reason
        assert "no upstream" in reason

    def test_push_detected_inside_chain(self, shell_env, git_repo):
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("git add -A && git commit -m x && git push"),
            cwd=git_repo,
        )
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_push_with_flags_between_git_and_push(self, shell_env, git_repo):
        res = shell_env.run(
            CLAUDE_HOOK, stdin=payload("git --no-pager push"), cwd=git_repo
        )
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_quoted_push_text_is_not_detected(self, shell_env, git_repo):
        res = shell_env.run(CLAUDE_HOOK, stdin=payload('echo "git push"'), cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_push_outside_git_repo_still_asks(self, shell_env, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(CLAUDE_HOOK, stdin=payload("git push"), cwd=outside)
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"


class TestCodexVariant:
    def test_non_push_command_passes_through(self, shell_env, git_repo):
        res = shell_env.run(CODEX_HOOK, stdin=payload("git status"), cwd=git_repo)
        assert res.returncode == 0
        assert res.stderr == ""

    def test_push_blocks_with_exit_two_and_stderr(self, shell_env, git_repo):
        res = shell_env.run(
            CODEX_HOOK, stdin=payload("git push origin main"), cwd=git_repo
        )
        assert res.returncode == 2
        assert "git push detected" in res.stderr
        assert "branch: main" in res.stderr
