"""Tests for bash-review-launcher.sh (fail-closed startup wrapper,
.claude JSON-ask variant and .codex exit-2 variant).

The launchers exist because a bare `python3 .../bash-review.py` hook command
fails OPEN when the review cannot happen at all: both runtimes treat a hook
that cannot start (python3 missing, script missing) or that crashes with an
unexpected exit code as a non-blocking error and run the Bash command anyway.
Each launcher converts every "review never happened" condition into its
runtime's fail-closed vocabulary -- an explicit `ask` decision for Claude,
exit 2 + stderr for Codex (which has no `ask`, and parses stdout as
structured output, so the fail path must not print there) -- and passes the
hook's normal vocabulary (exit 0 / exit 2 + stderr) through untouched.

A launcher resolves bash-review.py next to itself, so these tests copy it
into an isolated directory with a controllable fake sibling instead of
invoking the real review (which would want API keys and network).
"""

import json
import shutil

from conftest import REPO_ROOT

CLAUDE_LAUNCHER = REPO_ROOT / ".claude/hooks/bash-review-launcher.sh"
CODEX_LAUNCHER = REPO_ROOT / ".codex/hooks/bash-review-launcher.sh"

# Fake hook: echoes stdin back inside a valid decision, proving both stdout
# and stdin pass through the launcher unmodified.
ECHO_STDIN_HOOK = (
    "import json, sys\n"
    "data = sys.stdin.read()\n"
    "print(json.dumps({'hookSpecificOutput': {"
    "'hookEventName': 'PreToolUse', 'permissionDecision': 'allow',"
    " 'permissionDecisionReason': data}}))\n"
)

# Fake hook: crashes after emitting a partial line, like a traceback mid-write.
CRASH_HOOK = (
    "import sys\n"
    "sys.stdout.write('partial garbage')\n"
    "sys.stderr.write('Traceback: boom\\n')\n"
    "sys.exit(1)\n"
)

# Fake hook: the exit-2 blocking vocabulary (stderr is fed back to Claude).
BLOCK_HOOK = "import sys\nsys.stderr.write('blocked: reason\\n')\nsys.exit(2)\n"

# Fake hook: silent allow (no output at all).
SILENT_HOOK = "raise SystemExit(0)\n"


def payload(command: str) -> str:
    return json.dumps({"tool_input": {"command": command}})


def install_launcher(tmp_path, source, hook_body: str | None = None):
    """Copy the given launcher variant next to an optional fake bash-review.py."""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    launcher = hooks_dir / source.name
    shutil.copy(source, launcher)
    if hook_body is not None:
        (hooks_dir / "bash-review.py").write_text(hook_body, encoding="utf-8")
    return launcher


def decision_of(res) -> dict:
    return json.loads(res.stdout)["hookSpecificOutput"]


class TestNormalVocabularyPassesThrough:
    def test_decision_json_and_stdin_pass_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, ECHO_STDIN_HOOK)
        res = shell_env.run(launcher, stdin=payload("git status"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "allow"
        # stdin reached the hook intact (the fake echoes it into the reason).
        assert "git status" in output["permissionDecisionReason"]

    def test_silent_exit_zero_stays_silent(self, shell_env, tmp_path):
        # The launcher must not invent output (not even a bare newline) when
        # the hook legitimately allowed by silence.
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, SILENT_HOOK)
        res = shell_env.run(launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert res.stdout == ""

    def test_exit_two_blocking_passes_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, BLOCK_HOOK)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert "blocked: reason" in res.stderr
        # exit 2 already blocks; injecting an ask JSON here would be noise.
        assert res.stdout == ""


class TestReviewNeverHappenedFailsClosed:
    def test_missing_python3_asks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, ECHO_STDIN_HOOK)
        shell_env.hide("python3")
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "ask"
        assert "python3" in output["permissionDecisionReason"]

    def test_missing_hook_file_asks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, hook_body=None)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "ask"
        assert "bash-review.py" in output["permissionDecisionReason"]

    def test_crash_discards_partial_stdout_and_asks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, CRASH_HOOK)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 0
        # Partial hook output is discarded: forwarding a broken JSON fragment
        # would make Claude Code ignore the stdout entirely = fail open again.
        output = decision_of(res)
        assert output["permissionDecision"] == "ask"
        assert "exit code 1" in output["permissionDecisionReason"]
        assert "partial garbage" not in res.stdout
        # Diagnostics still reach stderr.
        assert "Traceback: boom" in res.stderr


# Fake hook for the Codex variant: allow (exit 0) only if stdin arrived
# intact; any other outcome exits 3, which the launcher must treat as a crash.
STDIN_PROBE_HOOK = (
    "import sys\nraise SystemExit(0 if 'git status' in sys.stdin.read() else 3)\n"
)


class TestCodexVariant:
    """Codex has no `ask` (returning it fails open) and parses hook stdout as
    structured output, so the fail path must be exit 2 + stderr with nothing
    on stdout, and blocks must carry the do-not-work-around directive that
    `.codex/hooks/bash-review.py` attaches to its own blocks."""

    def test_allow_exit_zero_stays_silent(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, SILENT_HOOK)
        res = shell_env.run(launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert res.stdout == ""

    def test_stdin_reaches_the_hook(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, STDIN_PROBE_HOOK)
        res = shell_env.run(launcher, stdin=payload("git status"))
        assert res.returncode == 0

    def test_exit_zero_stdout_passes_through(self, shell_env, tmp_path):
        # bash-review.py never prints on allow today, but the launcher's
        # contract is verbatim pass-through of a completed hook's stdout
        # (e.g. an authoritative deny JSON) -- pin it on this variant too.
        hook = 'print(\'{"decision": "deny"}\')\n'
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, hook)
        res = shell_env.run(launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert res.stdout == '{"decision": "deny"}\n'

    def test_exit_two_block_passes_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, BLOCK_HOOK)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert "blocked: reason" in res.stderr
        assert res.stdout == ""

    def test_missing_python3_blocks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, SILENT_HOOK)
        shell_env.hide("python3")
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert res.stdout == ""
        assert "python3" in res.stderr
        assert "do NOT retry" in res.stderr

    def test_missing_hook_file_blocks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, hook_body=None)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert res.stdout == ""
        assert "bash-review.py" in res.stderr
        assert "do NOT retry" in res.stderr

    def test_crash_discards_partial_stdout_and_blocks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, CRASH_HOOK)
        res = shell_env.run(launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        # Codex reads stdout as structured output: even a fragment of broken
        # JSON there makes the hook itself count as failed = fail open.
        assert res.stdout == ""
        assert "exit code 1" in res.stderr
        assert "Traceback: boom" in res.stderr
        assert "do NOT retry" in res.stderr
