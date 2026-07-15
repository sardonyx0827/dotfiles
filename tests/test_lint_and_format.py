"""Tests for lint.sh and auto-format.sh (.claude and .codex copies).

External linters/formatters are replaced with PATH stubs so the tests
exercise the hooks' dispatch and exit-code contract, not the tools.

The two copies do NOT share one contract, so they are not uniformly
parametrized (see .codex/hooks/README.md for the full rationale):

- lint.sh: both copies read `.tool_input.file_path`, so TestLint stays
  parametrized. They differ only in stdout — Codex parses hook stdout as
  structured JSON and marks the hook failed on plain text, so its copy
  keeps stdout empty and records progress in the log instead. That
  divergence is expressed by _assert_progress().
- auto-format.sh: the Codex copy is wired to Stop rather than PostToolUse
  and takes its targets from the git working tree instead of a payload
  path, so it shares no cases with the Claude copy. The two get separate
  classes, following the TestClaudeVariant/TestCodexVariant split in
  test_stop_audit.py.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REPO_ROOT, run_git

CLAUDE_LINT = REPO_ROOT / ".claude/hooks/lint.sh"
CODEX_LINT = REPO_ROOT / ".codex/hooks/lint.sh"
CLAUDE_FORMAT = REPO_ROOT / ".claude/hooks/auto-format.sh"
CODEX_FORMAT = REPO_ROOT / ".codex/hooks/auto-format.sh"

LINT_HOOKS = [CLAUDE_LINT, CODEX_LINT]


def payload(file_path) -> str:
    return json.dumps({"tool_input": {"file_path": str(file_path)}})


def _is_codex(hook_path: Path) -> bool:
    return ".codex" in hook_path.parts


def _assert_progress(res, hook_path: Path, expected: str) -> None:
    """Assert the hook's human-readable progress output for this variant.

    Claude surfaces hook stdout in transcript mode, so the message belongs
    there. Codex instead parses hook stdout as structured JSON and reports the
    hook as failed on anything else, so its copy must leave stdout empty and
    log the same information. Callers assert the log separately, which is what
    actually proves the hook did its work.
    """
    if _is_codex(hook_path):
        assert res.stdout == "", "Codex hooks must not write plain text to stdout"
    else:
        assert expected in res.stdout


def _log_dir_name(hook_path: Path) -> str:
    return ".codex" if ".codex" in hook_path.parts else ".claude"


def _log_file(shell_env, hook_path: Path, name: str) -> Path:
    return shell_env.home / _log_dir_name(hook_path) / "logs" / name


def _run_with_env(hook_path: Path, stdin: str, env: dict, cwd: Path | None = None):
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
        cwd=cwd,
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
        _assert_progress(res, LINT, "No linter configured")
        log = _log_file(shell_env, LINT, "lint.log")
        assert "PASSED: data.xyz" in log.read_text(encoding="utf-8")

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
        _assert_progress(res, LINT, "All lint checks passed")
        log = _log_file(shell_env, LINT, "lint.log")
        assert "PASSED: x.py" in log.read_text(encoding="utf-8")

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


# Claude's copy runs on PostToolUse and formats the single file named in the
# payload. The Codex copy cannot share these cases -- it runs on Stop and reads
# the git working tree instead. See TestCodexAutoFormat.
@pytest.mark.parametrize("FORMAT", [CLAUDE_FORMAT], ids=["claude"])
class TestAutoFormat:
    def test_missing_file_path_is_ignored(self, FORMAT, shell_env):
        res = shell_env.run(FORMAT, stdin="{}")
        assert res.returncode == 0
        assert "No file path found" in res.stderr

    def test_malformed_json_input_exits_zero(self, FORMAT, shell_env):
        # Fail-open: garbage stdin (jq parse error) must fall through to the
        # "no file path" exit, not abort the hook with jq's non-zero status.
        res = shell_env.run(FORMAT, stdin="not json at all")
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


class TestCodexAutoFormat:
    """The Codex copy is a Stop hook driven by the git working tree.

    Formatting a file straight after Codex edits it breaks Codex's own editing:
    apply_patch diffs against the content it expects to find, so a reformat
    between patches makes later patches fail and Codex falls back to writing
    through the shell. Hence Stop, and hence git rather than a payload path --
    the Stop payload carries no file paths. See .codex/hooks/README.md.
    """

    def test_formats_modified_tracked_file(self, shell_env, git_repo):
        shell_env.stub("shfmt")
        (git_repo / "x.sh").write_text("echo hi\n", encoding="utf-8")
        run_git(git_repo, "add", "x.sh")
        run_git(git_repo, "commit", "-q", "-m", "add x.sh")
        (git_repo / "x.sh").write_text("echo    hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_formats_untracked_file(self, shell_env, git_repo):
        # apply_patch creates files as well as editing them, so new files must
        # be picked up even though git diff alone would miss them.
        shell_env.stub("shfmt")
        (git_repo / "new.sh").write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_formats_every_changed_file(self, shell_env, git_repo):
        # A single Codex turn routinely touches more than one file.
        shell_env.stub("shfmt")
        shell_env.stub("ruff")
        (git_repo / "a.sh").write_text("echo hi\n", encoding="utf-8")
        (git_repo / "b.py").write_text("import os\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)
        assert any(c.startswith("ruff format ") for c in shell_env.calls)

    def test_finds_targets_when_cwd_is_subdirectory(self, shell_env, git_repo):
        # git reports paths relative to the repo root, and the hook may run from
        # anywhere inside the tree.
        shell_env.stub("shfmt")
        sub = git_repo / "sub"
        sub.mkdir()
        (git_repo / "x.sh").write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=sub)
        assert res.returncode == 0
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_clean_worktree_formats_nothing(self, shell_env, git_repo):
        shell_env.stub("shfmt")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert not any(c.startswith("shfmt") for c in shell_env.calls)
        assert not any("Format Done" in c for c in shell_env.calls)

    def test_outside_git_repo_is_ignored(self, shell_env, tmp_path):
        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        (outside / "x.sh").write_text("echo hi\n", encoding="utf-8")
        shell_env.stub("shfmt")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=outside)
        assert res.returncode == 0
        assert not any(c.startswith("shfmt") for c in shell_env.calls)

    def test_formats_path_needing_git_quoting(self, shell_env, git_repo):
        # git's core.quotePath is on by default and renders non-ASCII and
        # quote characters as `"\346..."` / `"evil\".sh"`, which cannot be
        # opened as-is. Reading the file list NUL-delimited avoids it; without
        # that these files are silently skipped.
        shell_env.stub("shfmt")
        for name in ("日本語.sh", 'evil".sh'):
            (git_repo / name).write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        formatted = [c for c in shell_env.calls if c.startswith("shfmt -i 2 -w")]
        assert len(formatted) == 2, f"both files should be formatted, got {formatted}"

    def test_stdout_stays_empty(self, shell_env, git_repo):
        # Codex parses hook stdout as structured JSON and marks the hook failed
        # on plain text, so progress output must never reach stdout.
        shell_env.stub("shfmt")
        (git_repo / "x.sh").write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.stdout == ""
        log = _log_file(shell_env, CODEX_FORMAT, "format.log")
        assert "DONE: x.sh (formatted)" in log.read_text(encoding="utf-8")

    def test_formatter_failure_does_not_notify(self, shell_env, git_repo):
        shell_env.stub("shfmt", body='echo "syntax error" >&2', exit_code=1)
        (git_repo / "x.sh").write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert "shfmt failed" in res.stderr
        assert not any("Format Done" in c for c in shell_env.calls)

    def test_file_cap_is_logged_not_silent(self, shell_env, git_repo):
        # The cap protects a very dirty tree from an unbounded run; truncating
        # silently would read as "everything was formatted".
        shell_env.stub("shfmt")
        for i in range(60):
            (git_repo / f"f{i:02d}.sh").write_text("echo hi\n", encoding="utf-8")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        log = _log_file(shell_env, CODEX_FORMAT, "format.log")
        assert "SKIP: 対象が 50 件を超えたため以降を打ち切り" in log.read_text(
            encoding="utf-8"
        )

    def test_notify_uses_env_indirection_not_interpolation(self, shell_env, git_repo):
        (shell_env.stub_bin / "terminal-notifier").unlink()
        shell_env.stub("shfmt")
        env = _env_without_real_terminal_notifier(shell_env)
        target = git_repo / 'evil".sh'
        target.write_text("echo hi\n", encoding="utf-8")
        res = _run_with_env(CODEX_FORMAT, "{}", env, cwd=git_repo)
        assert res.returncode == 0
        osascript_calls = [c for c in shell_env.calls if c.startswith("osascript ")]
        assert osascript_calls, "expected notify() to fall back to osascript"
        call = osascript_calls[0]
        assert target.name not in call
        assert "HOOK_NOTIFY_MESSAGE" in call
