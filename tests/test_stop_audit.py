"""Tests for stop-audit.sh (debug-statement audit on Stop)."""

import json

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

    def test_member_access_console_log_is_not_flagged(self, shell_env, git_repo):
        # obj.console.log / debuggerTool are not bare debug statements.
        (git_repo / "app.ts").write_text(
            "logger.console.log(1)\nconst debuggerTool = 1\n", encoding="utf-8"
        )
        res = shell_env.run(CLAUDE_HOOK, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert res.stdout == ""

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
