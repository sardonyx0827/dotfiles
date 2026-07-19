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


def _env_hiding(shell_env, *names: str) -> dict | None:
    """PATH with every directory providing `names` removed, stubs still first.

    Returns None when jq would be hidden as collateral: several of these tools
    share a directory with it (php and jq are both in /opt/homebrew/bin here),
    and without jq the hook bails out at payload parsing, so the test would
    pass for entirely the wrong reason.
    """
    path = shell_env.env["PATH"]
    for name in names:
        path = _path_without_executable(path, name)
    has_jq = any(
        os.access(os.path.join(d, "jq"), os.X_OK) for d in path.split(os.pathsep) if d
    )
    if not has_jq:
        return None
    return {**shell_env.env, "PATH": f"{shell_env.stub_bin}{os.pathsep}{path}"}


# (extension, tool under test, expected argv prefix, error tag, companions).
# `companions` are the other tools the same branch invokes; they get neutral
# stubs so a real one on the developer's machine cannot decide the outcome.
EXIT_CODE_LINTERS = [
    ("go", "go", "go vet ", "[go vet]", ["staticcheck"]),
    ("rb", "rubocop", "rubocop --no-color ", "[rubocop]", []),
    ("php", "phpstan", "phpstan analyse ", "[phpstan]", []),
]

# Linters whose failure is detected by grepping the tool's OUTPUT instead of
# its exit status. The stub exits 0 and must still be treated as a failure --
# this is the matrix's least obvious contract and the easiest thing to break.
#
# Every tool is pinned at *every* severity it can emit, because the one it uses
# by default is not the one its name suggests: checkstyle's bundled
# google_checks.xml sets severity=warning, so real findings arrive as [WARN] and
# an [ERROR] never appears; cppcheck is asked for four categories but only two
# of them were ever matched. A matcher that only knows the error spelling
# reports a clean bill of health for every finding the tool actually produces --
# the "found a problem but returned green" break this file's header calls the
# worst one available. Same class as the clippy cases below.
#
# The cppcheck rows are written in the `(severity)` shape the hook pins via
# --template, NOT cppcheck 2.x's default `severity:` shape. That pin is itself
# load-bearing and has its own test (test_cppcheck_template_and_matcher_agree).
GREP_LINTERS = [
    pytest.param(
        "java",
        "checkstyle",
        "Foo.java:1: [ERROR] missing javadoc",
        "[checkstyle]",
        id="checkstyle-error",
    ),
    pytest.param(
        "java",
        "checkstyle",
        "[WARN] Foo.java:1:1: missing javadoc",
        "[checkstyle]",
        id="checkstyle-warn",
    ),
    pytest.param(
        "cpp",
        "cppcheck",
        "x.cpp:10:13: (error) Null pointer dereference: p [nullPointer]",
        "[cppcheck]",
        id="cppcheck-error",
    ),
    pytest.param(
        "cpp",
        "cppcheck",
        "x.cpp:1:1: (warning) Possible null pointer dereference [nullPointerRedundantCheck]",
        "[cppcheck]",
        id="cppcheck-warning",
    ),
    pytest.param(
        "cpp",
        "cppcheck",
        "x.cpp:4:9: (style) The scope of the variable i can be reduced. [variableScope]",
        "[cppcheck]",
        id="cppcheck-style",
    ),
    pytest.param(
        "cpp",
        "cppcheck",
        "x.cpp:2:23: (performance) Function parameter s should be passed by const"
        " reference. [passedByValue]",
        "[cppcheck]",
        id="cppcheck-performance",
    ),
    pytest.param(
        "cpp",
        "cppcheck",
        "x.cpp:14:5: (portability) Returning an address value in a function with"
        " integer return type is not portable. [CastAddressToIntegerAtReturn]",
        "[cppcheck]",
        id="cppcheck-portability",
    ),
]

# The clean-output half of the same contract. One row per tool: the trigger is
# unused there, so splitting it by severity would only duplicate runs.
GREP_LINTERS_CLEAN = [
    pytest.param("java", "checkstyle", id="checkstyle"),
    pytest.param("cpp", "cppcheck", id="cppcheck"),
]


@pytest.mark.parametrize("LINT", LINT_HOOKS, ids=["claude", "codex"])
class TestLintLanguageMatrix:
    """Characterization of the per-language dispatch table.

    Only .py and .sh had coverage; js/ts, rs, go, java, c/c++, rb and php --
    roughly 230 lines duplicated verbatim between the two copies -- had none,
    so a mechanical edit there could not be caught. These pin *which* tool each
    extension dispatches to and *how* its failure is recognised, which is what
    an extraction has to preserve.
    """

    @pytest.mark.parametrize(
        "ext,tool,argv_prefix,tag,companions",
        EXIT_CODE_LINTERS,
        ids=[t[1] for t in EXIT_CODE_LINTERS],
    )
    def test_exit_code_linter_failure_blocks(
        self, LINT, shell_env, tmp_path, ext, tool, argv_prefix, tag, companions
    ):
        shell_env.stub(tool, body='echo "problem found"', exit_code=1)
        for companion in companions:
            shell_env.stub(companion)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert tag in res.stderr
        assert "problem found" in res.stderr
        assert any(c.startswith(argv_prefix) for c in shell_env.calls), (
            f"expected {tool} to be invoked as '{argv_prefix}...', got {shell_env.calls}"
        )
        log = _log_file(shell_env, LINT, "lint.log")
        assert f"FAILED: x.{ext}" in log.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        "ext,tool,argv_prefix,tag,companions",
        EXIT_CODE_LINTERS,
        ids=[t[1] for t in EXIT_CODE_LINTERS],
    )
    def test_exit_code_linter_success_passes(
        self, LINT, shell_env, tmp_path, ext, tool, argv_prefix, tag, companions
    ):
        shell_env.stub(tool)
        for companion in companions:
            shell_env.stub(companion)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        log = _log_file(shell_env, LINT, "lint.log")
        assert f"PASSED: x.{ext}" in log.read_text(encoding="utf-8")

    @pytest.mark.parametrize("ext,tool,trigger,tag", GREP_LINTERS)
    def test_grep_linter_fails_on_output_despite_exit_zero(
        self, LINT, shell_env, tmp_path, ext, tool, trigger, tag
    ):
        # exit_code=0 on purpose: these tools report findings on stdout while
        # exiting successfully, so the matrix greps their output.
        shell_env.stub(tool, body=f'echo "{trigger}"', exit_code=0)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            f"{tool} findings on stdout must block even when it exits 0"
        )
        assert tag in res.stderr

    @pytest.mark.parametrize("ext,tool", GREP_LINTERS_CLEAN)
    def test_grep_linter_passes_on_clean_output(
        self, LINT, shell_env, tmp_path, ext, tool
    ):
        shell_env.stub(tool, body='echo "no issues"', exit_code=0)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0

    @pytest.mark.parametrize("ext,tool", GREP_LINTERS_CLEAN)
    def test_grep_linter_invocation_failure_blocks(
        self, LINT, shell_env, tmp_path, ext, tool
    ):
        """A non-zero exit means the tool never ran -- that is not "clean".

        These tools exit 0 whether or not they found anything, so the matcher
        reads their output instead. The blind spot is the third case: the tool
        failing to start (unknown flag, unreadable config, internal error). Its
        message carries none of the severity markers, so a matcher looking only
        at output cannot tell it apart from a clean file and reports success --
        the same silent pass this file's header warns about, one level up. A
        wrong flag would then disable the gate permanently and invisibly.
        """
        shell_env.stub(
            tool,
            body=f'echo "{tool}: error: unrecognized command line option" >&2',
            exit_code=1,
        )
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            f"{tool} exiting non-zero means it never ran; treating that as a "
            "clean bill of health silently disables the gate"
        )
        assert f"[{tool}]" in res.stderr

    # Renders whatever --template it is handed, the way cppcheck would, so the
    # hook's own matcher is what decides the outcome. Exits 3 (not 0) when no
    # template arrives, because a silent pass is the failure under test.
    _CPPCHECK_RENDERING_STUB = r"""
tmpl=""
for a in "$@"; do
  case "$a" in --template=*) tmpl="${a#--template=}" ;; esac
done
if [ -z "$tmpl" ]; then
  echo "stub: hook passed no --template; cppcheck's default would decide" >&2
  exit 3
fi
out="$tmpl"
out="${out//\{file\}/x.cpp}"
out="${out//\{line\}/4}"
out="${out//\{column\}/9}"
out="${out//\{severity\}/style}"
out="${out//\{message\}/The scope of the variable i can be reduced.}"
out="${out//\{id\}/variableScope}"
echo "$out"
"""

    def test_checkstyle_default_config_resolves(self, LINT, shell_env, tmp_path):
        """The fallback config must be one checkstyle can actually load.

        The branch passed `-c google`, which does not resolve: real checkstyle
        (13.8) answers `Could not find config XML file 'google'.` and exits 255,
        so this linter never checked a single file. It looked healthy only
        because the exception carries no [ERROR]/[WARN] and the matcher read
        that as clean -- the same silent pass, one layer earlier. The bundled
        config is a classpath resource and has to be named like one.
        """
        shell_env.stub("checkstyle", body='echo "Starting audit..."', exit_code=0)
        target = tmp_path / "Foo.java"
        target.write_text("class Foo {}\n", encoding="utf-8")
        shell_env.run(LINT, stdin=payload(target))
        call = next((c for c in shell_env.calls if c.startswith("checkstyle ")), None)
        assert call is not None, "checkstyle was never invoked"
        assert "-c /google_checks.xml" in call, (
            "checkstyle's bundled Google config is the classpath resource "
            f"/google_checks.xml; a bare name does not resolve. got: {call}"
        )

    def test_cppcheck_template_and_matcher_agree(self, LINT, shell_env, tmp_path):
        """The pinned template must render the shape the matcher greps.

        cppcheck 1.x printed `(severity)`; 2.x changed its default to
        `severity:`. The matcher kept looking for the parenthesised spelling, so
        against any modern cppcheck it matched nothing at all and the gate
        reported success on every file it saw -- `(error)` findings included.
        The fix is to stop inheriting the tool's default and pin the format.

        Asserting the flag merely *exists* would not protect that: a pin of
        `{severity}:` -- the 2.x shape, i.e. the very desync this guards -- also
        contains "--template=" and passes such a check. So this stub renders the
        template it actually receives and lets the hook's matcher judge the
        result, which makes the two sides impossible to drift apart silently.
        """
        shell_env.stub("cppcheck", body=self._CPPCHECK_RENDERING_STUB, exit_code=0)
        target = tmp_path / "x.cpp"
        target.write_text("int main() {}\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            "cppcheck emitted a finding through the template the hook pinned, "
            "and the hook passed the file: the pinned template and the matcher "
            "regex no longer agree"
        )
        assert "[cppcheck]" in res.stderr
        # --template silently drops each finding's secondary locations unless
        # --template-location is set too (verified against cppcheck 2.21). Those
        # carry the "why" -- e.g. `Assignment 'p=0', assigned value is 0` for a
        # nullPointer -- into the text the agent self-corrects from, so losing
        # them costs no gate correctness and all of the explanation.
        call = next(c for c in shell_env.calls if c.startswith("cppcheck "))
        assert "--template-location=" in call, (
            "pinning --template without --template-location silently discards "
            "the notes that explain each finding"
        )

    # Every tool the branch could reach must be hidden, not merely left
    # un-stubbed: this machine really has go, staticcheck, php, cargo, eslint
    # and tsc, so "no stub" tests the developer's $PATH rather than the hook.
    @pytest.mark.parametrize(
        "ext,tools",
        [
            ("go", ["go", "staticcheck"]),
            ("rb", ["rubocop"]),
            ("php", ["phpstan", "php"]),
            ("java", ["checkstyle"]),
            ("cpp", ["cppcheck"]),
            ("rs", ["cargo"]),
            ("ts", ["eslint", "tsc"]),
        ],
    )
    def test_missing_linter_is_skipped_not_fatal(
        self, LINT, shell_env, tmp_path, ext, tools
    ):
        env = _env_hiding(shell_env, *tools)
        if env is None:
            pytest.skip(f"cannot hide {tools} without also hiding jq on this host")
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = _run_with_env(LINT, payload(target), env)
        assert res.returncode == 0, (
            f"a missing linter must degrade to a pass, got: {res.stderr}"
        )
        log = _log_file(shell_env, LINT, "lint.log")
        assert f"PASSED: x.{ext}" in log.read_text(encoding="utf-8")

    def test_php_falls_back_to_php_lint_without_phpstan(
        self, LINT, shell_env, tmp_path
    ):
        # phpstan absent -> `php -l` syntax check is the documented fallback.
        # "Absent" must be forced: the stub dir is only PREPENDED to the host
        # PATH, so a real phpstan (CI runner images ship one) would win.
        shell_env.hide("phpstan")
        shell_env.stub("php", body='echo "Parse error: syntax error"', exit_code=1)
        target = tmp_path / "x.php"
        target.write_text("<?php\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[php syntax]" in res.stderr
        assert any(c.startswith("php -l ") for c in shell_env.calls)

    def test_php_fallback_survives_host_phpstan(self, LINT, shell_env, tmp_path):
        # Regression: when the host PATH really does contain phpstan (the CI
        # runner image grew one), it used to hijack the fallback test — the
        # real phpstan even shells out to the stubbed `php`, relabelling the
        # stub's canned output as [phpstan]. hide() must survive that setup.
        host_bin = tmp_path / "host-bin"
        host_bin.mkdir()
        fake = host_bin / "phpstan"
        fake.write_text("#!/bin/bash\necho analysed\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        shell_env.env["PATH"] = f"{shell_env.env['PATH']}:{host_bin}"
        shell_env.hide("phpstan")
        shell_env.stub("php", body='echo "Parse error: syntax error"', exit_code=1)
        target = tmp_path / "x.php"
        target.write_text("<?php\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[php syntax]" in res.stderr
        assert not any(c.startswith("phpstan") for c in shell_env.calls)

    def test_phpstan_wins_over_php_lint_when_both_exist(
        self, LINT, shell_env, tmp_path
    ):
        shell_env.stub("phpstan")
        shell_env.stub("php")
        target = tmp_path / "x.php"
        target.write_text("<?php\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert any(c.startswith("phpstan analyse ") for c in shell_env.calls)
        assert not any(c.startswith("php -l") for c in shell_env.calls), (
            "php -l is a fallback and must not run when phpstan is available"
        )

    def test_clippy_skipped_without_cargo_toml(self, LINT, shell_env, git_repo):
        shell_env.stub("cargo")
        target = git_repo / "x.rs"
        target.write_text("fn main() {}\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0
        assert not any(c.startswith("cargo clippy") for c in shell_env.calls), (
            "clippy must not run outside a Cargo project"
        )

    def test_clippy_fails_on_error_line_despite_exit_zero(
        self, LINT, shell_env, git_repo
    ):
        # Like checkstyle/cppcheck, clippy's result is read off its output.
        # Note this stub is a *rustc* diagnostic, not a clippy lint: E0505 is
        # the borrow checker, which plain `cargo check` already rejects. It
        # pins the compile-failure path only -- the lints clippy exists to find
        # are the sibling test below.
        shell_env.stub("cargo", body='echo "error: borrow of moved value"', exit_code=0)
        (git_repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        target = git_repo / "x.rs"
        target.write_text("fn main() {}\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[clippy]" in res.stderr

    def test_clippy_fails_on_warning_despite_exit_zero(self, LINT, shell_env, git_repo):
        # The case that actually matters: clippy's own lints are warn-by-default
        # and it exits 0 on them, so `warning:` -- not `error` -- is the shape a
        # real finding arrives in. Matching only "error" means every genuine
        # clippy lint is waved through while the hook reports success.
        shell_env.stub(
            "cargo",
            body='echo "warning: this expression creates a reference which is'
            ' immediately dereferenced by the compiler"',
            exit_code=0,
        )
        (git_repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        target = git_repo / "x.rs"
        target.write_text("fn main() {}\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            "a warn-level clippy lint must block; it is clippy's default severity"
        )
        assert "[clippy]" in res.stderr

    def test_clippy_passes_on_clean_output(self, LINT, shell_env, git_repo):
        # Guards the other direction: the warning matcher must not fire on
        # cargo's ordinary chatter, or every .rs edit would block.
        shell_env.stub("cargo", body='echo "Finished dev profile"', exit_code=0)
        (git_repo / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        target = git_repo / "x.rs"
        target.write_text("fn main() {}\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0

    def test_eslint_skipped_without_config(self, LINT, shell_env, git_repo):
        shell_env.stub("eslint", exit_code=1)
        target = git_repo / "x.js"
        target.write_text("var x = 1\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, "no ESLint config -> skip, not fail"
        assert not any(c.startswith("eslint") for c in shell_env.calls)

    def test_eslint_runs_when_config_present(self, LINT, shell_env, git_repo):
        shell_env.stub("eslint", body='echo "1:1 error Unexpected var"', exit_code=1)
        (git_repo / "eslint.config.js").write_text(
            "export default []\n", encoding="utf-8"
        )
        target = git_repo / "x.js"
        target.write_text("var x = 1\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[ESLint]" in res.stderr

    def _tsc_project(self, shell_env, git_repo, filename: str):
        """A tsc that reports one error against `filename`, exiting non-zero."""
        (git_repo / "tsconfig.json").write_text("{}\n", encoding="utf-8")
        target = git_repo / filename
        target.write_text("export const x: number = 'no'\n", encoding="utf-8")
        shell_env.stub(
            "tsc",
            body=f"echo \"src/{filename}(1,14): error TS2322: Type 'string' is not"
            " assignable to type 'number'.\"",
            exit_code=1,
        )
        return target

    def test_tsc_error_blocks(self, LINT, shell_env, git_repo):
        # Control for the bracketed-filename case below: an ordinary name must
        # reach the gate, so a failure there is the filename, not the harness.
        target = self._tsc_project(shell_env, git_repo, "page.tsx")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2
        assert "[TypeScript]" in res.stderr

    def test_tsc_error_blocks_for_a_bracketed_filename(self, LINT, shell_env, git_repo):
        """tsc output was filtered with `grep "$BASENAME"` -- the filename went in
        as an ERE, not a literal.

        Next.js dynamic routes make this routine: `[id].tsx` parses as a
        character class ("one char, i or d"), which never matches tsc's own
        error line for that file. The extraction came back empty, so nothing
        was appended to LINT_ERRORS and the hook returned 0 -- the gate went
        green on code tsc had just rejected. A quality gate that misses is
        worse than a noisy one, so pin the literal match.
        """
        target = self._tsc_project(shell_env, git_repo, "[id].tsx")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 2, (
            "tsc failed, so the hook must block regardless of how the filename "
            "reads as a regex"
        )
        assert "[TypeScript]" in res.stderr
        assert "TS2322" in res.stderr, "the real tsc diagnostic must reach the caller"

    def test_local_eslint_preferred_over_path(self, LINT, shell_env, git_repo):
        # A project-local eslint must win over one merely on PATH, so the file
        # is linted with the project's own version/plugins.
        shell_env.stub("eslint", body='echo "from PATH"', exit_code=1)
        (git_repo / "eslint.config.js").write_text(
            "export default []\n", encoding="utf-8"
        )
        local_bin = git_repo / "node_modules" / ".bin"
        local_bin.mkdir(parents=True)
        local_eslint = local_bin / "eslint"
        local_eslint.write_text(
            f'#!/bin/bash\necho "local-eslint $*" >> "{shell_env.calls_file}"\nexit 0\n',
            encoding="utf-8",
        )
        local_eslint.chmod(0o755)
        target = git_repo / "x.js"
        target.write_text("var x = 1\n", encoding="utf-8")
        res = shell_env.run(LINT, stdin=payload(target))
        assert res.returncode == 0, "local eslint exits 0, so the hook must pass"
        assert any(c.startswith("local-eslint ") for c in shell_env.calls)
        assert not any(c.startswith("eslint ") for c in shell_env.calls)


# (extension, formatter stub, expected argv prefix). One entry per branch of
# auto-format.sh's dispatch table. Only py and sh had coverage before; the rest
# is ~190 lines duplicated between the two copies, so nothing caught an edit.
FORMATTERS = [
    ("js", "prettier", "prettier --write "),
    ("md", "prettier", "prettier --write "),
    ("rs", "rustfmt", "rustfmt "),
    ("go", "gofmt", "gofmt -w "),
    ("java", "google-java-format", "google-java-format "),
    ("cpp", "clang-format", "clang-format "),
    ("rb", "rubocop", "rubocop "),
    ("php", "php-cs-fixer", "php-cs-fixer "),
]


@pytest.mark.parametrize("FORMAT", [CLAUDE_FORMAT], ids=["claude"])
class TestAutoFormatMatrix:
    """Characterization of auto-format.sh's per-language dispatch table.

    Same gap as the lint matrix had: ten branches, coverage on two. These pin
    which formatter each extension reaches for and that a missing or failing
    one degrades to a pass, so the matrix can be moved into a shared file and
    proven unchanged.
    """

    @pytest.mark.parametrize(
        "ext,tool,argv_prefix", FORMATTERS, ids=[f"{f[0]}-{f[1]}" for f in FORMATTERS]
    )
    def test_extension_dispatches_to_formatter(
        self, FORMAT, shell_env, tmp_path, ext, tool, argv_prefix
    ):
        shell_env.stub(tool)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert any(c.startswith(argv_prefix) for c in shell_env.calls), (
            f".{ext} must reach {tool}, got {shell_env.calls}"
        )

    @pytest.mark.parametrize(
        "ext,tool,argv_prefix", FORMATTERS, ids=[f"{f[0]}-{f[1]}" for f in FORMATTERS]
    )
    def test_failing_formatter_does_not_block(
        self, FORMAT, shell_env, tmp_path, ext, tool, argv_prefix
    ):
        # auto-format is fail-open by design: a formatter blowing up must not
        # take the hook -- or the user's edit -- down with it.
        shell_env.stub(tool, body='echo "boom" >&2', exit_code=1)
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = shell_env.run(FORMAT, stdin=payload(target))
        assert res.returncode == 0
        assert f"{tool} failed" in res.stderr or "boom" in res.stderr

    def test_rustfmt_preferred_over_cargo_fmt(self, FORMAT, shell_env, tmp_path):
        shell_env.stub("rustfmt")
        shell_env.stub("cargo")
        target = tmp_path / "x.rs"
        target.write_text("fn main() {}\n", encoding="utf-8")
        shell_env.run(FORMAT, stdin=payload(target))
        assert any(c.startswith("rustfmt ") for c in shell_env.calls)
        assert not any(c.startswith("cargo fmt") for c in shell_env.calls), (
            "cargo fmt is the fallback and must not run when rustfmt exists"
        )

    def test_goimports_runs_after_gofmt(self, FORMAT, shell_env, tmp_path):
        # Both run for Go: gofmt formats, goimports fixes the import block.
        shell_env.stub("gofmt")
        shell_env.stub("goimports")
        target = tmp_path / "x.go"
        target.write_text("package main\n", encoding="utf-8")
        shell_env.run(FORMAT, stdin=payload(target))
        assert any(c.startswith("gofmt -w ") for c in shell_env.calls)
        assert any(c.startswith("goimports -w ") for c in shell_env.calls)

    @pytest.mark.parametrize(
        "ext,tools",
        [
            ("js", ["prettier"]),
            ("rs", ["rustfmt", "cargo"]),
            ("go", ["gofmt", "goimports"]),
            ("rb", ["rubocop"]),
            ("php", ["php-cs-fixer"]),
        ],
    )
    def test_missing_formatter_is_skipped(
        self, FORMAT, shell_env, tmp_path, ext, tools
    ):
        # Hide real tools rather than just not stubbing them: gofmt and cargo
        # exist on this machine, so "no stub" would test the developer's PATH.
        env = _env_hiding(shell_env, *tools)
        if env is None:
            pytest.skip(f"cannot hide {tools} without also hiding jq on this host")
        target = tmp_path / f"x.{ext}"
        target.write_text("content\n", encoding="utf-8")
        res = _run_with_env(FORMAT, payload(target), env)
        assert res.returncode == 0


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
