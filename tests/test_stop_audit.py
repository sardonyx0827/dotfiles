"""Tests for stop-audit.sh (debug-statement audit on Stop)."""

import json

import pytest
from conftest import REPO_ROOT

CLAUDE_HOOK = REPO_ROOT / ".claude/hooks/stop-audit.sh"
CODEX_HOOK = REPO_ROOT / ".codex/hooks/stop-audit.sh"


class TestClaudeVariant:
    def test_stop_hook_active_exits_immediately(self, shell_env, git_repo):
        (git_repo / "app.ts").write_text('console.log("x")\n', encoding="utf-8")
        res = shell_env.run(
            CLAUDE_HOOK, stdin=json.dumps({"stop_hook_active": True}), cwd=git_repo
        )
        assert res.returncode == 0
        assert res.stdout == ""

    def test_outside_git_repo_is_ignored(self, shell_env, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=outside)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_clean_worktree_passes(self, shell_env, git_repo):
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_console_log_in_untracked_ts_blocks(self, shell_env, git_repo):
        (git_repo / "app.ts").write_text(
            'const x = 1\nconsole.log("debug")\n', encoding="utf-8"
        )
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        result = json.loads(res.stdout)
        assert result["decision"] == "block"
        assert "app.ts" in result["reason"]
        assert "console.log" in result["reason"]

    def test_debugger_statement_blocks(self, shell_env, git_repo):
        (git_repo / "app.js").write_text("debugger;\n", encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert json.loads(res.stdout)["decision"] == "block"

    def test_python_breakpoint_blocks(self, shell_env, git_repo):
        # Split so this test file itself is not flagged by stop-audit.sh.
        debug_stmt = "break" + "point()\n"
        (git_repo / "app.py").write_text(debug_stmt, encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        result = json.loads(res.stdout)
        assert result["decision"] == "block"
        assert "app.py" in result["reason"]

    def test_identifier_fusion_is_not_flagged(self, shell_env, git_repo):
        # myconsole.log / debuggerTool are different identifiers fused with
        # "console"/"debugger", not bare debug statements, and must stay
        # excluded even after widening the console.log match to catch
        # `window.console.log(` (see test below).
        (git_repo / "app.ts").write_text(
            "myconsole.log(1)\nconst debuggerTool = 1\n", encoding="utf-8"
        )
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_window_console_log_is_flagged(self, shell_env, git_repo):
        # window.console IS the global console in browsers, so this is a
        # real, executable debug statement that must not be missed just
        # because a dot precedes "console".
        (git_repo / "app.ts").write_text('window.console.log("x")\n', encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        result = json.loads(res.stdout)
        assert result["decision"] == "block"
        assert "app.ts" in result["reason"]

    def test_non_source_files_are_not_scanned(self, shell_env, git_repo):
        (git_repo / "notes.txt").write_text("console.log(1)\n", encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

    def test_modified_tracked_file_is_scanned(self, shell_env, git_repo):
        from conftest import run_git

        (git_repo / "app.ts").write_text("const ok = 1\n", encoding="utf-8")
        run_git(git_repo, "add", "app.ts")
        run_git(git_repo, "commit", "-q", "-m", "add app.ts")
        (git_repo / "app.ts").write_text("console.log(2)\n", encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert json.loads(res.stdout)["decision"] == "block"

    def test_debug_statement_detected_when_cwd_is_subdirectory(
        self, shell_env, git_repo
    ):
        # git diff/ls-files return repo-root-relative paths regardless of
        # cwd. Running the hook from a subdirectory must still resolve
        # those paths against the repo root, not the hook's cwd.
        sub = git_repo / "sub"
        sub.mkdir()
        (git_repo / "app.ts").write_text('console.log("x")\n', encoding="utf-8")
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=sub)
        assert res.returncode == 0
        result = json.loads(res.stdout)
        assert result["decision"] == "block"
        assert "app.ts" in result["reason"]
        assert "console.log" in result["reason"]


class TestCodexVariant:
    def test_debug_statement_blocks_with_exit_two(self, shell_env, git_repo):
        (git_repo / "app.ts").write_text('console.log("x")\n', encoding="utf-8")
        res = shell_env.run(CODEX_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 2
        assert "app.ts" in res.stderr
        assert "console.log" in res.stderr

    def test_stop_hook_active_exits_immediately(self, shell_env, git_repo):
        (git_repo / "app.ts").write_text('console.log("x")\n', encoding="utf-8")
        res = shell_env.run(
            CODEX_HOOK, stdin=json.dumps({"stop_hook_active": True}), cwd=git_repo
        )
        assert res.returncode == 0

    def test_clean_worktree_passes(self, shell_env, git_repo):
        res = shell_env.run(CODEX_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0

    def test_debug_statement_detected_when_cwd_is_subdirectory(
        self, shell_env, git_repo
    ):
        sub = git_repo / "sub"
        sub.mkdir()
        (git_repo / "app.ts").write_text('console.log("x")\n', encoding="utf-8")
        res = shell_env.run(CODEX_HOOK, stdin="{}", cwd=sub)
        assert res.returncode == 2
        assert "app.ts" in res.stderr
        assert "console.log" in res.stderr


# --- Debug-scan parity across BOTH variants ----------------------------------
# The scan logic (which files, which patterns, member-access exemptions) is
# identical across the two copies; only the SIGNAL differs (claude emits a JSON
# "block", codex exits 2 with stderr). Previously the codex copy had far fewer
# cases, so a scan regression there would ship green. Run every case against
# both — the same drift guard rationale as test_hook_sync.py.
SCAN_CASES = [
    # (filename, content, should_block)
    ("app.ts", 'const x = 1\nconsole.log("debug")\n', True),
    ("app.js", "debugger;\n", True),
    # Split so this test file itself is not flagged by stop-audit.sh.
    ("app.py", "break" + "point()\n", True),
    # Fused identifiers are not bare debug statements.
    ("app.ts", "myconsole.log(1)\nconst debuggerTool = 1\n", False),
    # window.console IS the global console; a dot before "console" must not
    # hide it from the scan.
    ("app.ts", 'window.console.log("x")\n', True),
    # Non-source files are not scanned.
    ("notes.txt", "console.log(1)\n", False),
]

VARIANTS = [("claude", CLAUDE_HOOK), ("codex", CODEX_HOOK)]


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("filename,content,should_block", SCAN_CASES)
def test_debug_scan_parity(
    shell_env, git_repo, variant, hook, filename, content, should_block
):
    (git_repo / filename).write_text(content, encoding="utf-8")
    res = shell_env.run(hook, stdin="{}", cwd=git_repo)
    label = f"{variant}:{filename}"
    if should_block:
        if variant == "claude":
            assert res.returncode == 0, label
            result = json.loads(res.stdout)
            assert result["decision"] == "block", label
            assert filename in result["reason"], label
        else:
            assert res.returncode == 2, label
            assert filename in res.stderr, label
    else:
        assert res.returncode == 0, label
        if variant == "claude":
            assert res.stdout == "", label
        else:
            assert res.stderr == "", label


@pytest.mark.parametrize("variant,hook", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("filename", ["日本語.ts", 'evil".ts'])
def test_audits_paths_needing_git_quoting(shell_env, git_repo, variant, hook, filename):
    # git's core.quotePath is on by default and renders non-ASCII or quote
    # characters as "\346\227\245..." / "evil\".ts". Split on newlines, those
    # paths cannot be opened and the file drops out of the audit silently --
    # a gate that under-reports is worse than one that is merely noisy.
    (git_repo / filename).write_text('console.log("debug")\n', encoding="utf-8")
    res = shell_env.run(hook, stdin="{}", cwd=git_repo)
    label = f"{variant}:{filename}"
    if variant == "claude":
        assert res.returncode == 0, label
        result = json.loads(res.stdout)
        assert result["decision"] == "block", label
        assert filename in result["reason"], label
    else:
        assert res.returncode == 2, label
        assert filename in res.stderr, label
