"""Tests for git-push-review.sh (.claude JSON-ask variant, .codex exit-2 variant)."""

import json

import pytest
from conftest import REPO_ROOT

CLAUDE_HOOK = REPO_ROOT / ".claude/hooks/git-push-review.sh"
CODEX_HOOK = REPO_ROOT / ".codex/hooks/git-push-review.sh"


def payload(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}})


def make_target_repo(base):
    """A second throwaway repo (distinct branch/commit) to push -C at."""
    from conftest import run_git

    target = base / "target-repo"
    target.mkdir()
    run_git(target, "init", "-q", "-b", "feature-target")
    run_git(target, "config", "user.email", "test@example.com")
    run_git(target, "config", "user.name", "Test User")
    run_git(target, "config", "commit.gpgsign", "false")
    (target / "f.txt").write_text("x\n", encoding="utf-8")
    run_git(target, "add", "f.txt")
    run_git(target, "commit", "-q", "-m", "target repo commit")
    return target


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

    def test_push_with_space_separated_flag_value_is_detected(
        self, shell_env, git_repo
    ):
        # `git -C <dir> push` / `git --git-dir <dir> push`: the flag value is a
        # separate token, which the old regex failed to match (bypass).
        for command in (
            "git -C /tmp/repo push",
            "git --git-dir /tmp/repo/.git push origin main",
            "git -c user.name=x push",
        ):
            res = shell_env.run(CLAUDE_HOOK, stdin=payload(command), cwd=git_repo)
            output = json.loads(res.stdout)["hookSpecificOutput"]
            assert output["permissionDecision"] == "ask", command

    def test_local_stash_push_is_not_detected(self, shell_env, git_repo):
        res = shell_env.run(CLAUDE_HOOK, stdin=payload("git stash push"), cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

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

    def test_git_push_mentioned_only_inside_quoted_message_is_not_detected(
        self, shell_env, git_repo
    ):
        # "git" (the leading command) and "push" only co-occur inside the
        # quoted commit message here; the actual command is `git commit`.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('git commit -m "please dont git push this yet"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stdout == ""

    def test_apostrophe_in_double_quoted_message_does_not_hide_real_push(
        self, shell_env, git_repo
    ):
        # Regression: a naive "remove '...' then remove \"...\"" pass lets
        # the apostrophe in "it's" pair up with the *next* single quote
        # (opening 'done'), eating everything between them - including the
        # real, unquoted `git push` - and hiding it from detection.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("git commit -m \"it's fine\" && git push && echo 'done'"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_double_quote_in_single_quoted_message_does_not_hide_real_push(
        self, shell_env, git_repo
    ):
        # Mirror-image case: swapping quote kinds must not resurrect the bug.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('git commit -m \'it"s fine\' && git push && echo "done"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_multiple_fully_quoted_push_mentions_are_not_detected(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("echo \"git push\" && echo 'git push'"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stdout == ""

    def test_push_inside_double_quoted_command_substitution_is_detected(
        self, shell_env, git_repo
    ):
        # bash DOES execute $(...) inside double quotes, so a push placed
        # there is a real push, not inert quoted text. Stripping the whole
        # double-quoted range used to hide it from detection (bypass).
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('echo "log: $(git push origin main)"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_push_inside_double_quoted_backticks_is_detected(self, shell_env, git_repo):
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('echo "log: `git push origin main`"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_push_inside_bare_backticks_is_detected(self, shell_env, git_repo):
        # Unquoted backticks are also command substitution; the detection
        # regex must accept a backtick as a command boundary.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("echo `git push origin main`"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_escaped_substitution_in_double_quotes_is_not_detected(
        self, shell_env, git_repo
    ):
        # \$( does not start a command substitution; the text is inert.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('echo "costs \\$(git push)"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stdout == ""

    def test_substitution_in_single_quotes_is_not_detected(self, shell_env, git_repo):
        # Single quotes suppress command substitution entirely.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("echo 'see $(git push)'"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stdout == ""

    def test_push_followed_by_semicolon_is_detected(self, shell_env, git_repo):
        # `push` can be terminated by `;` `&` `|` `)` as well as whitespace;
        # requiring whitespace/EOL after `push` let `git push;true` through.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("git push;true"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_nested_quotes_inside_substitution_do_not_hide_push(
        self, shell_env, git_repo
    ):
        # A double-quoted argument INSIDE the substitution flips the naive
        # quote pairing; the push must still be detected.
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload('echo "$(git -C "/tmp/some repo" push)"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask"

    def test_dash_c_summary_reflects_target_repo(self, shell_env, tmp_path):
        # The detection regex already accepts `git -C <dir> push`, but the
        # confirmation summary must describe <dir>'s branch/commits, not
        # whatever repo happens to be the hook's cwd.
        target = make_target_repo(tmp_path)
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(
            CLAUDE_HOOK, stdin=payload(f"git -C {target} push"), cwd=outside
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "branch: feature-target" in reason
        assert "target repo commit" in reason


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

    def test_git_push_mentioned_only_inside_quoted_message_is_not_detected(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload('git commit -m "please dont git push this yet"'),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stderr == ""

    def test_apostrophe_in_double_quoted_message_does_not_hide_real_push(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload("git commit -m \"it's fine\" && git push && echo 'done'"),
            cwd=git_repo,
        )
        assert res.returncode == 2
        assert "git push detected" in res.stderr

    def test_double_quote_in_single_quoted_message_does_not_hide_real_push(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload('git commit -m \'it"s fine\' && git push && echo "done"'),
            cwd=git_repo,
        )
        assert res.returncode == 2
        assert "git push detected" in res.stderr

    def test_multiple_fully_quoted_push_mentions_are_not_detected(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload("echo \"git push\" && echo 'git push'"),
            cwd=git_repo,
        )
        assert res.returncode == 0
        assert res.stderr == ""

    def test_push_inside_double_quoted_command_substitution_blocks(
        self, shell_env, git_repo
    ):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload('echo "log: $(git push origin main)"'),
            cwd=git_repo,
        )
        assert res.returncode == 2
        assert "git push detected" in res.stderr

    def test_push_inside_double_quoted_backticks_blocks(self, shell_env, git_repo):
        res = shell_env.run(
            CODEX_HOOK,
            stdin=payload('echo "log: `git push origin main`"'),
            cwd=git_repo,
        )
        assert res.returncode == 2
        assert "git push detected" in res.stderr

    def test_dash_c_summary_reflects_target_repo(self, shell_env, tmp_path):
        target = make_target_repo(tmp_path)
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(
            CODEX_HOOK, stdin=payload(f"git -C {target} push"), cwd=outside
        )
        assert res.returncode == 2
        assert "branch: feature-target" in res.stderr
        assert "target repo commit" in res.stderr


# --- Detection parity across BOTH variants -----------------------------------
# The quote / substitution / flag parsing is identical in the two copies; only
# the SIGNAL differs (claude emits a JSON "ask", codex exits 2 with stderr).
# Previously only the claude copy exercised these bypass regressions, so a
# detection regression in the codex copy would ship green. Run every case
# against both — the same drift guard rationale as test_hook_sync.py.
DETECTION_CASES = [
    # (command, should_detect)
    ("git push origin main", True),
    ("git add -A && git commit -m x && git push", True),
    ("git --no-pager push", True),
    ("git -C /tmp/repo push", True),
    ("git --git-dir /tmp/repo/.git push origin main", True),
    ("git -c user.name=x push", True),
    ("git push;true", True),
    # Backslash line-continuation joins `git \` + newline + `push` into one
    # logical line at execution time; the detection grep must join it too
    # before matching, or it slips through as two independent lines.
    ("git \\\npush", True),
    ('echo "log: $(git push origin main)"', True),
    ('echo "log: `git push origin main`"', True),
    ("echo `git push origin main`", True),
    ('echo "$(git -C "/tmp/some repo" push)"', True),
    ("git status", False),
    ("git stash push", False),
    ('echo "git push"', False),
    ('git commit -m "please dont git push this yet"', False),
    ("echo \"git push\" && echo 'git push'", False),
    ('echo "costs \\$(git push)"', False),
    ("echo 'see $(git push)'", False),
]

VARIANTS = [("claude", CLAUDE_HOOK), ("codex", CODEX_HOOK)]


def _assert_push_detected(res, variant, command):
    if variant == "claude":
        assert res.returncode == 0, command
        decision = json.loads(res.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "ask", command
    else:
        assert res.returncode == 2, command
        assert "git push detected" in res.stderr, command


def _assert_push_not_detected(res, variant, command):
    assert res.returncode == 0, command
    if variant == "claude":
        assert res.stdout == "", command
    else:
        assert res.stderr == "", command


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("command,should_detect", DETECTION_CASES)
def test_detection_parity(shell_env, git_repo, variant, hook, command, should_detect):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    if should_detect:
        _assert_push_detected(res, variant, command)
    else:
        _assert_push_not_detected(res, variant, command)
