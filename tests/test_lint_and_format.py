"""Tests for lint.sh and auto-format.sh (.claude and .codex copies).

External linters/formatters are replaced with PATH stubs so the tests
exercise the hooks' dispatch and exit-code contract, not the tools.

Both copies share the same stdin protocol (`.tool_input.file_path` via
jq) and the same exit-code contract, so every test in this module is
parametrized over both the .claude and .codex hook, mirroring the
CLAUDE_HOOK/CODEX_HOOK pattern in test_git_push_review.py. Only the log
directory (.claude/logs vs .codex/logs) differs between the two.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT

CLAUDE_LINT = REPO_ROOT / ".claude/hooks/lint.sh"
CODEX_LINT = REPO_ROOT / ".codex/hooks/lint.sh"
CLAUDE_FORMAT = REPO_ROOT / ".claude/hooks/auto-format.sh"
CODEX_FORMAT = REPO_ROOT / ".codex/hooks/auto-format.sh"

LINT_HOOKS = [CLAUDE_LINT, CODEX_LINT]
FORMAT_HOOKS = [CLAUDE_FORMAT, CODEX_FORMAT]


def payload(file_path) -> str:
    return json.dumps({"tool_input": {"file_path": str(file_path)}})


def _log_dir_name(hook_path: Path) -> str:
    return ".codex" if ".codex" in hook_path.parts else ".claude"


def _log_file(shell_env, hook_path: Path, name: str) -> Path:
    return shell_env.home / _log_dir_name(hook_path) / "logs" / name


def _run_with_env(hook_path: Path, stdin: str, env: dict):
    # Resolve bash to an absolute path via the *real* PATH. `env` here has had
    # jq's directory stripped from PATH so the hook can't find jq; on Linux CI
    # bash and jq share /usr/bin, so that same stripping would otherwise leave
    # subprocess unable to locate the bash interpreter itself (POSIX resolves a
    # bare program name via env["PATH"], not the parent process's PATH).
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(
        [bash, str(hook_path)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _path_without_executable(path_value: str, name: str) -> str:
    """Drop every PATH entry that contains an executable called `name`.

    A plain shutil.which()-based single-directory exclusion is not enough:
    on this machine (and plausibly others) the same executable is reachable
    from more than one PATH entry (e.g. both a Homebrew and a system copy of
    jq), and shutil.which() only reports the first. Scanning every PATH
    directory directly avoids leaving a second copy reachable.
    """
    dirs = [p for p in path_value.split(os.pathsep) if p]
    kept = [d for d in dirs if not os.access(os.path.join(d, name), os.X_OK)]
    return os.pathsep.join(kept)


def _env_without_jq(shell_env) -> dict:
    return {
        **shell_env.env,
        "PATH": _path_without_executable(shell_env.env["PATH"], "jq"),
    }


def _env_without_real_terminal_notifier(shell_env) -> dict:
    # The fixture's own fake stub is removed by the caller before this runs;
    # this additionally hides any REAL terminal-notifier further down PATH
    # (e.g. a developer machine with it brew-installed) so the test can
    # never trigger an actual desktop notification.
    return {
        **shell_env.env,
        "PATH": _path_without_executable(shell_env.env["PATH"], "terminal-notifier"),
    }


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestLint:
    def test_missing_file_path_is_ignored(self, LINT, shell_env):
        res = shell_env.run(LINT, stdin="{}")
        assert res.returncode == 0

    def test_nonexistent_file_is_ignored(self, LINT, shell_env, tmp_path):
        res = shell_env.run(LINT, stdin=payload(tmp_path / "ghost.py"))
        assert res.returncode == 0

    def test_unsupported_extension_passes(self, LINT, shell_env, tmp_path):
        target = tmp_path / "data.xyz"
        target.write_text("whatever\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert "No linter configured" in res.stdout

    def test_python_lint_error_blocks_with_exit_two(self, LINT, shell_env, tmp_path):
        shell_env.stub("ruff", body='echo "x.py:1:1: F401 unused import"', exit_code=1)
        shell_env.stub("bandit")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[ruff]" in res.stderr
        assert "F401" in res.stderr
        assert "Please fix the above issues." in res.stderr
        log = _log_file(shell_env, LINT, "lint.log")
        assert "FAILED: x.py" in log.read_text(encoding="utf-8")

    def test_python_lint_clean_passes(self, LINT, shell_env, tmp_path):
        shell_env.stub("ruff")
        shell_env.stub("bandit")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert "All lint checks passed" in res.stdout

    def test_shellcheck_error_blocks(self, LINT, shell_env, tmp_path):
        shell_env.stub("shellcheck", body='echo "SC2086 unquoted"', exit_code=1)
        target = tmp_path / "x.sh"
        target.write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[shellcheck]" in res.stderr

    def test_missing_jq_exits_zero(self, LINT, shell_env):
        jq_path = shutil.which("jq")
        assert jq_path, "jq must be installed for this test to be meaningful"
        res = _run_with_env(LINT, "{}", _env_without_jq(shell_env))
        assert res.returncode == 0

    def test_notify_uses_env_indirection_not_interpolation(
        self, LINT, shell_env, tmp_path
    ):
        (shell_env.stub_bin / "terminal-notifier").unlink()
        env = _env_without_real_terminal_notifier(shell_env)
        target = tmp_path / 'evil".xyz'
        target.write_text("whatever\n", encoding="utf-8")
        res = _run_with_env(LINT, payload(target), env)
        assert res.returncode == 0
        osascript_calls = [c for c in shell_env.calls if c.startswith("osascript ")]
        assert osascript_calls, "expected notify() to fall back to osascript"
        call = osascript_calls[0]
        assert target.name not in call
        assert "HOOK_NOTIFY_MESSAGE" in call


@pytest.mark.parametrize("FORMAT", FORMAT_HOOKS, ids=["claude", "codex"])
class TestAutoFormat:
    def test_missing_file_path_is_ignored(self, FORMAT, shell_env):
        res = shell_env.run(FORMAT, stdin="{}")
        assert res.returncode == 0
        assert "No file path found" in res.stderr

    def test_python_file_runs_ruff_and_not_isort(self, FORMAT, shell_env, tmp_path):
        # When ruff is available, imports are sorted via ruff (--select I --fix)
        # and formatted with ruff format. isort must NOT run afterwards: its
        # output conflicts with `ruff format` and breaks `ruff format --check`.
        shell_env.stub("ruff")
        shell_env.stub("isort")
        target = tmp_path / "x.py"
        target.write_text("import os\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert any(
            c.startswith(f"ruff check --select I --fix {target}")
            for c in shell_env.calls
        )
        assert any(c.startswith(f"ruff format {target}") for c in shell_env.calls)
        assert not any(c.startswith(f"isort {target}") for c in shell_env.calls)
        notified = [c for c in shell_env.calls if "Format Done" in c]
        assert notified, "expected a Format Done notification"

    def test_shell_file_runs_shfmt(self, FORMAT, shell_env, tmp_path):
        shell_env.stub("shfmt")
        target = tmp_path / "x.sh"
        target.write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_unsupported_extension_does_not_notify(self, FORMAT, shell_env, tmp_path):
        target = tmp_path / "data.xyz"
        target.write_text("whatever\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert "No formatter configured" in res.stdout
        assert not any("Format Done" in c for c in shell_env.calls)

    def test_formatter_failure_does_not_set_success_flag(
        self, FORMAT, shell_env, tmp_path
    ):
        shell_env.stub("shfmt", body='echo "syntax error" >&2', exit_code=1)
        target = tmp_path / "x.sh"
        target.write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert "shfmt failed" in res.stderr
        assert not any("Format Done" in c for c in shell_env.calls)

    def test_missing_jq_exits_zero(self, FORMAT, shell_env):
        jq_path = shutil.which("jq")
        assert jq_path, "jq must be installed for this test to be meaningful"
        res = _run_with_env(FORMAT, "{}", _env_without_jq(shell_env))
        assert res.returncode == 0

    def test_notify_uses_env_indirection_not_interpolation(
        self, FORMAT, shell_env, tmp_path
    ):
        (shell_env.stub_bin / "terminal-notifier").unlink()
        shell_env.stub("shfmt")
        env = _env_without_real_terminal_notifier(shell_env)
        target = tmp_path / 'evil".sh'
        target.write_text("echo hi\n", encoding="utf-8")
        res = _run_with_env(FORMAT, payload(target), env)
        assert res.returncode == 0
        osascript_calls = [c for c in shell_env.calls if c.startswith("osascript ")]
        assert osascript_calls, "expected notify() to fall back to osascript"
        call = osascript_calls[0]
        assert target.name not in call
        assert "HOOK_NOTIFY_MESSAGE" in call
