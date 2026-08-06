"""Tests for git-push-review.sh (.claude JSON-ask variant, .codex exit-2 variant)."""

import json
import time

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

    def test_dash_c_summary_survives_path_qualified_git(self, shell_env, tmp_path):
        # Showing the WRONG repo's commits in a confirmation prompt is worse
        # than showing none: the user approves against a summary that does not
        # describe what is about to be pushed. The `-C` extraction has to count
        # `/` as a token boundary for the same reason detection does.
        target = make_target_repo(tmp_path)
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(
            CLAUDE_HOOK, stdin=payload(f"/usr/bin/git -C {target} push"), cwd=outside
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "branch: feature-target" in reason
        assert "target repo commit" in reason

    def test_lowercase_dash_c_config_flag_is_not_a_repo_path(self, shell_env, tmp_path):
        # `git -c <key>=<value>` sets config; it is NOT `-C <dir>`. Matching the
        # repo-path flag case-insensitively would consume the config value as a
        # directory, blanking the summary -- or, if the value happened to name a
        # real repo, describing the wrong one in the confirmation prompt.
        target = make_target_repo(tmp_path)
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload("git -c commit.gpgsign=false push"),
            cwd=target,
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "branch: feature-target" in reason
        assert "target repo commit" in reason

    def test_dash_c_in_earlier_chain_command_does_not_shadow_target(
        self, shell_env, tmp_path
    ):
        # A `-C` belonging to an earlier command in the chain (e.g. grep's
        # context-lines flag) must not shadow the push target's own -C. The
        # leftmost-match extraction used to grab `grep -C 3`'s value and run
        # `git -C 3 ...`, blanking (or misdirecting) the summary.
        target = make_target_repo(tmp_path)
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(
            CLAUDE_HOOK,
            stdin=payload(f"grep -C 3 needle /dev/null && git -C {target} push"),
            cwd=outside,
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "branch: feature-target" in reason
        assert "target repo commit" in reason

    def test_push_still_asks_when_jq_is_unavailable(self, shell_env, git_repo):
        # Without jq the command string cannot be extracted, so the push
        # detection below it silently matched nothing and the hook exited 0 --
        # the one gate in front of `git push` disappeared without a trace.
        # Absence must degrade to a coarse ask, never to a silent pass.
        shell_env.hide("jq")
        res = shell_env.run(
            CLAUDE_HOOK, stdin=payload("git push origin main"), cwd=git_repo
        )
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["permissionDecision"] == "ask"
        assert "jq" in output["permissionDecisionReason"]

    def test_non_push_still_passes_through_when_jq_is_unavailable(
        self, shell_env, git_repo
    ):
        # The jq-less fallback must stay scoped to pushes; turning every Bash
        # command into an ask would be worse than the gap it closes.
        shell_env.hide("jq")
        res = shell_env.run(CLAUDE_HOOK, stdin=payload("ls -la"), cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""


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

    def test_push_still_blocks_when_jq_is_unavailable(self, shell_env, git_repo):
        # Same silent-skip gap as the claude variant; here the fallback signal
        # is exit 2 + stderr rather than a JSON ask.
        shell_env.hide("jq")
        res = shell_env.run(
            CODEX_HOOK, stdin=payload("git push origin main"), cwd=git_repo
        )
        assert res.returncode == 2
        assert "jq" in res.stderr

    def test_non_push_still_passes_through_when_jq_is_unavailable(
        self, shell_env, git_repo
    ):
        shell_env.hide("jq")
        res = shell_env.run(CODEX_HOOK, stdin=payload("ls -la"), cwd=git_repo)
        assert res.returncode == 0
        assert res.stderr == ""


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
    # `eval` / `sh -c` / `bash -c` execute their *string argument* as code, so
    # the quoted range that strip_quoted_ranges discards as "just a message" is
    # exactly what runs. Same class as the line-continuation bypass above: a
    # shell mechanism turns supposedly inert text into an executed command.
    ('eval "git push origin main"', True),
    ("eval 'git push origin main'", True),
    ('bash -c "git push origin main"', True),
    ('sh -c "git push origin main"', True),
    ("zsh -c 'git push origin main'", True),
    ('bash -lc "git push origin main"', True),
    ('bash -e -c "git push origin main"', True),
    # A path-qualified interpreter is the same command. `/` has to count as a
    # token boundary or `/bin/bash -c` slips past the interpreter check while
    # the bare `bash -c` form is caught.
    ('/bin/bash -c "git push origin main"', True),
    ('/bin/sh -c "git push origin main"', True),
    ("/usr/bin/env bash -c 'git push origin main'", True),
    # `git` itself is the same command when path-qualified, for exactly the
    # reason the interpreter check above already counts `/` as a boundary. If
    # the push check disagrees, `/usr/bin/git push` slips past while the bare
    # form is caught -- the inconsistent hole that comment warns about.
    ("/usr/bin/git push origin main", True),
    ("/opt/homebrew/bin/git -C /repo push --force", True),
    ('sh -c "/usr/bin/git push"', True),
    # `${IFS}` expands to whitespace and then word-splits, so bash runs this as
    # `git push origin main` even though no literal space separates the tokens.
    ("git${IFS}push origin main", True),
    ("$IFS git${IFS}push --force", True),
    # The default macOS/Windows filesystems are case-insensitive, so `GIT` and
    # `BASH` really do resolve to git and bash. Matching folds case or the whole
    # gate is one Shift key away.
    ("GIT push origin main", True),
    ("Git Push origin main", True),
    ('BASH -c "git push origin main"', True),
    # The interpreter check reads the command too, so it needs the same `${IFS}`
    # normalisation the push check got -- otherwise the quoted payload is
    # discarded as an inert message and the push inside it disappears.
    ('sh${IFS}-c "git push origin main"', True),
    ('eval${IFS}"git push origin main"', True),
    ("GIT status", False),
    # ...but path-qualifying a non-push git command must stay quiet.
    ("/usr/bin/git status", False),
    ("git status", False),
    ("git stash push", False),
    ('echo "git push"', False),
    # ...but an interpreter that is NOT running a push must stay quiet: the
    # trigger is the push inside the executed string, not the interpreter.
    ('eval "echo hi"', False),
    ('bash -c "ls -la"', False),
    ('sh -c "git status"', False),
    ('/bin/bash -c "ls -la"', False),
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


# Detection parity above says both variants decide the SAME. It says nothing about what
# deciding COSTS, and the two had drifted badly apart there: commit 982c9be measured
# 4.5s for a 20KB command and added two guards to the .claude copy only -- an early
# "no `push` substring => exit 0" short-circuit, and a fast path that skips the
# character-at-a-time strip_quoted_ranges when the command holds no quote or backslash.
# The .codex copy is wired unconditionally on matcher "Bash" (.codex/hooks.json.template),
# so every Bash call in a Codex session paid the full O(n^2) scan. Measured here before
# the port: .claude 0.074s vs .codex 4.523s on the same input.
#
# The bound is deliberately loose (a shared CI runner is noisy, and this asserts an
# algorithmic class, not a stopwatch figure): an unguarded quadratic scan lands in
# seconds, a guarded one in tens of milliseconds, so anything under a second separates
# them without being flaky.
_QUADRATIC_BUDGET_SECONDS = 1.5


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_large_non_push_command_is_short_circuited(shell_env, git_repo, variant, hook):
    """A 20KB command with no `push` in it must not be scanned character by character."""
    big = 'echo "' + ("x" * 20_000) + '"'
    started = time.monotonic()
    res = shell_env.run(hook, stdin=payload(big), cwd=git_repo)
    elapsed = time.monotonic() - started

    _assert_push_not_detected(res, variant, big)
    assert elapsed < _QUADRATIC_BUDGET_SECONDS, (
        f"{variant}: a 20KB non-push command took {elapsed:.2f}s -- the early "
        f"short-circuit is missing, so every Bash call pays an O(n^2) quote scan"
    )


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_large_quote_free_push_command_skips_the_quote_scan(
    shell_env, git_repo, variant, hook
):
    """`push` present but no quotes: the strip_quoted_ranges fast path must apply.

    This is the case the short-circuit above cannot catch, so it pins the second guard
    independently -- without it, a large legitimate push command still pays the scan.
    """
    big = "git push origin main # " + ("x" * 20_000)
    started = time.monotonic()
    res = shell_env.run(hook, stdin=payload(big), cwd=git_repo)
    elapsed = time.monotonic() - started

    _assert_push_detected(res, variant, big)
    assert elapsed < _QUADRATIC_BUDGET_SECONDS, (
        f"{variant}: a 20KB quote-free push command took {elapsed:.2f}s -- the "
        f"strip_quoted_ranges fast path is missing"
    )
