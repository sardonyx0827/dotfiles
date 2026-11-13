"""Tests for git-push-review.sh (.claude JSON-ask variant, .codex exit-2 variant)."""

import concurrent.futures
import json
import os
import re
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
        # separate token, which a naive regex fails to match (bypass).
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
        # A naive "remove '...' then remove \"...\"" pass would let the
        # apostrophe in "it's" pair up with the *next* single quote
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
        # Mirror-image case: swapping quote kinds must not hide the push either.
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
        # double-quoted range would hide it from detection (bypass).
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
        # requiring whitespace/EOL after `push` would let `git push;true` through.
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
        # context-lines flag) must not shadow the push target's own -C. A
        # leftmost-match extraction would grab `grep -C 3`'s value and run
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
        # Without jq the command string cannot be extracted, so push detection
        # would silently match nothing and the hook exit 0 -- the one gate in
        # front of `git push` would vanish without a trace. Absence must
        # degrade to a coarse ask, never to a silent pass.
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
# Every case runs against both copies so a detection regression in either one
# cannot ship green — the same drift-guard rationale as test_hook_sync.py.
DETECTION_CASES = [
    # (command, should_detect)
    ("git push origin main", True),
    ("git add -A && git commit -m x && git push", True),
    ("git --no-pager push", True),
    ("git -C /tmp/repo push", True),
    ("git --git-dir /tmp/repo/.git push origin main", True),
    ("git -c user.name=x push", True),
    ("git push;true", True),
    # A redirect glued to `push` is still a push. The end-of-token class needs
    # `>` and `<` as well as `;&|)` and the backtick, or `git push>/dev/null`
    # slips past while `git push </dev/null` is caught.
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
    # --- A2-a: a comment, a heredoc body or a `$'..'` string must not open a phantom
    # quote. The lone apostrophe used to swallow the real push on a LATER line.
    ("# Check what's changed\ngit status\ngit push origin main", True),
    ("git commit -F - <<'EOF'\ndon't\nEOF\ngit push", True),
    ('git commit -m "x" # it\'s\ngit push', True),
    ("echo $'it\\'s'; git push", True),
    ("# it's\ngit --no-pager push", True),
    # every heredoc spelling: unquoted / <<- (tab-indented delimiter) / "quoted"
    ("cat <<EOF\ndon't\nEOF\ngit push", True),
    ("cat <<-EOF\n\tdon't\n\tEOF\ngit push", True),
    ('cat <<"EOF"\ndon\'t\nEOF\ngit push', True),
    # the rest of the `<<WORD` line is still code
    ("cat <<'EOF' && git push\ndon't\nEOF", True),
    # an unterminated quote fails toward detection (bash itself would reject it)
    ("echo don't; git push", True),
    ("echo \"it's; git push", True),
    # guards: these were detected before the change and must stay detected. A heredoc
    # body can be CODE (`bash <<EOF`), `$(...)` in an unquoted heredoc executes, and a
    # `#` that is not at word start is not a comment.
    ("bash <<'EOF'\ngit push origin main\nEOF", True),
    # a heredoc body that is code folds its line continuations, as the shell does
    ("bash <<'EOF'\ngit \\\npush origin main\nEOF", True),
    ("sh -s <<EOF\ngit pu\\\nsh\nEOF", True),
    # `<<` in arithmetic is a shift, not a heredoc: a `<<WORD` with no WORD line must
    # not swallow the lines after it (a line-continued push there still runs)
    ("echo $((1<<2))\ngit \\\npush", True),
    ("let x=1<<2\ngit pu\\\nsh", True),
    ('echo $((1<<2));\ngit "pu\\\nsh"', True),
    ("sh -s <<EOF\ngit push\nEOF", True),
    ("cat <<EOF\n$(git push)\nEOF", True),
    ('cat <<< "it\'s"; git push', True),
    ("echo $#; git push", True),
    ("echo ${#PATH}; git push", True),
    ('echo "x"#y; git push', True),
    # `)` of a `$(...)` / `$((...))` does not start a new word: `#x` continues it, so the
    # shell goes on to run the push after the `;` (checked in bash 3.2 and zsh)
    ('echo "z" $(true)#x; git push', True),
    ('echo "z" $((1))#x; git push', True),
    ('echo "z" `true`#x; git push', True),
    ('echo "z" ${HOME}#x; git push', True),
    # a `#` inside `${...}` (here after a space) is part of the pattern, not a comment
    ('echo "z" ${x/ #/y}; git push', True),
    ("echo a\\#b; git push", True),
    ("echo it\\'s # c\ngit push", True),
    # a real comment is inert, so push words inside it do not count
    ("git status # it's not git push", False),
    ("# it's git push time\ngit status", False),
    ('git commit -m "x" # git push later', False),
    # --- A2-b: a quoted span with no whitespace is one word, so its content counts.
    ('"git" push origin main', True),
    ('git "push" origin main', True),
    ("git 'push' origin", True),
    ("git $'push' origin", True),
    ('git $"push" origin', True),
    ('g"i"t push', True),
    ('"/usr/bin/git" push', True),
    ('git -C "/tmp/x" "push"', True),
    ('git "pu""sh"', True),
    # ...but a span WITH whitespace is still a message, not a command
    ('git commit -m "git push docs"', False),
    ("git commit -m 'git push docs'", False),
    ('git commit -m "push"', False),
    ('git "stash" push', False),
    ('git pus"XX"h', False),
    # ...and a bare `push` WORD after another subcommand is that subcommand's argument,
    # quoted or not (these did not prompt before the quoted-word change, and must not)
    ('git --no-pager log --grep "push"', False),
    ("git log -S 'push'", False),
    ('git --no-pager commit -m "push"', False),
    ('echo "git" "push"', False),
    ("echo 'git' 'push'", False),
    ("git --no-pager log --grep push", False),
    ("git -c core.pager=cat commit -m push", False),
    # the quoted-word rule applies where git is the command: after an env assignment or
    # a shell prefix word too
    ('FOO=1 git "push"', True),
    ('time "git" push', True),
    # ...and after reserved words and wrapper commands, where the push still runs
    ('if true; then git "push"; fi', True),
    ('if true; then "git" push; fi', True),
    ('{ git "push"; }', True),
    ('! git "push"', True),
    ("for x in 1; do git 'push'; done", True),
    ('case x in x) git "push";; esac', True),
    ('env A=1 git "push"', True),
    ('timeout 5 git "push"', True),
    ('nice git "push"', True),
    ('command -p git "push"', True),
    # options whose value is a separate word are skipped as a pair
    ("git --namespace n push", True),
    ("git --work-tree /w --git-dir /g push", True),
    # --- A2-c: forms that execute a string argument
    ("git submodule foreach 'git push origin main'", True),
    ('git submodule foreach --recursive "git push"', True),
    ('git rebase --exec "git push" HEAD~1', True),
    ('git rebase -x "git push" HEAD~1', True),
    ("git rebase -i --exec='git push' main", True),
    ('watch "git push"', True),
    ("watch -n 5 'git push origin main'", True),
    ("eval $'git push origin main'", True),
    ('git submodule foreach "git status"', False),
    ('git rebase --exec "make test" HEAD~1', False),
    ('git rebase main && echo "git push"', False),
    ('watch "ls -la"', False),
    # --- A backslash-escaped space inside a global option's value: the value
    # unit matched `[^[:space:]]+`, so `\ ` ended it early and `b` was read as
    # the subcommand. These start with `git -C`, so `ask: Bash(git push:*)` does
    # not catch them either, while `allow: Bash(git:*)` does.
    ("git -C /tmp/a\\ b push", True),
    ("git -C ~/My\\ Project push origin main", True),
    ("git -c user.name=My\\ Name push", True),
    ("git --git-dir=/x\\ y/.git push", True),
    ("git --work-tree /x\\ y push", True),
    # ...while an escaped BACKSLASH before the space is a literal `\` that ends
    # the value (regressed when `\ ` started to be kept).
    ("git -C /tmp/x\\\\ push", True),
    ("git -c core.x=a\\\\ push", True),
    ("git --git-dir=/g\\\\ --work-tree=/w\\\\ push", True),
    # A string-executing form whose single argument has an escaped space or tab:
    # the inner shell splits `git\ push` into `git push`.
    ("eval git\\ push", True),
    ("sh -c git\\ push", True),
    ("bash -c git\\ push", True),
    ("watch git\\ push", True),
    ("eval git\\\tpush", True),
    # --- Shell options before `-c`, or `c` not last in its cluster: only
    # dash-words were allowed between the shell and a cluster ending in `c`.
    ("bash -euo pipefail -c 'git push'", True),
    ("bash -o pipefail -c 'git push'", True),
    ("bash -O extglob -c 'git push'", True),
    ("bash +e -c 'git push'", True),
    ("bash -cl 'git push'", True),
    ("sh -ce 'git push origin main'", True),
    # --- A line continuation inside the executed string: the inner shell folds
    # `\<newline>` away, so the copy must delete it rather than leave a split.
    ("bash -c 'git\\\n push'", True),
    ("eval 'git\\\n push'", True),
    # --- Code fed to a shell on stdin, and shells reached by other names.
    ('echo "git push" | sh', True),
    ("printf 'git push\\n' | bash", True),
    ("bash <<< 'git push'", True),
    ("$SHELL -c 'git push'", True),
    ("fish -c 'git push'", True),
    # ...while a shell merely named in a message, or run on a script, stays quiet.
    ('git commit -m "fix bash completion for git push"', False),
    ('bash scripts/test.sh && git commit -m "no git push yet"', False),
    ('echo "git push" > notes.txt', False),
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
# deciding COSTS. Two guards keep it cheap in both copies: an early "no `push` substring
# => exit 0" short-circuit, and a fast path that skips the character-at-a-time
# strip_quoted_ranges when the command holds no quote or backslash. The .codex copy is
# wired unconditionally on matcher "Bash" (.codex/hooks.json.template), so without them
# every Bash call in a Codex session would pay the full O(n^2) scan (~4.5s for a 20KB
# command).
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

    The -C value must not be read off cmd_for_match, i.e. AFTER
    strip_quoted_ranges has removed every quoted range -- the path included:
    the regex would then take the next token (`push`) as the directory,
    `git -C push rev-parse` would fail, and the confirmation would carry an
    empty summary: an ask the user cannot judge.
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
def test_dash_c_with_an_escaped_space_summarises_that_repo(
    shell_env, tmp_path, git_repo, variant, hook
):
    """`git -C /a\\ b push` is the same repository as `git -C "/a b" push`.

    The hook runs in git_repo (branch main, "initial commit"), so a summary that
    fell back to the hook's cwd would show that repository instead.
    """
    base = tmp_path / "has space"
    base.mkdir()
    target = make_target_repo(base)
    escaped = str(target).replace(" ", "\\ ")
    res = shell_env.run(hook, stdin=payload(f"git -C {escaped} push"), cwd=git_repo)
    _assert_push_detected(res, variant, escaped)
    reason = _summary_text(res, variant)
    assert "initial commit" not in reason, reason
    assert "target repo commit" in reason or "could not be determined" in reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template",
    [
        "timeout 5 git -C {t} push",
        "env X=1 git -C {t} push",
        "nice git -C {t} push",
        "bash -c 'git -C {t} push'",
        "git push --dry-run && git -C {t} push",
    ],
)
def test_dash_c_the_hook_cannot_tie_to_the_push_never_summarises_the_cwd(
    shell_env, tmp_path, git_repo, variant, hook, template
):
    """A `-C` behind a wrapper or inside a string was not read, and the summary
    silently described the hook's cwd (git_repo) -- the README's "a summary of
    the wrong repository is worse than none". Either the target's summary or the
    "could not be determined" note is acceptable; the cwd's commits are not."""
    target = make_target_repo(tmp_path)
    command = template.format(t=target)
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, command)
    reason = _summary_text(res, variant)
    assert "initial commit" not in reason, reason
    # ...and the prompt says so, rather than carrying an empty summary.
    assert "could not be determined" in reason or "target repo commit" in reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_large_quoted_push_command_stays_fast(shell_env, git_repo, variant, hook):
    """Quotes AND a push: neither existing guard applies, so this must be bounded.

    The "no push substring" short-circuit and the "no quote" fast path both
    miss a long heredoc or commit message chained to a push -- the common
    shape -- and the O(n^2) quote scan would run in full (17s measured at 40KB)
    on every such Bash call. Past a size cap the scan falls back to dropping the
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


# Under the 4000-character cap the quote scan runs in full, and strip_span_counts runs
# once per whitespace-free quoted span. Locating the span's segment with a leading-`*`
# pattern (`${out##*[;&|(]}`) re-tries every prefix of the output so far, so a command
# of many short quoted words went cubic: 50s for 3.6KB measured, against 0.6s without
# span counting. A hook that slow outruns the hook timeout, and a timed-out push hook
# lets the push through unreviewed. The budget is loose for the same reason as above;
# it separates "under a second or two" from "tens of seconds".
_SPAN_SCAN_BUDGET_SECONDS = 8.0


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "body",
    [
        "echo 'a'\n" * 430,
        'echo "a"\n' * 430,
        "git push origin main " + '"a" ' * 900,
        # every `<<` here lacks a terminator line, so none of them is a heredoc; the
        # terminator lookup must not rescan the rest of the command per `<<`
        "echo $((1<<2))\n" * 250,
    ],
    ids=[
        "single-quoted-lines",
        "double-quoted-lines",
        "one-long-segment",
        "arithmetic-shift-lines",
    ],
)
def test_many_quoted_words_under_the_cap_stay_fast(
    shell_env, git_repo, variant, hook, body
):
    """Thousands of characters of short quoted words, still under the size cap."""
    big = "git -C . push origin main\n" + body
    assert len(big) <= 4000, len(big)
    started = time.monotonic()
    res = shell_env.run(hook, stdin=payload(big), cwd=git_repo)
    elapsed = time.monotonic() - started

    _assert_push_detected(res, variant, "git -C . push + many quoted words")
    assert elapsed < _SPAN_SCAN_BUDGET_SECONDS, (
        f"{variant}: a {len(big)}-char command of short quoted words took "
        f"{elapsed:.2f}s -- per-span segment lookup is super-linear"
    )


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_dash_c_of_an_unrelated_chained_git_call_does_not_supply_the_summary(
    shell_env, tmp_path, variant, hook
):
    """The -C that counts is the one on the git call that pushes.

    A regex over the whole command would take the leftmost quoted `-C` it can
    find, so `git -C "/gone" push && git -C "decoy" status` would summarise the
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

    The quote-stripped command reads `git -C  push`; preferring a -C value
    that is an existing directory would pick `push/` -- a decoy repo -- over
    the real quoted target. Word-splitting sees the quoted value and never
    consults the filesystem to choose.
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


# --- `cd <dir>` retargets the summary -----------------------------------------
# `-C` is not the only way a chained command changes the repo a push lands in:
# a plain `cd <dir> && git push` (shell builtin, no -C at all) changes the real
# cwd for every later command in the same shell. A summary generated for the
# hook's OWN cwd regardless would be the same "wrong summary is worse than no
# summary" gate defect as the -C case, just via a different verb.
#
# What the hook resolves with certainty, and what it gives up on (NO summary plus a
# short note, never a fallback to the cwd summary), is documented on
# cd_target_from_words in .claude/hooks/git-push-review.sh (the .codex copy points
# there); the cases below pin it.
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
    fallback must not grep cmd_for_match -- strip_quoted_ranges output, which
    DELETES quoted ranges' contents -- for cd/pushd/popd. A quoted `"cd"`
    (quoting a command name doesn't stop the shell recognising it as the cd
    builtin) would disappear from that string entirely, so the wrong (hook
    cwd) summary would be shown. The fallback must look at the command with
    quote CHARACTERS stripped but quoted CONTENTS kept, so a real `"cd"` still
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
    # `cd`/`pushd`/`popd` that is not the FIRST word of its segment: a
    # tokenizer that only checks the first word would miss a cd hiding behind
    # a group, a builtin-dispatch prefix, or a leading assignment, and the
    # summary would silently keep the (wrong) cwd data.
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
    # cwd, not X. Walking only one boundary past cd would miss this because
    # that boundary is AND, not BG.
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
    back to the git_c_dir_from_words -C lookup.
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
# push never runs in. The rules (bash 3.2, bash 5 and zsh agree) are documented on
# cd_target_from_words and at the raw-control-byte check in
# .claude/hooks/git-push-review.sh; the cases below pin them:
#   - Inside "...", a backslash escapes only $ ` " \ and newline; before any other
#     character it stays literal, so `cd "/x/a\b"` enters `a\b`, not `ab`.
#     Backslash-newline is removed (line continuation) unquoted and inside "...",
#     but is literal inside '...'.
#   - A leading `~` expands only when it is unquoted and nothing up to the first
#     unquoted `/` is quoted; mixed forms (`~"/x"`, `''~/x`) split the shells.
#   - Those mixed forms, a target holding a newline or other control character, a raw
#     control byte anywhere in the command, and a target that is not an existing
#     directory (that `cd` fails and the push runs elsewhere) all get the note.
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
    # non-repo cwd. Reading the target back through `read` would stop at the
    # newline and summarise <repo> instead.
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
    # unquoted rule) would make the hook summarise the real target-repo instead.
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
    # `cd <outer>/\<newline>inner` is `cd <outer>/inner` to the shell. Keeping
    # the newline in the word and cutting it there when reading it back would
    # summarise <outer> -- a real, different repository.
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
    # `~`), so the push runs in cwd. Dropping the quotes and applying tilde
    # expansion anyway would summarise $HOME/proj.
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
    # more of echo's arguments. The tokenizer uses 0x02 as its own segment
    # separator, so unguarded it would see `cd <repo>` as a command and
    # summarise <repo>.
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
    # The -C resolver has the same weakness with its own separator, 0x01: a
    # forged `git -C <repo> push` inside echo's arguments must not supply the
    # summary while the real push (the second one) runs in cwd.
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
    # the value, which would leave exactly <repo>.
    target = make_target_repo(tmp_path)
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    cmd = f"git -C {dash_c.format(t=target)} push origin main"
    res = shell_env.run(hook, stdin=payload(cmd), cwd=outside)
    _assert_note_only(res, variant, cmd, "target repo commit")


# --- cd operand shapes that the hook does not model --------------------------
# More than one operand (the shells disagree), an option word (`-x`, `-L`, `-P`, `-e`,
# `-@`, `--`: invalid ones make cd fail, valid ones change how the operand resolves) and
# a `..` component (the shells' cd is logical, `git -C` physical) each send the real
# `cd` somewhere other than the literal operand; see cd_target_from_words. All of them
# get the note. Redirections such as `2>/dev/null` count as operands for the hook too --
# a note where the shell would have resolved is acceptable. Bare `cd` -> $HOME and
# `cd -` -> note are unaffected.
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
    # option would leave a bare `cd`, which the hook resolves to $HOME.
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
# cd, and `--work-tree` / `GIT_WORK_TREE` travel with them. A summary that follows
# only -C would show the hook cwd's repository while the push acts on another one.
# Any mention of the four names -- even inside quoted text, and after dropping
# quotes/backslashes that the shell would remove -- gets the note.
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
# is a real push that must not go through with no confirmation at all. Such a
# word counts as a global option; the push is gated (ask / exit 2) and, as the
# repository it selects is unknown, the summary is a note.
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


# The same expansion inside double quotes still expands, but a summary check on the
# quote-stripped copy, where the quoted word is gone, would show the hook cwd's
# summary while the push acts on <B>. The check therefore also looks at the command
# with only the quote characters dropped (a `$` inside '...' is literal to the
# shell; over-matching it only costs a note).
QUOTED_EXPANSION_PUSH_FORMS = [
    'D=--git-d; git "${{D}}ir={b}/.git" push origin main',
    'git "$GIT_OPTS" push origin main',
    # Detection itself has to catch this one: without the quoted word the quote-
    # stripped command reads `git  <dir> push`, and `<dir>` is neither a flag nor
    # an expansion -- yet with OPT=-C it is a real push.
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
    # Detection also reads the command with only the quote characters
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


# --- B10-a: the summary follows the PUSH segment's own -C -------------------------
# A `-C` on some other git call in the same command line must never retarget the
# summary of a push that has no -C of its own: cwd is the repository the push runs in.
# "A summary of the wrong repository is worse than none."
OWN_DASH_C_CASES = [
    # (template, summarised repo) -- {B} is the other repo, {A} is the hook's cwd
    ('git -C "{B}" status && git push', "a"),
    ('git -C "{B}" log --oneline; git push origin main', "a"),
    ('git -C "{B}" fetch && git push', "a"),
    ('git push && git -C "{B}" status', "a"),
    # `stash push` is not a push: the real push below carries the -C
    ('git stash push && git -C "{B}" push', "b"),
    ('git -C "{A}" status && git -C "{B}" push', "b"),
    # `-c k=v` takes a value; it must not hide a later -C or the subcommand
    ('git -c core.pager=cat -C "{B}" push', "b"),
    ('git -C "{B}" -c core.pager=cat push', "b"),
    # a wrapper word before `git` must not hide the push segment from the reader (it
    # would then fall back to the other call's -C)
    ('git -C "{B}" status && FOO=1 git push', "a"),
    ('git -C "{B}" status && time git push', "a"),
    ('FOO=1 git -C "{B}" push', "b"),
    ('time git -C "{B}" push', "b"),
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("template,expected", OWN_DASH_C_CASES)
def test_summary_uses_the_dash_c_of_the_push_segment_only(
    shell_env, tmp_path, git_repo, variant, hook, template, expected
):
    other = make_target_repo(tmp_path)
    cmd = template.format(A=git_repo, B=other)
    res = shell_env.run(hook, stdin=payload(cmd), cwd=git_repo)
    _assert_push_detected(res, variant, cmd)
    reason = _summary_text(res, variant)
    if expected == "a":
        assert "branch: main" in reason, reason
        assert "feature-target" not in reason, reason
    else:
        assert "branch: feature-target" in reason, reason


# --- B10-b: the reason states what the push TARGETS, not just HEAD vs upstream -----
def _push_reason(shell_env, hook, variant, cmd, cwd):
    res = shell_env.run(hook, stdin=payload(cmd), cwd=cwd)
    _assert_push_detected(res, variant, cmd)
    return _summary_text(res, variant)


# (command, substrings that must appear in the WARNING part)
DESTRUCTIVE_PUSH_CASES = [
    ("git push --force origin main", ["--force"]),
    ("git push -f origin main", ["--force"]),
    ("git push --force-with-lease origin main", ["--force-with-lease"]),
    (
        "git push --force-with-lease=main:abc123 origin main",
        ["--force-with-lease=main:abc123"],
    ),
    ("git push --force-if-includes origin main", ["--force-if-includes"]),
    ("git push origin +main", ["+main"]),
    ("git push origin +main:main", ["+main:main"]),
    ("git push --delete origin old-branch", ["--delete"]),
    ("git push -d origin old-branch", ["--delete"]),
    ("git push origin :old-branch", [":old-branch"]),
    ("git push --mirror origin", ["--mirror"]),
    ("git push --all origin", ["--all"]),
    ("git push --tags origin", ["--tags"]),
    ("git push --prune origin", ["--prune"]),
    # short-flag clusters, either order
    ("git push -fu origin main", ["--force"]),
    ("git push -uf origin main", ["--force"]),
    ("git push -df origin old", ["--force", "--delete"]),
    # options after the positionals count too
    ("git push origin main --force", ["--force"]),
    # a quoted option is still the option
    ('git push "--force" origin main', ["--force"]),
    ("git push 'origin' '+main'", ["+main"]),
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,named", DESTRUCTIVE_PUSH_CASES)
def test_force_and_destructive_push_options_are_called_out(
    shell_env, git_repo, variant, hook, cmd, named
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "WARNING" in reason, reason
    for fragment in named:
        assert fragment in reason.split("WARNING", 1)[1], (fragment, reason)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push",
        "git push origin main",
        "git push -u origin HEAD",
        "git push --set-upstream origin main",
        "git push -o ci.skip origin main",
        "git push --dry-run origin main",
        "git push --follow-tags origin main",
        "git push origin main:main",
        "git push origin main # force it",
        "git push origin main > /dev/null 2>&1",
    ],
)
def test_an_ordinary_push_carries_no_destructive_warning(
    shell_env, git_repo, variant, hook, cmd
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "WARNING" not in reason, reason
    assert "not the current branch" not in reason, reason
    assert "commits to push:" in reason, reason


PUSH_TARGET_CASES = [
    # (command, exact `push target:` line)
    ("git push origin main", "push target: remote=origin refspec=main"),
    ("git push -u origin HEAD", "push target: remote=origin refspec=HEAD"),
    # `-o ci.skip` takes a value: it must not be read as the remote
    ("git push -o ci.skip origin main", "push target: remote=origin refspec=main"),
    ("git push --push-option=x origin main", "push target: remote=origin refspec=main"),
    ("git push --repo=upstream main", "push target: remote=upstream refspec=main"),
    ("git push origin main feature", "push target: remote=origin refspec=main feature"),
    ("git push origin", "push target: remote=origin refspec=(default)"),
    ("git push", "push target: remote=(default) refspec=(default)"),
    ("git push \"origin\" 'main'", "push target: remote=origin refspec=main"),
    ("git push origin main && echo done", "push target: remote=origin refspec=main"),
    ("git push origin main # it's fine", "push target: remote=origin refspec=main"),
    ("git push origin main > /tmp/log 2>&1", "push target: remote=origin refspec=main"),
    ("git --no-pager push origin main", "push target: remote=origin refspec=main"),
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd,line", PUSH_TARGET_CASES)
def test_the_reason_names_the_remote_and_refspecs(
    shell_env, git_repo, variant, hook, cmd, line
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert line in reason.splitlines(), reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_push_target_comes_before_the_branch_summary(
    shell_env, git_repo, variant, hook
):
    reason = _push_reason(
        shell_env, hook, variant, "git push --force origin main", git_repo
    )
    assert reason.index("push target:") < reason.index("branch: main"), reason
    assert reason.index("WARNING") < reason.index("branch: main"), reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push origin feature:main",
        "git push origin other",
        "git push origin other:other",
        "git push --all origin",
        "git push --tags origin",
        "git push --mirror origin",
        "git push --delete origin old",
    ],
)
def test_a_target_that_is_not_the_current_branch_is_not_presented_as_the_commit_list(
    shell_env, git_repo, variant, hook, cmd
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "commits to push:" not in reason, reason
    assert "NOT necessarily" in reason, reason
    # the HEAD summary is still shown, labelled for what it is
    assert "branch: main" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_note_names_the_refspec_source_that_differs(
    shell_env, git_repo, variant, hook
):
    reason = _push_reason(
        shell_env, hook, variant, "git push origin feature:main", git_repo
    )
    assert "refspec source 'feature' is not the current branch 'main'" in reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        "git push origin HEAD",
        "git push origin main",
        "git push origin refs/heads/main",
        "git push origin HEAD:refs/heads/main",
        "git push origin main:main",
        "git push origin +HEAD",
        "git push origin @",
        "git push -u origin HEAD",
    ],
)
def test_a_refspec_that_is_the_current_branch_keeps_the_commit_list(
    shell_env, git_repo, variant, hook, cmd
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "not the current branch" not in reason, reason
    assert "commits to push:" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        'git push origin "$BRANCH"',
        "git push origin $(git branch --show-current)",
        'git push origin "HEAD:$DEST"',
    ],
)
def test_an_expansion_in_the_refspec_is_reported_as_unverifiable(
    shell_env, git_repo, variant, hook, cmd
):
    # Never claim a mismatch (or a match) the hook cannot evaluate.
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "expansion" in reason, reason
    assert "not the current branch" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_every_push_in_a_chain_is_reported(shell_env, git_repo, variant, hook):
    cmd = "git push origin a && git push --force origin b"
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert reason.count("push target") == 2, reason
    assert "remote=origin refspec=a" in reason, reason
    assert "remote=origin refspec=b" in reason, reason
    assert "--force" in reason.split("WARNING", 1)[1], reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "cmd",
    [
        'eval "git push --force origin main"',
        "watch 'git push -f'",
        "git submodule foreach 'git push --force'",
        'git rebase --exec "git push -f" HEAD~1',
    ],
)
def test_a_push_inside_an_executed_string_says_its_args_were_not_parsed(
    shell_env, git_repo, variant, hook, cmd
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "push arguments could not be parsed" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_submodule_foreach_push_does_not_get_this_repos_summary(
    shell_env, git_repo, variant, hook
):
    # The push runs inside each submodule, not in the hook's repository.
    cmd = "git submodule foreach 'git push origin main'"
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "target repository could not be determined" in reason, reason
    assert "branch: main" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_unrecognised_push_option_is_flagged(shell_env, git_repo, variant, hook):
    reason = _push_reason(
        shell_env, hook, variant, "git push --made-up origin main", git_repo
    )
    assert "unrecognised push option" in reason, reason
    assert "--made-up" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_the_push_target_survives_an_unresolvable_repository(
    shell_env, tmp_path, git_repo, variant, hook
):
    # Even when the repository summary is a note, the push's own arguments are
    # exact information about the command and must still reach the prompt.
    cmd = f"cd {tmp_path}/no-such-dir && git push --force origin main"
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "target repository could not be determined" in reason, reason
    assert "WARNING" in reason, reason
    assert "push target: remote=origin refspec=main" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_credentials_in_a_remote_url_are_not_echoed(shell_env, git_repo, variant, hook):
    cmd = "git push https://user:s3cr3t@example.com/r.git main"
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "s3cr3t" not in reason, reason
    assert "example.com/r.git" in reason, reason


# --- the argument/-C reader follows the same shell syntax as the detector ----------
# The commit-message-then-push chain is the most common way a push is spelled, and a
# comment or heredoc body with a lone apostrophe must not blind the reader to the
# push that follows (it would fall back to "could not be parsed" or, worse, the wrong
# -C).
HEREDOC_THEN_FORCE_PUSH = [
    "git commit -m \"$(cat <<'EOF'\nfix: a thing\nEOF\n)\" && git push --force origin main",
    "git commit -m \"$(cat <<'EOF'\nfix: don't break\nEOF\n)\" && git push --force origin main",
    "git commit -F - <<'EOF'\ndon't\nEOF\ngit push --force origin main",
    "cat <<-EOF\n\tit's\n\tEOF\ngit push --force origin main",
    "# what's up\ngit push --force origin main",
    'git commit -m "$(echo a)" -m "$(echo b)" && git push --force origin main',
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("cmd", HEREDOC_THEN_FORCE_PUSH)
def test_a_force_push_after_a_heredoc_or_comment_is_still_called_out(
    shell_env, git_repo, variant, hook, cmd
):
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "push target: remote=origin refspec=main" in reason, reason
    assert "--force" in reason.split("WARNING", 1)[1], reason
    assert "could not be parsed" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_dash_c_after_a_commit_message_heredoc_still_selects_the_repo(
    shell_env, tmp_path, git_repo, variant, hook
):
    other = make_target_repo(tmp_path)
    cmd = (
        "git commit -m \"$(cat <<'EOF'\nfix: don't break\nEOF\n)\" "
        f'&& git -C "{other}" push'
    )
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "branch: feature-target" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_an_arithmetic_shift_does_not_hide_the_push_segment_from_the_dash_c_reader(
    shell_env, tmp_path, git_repo, variant, hook
):
    # `$((1<<2))` is a shift. Read as a heredoc opener with no `2` line, it would skip
    # every later line as body, lose the push segment, and summarise the cwd repo.
    other = make_target_repo(tmp_path)
    cmd = f'echo $((1<<2))\ngit -C "{other}" push'
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "branch: feature-target" in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
def test_a_push_hidden_in_a_heredoc_body_is_not_reported_as_the_target(
    shell_env, git_repo, variant, hook
):
    # `bash <<EOF` runs its body, so it is still caught...
    cmd = "bash <<'EOF'\ngit push --force origin main\nEOF"
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    # ...but the argument reader skips heredoc bodies, so it must say it did not parse
    assert "push arguments could not be parsed" in reason, reason


# --- a -C that belongs to a DIFFERENT segment than the push never picks the summary -----
# When the reader cannot find the push segment (the push is inside an executed string),
# the only -C it may fall back to is one on the segment that carries that string.
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template",
    [
        'git -C "{B}" status && eval "git push"',
        'git -C "{B}" status && watch "git push"',
        'git -C "{B}" status; sh -c "git push origin main"',
    ],
)
def test_a_dash_c_on_another_segment_is_not_used_for_a_push_inside_a_string(
    shell_env, tmp_path, git_repo, variant, hook, template
):
    other = make_target_repo(tmp_path)
    cmd = template.format(B=other)
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "target repository could not be determined" in reason, reason
    assert "feature-target" not in reason, reason
    assert "branch:" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "template",
    [
        'git -C "{B}" rebase --exec "git push" HEAD~1',
        "git -C \"{B}\" rebase -x 'git push origin main' HEAD~1",
    ],
)
def test_dash_c_on_the_segment_that_carries_the_pushing_string_still_selects_the_repo(
    shell_env, tmp_path, git_repo, variant, hook, template
):
    other = make_target_repo(tmp_path)
    cmd = template.format(B=other)
    reason = _push_reason(shell_env, hook, variant, cmd, git_repo)
    assert "branch: feature-target" in reason, reason


# --- pushes that are not spelled `git … push` ------------------------------------
# `git subtree push`, the plumbing `git push` itself runs (send-pack / http-push) and
# an alias all push without that word order. permissions.ask only matches a `git push`
# prefix and `Bash(git:*)` is allowed, so this hook is the one place that asks.
INDIRECT_PUSHES = [
    "git subtree push --prefix=docs origin gh-pages",
    "git subtree -P docs push origin gh-pages",
    "git -C /tmp subtree push --prefix=a origin main",
    "git add -A && git subtree push --prefix=a origin main",
    "git send-pack origin main",
    "git --no-pager send-pack origin main",
    "git http-push https://example.com/r.git main",
    "git -c alias.p=push p origin main",
    "git -c 'alias.p=push --force' p",
    "git config alias.ps push",
    "git config --global alias.ps 'push --force-with-lease'",
]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("command", INDIRECT_PUSHES)
def test_a_push_under_another_name_is_confirmed(
    shell_env, git_repo, variant, hook, command
):
    reason = _push_reason(shell_env, hook, variant, command, git_repo)
    # The usual commit summary would describe a push this command does not make.
    assert "target repository could not be determined" in reason, reason
    assert "branch:" not in reason, reason


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        "git subtree add --prefix=docs origin main",
        "git subtree split --prefix=push-notes",
        "git log --grep send-pack",
        "git log --oneline -- send-pack.c",
        "git config alias.st status",
        "git config --get alias.ps",
        "echo 'git send-pack origin main'",
        "git commit -m 'explain git subtree push'",
    ],
)
def test_a_lookalike_of_an_indirect_push_is_not_confirmed(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    assert res.returncode == 0, command
    assert res.stdout == "", command


# --- the over-4000-character fallback keeps what the quote scan would have resolved ---
# Past the cap the hook stops running its quote state machine and only drops quote
# characters. A backslash the state machine would have removed (`pu\sh`) and a line
# continuation (`git -C /x \<newline> push`) then hid a real push behind any long
# command, typically a heredoc or a commit message.
_LONG_PREFIX = 'echo "' + "x" * 4200 + '"; '


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "tail",
    [
        "git pu\\sh origin main",
        "git -C /tmp \\\n  push origin main",
        "git \\\n push",
        "gi\\t push origin main",
    ],
)
def test_a_backslash_does_not_hide_a_push_past_the_length_cap(
    shell_env, git_repo, variant, hook, tail
):
    command = _LONG_PREFIX + tail
    assert len(command) > 4000
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, tail)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("tail", ["git status", "git stash push", "echo pu\\sh"])
def test_a_long_command_without_a_push_still_passes(
    shell_env, git_repo, variant, hook, tail
):
    res = shell_env.run(hook, stdin=payload(_LONG_PREFIX + tail), cwd=git_repo)
    assert res.returncode == 0, tail
    assert res.stdout == "", tail


# --- an alias is read from the command the shell runs, not from quoted text ------
@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        "git conf\\ig alias.p push",
        "git -c al\\ias.p=push p",
        "git -\\c alias.p=push p",
        'git "config" alias.p push',
    ],
)
def test_a_backslash_or_quote_does_not_hide_an_alias(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    _assert_push_detected(res, variant, command)


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "doc: git config alias.ps push example"',
        "git commit -m 'explain git -c alias.p=push p'",
        'echo "git config alias.ps push"',
        "printf '%s\\n' 'git -c alias.p=push p'",
    ],
)
def test_an_alias_only_mentioned_in_quoted_text_is_not_confirmed(
    shell_env, git_repo, variant, hook, command
):
    res = shell_env.run(hook, stdin=payload(command), cwd=git_repo)
    assert res.returncode == 0, command
    assert res.stdout == "", command


# =============================================================================
# Output snapshots: what the two adapters emit, byte for byte
# =============================================================================
# The shared logic lives in one sourced library (_push_review_common.sh) and each
# adapter only chooses how to emit the result: Claude as a JSON "ask" on stdout
# (exit 0), Codex -- which has no "ask" -- as the same text on stderr (exit 2). These
# snapshots pin the complete emitted text, so a refactor of where the logic lives
# cannot change what the user is shown without a test saying so. Two details are the
# easy ones to lose and are pinned on purpose:
#   * the whitespace of the report (the line breaks between the `push target:` line,
#     the notes and the summary), and
#   * the TRAILING newline: with no repository the summary is empty, so the report's
#     own final "\n" is the last byte. Capturing the result with `$(...)` instead of
#     a variable strips it, and nothing else in the suite would notice.
CLAUDE_HEADER = (
    "git push detected. Review before pushing (see ~/.claude/rules/git-workflow.md):"
)
CODEX_HEADER = 'git push detected. Review before pushing (see ~/.codex/AGENTS.md "## 3. Git Workflow"):'

_NOT_PARSED_NOTE = (
    "note: push arguments could not be parsed reliably (the push is not a plain "
    "`git ... push` word sequence -- e.g. it is inside eval, sh -c, watch, --exec or "
    "foreach -- or its quoting is not followable) -- review the command itself\n"
)

# (id, command template, where the hook runs, expected text after the header line).
# {target} is a second repository; <HASH> stands for an abbreviated commit id.
SNAPSHOT_CASES = [
    (
        "plain-push",
        "git push origin main",
        "repo",
        "push target: remote=origin refspec=main\n"
        "branch: main\n"
        "commits to push:\n"
        "<HASH> initial commit\n"
        "(no upstream: new branch push)",
    ),
    (
        "dash-C-push",
        "git -C {target} push",
        "outside",
        "push target: remote=(default) refspec=(default)\n"
        "branch: feature-target\n"
        "commits to push:\n"
        "<HASH> target repo commit\n"
        "(no upstream: new branch push)",
    ),
    (
        "force-push",
        "git push --force origin main",
        "repo",
        "push target: remote=origin refspec=main\n"
        "WARNING: force/destructive push options: --force\n"
        "branch: main\n"
        "commits to push:\n"
        "<HASH> initial commit\n"
        "(no upstream: new branch push)",
    ),
    (
        "cd-chain-push",
        "cd {target} && git push",
        "outside",
        "push target: remote=(default) refspec=(default)\n"
        "branch: feature-target\n"
        "commits to push:\n"
        "<HASH> target repo commit\n"
        "(no upstream: new branch push)",
    ),
    (
        "eval-push",
        'eval "git push origin main"',
        "repo",
        _NOT_PARSED_NOTE + "branch: main\n"
        "commits to push:\n"
        "<HASH> initial commit\n"
        "(no upstream: new branch push)",
    ),
    (
        "push-outside-a-repository",
        "git push",
        "outside",
        # No summary at all: the report's own final newline is the last byte.
        "push target: remote=(default) refspec=(default)\n",
    ),
    (
        "send-pack-note",
        "git send-pack origin main",
        "outside",
        _NOT_PARSED_NOTE + "(target repository could not be determined: the command "
        "runs git send-pack / http-push, the transport git push itself uses -- "
        "review the target manually before approving)",
    ),
]


def _snapshot_world(tmp_path, git_repo):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    target = make_target_repo(tmp_path)
    return {"repo": git_repo, "outside": outside, "target": target}


def _normalise(text, world):
    """Replace what differs between runs (commit ids, temp paths) with placeholders."""
    text = re.sub(r"(?m)^[0-9a-f]{7,40} ", "<HASH> ", text)
    for label in ("target", "repo", "outside"):
        text = text.replace(str(world[label]), f"<{label.upper()}>")
    return text


def _run_snapshot(shell_env, hook, template, world, where):
    command = template.format(target=world["target"])
    return shell_env.run(hook, stdin=payload(command), cwd=world[where])


@pytest.mark.parametrize(
    "case_id,template,where,expected",
    SNAPSHOT_CASES,
    ids=[c[0] for c in SNAPSHOT_CASES],
)
def test_claude_output_snapshot(
    shell_env, tmp_path, git_repo, case_id, template, where, expected
):
    world = _snapshot_world(tmp_path, git_repo)
    res = _run_snapshot(shell_env, CLAUDE_HOOK, template, world, where)

    assert res.returncode == 0
    assert res.stderr == ""
    parsed = json.loads(res.stdout)
    # The envelope: key order and compact separators are part of the contract with
    # the harness, and jq ends the line with exactly one newline.
    assert list(parsed) == ["hookSpecificOutput"]
    output = parsed["hookSpecificOutput"]
    assert list(output) == [
        "hookEventName",
        "permissionDecision",
        "permissionDecisionReason",
    ]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "ask"
    assert res.stdout == (
        json.dumps(parsed, separators=(",", ":"), ensure_ascii=False) + "\n"
    )
    assert _normalise(output["permissionDecisionReason"], world) == (
        CLAUDE_HEADER + "\n" + expected
    )


@pytest.mark.parametrize(
    "case_id,template,where,expected",
    SNAPSHOT_CASES,
    ids=[c[0] for c in SNAPSHOT_CASES],
)
def test_codex_output_snapshot(
    shell_env, tmp_path, git_repo, case_id, template, where, expected
):
    world = _snapshot_world(tmp_path, git_repo)
    res = _run_snapshot(shell_env, CODEX_HOOK, template, world, where)

    assert res.returncode == 2
    assert res.stdout == ""
    # The heredoc that prints the text ends it with one newline of its own, after
    # whatever the body ends with.
    assert _normalise(res.stderr, world) == CODEX_HEADER + "\n" + expected + "\n"


@pytest.mark.parametrize(
    "case_id,template,where,expected",
    SNAPSHOT_CASES,
    ids=[c[0] for c in SNAPSHOT_CASES],
)
def test_claude_reason_body_equals_codex_stderr_body(
    shell_env, tmp_path, git_repo, case_id, template, where, expected
):
    """The two adapters differ in the header line and the channel, nothing else."""
    world = _snapshot_world(tmp_path, git_repo)
    claude = _run_snapshot(shell_env, CLAUDE_HOOK, template, world, where)
    codex = _run_snapshot(shell_env, CODEX_HOOK, template, world, where)

    reason = json.loads(claude.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    claude_header, claude_body = reason.split("\n", 1)
    codex_header, codex_body = codex.stderr.split("\n", 1)

    assert claude_header == CLAUDE_HEADER
    assert codex_header == CODEX_HEADER
    # Codex's stderr is the reason plus the newline its heredoc appends.
    assert codex_body == claude_body + "\n"


# =============================================================================
# The shared library: how each adapter loads it, and what it does when it cannot
# =============================================================================
# Everything the two adapters share lives in .claude/hooks/_push_review_common.sh (the
# helper functions plus the entry point hook_push_review_run). Each adapter sources it
# only AFTER its cheap "no `push` / `send-pack` in the command" guard, so an ordinary
# Bash call never even parses it. For the calls that do reach the load, a library that
# is missing, empty, cut short, syntactically broken, or lacking any required function
# must NOT pass the push through silently: this hook is the gate in front of
# `git push`, and "could not load" is not "not a push". It degrades to a fixed-text
# confirmation request (Claude: JSON ask, exit 0; Codex: stderr, exit 2).
LIBRARY_NAME = "_push_review_common.sh"
CLAUDE_LIBRARY = CLAUDE_HOOK.parent / LIBRARY_NAME

# The helpers moved out of the adapters, in file order; then the entry point, which
# must be the last function in the file (the load check treats it as the sentinel).
PUSH_REVIEW_HELPERS = [
    "strip_span_counts",
    "hd_read_delim",
    "hd_read_body",
    "strip_quoted_ranges",
    "git_c_dir_from_words",
    "cd_target_from_words",
    "push_show",
    "build_push_report",
]
PUSH_REVIEW_ENTRY = "hook_push_review_run"
PUSH_REVIEW_FUNCTIONS = [*PUSH_REVIEW_HELPERS, PUSH_REVIEW_ENTRY]

LOAD_FAILURE_COMMANDS = [
    "git push origin main",
    "GIT Push origin main",
    "git send-pack",
]


def _stage_adapters(tmp_path, library=None):
    """Copy both adapters into sibling .claude/hooks and .codex/hooks directories.

    The Codex adapter finds the library at ../../.claude/hooks relative to itself, so
    the siblings must really be siblings under one root: if the path escaped into the
    real repository, every "the library is broken" test would silently load the real
    one and pass for the wrong reason. `library` is the text of
    .claude/hooks/_push_review_common.sh, or None for no such file.
    """
    root = tmp_path / "staged"
    claude_dir = root / ".claude/hooks"
    codex_dir = root / ".codex/hooks"
    claude_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    shutil.copy2(CLAUDE_HOOK, claude_dir / CLAUDE_HOOK.name)
    shutil.copy2(CODEX_HOOK, codex_dir / CODEX_HOOK.name)
    if library is not None:
        (claude_dir / LIBRARY_NAME).write_text(library, encoding="utf-8")
    return {
        "claude": claude_dir / CLAUDE_HOOK.name,
        "codex": codex_dir / CODEX_HOOK.name,
    }, claude_dir


def _stub_definitions(names):
    """No-op definitions of the given functions."""
    return "".join(f"{name}() {{\n  :\n}}\n" for name in names)


def _entry_definition(body=":"):
    return f"{PUSH_REVIEW_ENTRY}() {{\n{body}\n}}\n"


def _stub_library(entry_body=":"):
    """A complete library whose helpers do nothing; only the entry has a body."""
    return _stub_definitions(PUSH_REVIEW_HELPERS) + _entry_definition(entry_body)


def _library_text(state):
    if state == "absent":
        return None
    if state == "empty":
        return ""
    if state == "truncated":
        # Cut off in the middle of the first function: the later ones, the entry point
        # included, were never defined.
        return 'strip_span_counts() {\n  local o="$1" sp="$2"\n  case "$sp" in\n'
    if state == "syntax-error":
        # A syntax error in the middle of an otherwise complete library. Whether the
        # shell stops reading at the error or carries on, the library has to be
        # refused: one that stops leaves the later functions undefined, one that
        # carries on defines them all but reports the failed read.
        return (
            _stub_definitions(PUSH_REVIEW_HELPERS[:4])
            + "if then fi ((\n"
            + _stub_definitions(PUSH_REVIEW_HELPERS[4:])
            + _entry_definition()
        )
    if state == "broken-after-the-sentinel":
        # Every function is defined, but the file does not parse to the end.
        return _stub_library() + "if then fi ((\n"
    raise AssertionError(state)


LOAD_FAILURE_STATES = [
    "absent",
    "empty",
    "truncated",
    "syntax-error",
    "broken-after-the-sentinel",
]


def _assert_load_failure(res, variant, claude_dir):
    resolved = str(claude_dir.resolve())
    if variant == "claude":
        assert res.returncode == 0
        output = json.loads(res.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        assert output["permissionDecision"] == "ask"
        reason = output["permissionDecisionReason"]
        assert "could not be loaded" in reason
        assert "Review the push manually" in reason
        # The reason is fixed text: nothing from the command or the environment is
        # interpolated into it.
        assert reason == (
            "git push detected, but the push-review library (_push_review_common.sh) "
            "could not be loaded, so this hook could not build the usual push summary. "
            "Review the push manually before pushing "
            "(see ~/.claude/rules/git-workflow.md)."
        )
        assert res.stderr == (
            f"git-push-review.sh: could not load {LIBRARY_NAME} from {resolved}\n"
        )
    else:
        assert res.returncode == 2
        assert res.stdout == ""
        assert res.stderr == (
            "git push detected, but the push-review library (_push_review_common.sh) "
            "could not be loaded, so this hook could not build the usual push summary. "
            "Review the push manually before pushing "
            '(see ~/.codex/AGENTS.md "## 3. Git Workflow").\n'
            f"git-push-review.sh: could not load {LIBRARY_NAME} from {resolved}\n"
        )


@pytest.mark.parametrize("variant", ["claude", "codex"])
@pytest.mark.parametrize("command", LOAD_FAILURE_COMMANDS)
@pytest.mark.parametrize("state", LOAD_FAILURE_STATES)
def test_an_unusable_library_degrades_to_a_confirmation_not_a_pass(
    shell_env, git_repo, tmp_path, variant, command, state
):
    adapters, claude_dir = _stage_adapters(tmp_path, _library_text(state))
    res = shell_env.run(adapters[variant], stdin=payload(command), cwd=git_repo)
    _assert_load_failure(res, variant, claude_dir)


@pytest.mark.parametrize("variant", ["claude", "codex"])
@pytest.mark.parametrize("missing", PUSH_REVIEW_FUNCTIONS)
def test_every_required_function_is_checked_before_the_library_is_trusted(
    shell_env, git_repo, tmp_path, variant, missing
):
    """Dropping ANY one of the nine functions must be refused, not just the entry point.

    The real library is used with one definition renamed, so a partially-defined
    library that still defines the entry point (and the others) is what is tested.
    """
    real = CLAUDE_LIBRARY.read_text(encoding="utf-8")
    renamed = real.replace(f"\n{missing}() {{", f"\n{missing}_renamed() {{", 1)
    assert renamed != real, f"{missing} is not defined at the start of a line"
    adapters, claude_dir = _stage_adapters(tmp_path, renamed)
    res = shell_env.run(
        adapters[variant], stdin=payload("git push origin main"), cwd=git_repo
    )
    _assert_load_failure(res, variant, claude_dir)


@pytest.mark.parametrize("variant", ["claude", "codex"])
@pytest.mark.parametrize("state", LOAD_FAILURE_STATES)
def test_a_command_that_is_not_a_push_never_touches_the_library(
    shell_env, git_repo, tmp_path, variant, state
):
    """The cheap guard runs before the load, so an unusable library cannot matter here."""
    adapters, _ = _stage_adapters(tmp_path, _library_text(state))
    res = shell_env.run(adapters[variant], stdin=payload("ls -la"), cwd=git_repo)
    assert res.returncode == 0
    assert res.stdout == ""
    assert res.stderr == ""


@pytest.mark.parametrize("variant", ["claude", "codex"])
def test_a_non_push_command_does_not_even_parse_the_library(
    shell_env, git_repo, tmp_path, variant
):
    """A library that announces itself and exits would show if it were sourced."""
    tripwire = 'echo "SOURCED" >&2\necho "SOURCED"\nexit 9\n'
    adapters, _ = _stage_adapters(tmp_path, tripwire)
    res = shell_env.run(adapters[variant], stdin=payload("git status"), cwd=git_repo)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


# The adapter acts on the entry point's return code: 0 = a body to show, 1 = not a push,
# anything else = the library did something unexpected, which is treated like a load
# failure. A stand-in library makes each of those answers deterministic.
FAKE_BODY = "FAKE-BODY line one\nline two with trailing newline\n"


def _fake_entry(code):
    return (
        "  push_review_body=$'FAKE-BODY line one\\nline two with trailing newline\\n'\n"
        f"  return {code}"
    )


@pytest.mark.parametrize("variant", ["claude", "codex"])
def test_rc_0_emits_the_library_body_unchanged(shell_env, git_repo, tmp_path, variant):
    library = _stub_library(_fake_entry(0))
    adapters, _ = _stage_adapters(tmp_path, library)
    res = shell_env.run(adapters[variant], stdin=payload("git push"), cwd=git_repo)

    if variant == "claude":
        assert res.returncode == 0
        assert res.stderr == ""
        reason = json.loads(res.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        # The body's own trailing newline survives into the reason.
        assert reason == CLAUDE_HEADER + "\n" + FAKE_BODY
    else:
        assert res.returncode == 2
        assert res.stdout == ""
        assert res.stderr == CODEX_HEADER + "\n" + FAKE_BODY + "\n"


@pytest.mark.parametrize("variant", ["claude", "codex"])
def test_rc_1_means_not_a_push_and_passes_silently(
    shell_env, git_repo, tmp_path, variant
):
    library = _stub_library(_fake_entry(1))
    adapters, _ = _stage_adapters(tmp_path, library)
    res = shell_env.run(adapters[variant], stdin=payload("git push"), cwd=git_repo)
    assert (res.returncode, res.stdout, res.stderr) == (0, "", "")


@pytest.mark.parametrize("variant", ["claude", "codex"])
@pytest.mark.parametrize("code", [2, 7, 127])
def test_any_other_rc_is_a_load_failure(shell_env, git_repo, tmp_path, variant, code):
    library = _stub_library(_fake_entry(code))
    adapters, claude_dir = _stage_adapters(tmp_path, library)
    res = shell_env.run(adapters[variant], stdin=payload("git push"), cwd=git_repo)
    _assert_load_failure(res, variant, claude_dir)
