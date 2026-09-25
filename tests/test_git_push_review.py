"""Tests for git-push-review.sh (.claude JSON-ask variant, .codex exit-2 variant)."""

import concurrent.futures
import json
import os
import shutil
import subprocess
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
    # A redirect glued to `push` is still a push. The end-of-token class had
    # `;&|)` and the backtick but neither `>` nor `<`, so `git push>/dev/null`
    # slipped past both variants while `git push </dev/null` was caught.
    ("git push>/dev/null 2>&1", True),
    ("git push</dev/null", True),
    ("git push>log", True),
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


def _summary_text(res, variant):
    if variant == "claude":
        return json.loads(res.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    return res.stderr


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_dash_c_summary_survives_a_quoted_path(shell_env, tmp_path, variant, hook):
    """`git -C "/path with space" push` must summarise THAT repo.

    The -C value was read off cmd_for_match, i.e. AFTER strip_quoted_ranges had
    removed every quoted range -- the path included. The regex then took the
    next token (`push`) as the directory, `git -C push rev-parse` failed, and
    the confirmation carried an empty summary: an ask the user cannot judge.
    """
    base = tmp_path / "has space"
    base.mkdir()
    target = make_target_repo(base)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    res = shell_env.run(hook, stdin=payload(f'git -C "{target}" push'), cwd=outside)
    _assert_push_detected(res, variant, str(target))
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_large_quoted_push_command_stays_fast(shell_env, git_repo, variant, hook):
    """Quotes AND a push: neither existing guard applies, so this must be bounded.

    The "no push substring" short-circuit and the "no quote" fast path both
    miss a long heredoc or commit message chained to a push -- the common
    shape -- and the O(n^2) quote scan ran in full (17s measured at 40KB) on
    every such Bash call. Past a size cap the scan falls back to dropping the
    quote characters, which can only ADD detections, never lose one.
    """
    big = 'echo "' + ("x" * 40_000) + '" && git push'
    started = time.monotonic()
    res = shell_env.run(hook, stdin=payload(big), cwd=git_repo)
    elapsed = time.monotonic() - started

    _assert_push_detected(res, variant, "echo <40KB> && git push")
    assert elapsed < _QUADRATIC_BUDGET_SECONDS, (
        f"{variant}: a 40KB quoted push command took {elapsed:.2f}s"
    )


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_dash_c_of_an_unrelated_chained_git_call_does_not_supply_the_summary(
    shell_env, tmp_path, variant, hook
):
    """The -C that counts is the one on the git call that pushes.

    A regex over the whole command took the leftmost quoted `-C` it could
    find, so `git -C "/gone" push && git -C "decoy" status` summarised the
    decoy's commits as if they were about to be pushed. With the push
    target missing the honest answer is a note, never the decoy's summary.
    """
    decoy = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f'git -C "{tmp_path}/gone" push && git -C "{decoy}" status'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    assert "feature-target" not in _summary_text(res, variant)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_directory_named_push_in_cwd_cannot_hijack_the_summary(
    shell_env, tmp_path, variant, hook
):
    """`git -C "real" push` with a sibling directory literally named `push`.

    The quote-stripped command reads `git -C  push`, and a preference for a
    -C value that is an existing directory then picked `push/` -- a decoy
    repo -- over the real quoted target. Word-splitting sees the quoted value
    and never consults the filesystem to choose.
    """
    from conftest import run_git

    real = make_target_repo(tmp_path)
    cwd = tmp_path / "cwd"
    (cwd / "push-parent").mkdir(parents=True)
    decoy = make_target_repo(cwd / "push-parent")
    (decoy / "g.txt").write_text("y\n", encoding="utf-8")
    run_git(decoy, "add", "g.txt")
    run_git(decoy, "commit", "-q", "-m", "decoy commit")
    (cwd / "push").symlink_to(decoy)
    cmd = f'git -C "{real}" push origin main'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=cwd)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "target repo commit" in reason, reason


# --- `cd <dir>` retargets the summary (bug #4) --------------------------------
# `-C` is not the only way a chained command changes the repo a push lands in:
# a plain `cd <dir> && git push` (shell builtin, no -C at all) changes the real
# cwd for every later command in the same shell, and neither the detection
# regex nor git_c_dir_from_words ever looked at `cd`. The confirmation summary
# was generated for the hook's OWN cwd regardless -- the same "wrong summary is
# worse than no summary" gate defect as the -C case, just via a different verb.
#
# Design decisions (see cd_target_from_words in both hook files for the full
# rationale):
#   - bare `cd` (no argument) resolves to $HOME, matching real shell behaviour.
#   - `~` / `~/rest` resolve against $HOME (plain tilde expansion); `~user` is
#     treated as unresolvable (needs a password-database lookup).
#   - Anything the hook cannot resolve with certainty (variables, command
#     substitution, `cd -`, `~user`, pushd/popd, a subshell, a heredoc/comment
#     line that merely LOOKS like a cd, or more than one cd before the push)
#     emits NO summary plus a short note -- never a fallback to the cwd summary.
#   - A subshell anywhere in the command is treated as disqualifying even when
#     it does not actually scope the push away from the cd (e.g.
#     `cd X && git push -u origin $(git branch --show-current)`): telling
#     "same subshell as the push" apart from "sibling/unrelated subshell"
#     needs real parsing this hook does not do. Known tradeoff: such commands
#     get the note instead of a real summary.
def _repo_at(path, commit_message, branch="custom-branch"):
    """A throwaway repo at an exact path, with a caller-chosen distinguishing
    commit message/branch so tests can tell which of several repos a summary
    came from."""
    from conftest import run_git

    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", branch)
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test User")
    run_git(path, "config", "commit.gpgsign", "false")
    (path / "f.txt").write_text("x\n", encoding="utf-8")
    run_git(path, "add", "f.txt")
    run_git(path, "commit", "-q", "-m", commit_message)
    return path


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_with_and_before_push_summary_reflects_target_repo(
    shell_env, tmp_path, variant, hook
):
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target} && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_with_semicolon_before_push_summary_reflects_target_repo(
    shell_env, tmp_path, variant, hook
):
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target}; git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_quoted_path_with_space_before_push(shell_env, tmp_path, variant, hook):
    base = tmp_path / "has space"
    base.mkdir()
    target = make_target_repo(base)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f'cd "{target}" && git push'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_combined_with_relative_dash_c(shell_env, tmp_path, variant, hook):
    # `cd <parent> && git -C <relative sub> push`: the -C value is relative to
    # the directory the cd already switched into, exactly like a real shell.
    parent = tmp_path / "workspace"
    _repo_at(parent / "sub", "sub repo commit", branch="sub-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {parent} && git -C sub push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: sub-branch" in reason, reason
    assert "sub repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_combined_with_absolute_dash_c_wins(shell_env, tmp_path, variant, hook):
    # An absolute -C is not relative to the cd'd directory at all; it must win
    # outright, same as real `git -C /abs` semantics.
    parent = _repo_at(tmp_path / "parent", "parent repo commit", branch="parent-branch")
    other = _repo_at(tmp_path / "elsewhere", "other repo commit", branch="other-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {parent} && git -C {other} push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: other-branch" in reason, reason
    assert "other repo commit" in reason, reason
    assert "parent repo commit" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_relative_path_resolves_against_hook_cwd(shell_env, tmp_path, variant, hook):
    # A relative `cd sub` target is not resolved by hand: it is handed to the
    # real `git -C sub`, which resolves it against the hook's actual process
    # cwd -- exactly matching what `cd sub` would really do. That only holds
    # while CDPATH is empty (a non-empty CDPATH turns `cd sub` into a note, see
    # test_cdpath_in_the_hook_env_makes_a_plain_relative_cd_unresolvable), so
    # keep a host-exported CDPATH from leaking in.
    shell_env.env.pop("CDPATH", None)
    parent = tmp_path / "parent"
    _repo_at(parent / "sub", "sub repo commit", branch="sub-branch")
    cmd = "cd sub && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=parent)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: sub-branch" in reason, reason
    assert "sub repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_bare_cd_resolves_to_home(shell_env, tmp_path, variant, hook):
    # `cd` with no argument goes to $HOME, same as the real shell builtin.
    _repo_at(shell_env.home, "home repo commit", branch="home-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = "cd && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: home-branch" in reason, reason
    assert "home repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_tilde_slash_resolves_to_home_subdir(shell_env, tmp_path, variant, hook):
    _repo_at(shell_env.home / "proj", "proj repo commit", branch="proj-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = "cd ~/proj && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: proj-branch" in reason, reason
    assert "proj repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_in_earlier_and_chain_before_push_still_resolves(
    shell_env, tmp_path, variant, hook
):
    # `a && cd X && git push`: cd is in the push's own &&-chain -- if push
    # ran, cd must have run and succeeded first. Certain.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"true && cd {target} && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_first_in_semicolon_group_then_and_push_still_resolves(
    shell_env, tmp_path, variant, hook
):
    # `cd X; b && git push`: cd is the first (unconditionally reached)
    # command of an earlier `;`-group. Certain.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target}; true && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_and_chain_terminated_by_semicolon_still_resolves(
    shell_env, tmp_path, variant, hook
):
    # `cd X && make; git push`: cd's and-or list ends at the `;`, which is
    # an unconditional terminator -- unlike a trailing `&`, it does not
    # background the list. Rule B must walk PAST the `&&` to find this real
    # terminator, not stop at the first boundary after cd's own segment.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target} && true; git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_alone_in_semicolon_group_before_backgrounded_filler_still_resolves(
    shell_env, tmp_path, variant, hook
):
    # `cd X; a & git push`: cd's OWN group is just "cd X", terminated by
    # `;` before the `&` even appears -- the backgrounding applies to `a`
    # alone, not to cd. Must stay certain.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target}; true & git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_before_push_across_a_linebreak_still_resolves(
    shell_env, tmp_path, variant, hook
):
    # A newline right after `&&` is just a linebreak in bash grammar (the
    # list continues); it must not be treated as a hard `;`-style boundary.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target} &&\ngit push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: feature-target" in reason, reason
    assert "target repo commit" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_target_from_words_desync_with_quoted_cd_still_caught(
    shell_env, git_repo, variant, hook
):
    """When cd_target_from_words loses the push segment (desync), the
    fallback used to grep cmd_for_match -- strip_quoted_ranges output, which
    DELETES quoted ranges' contents -- for cd/pushd/popd. A quoted `"cd"`
    (quoting a command name doesn't stop the shell recognising it as the cd
    builtin) disappeared from that string entirely, so the wrong (hook cwd)
    summary was shown. The fallback must look at the command with quote
    CHARACTERS stripped but quoted CONTENTS kept, so a real `"cd"` still
    shows up (over-matching plain commit-message text only costs a note).
    """
    cmd = 'git commit -m "v $(date)" && "cd" /tmp/decoy && git push'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "could not be determined" in reason, reason
    assert "branch:" not in reason, reason


# Cases the hook cannot resolve with certainty: gating must stay unchanged
# (ask / exit 2), but the summary must be suppressed with a short note --
# never a fallback to the hook's own cwd.
CD_AMBIGUOUS_CASES = [
    'cd "$SOME_VAR" && git push',
    "cd $(pwd)/x && git push",
    "cd `pwd`/x && git push",
    "cd - && git push",
    "cd ~nobody && git push",
    "pushd /tmp && git push",
    "cd /a && cd /b && git push",
    "(cd /tmp) && git push",
    'bash -c "cd /tmp && git push"',
    'git commit -m "v $(date)" && cd /tmp && git push',
    # `cd`/`pushd`/`popd` that is not the FIRST word of its segment: the
    # tokenizer only checked the first word, so a cd hiding behind a group,
    # a builtin-dispatch prefix, or a leading assignment was invisible and
    # the summary silently kept the (wrong) cwd data.
    "{ cd /tmp; } && git push",
    "command cd /tmp && git push",
    "builtin cd /tmp && git push",
    "eval cd /tmp && git push",
    "FOO=1 cd /tmp && git push",
    # Separator semantics: a cd only counts as certain if the push is
    # guaranteed to see its effect. `&` backgrounds the cd (forks a
    # subshell, the foreground push never sees it); `false && cd /tmp`
    # never actually runs cd (left side fails); `true || cd /tmp` also
    # never runs cd (left side already succeeded); a pipeline runs cd in
    # a subshell either side. All four must emit the note, not resolve.
    "cd /tmp & git push",
    "false && cd /tmp; git push",
    "true || cd /tmp && git push",
    "cd /tmp | cat; git push",
    "true; cd /tmp & git push",
    # cd at the TAIL of a pipeline: `true | cd /tmp` also runs cd in a
    # subshell (the LEFT side of `&&` here is the whole pipeline, not cd
    # alone), so the outer shell's cwd never changes.
    "true | cd /tmp && git push",
    "true |& cd /tmp && git push",
    # A newline right after `&&`/`||`/`|` is just a linebreak in bash
    # grammar -- the list continues exactly as if written on one line --
    # so these must resolve the SAME as their single-line forms above.
    "false &&\ncd /tmp; git push",
    "true ||\ncd /tmp && git push",
    "true |\ncd /tmp && git push",
    # Rule B must find the TERMINATOR of the and-or list containing cd, not
    # just the operator directly after cd's own segment. `&` has LOWER
    # precedence than `&&`/`||`, so a trailing `&` backgrounds the WHOLE
    # `cd X && true` list as one job -- the push then runs in the hook's
    # cwd, not X. Walking only one boundary past cd (the old check) missed
    # this because that boundary is AND, not BG.
    "cd /tmp && true & git push",
    "cd /tmp && true || false & git push",
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("command", CD_AMBIGUOUS_CASES)
def test_cd_ambiguous_cases_emit_no_summary(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, command)
    reason = _summary_text(res, variant)
    assert "could not be determined" in reason, reason
    assert "branch:" not in reason, reason
    assert "initial commit" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_inside_heredoc_body_does_not_retarget(shell_env, git_repo, variant, hook):
    # A `cd` line inside a heredoc body never executes -- it is data being
    # written to a file -- but a naive line-based scan cannot tell it apart
    # from a real command. Must not retarget (or silently keep) the summary.
    cmd = (
        "cat > s.sh <<'EOF'\n"
        "cd /decoy\n"
        "EOF\n"
        "git add s.sh && git commit -m x && git push"
    )
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "could not be determined" in reason, reason
    assert "branch:" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_target_from_words_desync_falls_back_to_legacy_dash_c(
    shell_env, tmp_path, git_repo, variant, hook
):
    """When cd_target_from_words itself loses track of the push segment (a
    quote/command-substitution desync inside an unrelated -m value, not cd's
    fault -- no cd is involved here at all), it must not silently default to
    the empty git_c_opt (which means the cwd summary): it must still fall
    back to the pre-existing git_c_dir_from_words -C lookup, exactly as it
    did before this fix existed.
    """
    target = make_target_repo(tmp_path)
    cmd = (
        f"git -C {target} add . && "
        f'git -C {target} commit -m "v $(date)" && '
        f"git -C {target} push"
    )
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "target repo commit" in reason, reason
    assert "initial commit" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_mentioned_in_commit_message_does_not_retarget(
    shell_env, tmp_path, git_repo, variant, hook
):
    decoy = make_target_repo(tmp_path)
    cmd = f'git commit -m "cd {decoy}" && git push'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: main" in reason, reason
    assert "initial commit" in reason, reason
    assert "feature-target" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_after_push_does_not_retarget(shell_env, tmp_path, git_repo, variant, hook):
    decoy = make_target_repo(tmp_path)
    cmd = f"git push && cd {decoy}"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    assert "branch: main" in reason, reason
    assert "initial commit" in reason, reason
    assert "feature-target" not in reason, reason


# --- Oracle sweep: ground truth from a real shell -----------------------------
# Hand-picked cases (above) can only catch bugs someone thought to write down.
# This sweeps small cd/filler/push shapes -- built from a few atoms (`cd <dir>`,
# `true`, `false`, the push) joined by every separator the hook distinguishes
# (`;`, `&&`, `||`, `&`, `|`, newline) -- and checks each shape's HOOK claim
# against what /bin/bash (and zsh, when available) ACTUALLY do when the exact
# same string is run for real, with the push replaced by a cwd dump. A hook
# note (no directory claim) is always acceptable -- the hook is honestly
# saying "can't tell". A wrong claim is not.
#
# `true` AND `false` both appear as the filler: the hook's tokenizer treats
# them identically (plain words), so exercising only `true` would validate
# claims that happen to be right for the success case while missing ones that
# are only right when the filler fails (or vice versa) -- exactly the gap a
# hand-picked-case suite has.
ORACLE_SEPARATORS = [" ; ", " && ", " || ", " & ", " | ", " \n "]
ORACLE_FILLERS = ["true", "false"]


def _oracle_templates():
    """Command shapes with `{D}` (the cd target) and `{PUSH}` placeholders."""
    templates = []
    for sep in ORACLE_SEPARATORS:
        templates.append(f"cd {{D}}{sep}{{PUSH}}")
    for sep1 in ORACLE_SEPARATORS:
        for sep2 in ORACLE_SEPARATORS:
            for filler in ORACLE_FILLERS:
                templates.append(f"cd {{D}}{sep1}{filler}{sep2}{{PUSH}}")
    for sep1 in ORACLE_SEPARATORS:
        for sep2 in ORACLE_SEPARATORS:
            for filler in ORACLE_FILLERS:
                templates.append(f"{filler}{sep1}cd {{D}}{sep2}{{PUSH}}")
    return templates


ORACLE_TEMPLATES = _oracle_templates()


def _oracle_shell_cwd(argv, script, cwd, outfile, env=None):
    """Run `script` (push already replaced by a cwd dump) in a real shell.

    Returns the resolved cwd the shell was at when it reached the push
    point, or None if that point was never reached at all (e.g. `false &&
    cd D; push` never runs cd, and `... || push` after an all-success chain
    never runs push either) -- nothing to check in that case.

    `env` defaults to this process's environment; pass the hook's env when the
    shape depends on it (a `~` in the script must expand to the hook's HOME).
    """
    try:
        outfile.unlink()
    except FileNotFoundError:
        pass
    # `&` needs a trailing `wait` so the background job's write lands before
    # we read the file; `\n` (not `;`) because `... &;` is a syntax error.
    subprocess.run(
        [*argv, script + "\nwait\n"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if not outfile.exists():
        return None
    content = outfile.read_text(encoding="utf-8").strip()
    return content or None


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_certainty_oracle_sweep(shell_env, tmp_path, variant, hook):
    cwd_dir = _repo_at(tmp_path / "cwd-repo", "CWD_MARKER", branch="cwd-branch")
    target_dir = _repo_at(
        tmp_path / "target-repo", "TARGET_MARKER", branch="target-branch"
    )
    cwd_real = os.path.realpath(cwd_dir)
    target_real = os.path.realpath(target_dir)

    shells = [("bash", ["/bin/bash", "-c"])]
    if shutil.which("zsh"):
        # `-f`: skip ~/.zshenv even non-interactively, so the oracle is not
        # perturbed by whatever the host's zsh startup files happen to do.
        shells.append(("zsh", ["zsh", "-f", "-c"]))

    outdir = tmp_path / "oracle-out"
    outdir.mkdir()

    def _check_one(i, template):
        hook_cmd = template.format(D=str(target_dir), PUSH="git push")
        res = shell_env.run(hook, stdin=payload(hook_cmd), cwd=cwd_dir)
        reason = _summary_text(res, variant)
        if "TARGET_MARKER" in reason:
            claim = target_real
        elif "CWD_MARKER" in reason:
            claim = cwd_real
        else:
            return []  # note (or no output at all): always acceptable

        violations = []
        for shell_name, argv in shells:
            outfile = outdir / f"{i}-{shell_name}.out"
            oracle_cmd = template.format(
                D=str(target_dir), PUSH=f'pwd -P > "{outfile}"'
            )
            actual = _oracle_shell_cwd(argv, oracle_cmd, cwd_dir, outfile)
            if actual is None:
                continue  # push point never reached in this shell; nothing to check
            if os.path.realpath(actual) != claim:
                violations.append(
                    f"{shell_name}: hook claimed {claim!r} but real cwd was "
                    f"{actual!r} for: {hook_cmd!r}"
                )
        return violations

    started = time.monotonic()
    all_violations = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_check_one, i, tmpl) for i, tmpl in enumerate(ORACLE_TEMPLATES)
        ]
        for fut in concurrent.futures.as_completed(futures):
            all_violations.extend(fut.result())
    elapsed = time.monotonic() - started

    assert not all_violations, (
        f"{variant}: {len(all_violations)} violation(s) among "
        f"{len(ORACLE_TEMPLATES)} shapes in {elapsed:.2f}s:\n"
        + "\n".join(all_violations[:20])
    )


# --- Quoting, escaping and raw control bytes in the target --------------------
# The tokenizer that finds `cd <dir>` / `-C <dir>` only helps if it splits words
# exactly as the shell does; where it differs, the summary names a directory the
# push never runs in. The rules pinned here (bash 3.2, bash 5 and zsh agree):
#   - Inside "...", a backslash escapes only $ ` " \ and newline; before any other
#     character it stays literal, so `cd "/x/a\b"` enters `a\b`, not `ab`.
#     Backslash-newline is removed (line continuation) unquoted and inside "...",
#     but is literal inside '...'.
#   - A target holding a newline or any other control character is never
#     resolved. The hook's own plumbing cut such a name at the newline, which is
#     how `cd "<repo>\n/nonexistent"` came to show <repo>'s summary.
#   - A leading `~` expands only when it is unquoted and nothing up to the first
#     unquoted `/` is quoted. `"~/x"`, `'~/x'` and `\~/x` are literal. Mixed
#     forms such as `~"/x"` and `''~/x` split the shells (bash keeps them
#     literal, zsh expands them), so they get the note.
#   - A raw control byte anywhere in the command (other than tab and newline)
#     can collide with the tokenizers' in-band separators (\x01-\x08, \x1c), so
#     its presence turns target resolution off altogether. \r is included: bash
#     treats it as an ordinary word byte, and a terminal does not show it.
#   - A target that is not an existing directory gets the note as well, because
#     that `cd` fails and the push then runs somewhere else.
NOTE = "could not be determined"


def _assert_note_only(res, variant, cmd, *forbidden):
    _assert_push_detected(res, variant, repr(cmd))
    reason = _summary_text(res, variant)
    assert NOTE in reason, reason
    assert "branch:" not in reason, reason
    for marker in forbidden:
        assert marker not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_target_with_a_quoted_newline_is_unresolvable(
    shell_env, tmp_path, variant, hook
):
    # The real cd fails (no such directory) and the push runs in the hook's
    # non-repo cwd. The hook read the target back through `read`, which stops
    # at the newline, and summarised <repo> instead.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f'cd "{target}\n/nonexistent"; git push origin main'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_double_quoted_backslash_before_a_letter_stays_literal(
    shell_env, tmp_path, variant, hook
):
    # `"...target-\repo"`: the shell keeps the backslash, so the directory does
    # not exist, cd fails and the push runs in cwd. Dropping the backslash (the
    # unquoted rule) made the hook summarise the real target-repo instead.
    target = str(make_target_repo(tmp_path))
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    escaped = target[:-4] + "\\" + target[-4:]
    cmd = f'cd "{escaped}"; git push origin main'
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


BACKSLASH_TARGET_CASES = [
    # (cd target template, marker of the repo the shell really enters)
    ('"{base}/a\\b"', "BSLASH_DIR_COMMIT"),  # "\b": backslash kept
    ("'{base}/a\\b'", "BSLASH_DIR_COMMIT"),  # '...': fully literal
    ('"{base}/a\\\\b"', "BSLASH_DIR_COMMIT"),  # "\\": one backslash
    ("{base}/a\\\\b", "BSLASH_DIR_COMMIT"),  # unquoted \\: one backslash
    ("{base}/a\\b", "PLAIN_DIR_COMMIT"),  # unquoted \b: just b
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("target_tmpl,expected", BACKSLASH_TARGET_CASES)
def test_backslash_rules_pick_the_directory_the_shell_enters(
    shell_env, tmp_path, variant, hook, target_tmpl, expected
):
    # Two sibling repos whose names differ only by a literal backslash: `a\b`
    # and `ab`. Whichever the escaping rule picks is a real repository, so a
    # wrong rule shows a wrong summary instead of an empty one.
    base = tmp_path / "bs"
    _repo_at(base / "a\\b", "BSLASH_DIR_COMMIT", branch="bslash-branch")
    _repo_at(base / "ab", "PLAIN_DIR_COMMIT", branch="plain-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {target_tmpl.format(base=base)} && git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    other = {"BSLASH_DIR_COMMIT", "PLAIN_DIR_COMMIT"} - {expected}
    assert expected in reason, reason
    assert other.pop() not in reason, reason


CONTINUATION_CASES = [
    # (command template, marker of the repo the shell really enters or None)
    ("cd {outer}/\\\ninner; git push", "INNER_DIR_COMMIT"),  # joined
    ('cd "{outer}/\\\ninner"; git push', "INNER_DIR_COMMIT"),  # joined in "..."
    ("cd '{outer}/\\\ninner'; git push", None),  # literal in '...': note
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd_tmpl,expected", CONTINUATION_CASES)
def test_backslash_newline_in_the_target_follows_line_continuation(
    shell_env, tmp_path, variant, hook, cmd_tmpl, expected
):
    # `cd <outer>/\<newline>inner` is `cd <outer>/inner` to the shell. The hook
    # kept the newline in the word, then cut it there when reading it back, and
    # summarised <outer> -- a real, different repository.
    outer = _repo_at(tmp_path / "outer", "OUTER_DIR_COMMIT", branch="outer-branch")
    _repo_at(outer / "inner", "INNER_DIR_COMMIT", branch="inner-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = cmd_tmpl.format(outer=outer)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    if expected is None:
        _assert_note_only(res, variant, cmd, "OUTER_DIR_COMMIT", "INNER_DIR_COMMIT")
        return
    _assert_push_detected(res, variant, repr(cmd))
    reason = _summary_text(res, variant)
    assert expected in reason, reason
    assert "OUTER_DIR_COMMIT" not in reason, reason


TILDE_CASES = [
    # (cd argument, whether both shells expand it to $HOME/proj)
    ('"~/proj"', False),  # quoted tilde: literal in bash and zsh
    ("'~/proj'", False),
    ("\\~/proj", False),
    ('~"/proj"', False),  # bash: literal, zsh: expanded -> note
    ("''~/proj", False),  # bash: literal, zsh: expanded -> note
    ('~/"proj"', True),  # unquoted tilde-prefix: expanded in both
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cd_arg,expands", TILDE_CASES)
def test_only_an_unquoted_tilde_prefix_expands_to_home(
    shell_env, tmp_path, variant, hook, cd_arg, expands
):
    # `cd "~/proj"` fails in the shell (there is no directory literally named
    # `~`), so the push runs in cwd. The tokenizer had already dropped the
    # quotes and applied tilde expansion anyway, summarising $HOME/proj.
    _repo_at(shell_env.home / "proj", "HOME_PROJ_COMMIT", branch="proj-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {cd_arg}; git push"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    if expands:
        _assert_push_detected(res, variant, cmd)
        assert "HOME_PROJ_COMMIT" in _summary_text(res, variant)
    else:
        _assert_note_only(res, variant, cmd, "HOME_PROJ_COMMIT")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd_tmpl",
    [
        "cd {tmp}/does-not-exist; git push origin main",
        "cd {tmp}/does-not-exist && git -C sub push origin main",
        "git -C {tmp}/does-not-exist push origin main",
    ],
    ids=["cd", "cd-then-relative-dash-c", "dash-c"],
)
def test_a_target_that_is_not_a_directory_gets_the_note(
    shell_env, tmp_path, variant, hook, cmd_tmpl
):
    # Nothing can be summarised for a directory that does not exist, and saying
    # so beats an empty summary: the reader is told the target is unverified
    # rather than left to guess why the commit list is missing.
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = cmd_tmpl.format(tmp=tmp_path)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd)


CONTROL_BYTES = [
    "\x00",
    "\x01",
    "\x02",
    "\x03",
    "\x08",
    "\x0b",
    "\x0c",
    "\r",
    "\x0e",
    "\x1b",
    "\x1c",
    "\x1f",
    "\x7f",
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "ch", CONTROL_BYTES, ids=[f"0x{ord(c):02x}" for c in CONTROL_BYTES]
)
def test_raw_control_byte_turns_target_resolution_off(
    shell_env, tmp_path, variant, hook, ch
):
    # `echo x <0x02> cd <repo> && git push`: to the shell, `cd <repo>` is just
    # more of echo's arguments. The tokenizer used 0x02 as its own segment
    # separator, so it saw `cd <repo>` as a command and summarised <repo>.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"echo x {ch} cd {target} && git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_raw_separator_byte_cannot_forge_a_dash_c_push_segment(
    shell_env, tmp_path, git_repo, variant, hook
):
    # The -C resolver has the same weakness with its own separator, 0x01: the
    # forged `git -C <repo> push` inside echo's arguments supplied the summary
    # while the real push (the second one) runs in cwd.
    target = make_target_repo(tmp_path)
    cmd = f"echo x \x01 git -C {target} push origin main; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_note_only(res, variant, cmd, "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "dash_c", ['"{t}\n"', '"{t}\t"'], ids=["trailing-newline", "trailing-tab"]
)
def test_dash_c_value_with_a_control_character_is_unresolvable(
    shell_env, tmp_path, variant, hook, dash_c
):
    # `git -C "<repo>\n" push` pushes from a directory whose name ends in a
    # newline. Command substitution strips that newline when the hook captures
    # the value, which left exactly <repo>.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"git -C {dash_c.format(t=target)} push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


# --- cd operand shapes that the hook does not model --------------------------
# Three more ways the real `cd` ends up somewhere other than the literal operand:
#   - More than one operand: bash 3.2 uses the first, bash 5 fails with "too many
#     arguments", zsh treats `cd old new` as a substitution in $PWD (and fails
#     here). Redirections such as `2>/dev/null` count as operands for the hook
#     too -- a note in a case the shell would have resolved is acceptable.
#   - An option word (`-x`, `-L`, `-P`, `-e`, `-@`, `--`): invalid options make
#     cd fail (the hook used to skip them and read a bare `cd` as $HOME), and the
#     valid ones change how the operand is resolved.
#   - A `..` component: the shells' default cd is logical (`link/..` is the
#     directory holding `link`), while `git -C` resolves physically (the parent
#     of the link's target), and the hook's own cwd may be reached through
#     symlinks as well.
# All of them get the note. Bare `cd` -> $HOME and `cd -` -> note stay as they are.
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cd_part",
    ["cd {t} extra", "cd {t} {t}"],
    ids=["extra-operand", "target-twice"],
)
def test_cd_with_more_than_one_operand_is_unresolvable(
    shell_env, tmp_path, variant, hook, cd_part
):
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"{cd_part.format(t=target)}; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


CD_OPTION_CASES = ["-x", "-L", "-P", "-e", "-@", "--"]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("opt", CD_OPTION_CASES)
def test_cd_with_an_option_word_is_unresolvable(
    shell_env, tmp_path, variant, hook, opt
):
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {opt} {target}; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_with_only_an_invalid_option_does_not_mean_home(
    shell_env, tmp_path, variant, hook
):
    # `cd -x` fails in bash and zsh and the push runs in cwd. Skipping the
    # option left a bare `cd`, which the hook resolves to $HOME.
    _repo_at(shell_env.home, "HOME_REPO_COMMIT", branch="home-branch")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = "cd -x; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "HOME_REPO_COMMIT")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_through_a_symlink_then_dotdot_is_unresolvable(
    shell_env, tmp_path, variant, hook
):
    # `cd <outer>/lnk/..` lands in <outer> (logical), but `git -C` of the same
    # string goes to the parent of the link's target: <real>, another repo.
    outer = _repo_at(tmp_path / "outer", "OUTER_DIR_COMMIT", branch="outer-branch")
    real = _repo_at(tmp_path / "real", "REAL_DIR_COMMIT", branch="real-branch")
    (real / "sub").mkdir()
    (outer / "lnk").symlink_to(real / "sub")
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {outer}/lnk/..; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "OUTER_DIR_COMMIT", "REAL_DIR_COMMIT")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cd_arg",
    ["../target-repo", "{tmp}/not-a-repo/../target-repo", "..", "~/../target-repo"],
    ids=["leading", "middle", "bare", "after-tilde"],
)
def test_cd_target_with_a_dotdot_component_is_unresolvable(
    shell_env, tmp_path, variant, hook, cd_arg
):
    # Without symlinks these would resolve correctly, but the hook cannot tell
    # whether any component on the way is a link, so every `..` gets the note.
    make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"cd {cd_arg.format(tmp=tmp_path)}; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


# --- The repository chosen by something other than -C or cd -----------------
# `--git-dir` / `GIT_DIR` pick the repository the push acts on without any -C or
# cd, and `--work-tree` / `GIT_WORK_TREE` travel with them. The summary followed
# only -C, so these showed the hook cwd's repository while the push acted on
# another one. Any mention of the four names -- even inside quoted text, and
# after dropping quotes/backslashes that the shell would remove -- gets the note.
GIT_DIR_FORMS = [
    "git --git-dir={b}/.git push origin main",
    "git --git-dir {b}/.git push origin main",
    "GIT_DIR={b}/.git git push origin main",
    "export GIT_DIR={b}/.git; git push origin main",
    "git --work-tree={b} push origin main",
    "GIT_WORK_TREE={b} git push origin main",
    'git --git-d""ir={b}/.git push origin main',
    "git --git\\-dir={b}/.git push origin main",
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("form", GIT_DIR_FORMS)
def test_git_dir_or_work_tree_override_gets_the_note(
    shell_env, tmp_path, git_repo, variant, hook, form
):
    other = make_target_repo(tmp_path)
    cmd = form.format(b=other)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_note_only(res, variant, cmd, "initial commit", "target repo commit")


# CDPATH: bash and zsh look a relative `cd sub` up in CDPATH before the current
# directory (not for operands starting with `/`, `./` or `../`), so with CDPATH
# set in the hook's own environment `git -C sub` names the wrong directory. The
# shells run here with the same environment as the hook and are the oracle: any
# summary shown must name where they really went.
CDPATH_CASES = [
    # (cd operand, whether the hook must still resolve it)
    ("sub", False),
    ('"sub"', False),
    ("./sub", True),
    ("{work}/sub", True),
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("operand,resolves", CDPATH_CASES)
def test_cdpath_in_the_hook_env_makes_a_plain_relative_cd_unresolvable(
    shell_env, tmp_path, variant, hook, operand, resolves
):
    work = tmp_path / "work"
    local = _repo_at(work / "sub", "LOCAL_SUB_COMMIT", branch="local-sub")
    via_cdpath = _repo_at(
        tmp_path / "cdpath" / "sub", "CDPATH_SUB_COMMIT", branch="cdpath-sub"
    )
    shell_env.env["CDPATH"] = str(tmp_path / "cdpath")
    cd_part = f"cd {operand.format(work=work)}"
    cmd = f"{cd_part}; git push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=work)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    claims = {
        mark: os.path.realpath(path)
        for mark, path in (
            ("LOCAL_SUB_COMMIT", local),
            ("CDPATH_SUB_COMMIT", via_cdpath),
        )
        if mark in reason
    }
    if resolves:
        assert list(claims) == ["LOCAL_SUB_COMMIT"], reason
    else:
        assert NOTE in reason and not claims, reason

    shells = [("bash", ["/bin/bash", "-c"])]
    if shutil.which("zsh"):
        shells.append(("zsh", ["zsh", "-f", "-c"]))
    for shell_name, argv in shells:
        outfile = tmp_path / f"cdpath-{shell_name}.out"
        actual = _oracle_shell_cwd(
            argv,
            f'{cd_part} >/dev/null; pwd -P > "{outfile}"',
            work,
            outfile,
            env=shell_env.env,
        )
        for claim in claims.values():
            assert os.path.realpath(actual) == claim, (shell_name, cmd, actual)
        if not resolves and shell_name == "bash":
            # The shape is only worth a note if CDPATH really redirects it. bash
            # follows CDPATH ahead of the current directory; zsh (measured) tries
            # the current directory first -- the shells disagree, so it is a note.
            want = os.path.realpath(via_cdpath)
            assert os.path.realpath(actual) == want, (shell_name, cmd, actual)


# CDPATH set by the command itself is just as invisible to the hook's environment
# check: `CDPATH=<alt>; cd sub` sends bash to <alt>/sub. Any mention of CDPATH (or
# zsh's tied `cdpath`) in the command, after dropping quotes and backslashes the
# way the shell would, makes plain relative cd targets a note as well.
INLINE_CDPATH_FORMS = [
    # (command, whether bash really goes to <alt>/sub)
    ("CDPATH={alt}; cd sub && git push origin main", True),
    ("export CDPATH={alt}; cd sub && git push origin main", True),
    ('export CD""PATH={alt}; cd sub && git push origin main', True),
    ("cdpath=({alt}); cd sub && git push origin main", False),  # zsh's form
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("form,bash_redirects", INLINE_CDPATH_FORMS)
def test_cdpath_set_inside_the_command_makes_a_plain_relative_cd_unresolvable(
    shell_env, tmp_path, variant, hook, form, bash_redirects
):
    shell_env.env.pop("CDPATH", None)
    work = tmp_path / "work"
    _repo_at(work / "sub", "LOCAL_SUB_COMMIT", branch="local-sub")
    alt = _repo_at(tmp_path / "alt" / "sub", "ALT_SUB_COMMIT", branch="alt-sub")
    cmd = form.format(alt=tmp_path / "alt")
    res = shell_env.run(hook, stdin=payload(cmd), cwd=work)
    _assert_note_only(res, variant, cmd, "LOCAL_SUB_COMMIT", "ALT_SUB_COMMIT")
    if bash_redirects:
        # Ground truth: the shape really moves bash away from the cwd's `sub`.
        outfile = tmp_path / "inline-cdpath.out"
        script = cmd.replace("git push origin main", f'pwd -P > "{outfile}"')
        actual = _oracle_shell_cwd(
            ["/bin/bash", "-c"], script, work, outfile, env=shell_env.env
        )
        assert os.path.realpath(actual) == os.path.realpath(alt), (cmd, actual)


# --- Push detection through an unresolvable expansion -------------------------
# The detection regex lets only `-flag [value]` words sit between `git` and the
# subcommand. A word holding `$` or a backtick can expand to anything -- an
# option, an option plus its value -- so `D=--git-d; git ${D}ir=<B>/.git push`
# is a real push that the hook let through with no confirmation at all. Such a
# word now counts as a global option; the push is gated (ask / exit 2) and, as
# the repository it selects is unknown, the summary is a note.
EXPANSION_PUSH_FORMS = [
    "D=--git-d; git ${{D}}ir={b}/.git push origin main",
    "git $OPTS push origin main",
    "git ${{X}} push",
    "git $OPT {b} push origin main",
    "git $(printf %s --git-dir={b}/.git) push origin main",
    "git `printf %s -C` {b} push origin main",
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("form", EXPANSION_PUSH_FORMS)
def test_expansion_among_git_global_options_still_counts_as_push(
    shell_env, tmp_path, git_repo, variant, hook, form
):
    other = make_target_repo(tmp_path)
    cmd = form.format(b=other)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_note_only(res, variant, cmd, "initial commit", "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        "git $X status",
        "git ${X} log --oneline",
        "git commit -m $MSG push",
        "echo $X push",
        # Not listed: `git $X stash push` is asked about on purpose -- $X may be
        # `-C`, which makes `stash` its value and `push` the subcommand.
    ],
)
def test_expansion_without_a_push_subcommand_stays_quiet(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_not_detected(res, variant, command)


# The same expansion inside double quotes still expands, but the summary check ran
# on the quote-stripped copy, where the quoted word is gone -- so the hook cwd's
# summary was shown while the push acted on <B>. The check now also looks at the
# command with only the quote characters dropped (a `$` inside '...' is literal to
# the shell; over-matching it only costs a note).
QUOTED_EXPANSION_PUSH_FORMS = [
    'D=--git-d; git "${{D}}ir={b}/.git" push origin main',
    'git "$GIT_OPTS" push origin main',
    # Detection itself used to miss this one: without the quoted word the quote-
    # stripped command reads `git  <dir> push`, and `<dir>` is neither a flag nor
    # an expansion -- yet with OPT=-C it is a real push, and nothing was asked.
    'git "${{OPT}}" {b} push origin main',
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("form", QUOTED_EXPANSION_PUSH_FORMS)
def test_quoted_expansion_among_git_global_options_gets_the_note(
    shell_env, tmp_path, git_repo, variant, hook, form
):
    other = make_target_repo(tmp_path)
    cmd = form.format(b=other)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_note_only(res, variant, cmd, "initial commit", "target repo commit")


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_quoted_git_expansion_push_text_is_an_intentional_over_ask(
    shell_env, git_repo, variant, hook
):
    # Detection now also reads the command with only the quote characters
    # dropped, looking for `git <expansion...> push`. That cannot tell a quoted
    # string from a real command, so this echo is asked about too. Accepted on
    # purpose: an extra confirmation is the safe side, a missed push is not.
    command = 'echo "git $x push"'
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, command)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        "git log",
        "git log --oneline -5",
        'git log --grep="$X push"',
        'git commit -m "docs: explain git push"',
        'git commit -m "please dont git push this yet"',
        'echo "git push"',
    ],
)
def test_quoted_text_without_a_git_option_expansion_stays_quiet(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_not_detected(res, variant, command)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        "git push origin $BRANCH",
        'git push origin "$BRANCH"',
        'git push -u origin "$(git branch --show-current)"',
        'git -c "user.name=Some One" push origin main',
    ],
)
def test_expansion_after_the_push_subcommand_keeps_the_summary(
    shell_env, git_repo, variant, hook, command
):
    # Words after `push` are its own arguments (remote, refspec); they cannot
    # select another repository, so the normal summary stays.
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, command)
    reason = _summary_text(res, variant)
    assert "initial commit" in reason, reason
    assert NOTE not in reason, reason


# Oracle sweep for the rules above: every atom is a cd written with some quoting
# or escaping, joined to the push by `;`, `&&` or a newline, and run for real in
# bash and zsh (push replaced by a cwd dump, HOME set to the hook's HOME). Any
# summary the hook shows must name the directory the shells actually ended up
# in; a note is always acceptable. So that a hook which notes everything cannot
# pass, atoms marked True must produce a summary.
#
# Placeholders: {T} a repo; {T_BS} {T} with a backslash before its last three
# letters; {T_ESC} {T} with every `/` written `\/`; {S} a repo under a directory
# whose name holds a space, {S_ESC} the same with the space escaped; {B} holds
# sibling repos `a\b` and `ab`; {O} is a repo with a nested repo `inner` and a
# symlink `lnk` to {T}/sub (so `{O}/lnk/..` is {O} logically, {T} physically).
QUOTING_ORACLE_ATOMS = [
    ('cd "{T}"', True),
    ("cd '{T}'", True),
    ("cd {T_ESC}", True),
    ("cd {S_ESC}", True),
    ('cd "{S}"', True),
    ("cd '{S}'", True),
    ('cd "{T_BS}"', False),
    ("cd {T_BS}", True),
    ("cd '{T_BS}'", False),
    ('cd "{B}/a\\b"', True),
    ("cd '{B}/a\\b'", True),
    ("cd {B}/a\\b", True),
    ('cd "{B}/a\\\\b"', True),
    ("cd {B}/a\\\\b", True),
    ('cd "{T}\n/nonexistent"', False),
    ("cd '{T}\n'", False),
    ('cd "{T}\t"', False),
    ("cd {O}/\\\ninner", True),
    ('cd "{O}/\\\ninner"', True),
    ("cd '{O}/\\\ninner'", False),
    ("echo x \x02 cd {T}", False),
    ("cd {T}\r", False),
    ("cd {T}\x1c", False),
    ("cd {T}\x7f", False),
    ('cd "~/proj"', False),
    ("cd '~/proj'", False),
    ("cd \\~/proj", False),
    ('cd ~"/proj"', False),
    ("cd ''~/proj", False),
    ('cd ~/"proj"', True),
    ("cd ~/proj", True),
    ("cd $'{T}'", False),
    # Operand shapes the hook does not model (see the section above): all notes.
    ("cd {T} extra", False),
    ("cd -x", False),
    ("cd -P {T}", False),
    ("cd -L {T}", False),
    ("cd -e {T}", False),
    ("cd -@ {T}", False),
    ("cd -- {T}", False),
    ("cd {O}/lnk/..", False),
    ("cd ../target", False),
    ("cd {T}/../target", False),
]
QUOTING_ORACLE_SEPARATORS = [" ; ", " && ", " \n "]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_cd_quoting_oracle_sweep(shell_env, tmp_path, variant, hook):
    root = tmp_path / "qo"
    repos = {
        "QO_CWD_MARK": _repo_at(root / "cwd", "QO_CWD_MARK", branch="qo-cwd"),
        "QO_TARGET_MARK": _repo_at(root / "target", "QO_TARGET_MARK", branch="qo-t"),
        "QO_SPACE_MARK": _repo_at(
            root / "has space" / "repo", "QO_SPACE_MARK", branch="qo-s"
        ),
        "QO_BSLASH_MARK": _repo_at(
            root / "bs" / "a\\b", "QO_BSLASH_MARK", branch="qo-bs"
        ),
        "QO_PLAIN_MARK": _repo_at(root / "bs" / "ab", "QO_PLAIN_MARK", branch="qo-p"),
        "QO_OUTER_MARK": _repo_at(root / "outer", "QO_OUTER_MARK", branch="qo-o"),
        "QO_INNER_MARK": _repo_at(
            root / "outer" / "inner", "QO_INNER_MARK", branch="qo-i"
        ),
        "QO_HOMEPROJ_MARK": _repo_at(
            shell_env.home / "proj", "QO_HOMEPROJ_MARK", branch="qo-h"
        ),
    }
    (repos["QO_TARGET_MARK"] / "sub").mkdir()
    (repos["QO_OUTER_MARK"] / "lnk").symlink_to(repos["QO_TARGET_MARK"] / "sub")
    real = {mark: os.path.realpath(path) for mark, path in repos.items()}
    cwd_dir = repos["QO_CWD_MARK"]
    target = str(repos["QO_TARGET_MARK"])
    spaced = str(repos["QO_SPACE_MARK"])
    paths = {
        "T": target,
        "T_BS": target[:-3] + "\\" + target[-3:],
        "T_ESC": target.replace("/", "\\/"),
        "S": spaced,
        "S_ESC": spaced.replace(" ", "\\ "),
        "B": str(root / "bs"),
        "O": str(repos["QO_OUTER_MARK"]),
    }

    shells = [("bash", ["/bin/bash", "-c"])]
    if shutil.which("zsh"):
        shells.append(("zsh", ["zsh", "-f", "-c"]))
    outdir = tmp_path / "quoting-oracle-out"
    outdir.mkdir()

    shapes = [
        (atom.format(**paths), must_resolve, sep)
        for atom, must_resolve in QUOTING_ORACLE_ATOMS
        for sep in QUOTING_ORACLE_SEPARATORS
    ]

    def _check_one(i, cd_part, must_resolve, sep):
        hook_cmd = f"{cd_part}{sep}git push"
        res = shell_env.run(hook, stdin=payload(hook_cmd), cwd=cwd_dir)
        reason = _summary_text(res, variant)
        claims = [mark for mark in real if mark in reason]
        if not claims:
            if must_resolve:
                return [f"no summary (expected one) for: {hook_cmd!r}"], 0
            return [], 0
        if len(claims) > 1:
            return [f"several repos in one summary {claims} for: {hook_cmd!r}"], 1
        claim = real[claims[0]]
        violations = []
        for shell_name, argv in shells:
            outfile = outdir / f"{i}-{shell_name}.out"
            oracle_cmd = f'{cd_part}{sep}pwd -P > "{outfile}"'
            actual = _oracle_shell_cwd(
                argv, oracle_cmd, cwd_dir, outfile, env=shell_env.env
            )
            if actual is None:
                continue
            if os.path.realpath(actual) != claim:
                violations.append(
                    f"{shell_name}: hook claimed {claim!r} but real cwd was "
                    f"{actual!r} for: {hook_cmd!r}"
                )
        return violations, 1

    started = time.monotonic()
    all_violations = []
    resolved = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(_check_one, i, cd_part, must_resolve, sep)
            for i, (cd_part, must_resolve, sep) in enumerate(shapes)
        ]
        for fut in concurrent.futures.as_completed(futures):
            violations, summarised = fut.result()
            all_violations.extend(violations)
            resolved += summarised
    elapsed = time.monotonic() - started

    assert not all_violations, (
        f"{variant}: {len(all_violations)} violation(s) among {len(shapes)} "
        f"shapes ({resolved} summarised) in {elapsed:.2f}s:\n"
        + "\n".join(all_violations[:20])
    )
