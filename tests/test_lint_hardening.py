"""Hardening tests for lint.sh / auto-format.sh.

Kept apart from test_lint_and_format.py (which pins the per-language dispatch
table) so each contract reads as one block:

- a linter that cannot start is a notice, never an exit-2 finding
- tsc runs against the nearest tsconfig (solution-style aware), never `-b`
- mypy output is reduced to the edited file
- formatters honour project config instead of imposing a default style
- timeouts (and what a malformed timeout setting falls back to), clippy scoped
  to the edited file, no "Lint Passed" for files no linter looked at
- a symlinked target is skipped with its own log line, not "not installed"
- lint output handed back to the model is capped (statusline: test_statusline)
- the Dockerfile / compose arm

The Stop audit is not covered here (see test_stop_audit.py); the shared helpers
behind all of these are tested in test_hook_common.py.
"""

import os
import shutil
import subprocess
import time

import pytest
from conftest import REPO_ROOT
from test_lint_and_format import (
    CLAUDE_FORMAT,
    CLAUDE_LINT,
    CODEX_FORMAT,
    CODEX_LINT,
    LINT_HOOKS,
    _log_file,
    payload,
)

NOTIFIED = "terminal-notifier"


def _log(shell_env, hook, name="lint.log") -> str:
    path = _log_file(shell_env, hook, name)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _notified(shell_env, title: str) -> list[str]:
    return [c for c in shell_env.calls if c.startswith(NOTIFIED) and title in c]


def _write(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _say(shell_env, tool, text, exit_code=1, stream=2):
    """Stub `tool` so it prints `text` verbatim (no shell quoting to get wrong)."""
    msg = shell_env.stub_bin.parent / f"{tool}.msg"
    msg.write_text(text, encoding="utf-8")
    return shell_env.stub(tool, body=f'cat "{msg}" >&{stream}', exit_code=exit_code)


def _hide_timeout_binaries(shell_env) -> None:
    shell_env.hide("timeout")
    shell_env.hide("gtimeout")


# --------------------------------------------------------------------------
# A linter that cannot start is a notice
# --------------------------------------------------------------------------

PYENV_MISSING = (
    "pyenv: {tool}: command not found\n\n"
    "The `{tool}' command exists in these Python versions:\n  3.12.4\n"
)


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestLauncherFailureIsANotice:
    def _pyenv_stub(self, shell_env, tool, exit_code=127):
        _say(shell_env, tool, PYENV_MISSING.format(tool=tool), exit_code)

    def test_pyenv_shim_without_the_tool_does_not_block(
        self, LINT, shell_env, tmp_path
    ):
        self._pyenv_stub(shell_env, "ruff")
        shell_env.stub("bandit")
        target = _write(tmp_path / "x.py", "import os\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "[ruff]" not in res.stderr
        log = _log(shell_env, LINT)
        assert "NOTICE" in log and "ruff" in log
        assert "FAILED" not in log

    def test_exit_127_alone_is_unavailable(self, LINT, shell_env, tmp_path):
        shell_env.stub("shellcheck", body="echo 'whatever' >&2", exit_code=127)
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "NOTICE" in _log(shell_env, LINT)

    def test_exit_126_alone_is_unavailable(self, LINT, shell_env, tmp_path):
        shell_env.stub("shellcheck", body="echo 'denied' >&2", exit_code=126)
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr

    def test_a_shim_message_with_an_ordinary_exit_status_is_unavailable(
        self, LINT, shell_env, tmp_path
    ):
        # pyenv also reports "version ... is not installed" with exit 1.
        _say(
            shell_env,
            "shellcheck",
            "pyenv: version `3.9.1' is not installed (set by /x/.python-version)\n",
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr

    @pytest.mark.parametrize(
        "message",
        [
            "error: rustup could not choose a version of cargo to run, because one"
            " wasn't specified explicitly, and no default is configured.",
            "error: no override and no default toolchain set",
        ],
        ids=["rustup-current", "rustup-legacy"],
    )
    def test_rustup_without_a_toolchain_does_not_block(
        self, LINT, shell_env, git_repo, message
    ):
        _say(shell_env, "cargo", message + "\n")
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "x.rs", "fn main() {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        # The launcher phrase must be what is recognised. A bare "NOTICE" is
        # also written when cargo merely failed without diagnostics ("did not
        # complete", see TestClippyThatFailedWithoutDiagnostics), so it cannot
        # tell the two apart.
        log = _log(shell_env, LINT)
        assert "clippy unavailable (could not start" in log, log
        assert "did not complete" not in log, log
        assert not _notified(shell_env, "Lint Passed")

    def test_cargo_without_the_clippy_component_does_not_block(
        self, LINT, shell_env, git_repo
    ):
        _say(shell_env, "cargo", "error: no such command: `clippy`\n", exit_code=101)
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "x.rs", "fn main() {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        log = _log(shell_env, LINT)
        assert "clippy unavailable (could not start" in log, log
        assert "did not complete" not in log, log
        assert not _notified(shell_env, "Lint Passed")

    def test_a_real_finding_still_blocks(self, LINT, shell_env, tmp_path):
        # Control: the narrowing must not turn every non-zero exit into a pass.
        shell_env.stub("ruff", body='echo "x.py:1:1: F401 unused import"', exit_code=1)
        shell_env.stub("bandit")
        target = _write(tmp_path / "x.py", "import os\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[ruff]" in res.stderr

    def test_a_finding_that_merely_quotes_command_not_found_still_blocks(
        self, LINT, shell_env, tmp_path
    ):
        # The launcher phrases are matched at the start of a line; a diagnostic
        # that quotes one mid-line is a real finding about the source.
        shell_env.stub(
            "shellcheck",
            body='echo "x.sh:3:1: SC2154 foo: command not found is referenced but not assigned"',
            exit_code=1,
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[shellcheck]" in res.stderr

    def test_a_tool_that_could_not_start_does_not_say_lint_passed(
        self, LINT, shell_env, tmp_path
    ):
        shell_env.stub("shellcheck", body="echo 'x' >&2", exit_code=127)
        target = _write(tmp_path / "x.sh", "echo hi\n")
        shell_env.run(LINT, stdin=payload(target))
        assert not _notified(shell_env, "Lint Passed")


# --------------------------------------------------------------------------
# tsc against the nearest (solution-aware) tsconfig, never `-b`
# --------------------------------------------------------------------------

_TSC_BY_CONFIG = """\
case "$*" in
*tsconfig.app.json*)
  echo "src/a.ts(1,14): error TS2322: Type 'string' is not assignable to type 'number'."
  exit 1 ;;
*tsconfig.node.json*)
  echo "vite.config.ts(1,1): error TS2304: Cannot find name 'node'."
  exit 1 ;;
esac
exit 0
"""


def _vite_project(repo):
    """The Vite template's shape: a solution-style root and two real projects."""
    _write(
        repo / "tsconfig.json",
        '{\n  "files": [],\n  "references": [\n'
        '    { "path": "./tsconfig.app.json" },\n'
        '    { "path": "./tsconfig.node.json" }\n  ]\n}\n',
    )
    _write(
        repo / "tsconfig.app.json",
        "{\n  // JSONC: comments and trailing commas are legal here\n"
        '  "compilerOptions": { "noEmit": true, "strict": true, },\n'
        '  "include": ["src"],\n}\n',
    )
    _write(
        repo / "tsconfig.node.json",
        '{"compilerOptions": {"noEmit": true}, "include": ["vite.config.ts"]}\n',
    )
    _write(repo / "src" / "a.ts", "export const x: number = 'no'\n")
    _write(repo / "vite.config.ts", "export default {}\n")


def _tsc_calls(shell_env):
    return [c for c in shell_env.calls if c.startswith("tsc ")]


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestTscUsesTheNearestProject:
    def test_solution_style_root_resolves_to_the_referenced_project(
        self, LINT, shell_env, git_repo
    ):
        _vite_project(git_repo)
        shell_env.stub("tsc", body=_TSC_BY_CONFIG)
        res = shell_env.run(LINT, stdin=payload(git_repo / "src" / "a.ts"))
        assert res.returncode == 2, (
            "a type error in the app project must block; a bare `tsc --noEmit` "
            f"against the solution root checks nothing. calls={shell_env.calls}"
        )
        assert "[TypeScript]" in res.stderr
        assert "TS2322" in res.stderr
        calls = _tsc_calls(shell_env)
        assert len(calls) == 1
        assert " -p " in calls[0] and calls[0].endswith("tsconfig.app.json"), calls
        assert "--noEmit" in calls[0]

    def test_the_node_project_is_chosen_for_the_config_file(
        self, LINT, shell_env, git_repo
    ):
        _vite_project(git_repo)
        shell_env.stub("tsc", body=_TSC_BY_CONFIG)
        res = shell_env.run(LINT, stdin=payload(git_repo / "vite.config.ts"))
        assert res.returncode == 2
        assert _tsc_calls(shell_env)[0].endswith("tsconfig.node.json")

    def test_build_mode_is_never_used(self, LINT, shell_env, git_repo):
        # `tsc -b` writes build output (and .tsbuildinfo) into the host project.
        _vite_project(git_repo)
        shell_env.stub("tsc", body=_TSC_BY_CONFIG)
        shell_env.run(LINT, stdin=payload(git_repo / "src" / "a.ts"))
        assert not any(" -b" in c or " --build" in c for c in _tsc_calls(shell_env))

    def test_a_file_no_referenced_project_includes_is_a_notice_not_a_verdict(
        self, LINT, shell_env, git_repo
    ):
        _vite_project(git_repo)
        stray = _write(git_repo / "scripts" / "tool.ts", "export const y = 1\n")
        shell_env.stub("tsc", body=_TSC_BY_CONFIG)
        res = shell_env.run(LINT, stdin=payload(stray))
        assert res.returncode == 0, res.stderr
        assert not _tsc_calls(shell_env), "nothing can be said about an uncovered file"
        assert "NOTICE" in _log(shell_env, LINT)
        assert not _notified(shell_env, "Lint Passed")

    def test_nearest_tsconfig_wins_over_the_git_root_one(
        self, LINT, shell_env, git_repo
    ):
        _write(git_repo / "tsconfig.json", "{}\n")
        pkg = git_repo / "packages" / "web"
        _write(pkg / "tsconfig.json", "{}\n")
        target = _write(pkg / "src" / "page.ts", "export const y = 1\n")
        shell_env.stub(
            "tsc",
            body='echo "src/page.ts(1,14): error TS2322: bad."',
            exit_code=1,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            "diagnostics are relative to the config's directory; resolving them "
            "against the git root would make every -ef comparison fail"
        )
        call = _tsc_calls(shell_env)[0]
        assert f"{os.sep}packages{os.sep}web{os.sep}tsconfig.json" in call

    def test_a_config_with_references_and_its_own_include_is_used_directly(
        self, LINT, shell_env, git_repo
    ):
        _write(
            git_repo / "tsconfig.json",
            '{"include": ["src"], "references": [{"path": "./lib"}]}\n',
        )
        _write(git_repo / "lib" / "tsconfig.json", '{"include": ["."]}\n')
        target = _write(git_repo / "src" / "a.ts", "export const y = 1\n")
        shell_env.stub("tsc")
        shell_env.run(LINT, stdin=payload(target))
        calls = _tsc_calls(shell_env)
        assert len(calls) == 1
        tokens = calls[0].split()
        project = tokens[tokens.index("-p") + 1]
        assert os.path.realpath(project) == os.path.realpath(git_repo / "tsconfig.json")

    def test_no_tsconfig_at_all_skips_the_type_check(self, LINT, shell_env, git_repo):
        target = _write(git_repo / "a.ts", "export const y = 1\n")
        shell_env.stub("tsc", exit_code=1)
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not _tsc_calls(shell_env)


@pytest.mark.parametrize("LINT", [CLAUDE_LINT], ids=["claude"])
def test_real_tsc_checks_the_app_project_and_emits_no_build_output(
    LINT, shell_env, git_repo
):
    """End to end against the real compiler, on the shape that used to go green.

    The app project keeps the Vite template's `incremental` + `tsBuildInfoFile`,
    so `--noEmit -p` may still write that one .tsbuildinfo (under node_modules/.tmp
    in the template). It cannot be suppressed unconditionally: `--incremental
    false` is an error on a composite project and `--tsBuildInfoFile` alone is an
    error without `incremental`. What `-b` would have added -- emitted .js / .d.ts
    -- must not appear anywhere outside node_modules.
    """
    if shutil.which("tsc", path=shell_env.env["PATH"]) is None:
        pytest.skip("tsc is not installed on this host")
    _vite_project(git_repo)
    _write(
        git_repo / "tsconfig.app.json",
        '{"compilerOptions": {"tsBuildInfoFile": "./node_modules/.tmp/app.tsbuildinfo",'
        ' "noEmit": true, "incremental": true, "strict": true, "skipLibCheck": true,'
        ' "target": "ES2022", "module": "ESNext", "moduleResolution": "bundler"},'
        ' "include": ["src"]}\n',
    )
    res = shell_env.run(LINT, stdin=payload(git_repo / "src" / "a.ts"))
    assert res.returncode == 2, res.stderr
    assert "TS2322" in res.stderr
    emitted = [
        p.relative_to(git_repo)
        for p in git_repo.rglob("*")
        if ".git" not in p.parts
        and "node_modules" not in p.parts
        and p.suffix in {".js", ".d.ts", ".tsbuildinfo", ".map"}
    ]
    assert emitted == [], "the type check must not emit build output into the project"


# --------------------------------------------------------------------------
# mypy output reduced to the edited file
# --------------------------------------------------------------------------


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestMypyIsScopedToTheEditedFile:
    def _project(self, shell_env, git_repo, body, exit_code=1):
        shell_env.stub("ruff")
        shell_env.stub("bandit")
        _write(git_repo / "mypy.ini", "[mypy]\n")
        shell_env.stub("mypy", body=body, exit_code=exit_code)
        return _write(git_repo / "x.py", "x = 1\n")

    def test_errors_in_an_imported_module_do_not_block(self, LINT, shell_env, git_repo):
        _write(git_repo / "other.py", "y = 1\n")
        target = self._project(
            shell_env,
            git_repo,
            'echo "other.py:7: error: Incompatible types [assignment]"\n'
            'echo "Found 1 error in 1 file (checked 1 source file)"',
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "[mypy]" not in res.stderr

    def test_an_error_in_the_edited_file_blocks_and_only_it_is_reported(
        self, LINT, shell_env, git_repo
    ):
        _write(git_repo / "other.py", "y = 1\n")
        target = self._project(
            shell_env,
            git_repo,
            'echo "other.py:7: error: not mine [assignment]"\n'
            'echo "x.py:3: error: mine [assignment]"\n'
            'echo "x.py:3: note: a note that belongs to it"\n'
            'echo "Found 2 errors in 2 files (checked 1 source file)"',
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "x.py:3: error: mine" in res.stderr
        assert "a note that belongs to it" in res.stderr
        assert "other.py" not in res.stderr
        assert "Found 2 errors" not in res.stderr

    def test_an_absolute_path_in_the_output_is_matched(self, LINT, shell_env, git_repo):
        target = self._project(shell_env, git_repo, "")
        shell_env.stub(
            "mypy", body=f'echo "{target}:3: error: mine [assignment]"', exit_code=1
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2

    def test_a_crash_that_names_no_file_is_a_notice(self, LINT, shell_env, git_repo):
        target = self._project(
            shell_env, git_repo, 'echo "mypy: error: cannot read config" >&2', 2
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "NOTICE" in _log(shell_env, LINT)

    def test_a_syntax_error_in_the_edited_file_still_blocks_on_exit_two(
        self, LINT, shell_env, git_repo
    ):
        # mypy exits 2 for blocking errors such as syntax errors; the file-level
        # diagnostic is what makes it a finding.
        target = self._project(
            shell_env,
            git_repo,
            'echo "x.py:1: error: invalid syntax  [syntax]"\n'
            'echo "Found 1 error in 1 file (errors prevented further checking)"',
            2,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2

    def test_mypy_runs_from_the_project_root(self, LINT, shell_env, git_repo):
        # mypy finds mypy.ini / pyproject.toml from its cwd, and the hook has
        # already decided "there is a config" by looking at the project root.
        sub = git_repo / "pkg"
        self._project(shell_env, git_repo, 'echo "PWD=$PWD" >> "$0.pwd"')
        moved = _write(sub / "m.py", "x = 1\n")
        shell_env.run(LINT, stdin=payload(moved))
        pwd_file = shell_env.stub_bin / "mypy.pwd"
        assert pwd_file.exists()
        assert os.path.realpath(
            pwd_file.read_text(encoding="utf-8").split("=", 1)[1].strip()
        ) == os.path.realpath(git_repo)


# --------------------------------------------------------------------------
# Timeouts, clippy scope, "Lint Passed" only when something ran
# --------------------------------------------------------------------------

_SLEEPER = "sleep 30"


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
@pytest.mark.parametrize("timeout_impl", ["binary", "fallback"])
class TestSlowToolsAreCutOff:
    def _prepare(self, shell_env, timeout_impl):
        if timeout_impl == "fallback":
            _hide_timeout_binaries(shell_env)

    def _assert_prompt(self, started):
        assert time.monotonic() - started < 15, "the hook waited for the slow tool"

    def test_tsc_timeout_is_a_notice(self, LINT, shell_env, git_repo, timeout_impl):
        self._prepare(shell_env, timeout_impl)
        shell_env.env["HOOK_LINT_TIMEOUT_TSC"] = "1"
        _write(git_repo / "tsconfig.json", "{}\n")
        target = _write(git_repo / "a.ts", "export const y = 1\n")
        shell_env.stub("tsc", body=_SLEEPER, exit_code=1)
        started = time.monotonic()
        res = shell_env.run(LINT, stdin=payload(target))
        self._assert_prompt(started)
        assert res.returncode == 0, res.stderr
        log = _log(shell_env, LINT)
        assert "NOTICE" in log and "timed out" in log

    def test_clippy_timeout_is_a_notice(self, LINT, shell_env, git_repo, timeout_impl):
        self._prepare(shell_env, timeout_impl)
        shell_env.env["HOOK_LINT_TIMEOUT_CLIPPY"] = "1"
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "x.rs", "fn main() {}\n")
        shell_env.stub("cargo", body=_SLEEPER, exit_code=0)
        started = time.monotonic()
        res = shell_env.run(LINT, stdin=payload(target))
        self._assert_prompt(started)
        assert res.returncode == 0, res.stderr
        assert "timed out" in _log(shell_env, LINT)

    def test_mypy_timeout_is_a_notice(self, LINT, shell_env, git_repo, timeout_impl):
        self._prepare(shell_env, timeout_impl)
        shell_env.env["HOOK_LINT_TIMEOUT_MYPY"] = "1"
        shell_env.stub("ruff")
        shell_env.stub("bandit")
        _write(git_repo / "mypy.ini", "[mypy]\n")
        shell_env.stub("mypy", body=_SLEEPER, exit_code=1)
        target = _write(git_repo / "x.py", "x = 1\n")
        started = time.monotonic()
        res = shell_env.run(LINT, stdin=payload(target))
        self._assert_prompt(started)
        assert res.returncode == 0, res.stderr
        assert "timed out" in _log(shell_env, LINT)

    def test_a_grandchild_holding_the_pipe_does_not_stall_the_hook(
        self, LINT, shell_env, git_repo, timeout_impl
    ):
        # The stub's `sleep` is a child of the stub's bash; killing only the
        # direct child would leave it holding the pipe `$(...)` waits on.
        self._prepare(shell_env, timeout_impl)
        shell_env.env["HOOK_LINT_TIMEOUT_TSC"] = "1"
        _write(git_repo / "tsconfig.json", "{}\n")
        target = _write(git_repo / "a.ts", "export const y = 1\n")
        shell_env.stub("tsc", body="sleep 30; echo late", exit_code=1)
        started = time.monotonic()
        shell_env.run(LINT, stdin=payload(target))
        self._assert_prompt(started)


HOOK_COMMON = REPO_ROOT / ".claude/hooks/_hook_common.sh"
LINT_COMMON = REPO_ROOT / ".claude/hooks/_lint_common.sh"

# Not a positive duration. `00` is "no limit" to GNU / BSD timeout, `.` and `1.2.3`
# make it exit 125 (read as a finding), and the pure-bash fallback kills the tool
# at its first instant for all three. `abc`, `0` and `0.0` were already run
# unbounded; they now take the default as well.
NOT_A_TIMEOUT = ["00", ".", "1.2.3", "abc", "0", "0.0"]


def _lint_timeout_setting(value, default="45"):
    env = {k: v for k, v in os.environ.items() if k != "HOOK_LINT_TIMEOUT_TSC"}
    if value is not None:
        env["HOOK_LINT_TIMEOUT_TSC"] = value
    res = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'. "{HOOK_COMMON}"\n. "{LINT_COMMON}"\n_lint_timeout TSC {default}',
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return res.stdout


class TestLintTimeoutSetting:
    @pytest.mark.parametrize("value", [*NOT_A_TIMEOUT, "", None])
    def test_a_missing_or_malformed_setting_gives_the_tools_default(self, value):
        # The default, not "no limit": a tool that never returns would hold the
        # hook for as long as it runs.
        assert _lint_timeout_setting(value) == "45"
        assert _lint_timeout_setting(value, default="20") == "20"

    @pytest.mark.parametrize("value", ["1", "2.5", "30", "0.5"])
    def test_a_positive_number_is_used_as_given(self, value):
        assert _lint_timeout_setting(value) == value


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
@pytest.mark.parametrize("timeout_impl", ["binary", "fallback"])
class TestMalformedTimeoutSettingDoesNotBreakTheTool:
    @pytest.mark.parametrize("value", NOT_A_TIMEOUT)
    def test_a_quick_clean_tool_still_passes(
        self, LINT, shell_env, git_repo, timeout_impl, value
    ):
        if timeout_impl == "fallback":
            _hide_timeout_binaries(shell_env)
        shell_env.env["HOOK_LINT_TIMEOUT_TSC"] = value
        _write(git_repo / "tsconfig.json", "{}\n")
        target = _write(git_repo / "a.ts", "export const y = 1\n")
        # Long enough that a watchdog which fires at once would catch it.
        shell_env.stub("tsc", body="sleep 0.5")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "timed out" not in _log(shell_env, LINT)
        assert _notified(shell_env, "Lint Passed")


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestClippyIsScopedToTheEditedFile:
    def test_a_warning_in_another_file_does_not_block(self, LINT, shell_env, git_repo):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(
            shell_env,
            "cargo",
            "src/other.rs:3:9: warning: unused variable: `x`\n",
            exit_code=0,
            stream=1,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr

    def test_a_warning_in_the_edited_file_blocks(self, LINT, shell_env, git_repo):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        shell_env.stub(
            "cargo",
            body='echo "src/other.rs:3:9: warning: not mine"\n'
            'echo "src/lib.rs:1:5: warning: this one is"',
            exit_code=0,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[clippy]" in res.stderr
        assert "src/lib.rs:1:5" in res.stderr
        assert "not mine" not in res.stderr

    def test_a_compile_error_in_another_file_does_not_block(
        self, LINT, shell_env, git_repo
    ):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(
            shell_env,
            "cargo",
            "src/other.rs:9:1: error[E0425]: cannot find value `y`\n"
            "error: could not compile `demo` (lib) due to 1 previous error\n",
            exit_code=101,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr

    def test_the_invocation_asks_for_one_line_diagnostics(
        self, LINT, shell_env, git_repo
    ):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "x.rs", "fn main() {}\n")
        shell_env.stub("cargo")
        shell_env.run(LINT, stdin=payload(target))
        assert any(
            c.startswith("cargo clippy") and "--message-format=short" in c
            for c in shell_env.calls
        ), shell_env.calls


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestClippyThatFailedWithoutDiagnostics:
    """A non-zero cargo exit with no `path:line:col:` line anywhere in the output
    (an unreadable manifest, a registry failure) means clippy never looked at
    the file. That is a notice, and it must not be announced as a passed lint
    (the same rule mypy follows for a crash that names no file)."""

    @pytest.mark.parametrize(
        "message",
        [
            "error: failed to parse manifest at `/p/Cargo.toml`\n\n"
            "Caused by:\n  TOML parse error at line 1, column 9\n",
            "error: failed to get `serde` as a dependency of package `demo v0.1.0`\n\n"
            "Caused by:\n  download of config.json failed\n",
            "error: could not compile `demo` (lib) due to a previous error\n",
        ],
        ids=["manifest", "registry", "no-span"],
    )
    def test_it_is_a_notice_and_not_a_pass(self, LINT, shell_env, git_repo, message):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(shell_env, "cargo", message, exit_code=101)
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert not _notified(shell_env, "Lint Passed")
        assert "cargo clippy passed" not in res.stdout
        log = _log(shell_env, LINT)
        assert "NOTICE: cargo clippy did not complete" in log, log
        assert "exit 101" in log, log
        # The head of cargo's own message is carried so the cause is findable.
        assert message.splitlines()[0][:40] in log, log
        # Not the launcher notice: that one means cargo could not start.
        assert "could not start" not in log, log

    def test_the_quoted_head_is_bounded(self, LINT, shell_env, git_repo):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(shell_env, "cargo", "error: " + "x" * 5000 + "\n", exit_code=101)
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        log = _log(shell_env, LINT)
        assert "error: " + "x" * 50 in log, "the head is carried"
        assert "x" * 200 not in log, "the head is bounded"

    def test_a_blank_first_line_does_not_hide_the_message(
        self, LINT, shell_env, git_repo
    ):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(shell_env, "cargo", "\n\nerror: registry is down\n", exit_code=101)
        shell_env.run(LINT, stdin=payload(target))
        assert "registry is down" in _log(shell_env, LINT)

    def test_diagnostics_elsewhere_still_count_as_a_run(
        self, LINT, shell_env, git_repo
    ):
        # Control: this is the discriminator. With a `path:line:col:` line in the
        # output clippy did analyse the project, so a failure in another file
        # is still ignored and announced exactly as before.
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        _say(
            shell_env,
            "cargo",
            "src/other.rs:9:1: error[E0425]: cannot find value `y`\n"
            "error: could not compile `demo` (lib) due to 1 previous error\n",
            exit_code=101,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert _notified(shell_env, "Lint Passed")
        log = _log(shell_env, LINT)
        assert "diagnostics elsewhere ignored" in log, log
        assert "did not complete" not in log, log

    def test_a_clean_run_is_announced(self, LINT, shell_env, git_repo):
        _write(git_repo / "Cargo.toml", "[package]\n")
        target = _write(git_repo / "src" / "lib.rs", "pub fn f() {}\n")
        shell_env.stub("cargo")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert _notified(shell_env, "Lint Passed")
        assert "NOTICE" not in _log(shell_env, LINT)


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestLintPassedOnlyWhenSomethingRan:
    @pytest.mark.parametrize("name", ["notes.txt", "data.xyz", "README"])
    def test_a_file_no_linter_handles_is_not_announced(
        self, LINT, shell_env, tmp_path, name
    ):
        target = _write(tmp_path / name)
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not _notified(shell_env, "Lint Passed")

    def test_a_linter_that_ran_clean_is_announced(self, LINT, shell_env, tmp_path):
        shell_env.stub("shellcheck")
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert _notified(shell_env, "Lint Passed")

    def test_a_missing_linter_is_not_announced(self, LINT, shell_env, tmp_path):
        shell_env.hide("shellcheck")
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not _notified(shell_env, "Lint Passed")

    def test_the_notification_still_uses_env_indirection(
        self, LINT, shell_env, tmp_path
    ):
        from test_lint_and_format import (
            _env_without_real_terminal_notifier,
            _run_with_env,
        )

        (shell_env.stub_bin / "terminal-notifier").unlink()
        shell_env.stub("shellcheck")
        env = _env_without_real_terminal_notifier(shell_env)
        target = _write(tmp_path / 'evil".sh', "echo hi\n")
        res = _run_with_env(LINT, payload(target), env)
        assert res.returncode == 0
        osascript_calls = [c for c in shell_env.calls if c.startswith("osascript ")]
        assert osascript_calls, "expected notify() to fall back to osascript"
        assert target.name not in osascript_calls[0]
        assert "HOOK_NOTIFY_MESSAGE" in osascript_calls[0]


# --------------------------------------------------------------------------
# The report handed to the model is capped
# --------------------------------------------------------------------------


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestLintOutputIsCapped:
    def test_thousands_of_lines_are_cut_with_a_note(self, LINT, shell_env, tmp_path):
        shell_env.stub(
            "shellcheck",
            body='i=0; while [ $i -lt 5000 ]; do echo "x.sh:$i:1: SC0000 finding $i"; i=$((i+1)); done',
            exit_code=1,
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        lines = res.stderr.splitlines()
        assert len(lines) < 260, len(lines)
        assert len(res.stderr.encode("utf-8")) < 40_000
        assert "finding 0" in res.stderr, "the head of the report is what is kept"
        assert "truncated" in res.stderr
        assert "finding 4999" not in res.stderr
        log = _log(shell_env, LINT)
        assert len(log.encode("utf-8")) < 40_000, "the log must not take the full dump"

    def test_one_enormous_line_is_cut(self, LINT, shell_env, tmp_path):
        shell_env.stub(
            "shellcheck",
            body="head -c 400000 /dev/zero | tr '\\0' 'a'; echo",
            exit_code=1,
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert len(res.stderr.encode("utf-8")) < 40_000
        assert "truncated" in res.stderr

    def test_a_short_report_is_untouched(self, LINT, shell_env, tmp_path):
        shell_env.stub("shellcheck", body='echo "x.sh:1:1: SC2086 once"', exit_code=1)
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "SC2086 once" in res.stderr
        assert "truncated" not in res.stderr

    def test_the_limits_are_configurable(self, LINT, shell_env, tmp_path):
        shell_env.env["HOOK_LINT_MAX_LINES"] = "5"
        shell_env.stub(
            "shellcheck",
            body='for i in 1 2 3 4 5 6 7 8 9 10; do echo "line $i"; done',
            exit_code=1,
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        # The `[shellcheck]` heading is one of the five lines.
        assert "line 4" in res.stderr and "line 6" not in res.stderr
        assert "truncated" in res.stderr

    @pytest.mark.parametrize("value", ["abc", "0", "1.5", "10 20"])
    def test_a_malformed_limit_still_returns_the_head_of_the_report(
        self, LINT, shell_env, tmp_path, value
    ):
        # A limit that is not a number used to make the comparison fail, which
        # dropped the whole report and left a lone "… truncated" line that was
        # itself the exit-2 finding.
        shell_env.env["HOOK_LINT_MAX_LINES"] = value
        shell_env.env["HOOK_LINT_MAX_BYTES"] = value
        shell_env.stub(
            "shellcheck",
            body='i=0; while [ $i -lt 5000 ]; do echo "x.sh:$i:1: SC0000 finding $i"; i=$((i+1)); done',
            exit_code=1,
        )
        target = _write(tmp_path / "x.sh", "echo hi\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "finding 0" in res.stderr, res.stderr[:300]
        assert "finding 4999" not in res.stderr
        lines = res.stderr.splitlines()
        assert 150 < len(lines) < 260, len(lines)
        assert "truncated" in res.stderr


@pytest.mark.parametrize("value", ["abc", "0", "1.5"])
def test_codex_aggregate_limit_that_is_malformed_takes_the_aggregate_default(
    shell_env, tmp_path, value
):
    # The fallback is the aggregate's own default (400 lines / 32 KB), not the
    # per-file one (200 lines / 16 KB) that hook_cap_output would otherwise pick.
    shell_env.env["HOOK_LINT_TOTAL_MAX_LINES"] = value
    shell_env.env["HOOK_LINT_TOTAL_MAX_BYTES"] = value
    shell_env.stub(
        "shellcheck",
        body='i=0; while [ $i -lt 150 ]; do echo "finding $i of $1"; i=$((i+1)); done',
        exit_code=1,
    )
    targets = [_write(tmp_path / f"f{n}.sh", "echo hi\n") for n in range(40)]
    patch = "*** Begin Patch\n" + "".join(
        f"*** Update File: {t}\n@@\n-x\n+y\n" for t in targets
    )
    import json

    res = shell_env.run(
        CODEX_LINT, stdin=json.dumps({"tool_input": {"command": patch}})
    )
    assert res.returncode == 2
    assert "finding 0 of" in res.stderr, res.stderr[:300]
    # The note names the limits that were applied.
    assert "capped at 400 lines / 32768 bytes" in res.stderr, res.stderr[-300:]
    assert 380 < len(res.stderr.splitlines()) < 430


def test_codex_caps_the_aggregate_over_many_files(shell_env, tmp_path):
    # 40 files x 150 lines each stay under the per-file cap but would flood the
    # model together; Codex lints a whole apply_patch in one report.
    shell_env.stub(
        "shellcheck",
        body='i=0; while [ $i -lt 150 ]; do echo "finding $i of $1"; i=$((i+1)); done',
        exit_code=1,
    )
    targets = [_write(tmp_path / f"f{n}.sh", "echo hi\n") for n in range(40)]
    patch = "*** Begin Patch\n" + "".join(
        f"*** Update File: {t}\n@@\n-x\n+y\n" for t in targets
    )
    import json

    res = shell_env.run(
        CODEX_LINT, stdin=json.dumps({"tool_input": {"command": patch}})
    )
    assert res.returncode == 2
    assert len(res.stderr.splitlines()) < 700
    assert len(res.stderr.encode("utf-8")) < 80_000
    assert "truncated" in res.stderr


# --------------------------------------------------------------------------
# Formatters respect project config; symlinks are skipped on purpose
# --------------------------------------------------------------------------


def _format_calls(shell_env, tool):
    return [c for c in shell_env.calls if c.startswith(f"{tool} ")]


class TestShfmtFollowsEditorconfig:
    @pytest.fixture
    def script(self, git_repo):
        return _write(git_repo / "x.sh", "echo hi\n")

    def test_without_editorconfig_the_default_style_stays(
        self, shell_env, git_repo, script
    ):
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_an_editorconfig_that_sets_the_indent_is_left_to_shfmt(
        self, shell_env, git_repo, script
    ):
        # shfmt reads .editorconfig only when no style flag is given.
        _write(git_repo / ".editorconfig", "[*.sh]\nindent_style = tab\n")
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        calls = _format_calls(shell_env, "shfmt")
        assert calls and all(" -i " not in c for c in calls), calls
        assert any(c.startswith("shfmt -w ") for c in calls)

    def test_a_catch_all_section_counts(self, shell_env, git_repo, script):
        _write(git_repo / ".editorconfig", "root = true\n\n[*]\nindent_size = 4\n")
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        assert all(" -i " not in c for c in _format_calls(shell_env, "shfmt"))

    def test_an_editorconfig_without_an_indent_setting_does_not_count(
        self, shell_env, git_repo, script
    ):
        # shfmt would fall back to tabs for it; keep the existing default.
        _write(git_repo / ".editorconfig", "root = true\n\n[*]\ncharset = utf-8\n")
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_an_indent_for_another_language_does_not_count(
        self, shell_env, git_repo, script
    ):
        _write(git_repo / ".editorconfig", "[*.py]\nindent_size = 4\n")
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_root_true_stops_the_search(self, shell_env, git_repo):
        _write(git_repo / ".editorconfig", "[*]\nindent_style = tab\n")
        pkg = git_repo / "pkg"
        _write(pkg / ".editorconfig", "root = true\n")
        script = _write(pkg / "x.sh", "echo hi\n")
        shell_env.stub("shfmt")
        shell_env.run(CLAUDE_FORMAT, stdin=payload(script))
        assert any(c.startswith("shfmt -i 2 -w") for c in shell_env.calls)

    def test_codex_copy_follows_the_same_rule(self, shell_env, git_repo):
        _write(git_repo / ".editorconfig", "[*]\nindent_style = tab\n")
        _write(git_repo / "x.sh", "echo hi\n")
        shell_env.stub("shfmt")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        calls = _format_calls(shell_env, "shfmt")
        assert calls and all(" -i " not in c for c in calls)


PRETTIER_CONFIGS = [
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yaml",
    ".prettierrc.yml",
    ".prettierrc.json5",
    ".prettierrc.js",
    ".prettierrc.cjs",
    ".prettierrc.mjs",
    ".prettierrc.toml",
    "prettier.config.js",
    "prettier.config.cjs",
    "prettier.config.mjs",
    "prettier.config.ts",
]


class TestPrettierNeedsAProjectConfig:
    @pytest.fixture
    def FORMAT(self):
        return CLAUDE_FORMAT

    def test_no_config_means_the_file_is_left_alone(self, FORMAT, shell_env, git_repo):
        shell_env.stub("prettier")
        target = _write(git_repo / "x.js", "var x=1\n")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert not _format_calls(shell_env, "prettier"), (
            "a repo without a prettier config must not be rewritten in the default style"
        )
        assert "prettier" in _log(shell_env, FORMAT, "format.log").lower()
        assert not any("Format Done" in c for c in shell_env.calls)

    @pytest.mark.parametrize("config", PRETTIER_CONFIGS)
    def test_every_config_spelling_enables_it(
        self, FORMAT, shell_env, git_repo, config
    ):
        shell_env.stub("prettier")
        _write(git_repo / config, "{}\n")
        target = _write(git_repo / "x.js", "var x=1\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "prettier"), config

    def test_a_prettier_key_in_package_json_enables_it(
        self, FORMAT, shell_env, git_repo
    ):
        shell_env.stub("prettier")
        _write(
            git_repo / "package.json", '{"name": "a", "prettier": {"semi": false}}\n'
        )
        target = _write(git_repo / "x.js", "var x=1\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "prettier")

    def test_prettier_as_a_dev_dependency_alone_is_not_a_config(
        self, FORMAT, shell_env, git_repo
    ):
        shell_env.stub("prettier")
        _write(
            git_repo / "package.json",
            '{"name": "a", "devDependencies": {"prettier": "^3.0.0"}}\n',
        )
        target = _write(git_repo / "x.js", "var x=1\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert not _format_calls(shell_env, "prettier")

    def test_a_package_json_without_the_key_does_not_stop_the_walk(
        self, FORMAT, shell_env, git_repo
    ):
        shell_env.stub("prettier")
        _write(git_repo / ".prettierrc", "{}\n")
        pkg = git_repo / "packages" / "web"
        _write(pkg / "package.json", '{"name": "web"}\n')
        target = _write(pkg / "app.js", "var x=1\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "prettier")

    def test_outside_a_repo_only_the_files_own_directory_is_searched(
        self, FORMAT, shell_env, tmp_path
    ):
        shell_env.stub("prettier")
        work = tmp_path / "work"
        _write(tmp_path / ".prettierrc", "{}\n")
        target = _write(work / "x.js", "var x=1\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert not _format_calls(shell_env, "prettier")
        _write(work / ".prettierrc", "{}\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "prettier")

    def test_codex_copy_follows_the_same_rule(self, shell_env, git_repo):
        shell_env.stub("prettier")
        _write(git_repo / "x.js", "var x=1\n")
        res = shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert res.returncode == 0
        assert not _format_calls(shell_env, "prettier")
        _write(git_repo / ".prettierrc", "{}\n")
        shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert _format_calls(shell_env, "prettier")


class TestClangFormatNeedsAProjectConfig:
    @pytest.fixture
    def FORMAT(self):
        return CLAUDE_FORMAT

    def test_no_config_means_the_file_is_left_alone(self, FORMAT, shell_env, git_repo):
        shell_env.stub("clang-format")
        target = _write(git_repo / "x.cpp", "int main(){}\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert not _format_calls(shell_env, "clang-format")

    @pytest.mark.parametrize("name", [".clang-format", "_clang-format"])
    def test_a_config_up_the_tree_enables_it(self, FORMAT, shell_env, git_repo, name):
        shell_env.stub("clang-format")
        _write(git_repo / name, "BasedOnStyle: LLVM\n")
        target = _write(git_repo / "src" / "deep" / "x.cpp", "int main(){}\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "clang-format")

    def test_codex_copy_follows_the_same_rule(self, shell_env, git_repo):
        shell_env.stub("clang-format")
        _write(git_repo / "x.cpp", "int main(){}\n")
        shell_env.run(CODEX_FORMAT, stdin="{}", cwd=git_repo)
        assert not _format_calls(shell_env, "clang-format")


class TestSymlinkedTargetsAreSkippedOnPurpose:
    @pytest.fixture
    def FORMAT(self):
        return CLAUDE_FORMAT

    def _link(self, tmp_path, name="x.sh", body="echo hi\n"):
        real = _write(tmp_path / "real" / f"real_{name}", body)
        link = tmp_path / name
        link.symlink_to(real)
        return real, link

    def test_a_symlinked_script_is_not_handed_to_shfmt(
        self, FORMAT, shell_env, tmp_path
    ):
        # shfmt refuses to replace a symlink with a regular file.
        shell_env.stub("shfmt")
        _, link = self._link(tmp_path)
        res = shell_env.run(FORMAT, stdin=payload(link))
        assert res.returncode == 0
        assert not _format_calls(shell_env, "shfmt")

    def test_the_log_says_symlink_not_not_installed(self, FORMAT, shell_env, tmp_path):
        shell_env.stub("shfmt")
        _, link = self._link(tmp_path)
        shell_env.run(FORMAT, stdin=payload(link))
        log = _log(shell_env, FORMAT, "format.log")
        assert "skipped (symlink)" in log
        assert "未導入" not in log

    def test_a_symlinked_json_is_not_handed_to_prettier(
        self, FORMAT, shell_env, tmp_path
    ):
        shell_env.stub("prettier")
        _write(tmp_path / ".prettierrc", "{}\n")
        _, link = self._link(tmp_path, "a.json", "{}\n")
        shell_env.run(FORMAT, stdin=payload(link))
        assert not _format_calls(shell_env, "prettier")
        assert "skipped (symlink)" in _log(shell_env, FORMAT, "format.log")

    def test_a_regular_file_is_unaffected(self, FORMAT, shell_env, tmp_path):
        shell_env.stub("shfmt")
        target = _write(tmp_path / "x.sh", "echo hi\n")
        shell_env.run(FORMAT, stdin=payload(target))
        assert _format_calls(shell_env, "shfmt")

    def test_a_file_reached_through_a_symlinked_directory_is_still_formatted(
        self, FORMAT, shell_env, tmp_path
    ):
        # ~/.claude -> <repo>/.claude is how this very repo is installed.
        shell_env.stub("shfmt")
        real = tmp_path / "real"
        _write(real / "x.sh", "echo hi\n")
        (tmp_path / "linkdir").symlink_to(real, target_is_directory=True)
        shell_env.run(FORMAT, stdin=payload(tmp_path / "linkdir" / "x.sh"))
        assert _format_calls(shell_env, "shfmt")


# --------------------------------------------------------------------------
# Docker: Dockerfile -> hadolint, compose -> docker compose config
# --------------------------------------------------------------------------

DOCKERFILE_NAMES = ["Dockerfile", "Dockerfile.dev", "app.Dockerfile", "Containerfile"]
COMPOSE_NAMES = [
    "compose.yaml",
    "compose.yml",
    "compose.prod.yaml",
    "compose.override.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
    "docker-compose.override.yml",
    "docker-compose.prod.yaml",
]
NOT_DOCKER_NAMES = [
    "Dockerfile.dockerignore",
    "values.yaml",
    "config.yml",
    "my-compose.yml",
]


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestDockerfileArm:
    @pytest.mark.parametrize("name", DOCKERFILE_NAMES)
    def test_findings_block(self, LINT, shell_env, tmp_path, name):
        shell_env.stub(
            "hadolint",
            body='echo "Dockerfile:1 DL3006 warning: Always tag"',
            exit_code=1,
        )
        target = _write(tmp_path / name, "FROM ubuntu\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, res.stderr
        assert "[hadolint]" in res.stderr
        assert "DL3006" in res.stderr
        assert any(
            c.startswith("hadolint ") and c.endswith(str(target))
            for c in shell_env.calls
        )

    @pytest.mark.parametrize("name", DOCKERFILE_NAMES)
    def test_a_clean_file_passes_and_is_announced(
        self, LINT, shell_env, tmp_path, name
    ):
        shell_env.stub("hadolint")
        target = _write(tmp_path / name, "FROM ubuntu:22.04\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert _notified(shell_env, "Lint Passed")

    @pytest.mark.parametrize("name", NOT_DOCKER_NAMES)
    def test_other_names_are_not_linted_as_docker_files(
        self, LINT, shell_env, tmp_path, name
    ):
        shell_env.stub("hadolint", exit_code=1)
        shell_env.stub("docker", exit_code=1)
        target = _write(tmp_path / name, "x: 1\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not any(c.startswith(("hadolint ", "docker ")) for c in shell_env.calls)

    def test_the_basename_decides_even_when_the_directory_has_dots(
        self, LINT, shell_env, tmp_path
    ):
        shell_env.stub("hadolint")
        target = _write(tmp_path / "v1.2" / "Dockerfile", "FROM ubuntu:22.04\n")
        shell_env.run(LINT, stdin=payload(target))
        assert any(c.startswith("hadolint ") for c in shell_env.calls)

    def test_missing_hadolint_is_not_a_failure(self, LINT, shell_env, tmp_path):
        shell_env.hide("hadolint")
        target = _write(tmp_path / "Dockerfile", "FROM ubuntu\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not _notified(shell_env, "Lint Passed")

    def test_a_hadolint_that_cannot_start_is_a_notice(self, LINT, shell_env, tmp_path):
        shell_env.stub("hadolint", body="echo 'boom' >&2", exit_code=127)
        target = _write(tmp_path / "Dockerfile", "FROM ubuntu\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert "NOTICE" in _log(shell_env, LINT)

    @pytest.mark.parametrize("impl", ["binary", "fallback"])
    def test_a_hung_hadolint_is_cut_off(self, LINT, shell_env, tmp_path, impl):
        if impl == "fallback":
            _hide_timeout_binaries(shell_env)
        shell_env.env["HOOK_LINT_TIMEOUT_HADOLINT"] = "1"
        shell_env.stub("hadolint", body=_SLEEPER, exit_code=1)
        target = _write(tmp_path / "Dockerfile", "FROM ubuntu\n")
        started = time.monotonic()
        res = shell_env.run(LINT, stdin=payload(target))
        assert time.monotonic() - started < 15
        assert res.returncode == 0
        assert "timed out" in _log(shell_env, LINT)

    @pytest.mark.parametrize("cfg_name", [".hadolint.yaml", ".hadolint.yml"])
    def test_a_project_config_is_passed_explicitly(
        self, LINT, shell_env, git_repo, cfg_name
    ):
        # hadolint only looks in its own cwd; the hook must hand it the
        # project's file or the rules the team configured are silently ignored.
        shell_env.stub("hadolint")
        cfg = _write(git_repo / cfg_name, "ignored: [DL3008]\n")
        target = _write(git_repo / "docker" / "app" / "Dockerfile", "FROM ubuntu\n")
        shell_env.run(LINT, stdin=payload(target))
        call = next(c for c in shell_env.calls if c.startswith("hadolint "))
        tokens = call.split()
        assert "--config" in tokens or "-c" in tokens, call
        flag = tokens.index("--config") if "--config" in tokens else tokens.index("-c")
        assert os.path.realpath(tokens[flag + 1]) == os.path.realpath(cfg)

    def test_without_a_project_config_none_is_passed(self, LINT, shell_env, git_repo):
        shell_env.stub("hadolint")
        target = _write(git_repo / "Dockerfile", "FROM ubuntu\n")
        shell_env.run(LINT, stdin=payload(target))
        call = next(c for c in shell_env.calls if c.startswith("hadolint "))
        assert "--config" not in call and " -c " not in call


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestComposeArm:
    SCHEMA_ERROR = (
        "validating {path}: services.web Additional property imagee is not allowed"
    )

    @pytest.mark.parametrize("name", COMPOSE_NAMES)
    def test_it_runs_config_without_interpolation(
        self, LINT, shell_env, tmp_path, name
    ):
        shell_env.stub("docker")
        target = _write(tmp_path / name, "services: {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert (
            f"docker compose -f {target} config -q --no-interpolate" in shell_env.calls
        ), shell_env.calls

    def test_a_schema_finding_blocks(self, LINT, shell_env, tmp_path):
        target = _write(tmp_path / "compose.yaml", "services: {}\n")
        shell_env.stub(
            "docker",
            body=f'echo "{self.SCHEMA_ERROR.format(path=target)}" >&2',
            exit_code=1,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, res.stderr
        assert "[docker compose]" in res.stderr
        assert "Additional property" in res.stderr

    def test_a_yaml_syntax_error_blocks(self, LINT, shell_env, tmp_path):
        target = _write(tmp_path / "compose.yaml", "services: [\n")
        shell_env.stub(
            "docker",
            body='echo "yaml: line 1: did not find expected node content" >&2',
            exit_code=1,
        )
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2

    @pytest.mark.parametrize(
        "message",
        [
            "env file /w/.env not found: stat /w/.env: no such file or directory",
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
            " Is the docker daemon running?",
            "permission denied while trying to connect to the Docker daemon socket",
            "docker: 'compose' is not a docker command.",
            "unknown flag: --no-interpolate",
            "error getting credentials - err: exit status 1",
            'context "colima" not found',
        ],
        ids=[
            "missing-env-file",
            "daemon-down",
            "daemon-permission",
            "no-compose-plugin",
            "old-compose",
            "credential-helper",
            "unknown-context",
        ],
    )
    def test_environment_trouble_is_a_notice(self, LINT, shell_env, tmp_path, message):
        target = _write(tmp_path / "compose.yaml", "services: {}\n")
        _say(shell_env, "docker", message + "\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr
        assert "NOTICE" in _log(shell_env, LINT)
        assert not _notified(shell_env, "Lint Passed")

    def test_a_partial_override_is_not_judged_on_its_own(
        self, LINT, shell_env, tmp_path
    ):
        # Overrides and environment files are meant to be merged with a base;
        # alone they fail `config` for being incomplete, which says nothing
        # about the edit.
        _say(
            shell_env,
            "docker",
            'service "web" has neither an image nor a build context specified:'
            " invalid compose project\n",
        )
        target = _write(tmp_path / "compose.override.yaml", "services:\n  web: {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, res.stderr

    def test_the_same_error_in_a_base_file_is_a_finding(
        self, LINT, shell_env, tmp_path
    ):
        _say(
            shell_env,
            "docker",
            'service "web" has neither an image nor a build context specified:'
            " invalid compose project\n",
        )
        target = _write(tmp_path / "compose.yaml", "services:\n  web: {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2

    def test_a_missing_docker_cli_is_not_a_failure(self, LINT, shell_env, tmp_path):
        shell_env.hide("docker")
        target = _write(tmp_path / "compose.yaml", "services: {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not _notified(shell_env, "Lint Passed")

    @pytest.mark.parametrize("impl", ["binary", "fallback"])
    def test_a_hung_docker_is_cut_off(self, LINT, shell_env, tmp_path, impl):
        if impl == "fallback":
            _hide_timeout_binaries(shell_env)
        shell_env.env["HOOK_LINT_TIMEOUT_COMPOSE"] = "1"
        shell_env.stub("docker", body=_SLEEPER, exit_code=1)
        target = _write(tmp_path / "compose.yaml", "services: {}\n")
        started = time.monotonic()
        res = shell_env.run(LINT, stdin=payload(target))
        assert time.monotonic() - started < 15
        assert res.returncode == 0
        assert "timed out" in _log(shell_env, LINT)

    def test_a_clean_file_passes_and_is_announced(self, LINT, shell_env, tmp_path):
        shell_env.stub("docker")
        target = _write(tmp_path / "compose.yaml", "services: {}\n")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert _notified(shell_env, "Lint Passed")


# --------------------------------------------------------------------------
# Safety net for the unborn-branch / git helpers used by the Codex copy
# --------------------------------------------------------------------------


def test_codex_lint_of_two_text_files_announces_nothing(shell_env, git_repo):
    import json

    for n in ("a.txt", "b.txt"):
        _write(git_repo / n)
    patch = "*** Begin Patch\n" + "".join(
        f"*** Update File: {git_repo / n}\n@@\n-x\n+y\n" for n in ("a.txt", "b.txt")
    )
    res = shell_env.run(
        CODEX_LINT, stdin=json.dumps({"tool_input": {"command": patch}})
    )
    assert res.returncode == 0
    assert not _notified(shell_env, "Lint Passed")
