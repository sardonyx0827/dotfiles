"""Representative tests for .claude/hooks/lint.sh and auto-format.sh.

External linters/formatters are replaced with PATH stubs so the tests
exercise the hooks' dispatch and exit-code contract, not the tools.
"""

import json

from conftest import REPO_ROOT

LINT = REPO_ROOT / ".claude/hooks/lint.sh"
FORMAT = REPO_ROOT / ".claude/hooks/auto-format.sh"


def payload(file_path) -> str:
    return json.dumps({"tool_input": {"file_path": str(file_path)}})


class TestLint:
    def test_missing_file_path_is_ignored(self, shell_env):
        res = shell_env.run(LINT, stdin="{}")
        assert res.returncode == 0

    def test_nonexistent_file_is_ignored(self, shell_env, tmp_path):
        res = shell_env.run(LINT, stdin=payload(tmp_path / "ghost.py"))
        assert res.returncode == 0

    def test_unsupported_extension_passes(self, shell_env, tmp_path):
        target = tmp_path / "data.xyz"
        target.write_text("whatever\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert "No linter configured" in res.stdout

    def test_python_lint_error_blocks_with_exit_two(self, shell_env, tmp_path):
        shell_env.stub("ruff", body='echo "x.py:1:1: F401 unused import"', exit_code=1)
        shell_env.stub("bandit")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[ruff]" in res.stderr
        assert "F401" in res.stderr
        assert "Please fix the above issues." in res.stderr
        log = shell_env.home / ".claude/logs/lint.log"
        assert "FAILED: x.py" in log.read_text(encoding="utf-8")

    def test_python_lint_clean_passes(self, shell_env, tmp_path):
        shell_env.stub("ruff")
        shell_env.stub("bandit")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert "All lint checks passed" in res.stdout

    def test_shellcheck_error_blocks(self, shell_env, tmp_path):
        shell_env.stub("shellcheck", body='echo "SC2086 unquoted"', exit_code=1)
        target = tmp_path / "x.sh"
        target.write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[shellcheck]" in res.stderr


class TestAutoFormat:
    def test_missing_file_path_is_ignored(self, shell_env):
        res = shell_env.run(FORMAT, stdin="{}")
        assert res.returncode == 0
        assert "No file path found" in res.stderr

    def test_python_file_runs_ruff_format_and_isort(self, shell_env, tmp_path):
        shell_env.stub("ruff")
        shell_env.stub("isort")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert any(c.startswith(f"ruff format {target}") for c in shell_env.calls)
        assert any(c.startswith(f"isort {target}") for c in shell_env.calls)
        notified = [c for c in shell_env.calls if "Format Done" in c]
        assert notified, "expected a Format Done notification"

    def test_shell_file_runs_shfmt(self, shell_env, tmp_path):
        shell_env.stub("shfmt")
        target = tmp_path / "x.sh"
        target.write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_unsupported_extension_does_not_notify(self, shell_env, tmp_path):
        target = tmp_path / "data.xyz"
        target.write_text("whatever\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert "No formatter configured" in res.stdout
        assert not any("Format Done" in c for c in shell_env.calls)
