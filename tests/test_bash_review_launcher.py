"""Tests for bash-review-launcher.sh (fail-closed startup wrapper,
.claude JSON-ask variant and .codex exit-2 variant).

A bare `python3 .../bash-review.py` hook command fails OPEN when the review
cannot happen at all (see the header of bash-review-launcher.sh). Each launcher
converts every "review never happened" condition (python3 missing, script
missing, crash with an unexpected exit code) into its runtime's fail-closed
vocabulary -- an explicit `ask` decision for Claude, exit 2 + stderr for Codex
(which has no `ask`, and parses stdout as structured output, so the fail path
must not print there) -- and passes the hook's normal vocabulary (exit 0 with a
decision, exit 2 + stderr) through untouched.

The vocabularies differ on one point: for Claude an exit 0 with an empty stdout
is NOT a normal outcome (Claude Code runs the command on "exit 0, no output", so
passing it through would be a silent fail-open), while for Codex it is the normal
allow.

A launcher resolves bash-review.py next to itself, so these tests copy it
into an isolated directory with a controllable fake sibling instead of
invoking the real review (which would want API keys and network).
"""

import json
import os
import shutil
import subprocess
import sys

import pytest
from conftest import REPO_ROOT
from test_sandbox_gate import TEST_OWNERS, TRUST_CASES, _exe, trust_case, trust_cleanup

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _sandbox_gate as gate  # noqa: E402

CLAUDE_LAUNCHER = REPO_ROOT / ".claude/hooks/bash-review-launcher.sh"
CODEX_LAUNCHER = REPO_ROOT / ".codex/hooks/bash-review-launcher.sh"
LAUNCHERS = [CLAUDE_LAUNCHER, CODEX_LAUNCHER]
# The hooks run on the trusted system python3. Without one, every
# launcher fails closed, so the pass-through tests need one.
TRUSTED = gate.trusted_python()
needs_trusted = pytest.mark.skipif(
    TRUSTED is None, reason="no trusted system python3 on this machine"
)
# settings.json and hooks.json start the launchers with /bin/bash (bash 3.2 on macOS).
BASH = "/bin/bash"
NOT_TRUSTED = "信頼できるシステム python3 が無い"


def run_launcher(shell_env, launcher, *, stdin="", cwd=None, env=None, flags=("-p",)):
    """Start a launcher the way settings.json / hooks.json do: /bin/bash -p."""
    return subprocess.run(  # noqa: S603
        [BASH, *flags, str(launcher)],
        input=stdin,
        capture_output=True,
        text=True,
        env={**shell_env.env, **(env or {})},
        cwd=cwd,
        timeout=60,
    )


def run_sourced(launcher, script, *args, stdin="", env=None, cwd=None):
    """Source a launcher (functions only) and run `script` with "$@" = args."""
    return subprocess.run(  # noqa: S603
        # $0 is not the launcher: run as a script, the launcher would start its main
        [BASH, "-c", f'source "$1"; shift; {script}', "test", str(launcher), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


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


@needs_trusted
class TestNormalVocabularyPassesThrough:
    def test_decision_json_and_stdin_pass_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, ECHO_STDIN_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("git status"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "allow"
        # stdin reached the hook intact (the fake echoes it into the reason).
        assert "git status" in output["permissionDecisionReason"]

    def test_exit_two_blocking_passes_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, BLOCK_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert "blocked: reason" in res.stderr
        # exit 2 already blocks; injecting an ask JSON here would be noise.
        assert res.stdout == ""


@needs_trusted
class TestReviewNeverHappenedFailsClosed:
    def test_python3_missing_from_path_does_not_matter(self, shell_env, tmp_path):
        # The hook runs on the trusted system python3, never on PATH's.
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, ECHO_STDIN_HOOK)
        shell_env.hide("python3")
        res = run_launcher(shell_env, launcher, stdin=payload("git status"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "allow"
        assert "git status" in output["permissionDecisionReason"]

    def test_missing_hook_file_asks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, hook_body=None)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["permissionDecision"] == "ask"
        assert "bash-review.py" in output["permissionDecisionReason"]

    def test_silent_exit_zero_asks(self, shell_env, tmp_path):
        # "exit 0, no output" is a successful hook with no decision: Claude Code
        # would run the command. The review did not happen, so ask.
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, SILENT_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 0
        output = decision_of(res)
        assert output["hookEventName"] == "PreToolUse"
        assert output["permissionDecision"] == "ask"
        assert "NOT reviewed" in output["permissionDecisionReason"]
        # The reason is a fixed sentence: nothing of the command or of the
        # hook's stdin is echoed into it.
        assert "rm -rf" not in output["permissionDecisionReason"]

    def test_whitespace_only_exit_zero_asks(self, shell_env, tmp_path):
        # A bare newline / spaces is just as decision-less as nothing.
        hook = "import sys\nsys.stdout.write('  \\n\\t\\n')\nraise SystemExit(0)\n"
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, hook)
        res = run_launcher(shell_env, launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert decision_of(res)["permissionDecision"] == "ask"

    def test_silent_exit_zero_with_stderr_still_asks_and_keeps_diagnostics(
        self, shell_env, tmp_path
    ):
        hook = "import sys\nsys.stderr.write('shim: no decision\\n')\nraise SystemExit(0)\n"
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, hook)
        res = run_launcher(shell_env, launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert decision_of(res)["permissionDecision"] == "ask"
        assert "shim: no decision" in res.stderr

    def test_crash_discards_partial_stdout_and_asks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CLAUDE_LAUNCHER, CRASH_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
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


@needs_trusted
class TestCodexVariant:
    """Codex has no `ask` (returning it fails open) and parses hook stdout as
    structured output, so the fail path must be exit 2 + stderr with nothing
    on stdout, and blocks must carry the do-not-work-around directive that
    `.codex/hooks/bash-review.py` attaches to its own blocks."""

    def test_allow_exit_zero_stays_silent(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, SILENT_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert res.stdout == ""

    def test_stdin_reaches_the_hook(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, STDIN_PROBE_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("git status"))
        assert res.returncode == 0

    def test_exit_zero_stdout_passes_through(self, shell_env, tmp_path):
        # bash-review.py never prints on allow, but the launcher's
        # contract is verbatim pass-through of a completed hook's stdout
        # (e.g. an authoritative deny JSON) -- pin it on this variant too.
        hook = 'print(\'{"decision": "deny"}\')\n'
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, hook)
        res = run_launcher(shell_env, launcher, stdin=payload("ls"))
        assert res.returncode == 0
        assert res.stdout == '{"decision": "deny"}\n'

    def test_exit_two_block_passes_through(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, BLOCK_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert "blocked: reason" in res.stderr
        assert res.stdout == ""

    def test_python3_missing_from_path_does_not_matter(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, STDIN_PROBE_HOOK)
        shell_env.hide("python3")
        res = run_launcher(shell_env, launcher, stdin=payload("git status"))
        assert res.returncode == 0, res.stderr
        assert res.stdout == ""

    def test_missing_hook_file_blocks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, hook_body=None)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        assert res.stdout == ""
        assert "bash-review.py" in res.stderr
        assert "do NOT retry" in res.stderr

    def test_crash_discards_partial_stdout_and_blocks(self, shell_env, tmp_path):
        launcher = install_launcher(tmp_path, CODEX_LAUNCHER, CRASH_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("rm -rf /"))
        assert res.returncode == 2
        # Codex reads stdout as structured output: even a fragment of broken
        # JSON there makes the hook itself count as failed = fail open.
        assert res.stdout == ""
        assert "exit code 1" in res.stderr
        assert "Traceback: boom" in res.stderr
        assert "do NOT retry" in res.stderr


# ---------------------------------------------------------------------------
# The launchers start the hook on the trusted system python3 only
# ---------------------------------------------------------------------------
# Fake hook: reports how it was started, inside a decision both variants pass on.
PROBE_HOOK = (
    "import json, os, sys\n"
    "sys.stdin.read()\n"
    "probe = {'executable': sys.executable, 'isolated': sys.flags.isolated,"
    " 'no_site': sys.flags.no_site, 'env': sorted(os.environ)}\n"
    "print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',"
    " 'permissionDecision': 'allow', 'permissionDecisionReason': json.dumps(probe)}}))\n"
)


def probe_of(res) -> dict:
    return json.loads(decision_of(res)["permissionDecisionReason"])


def _fails_closed(res, launcher) -> None:
    if launcher == CLAUDE_LAUNCHER:
        assert res.returncode == 0, res.stderr
        output = decision_of(res)
        assert output["permissionDecision"] == "ask"
        assert NOT_TRUSTED in output["permissionDecisionReason"]
    else:
        assert res.returncode == 2
        assert res.stdout == ""
        assert NOT_TRUSTED in res.stderr and "do NOT retry" in res.stderr


@needs_trusted
@pytest.mark.parametrize("source", LAUNCHERS, ids=["claude", "codex"])
class TestTrustedInterpreter:
    def test_the_hook_runs_isolated_on_the_trusted_python(
        self, shell_env, tmp_path, source
    ):
        launcher = install_launcher(tmp_path, source, PROBE_HOOK)
        res = run_launcher(shell_env, launcher, stdin=payload("ls"))
        assert res.returncode == 0, res.stderr
        probe = probe_of(res)
        assert probe["executable"] == TRUSTED
        assert probe["isolated"] == 1 and probe["no_site"] == 1

    def test_the_launch_variables_never_reach_the_hook(
        self, shell_env, tmp_path, source
    ):
        launcher = install_launcher(tmp_path, source, PROBE_HOOK)
        env = {key: "/evil" for key in gate.LAUNCH_ENV_DROP}
        res = run_launcher(shell_env, launcher, stdin=payload("ls"), env=env)
        assert res.returncode == 0, res.stderr
        probe = probe_of(res)
        assert not set(gate.LAUNCH_ENV_DROP) & set(probe["env"])
        # PYTHONEXECUTABLE / __PYVENV_LAUNCHER__ would have renamed sys.executable
        assert probe["executable"] == TRUSTED

    def test_path_pyenv_and_python_version_are_never_used(
        self, shell_env, tmp_path, source
    ):
        launcher = install_launcher(tmp_path, source, PROBE_HOOK)
        # PATH's python3 (a pyenv shim, say) and every tool the old launcher
        # looked up on PATH: none may run
        for name in ("python3", "python", "pyenv", "stat", "readlink", "dirname"):
            shell_env.stub(name, exit_code=0)
        shims = tmp_path / ".pyenv" / "shims"
        shims.mkdir(parents=True)
        shutil.copy(shell_env.stub_bin / "python3", shims / "python3")
        work = tmp_path / "work"
        work.mkdir()
        (work / ".python-version").write_text("evil\n")
        env = {
            "PATH": f"{shims}:{shell_env.env['PATH']}",
            "PYENV_VERSION": "evil",
        }
        res = run_launcher(shell_env, launcher, stdin=payload("ls"), cwd=work, env=env)
        assert res.returncode == 0, res.stderr
        assert probe_of(res)["executable"] == TRUSTED
        assert shell_env.calls == []

    @pytest.mark.parametrize("candidate", ["missing", "user-owned", "old"])
    def test_no_trusted_candidate_fails_closed(
        self, shell_env, tmp_path, source, candidate
    ):
        launcher = install_launcher(tmp_path, source, PROBE_HOOK)
        marker = tmp_path / "ran"
        if candidate == "missing":
            cand = str(tmp_path / "no" / "python3")
        elif candidate == "user-owned":
            # a real interpreter, but anyone with our uid could replace it
            link = tmp_path / "mine" / "python3"
            link.parent.mkdir()
            link.symlink_to(TRUSTED)
            cand = str(link)
        else:
            cand = str(
                _exe(tmp_path / "old" / "python3", f"#!/bin/sh\ntouch {marker}\n")
            )
        res = run_sourced(
            launcher,
            '_trusted_python_candidates() { _TRUSTED_CANDIDATES=("$1"); }; _launcher_main',
            cand,
            stdin=payload("ls"),
            env=shell_env.env,
        )
        _fails_closed(res, source)
        # neither the hook nor (for an untrusted path) the candidate ran
        assert "executable" not in res.stdout
        assert not marker.exists()

    def test_no_candidate_on_this_platform_fails_closed(
        self, shell_env, tmp_path, source
    ):
        launcher = install_launcher(tmp_path, source, PROBE_HOOK)
        res = run_sourced(
            launcher,
            "OSTYPE=plan9; _launcher_main",
            stdin=payload("ls"),
            env=shell_env.env,
        )
        _fails_closed(res, source)


class TestShellAndPythonAgree:
    """The launchers cannot run an interpreter before trusting it, so the rule
    lives twice: _sandbox_gate.trusted_path / trusted_python and the shell
    _trusted_path / _trusted_python. The same fixtures must get the same answer."""

    @pytest.mark.parametrize(("name", "trusted"), TRUST_CASES)
    def test_trusted_path(self, tmp_path, name, trusted):
        path, owners, top = trust_case(tmp_path, name)
        try:
            res = run_sourced(
                CLAUDE_LAUNCHER,
                '_trusted_path "$1" "$2" "$3"',
                path,
                " ".join(map(str, sorted(owners))),
                top,
            )
            python = gate.trusted_path(path, owners=owners, top=top) == ""
            assert python is trusted
            assert (res.returncode == 0) is python, res.stderr
        finally:
            trust_cleanup(tmp_path)

    def test_trusted_python_picks_the_same_candidate(self, tmp_path):
        top = tmp_path / "top"
        marker = tmp_path / "untrusted-ran"
        untrusted = _exe(top / "w" / "python3", f"#!/bin/sh\ntouch {marker}\n")
        (top / "w").chmod(0o777)
        old = _exe(top / "old" / "python3", "#!/bin/sh\nexit 1\n")
        good = _exe(top / "good" / "python3")
        second = _exe(top / "second" / "python3")
        owners = " ".join(map(str, sorted(TEST_OWNERS)))
        try:
            for cands in (
                [
                    str(top / "none" / "python3"),
                    str(untrusted),
                    str(old),
                    str(good),
                    str(second),
                ],
                [str(untrusted), str(old)],
                [],
            ):
                res = run_sourced(
                    CLAUDE_LAUNCHER,
                    '_trusted_python "$@"',
                    owners,
                    str(top),
                    *cands,
                )
                python = gate.trusted_python(cands, owners=TEST_OWNERS, top=str(top))
                shell = res.stdout.strip() if res.returncode == 0 else None
                assert shell == python
            assert not marker.exists(), "an untrusted candidate was started"
        finally:
            (top / "w").chmod(0o755)

    @pytest.mark.parametrize(
        ("ostype", "platform"),
        [("darwin24", "darwin"), ("linux-gnu", "linux"), ("msys", "win32")],
    )
    def test_candidates(self, ostype, platform):
        res = run_sourced(
            CLAUDE_LAUNCHER,
            f'OSTYPE={ostype}; _trusted_python_candidates; printf "%s\\n" "${{_TRUSTED_CANDIDATES[@]}}"',
        )
        got = tuple(line for line in res.stdout.splitlines() if line)
        assert got == gate.trusted_python_candidates(platform)

    def test_this_machine(self):
        res = run_sourced(
            CLAUDE_LAUNCHER,
            '_trusted_python_candidates; _trusted_python 0 / "${_TRUSTED_CANDIDATES[@]}"',
        )
        shell = res.stdout.strip() if res.returncode == 0 else None
        assert shell == gate.trusted_python()


class TestLauncherText:
    @pytest.mark.parametrize("source", LAUNCHERS, ids=["claude", "codex"])
    def test_the_fixed_pieces_match_the_gate(self, source):
        text = source.read_text(encoding="utf-8")
        assert "\nunset " + " ".join(gate.LAUNCH_ENV_DROP) + "\n" in text
        assert f"'{gate.TRUSTED_PYTHON_PROBE}'" in text
        assert '"$py" -I -S "$hook"' in text
        # nothing is looked up on PATH: no `command -v`, no bare python3 / dirname
        assert "command -v" not in text
        assert "$(dirname" not in text
        code = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
        assert not [ln for ln in code if ln.strip().startswith(("python3 ", "env "))]

    def test_both_launchers_carry_the_same_trust_block(self):
        def block(path):
            text = path.read_text(encoding="utf-8")
            return text[
                text.index("# >>> trusted-python") : text.index("# <<< trusted-python")
            ]

        assert block(CLAUDE_LAUNCHER) == block(CODEX_LAUNCHER)

    @needs_trusted
    def test_the_wrapper_starts_the_cli_without_the_launch_variables(self, tmp_path):
        # The agent's shell starts csb, so the launchers' clean environment never
        # reaches it: the wrapper drops the variables itself.
        home = tmp_path / "home"
        hooks = home / ".claude" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "claude_sandbox.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'executable': sys.executable, 'no_site': sys.flags.no_site,"
            " 'isolated': sys.flags.isolated, 'env': sorted(os.environ)}))\n"
        )
        env = {**os.environ, **{k: "/evil" for k in gate.LAUNCH_ENV_DROP}}

        def run(text):
            wrapper = home / "csb"
            wrapper.write_text(text)
            wrapper.chmod(0o755)
            res = subprocess.run(  # noqa: S603
                [str(wrapper)], capture_output=True, text=True, env=env, timeout=60
            )
            assert res.returncode == 0, res.stderr
            return json.loads(res.stdout)

        probe = run(gate.csb_wrapper_text(TRUSTED, str(home)))
        assert probe["executable"] == TRUSTED
        assert probe["isolated"] == 1 and probe["no_site"] == 1
        assert not set(gate.LAUNCH_ENV_DROP) & set(probe["env"])
        # the twin: a wrapper without the `unset` line lets them through. (A macOS framework
        # python removes __PYVENV_LAUNCHER__ from its own environ after reading it,
        # so that one cannot be seen from inside either way.)
        cli = hooks / "claude_sandbox.py"
        old = run(f"#!/bin/sh\nexec '{TRUSTED}' -I '{cli}' \"$@\"\n")
        assert set(gate.LAUNCH_ENV_DROP) - {"__PYVENV_LAUNCHER__"} <= set(old["env"])

    def test_the_wrapper_drops_the_same_variables(self):
        text = gate.csb_wrapper_text("/usr/bin/python3", "/home/t")
        assert "\nunset " + " ".join(gate.LAUNCH_ENV_DROP) + "\n" in text
        assert " -I -S " in text


@needs_trusted
@pytest.mark.parametrize(
    "hook", [".claude/hooks/bash-review.py", ".codex/hooks/bash-review.py"]
)
def test_the_hook_imports_on_the_trusted_python(hook):
    """The hooks now run on the system python3 (3.9 on macOS) with -I -S: their
    top-level imports, and the modules they load, must work there."""
    path = str(REPO_ROOT / hook)
    code = (
        "import ast, sys\n"
        "path = sys.argv[1]\n"
        "tree = ast.parse(open(path, encoding='utf-8').read())\n"
        "keep = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))"
        " or (isinstance(n, ast.Expr) and 'sys.path' in ast.get_source_segment("
        "open(path, encoding='utf-8').read(), n))]\n"
        "exec(compile(ast.Module(body=keep, type_ignores=[]), path, 'exec'),"
        " {'__name__': 'smoke', '__file__': path})\n"
        "import claude_sandbox\n"
        "print('ok')\n"
    )
    res = subprocess.run(  # noqa: S603
        [TRUSTED, "-I", "-S", "-c", code, path],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k not in gate.LAUNCH_ENV_DROP},
        timeout=60,
        check=False,
    )
    assert res.returncode == 0 and res.stdout.strip() == "ok", res.stderr


# ---------------------------------------------------------------------------
# /bin/bash -p: the hook commands read no $BASH_ENV and import no function
# ---------------------------------------------------------------------------
@needs_trusted
@pytest.mark.parametrize("source", LAUNCHERS, ids=["claude", "codex"])
class TestPrivilegedBash:
    MARK = "MARK-from-BASH_ENV"

    def _env(self, tmp_path):
        rc = tmp_path / "bash_env.sh"
        rc.write_text(f"echo {self.MARK} >&2\n")
        return {
            "BASH_ENV": str(rc),
            "ENV": str(rc),
            "BASH_FUNC_printf%%": '() { echo HIJACKED >&2; builtin printf "$@"; }',
        }

    def test_with_p_neither_runs(self, shell_env, tmp_path, source):
        launcher = install_launcher(tmp_path, source, ECHO_STDIN_HOOK)
        res = run_launcher(
            shell_env, launcher, stdin=payload("ls"), env=self._env(tmp_path)
        )
        assert res.returncode == 0, res.stderr
        assert self.MARK not in res.stderr and "HIJACKED" not in res.stderr

    def test_without_p_both_would_run(self, shell_env, tmp_path, source):
        # the twin: what -p prevents
        launcher = install_launcher(tmp_path, source, ECHO_STDIN_HOOK)
        res = run_launcher(
            shell_env, launcher, stdin=payload("ls"), env=self._env(tmp_path), flags=()
        )
        assert self.MARK in res.stderr
        if source == CLAUDE_LAUNCHER:  # the Codex variant prints nothing on allow
            assert "HIJACKED" in res.stderr
