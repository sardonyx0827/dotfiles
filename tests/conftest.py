"""Shared pytest fixtures and helpers for the dotfiles test suite.

Python hooks under .claude/hooks and .codex/hooks are top-level scripts
(no main() function): they read hook JSON from stdin, decide, and exit.
The `run_hook` fixture executes them with compile()+exec() against a
private globals dict so that:

- stdin is replaced with synthetic hook JSON
- platform.system() returns "TestOS" so no desktop notification fires
- network (urllib.request.urlopen) and subprocess.run raise unless a
  test provides an explicit fake
- the hardcoded /tmp/claude_hooks log dir and ~ are remapped under
  tmp_path by wrapping the filesystem boundary (open/makedirs/...)
- SystemExit is caught; the globals dict stays available so pure
  functions (_parse_verdict, _split_commands, ...) can be unit-tested

Shell scripts are executed as real subprocesses with HOME pointing at a
temp dir and a stub bin directory prepended to PATH (fake tmux,
terminal-notifier, ruff, ...) so tests stay hermetic.
"""

import io
import json
import os
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# scripts/ holds importable tools (unlike the hooks, which are top-level
# scripts run via the run_hook fixture). Put it on the path so tests can
# import them directly rather than re-implementing their parsing.
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DEFAULT_HOOK_ENV = {
    "GEMINI_API_KEY": "test-api-key",
    "GEMINI_MODEL": "primary-model",
    "GEMINI_FLASH_MODEL": "fallback-model",
}


def hook_payload(command: str, tool_name: str = "Bash") -> dict:
    """Build a minimal PreToolUse hook input for a Bash command."""
    return {"tool_name": tool_name, "tool_input": {"command": command}}


@dataclass
class HookResult:
    exit_code: int | None
    stdout: str
    stderr: str
    globals: dict
    home: Path
    fake_tmp: Path

    @property
    def hook_output(self) -> dict:
        return json.loads(self.stdout.strip().splitlines()[-1])

    @property
    def decision(self) -> str:
        return self.hook_output["hookSpecificOutput"]["permissionDecision"]

    @property
    def reason(self) -> str:
        return self.hook_output["hookSpecificOutput"]["permissionDecisionReason"]


class _FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_gemini(*replies, calls: list | None = None):
    """Build a urlopen() fake returning Gemini-style responses.

    Each call consumes the next reply; the last reply repeats. A reply
    that is an Exception instance is raised instead of returned.
    """
    queue = list(replies)

    def _urlopen(req, timeout=None):
        if calls is not None:
            calls.append(req)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        body = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": reply}]}}]}
        ).encode("utf-8")
        return _FakeHTTPResponse(body)

    return _urlopen


def fake_run(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
    calls: list | None = None,
    exc: Exception | None = None,
):
    """Build a subprocess.run() fake for the codex / claude CLI calls."""

    def _run(cmd, **kwargs):
        if calls is not None:
            calls.append((cmd, kwargs))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return _run


@pytest.fixture
def run_hook(tmp_path, capsys):
    """Execute a Python hook script hermetically. See module docstring."""

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fake_tmp = tmp_path / "fake-tmp"

    real_open = open
    real_makedirs = os.makedirs
    real_listdir = os.listdir
    real_remove = os.remove

    def remap(path):
        try:
            s = os.fspath(path)
        except TypeError:
            return path
        # Matches the literal log dirs hardcoded in the hooks so writes are
        # redirected INTO tmp_path (never actually touching /tmp).
        # NOTE: only open/os.makedirs/os.listdir/os.remove/os.path.expanduser
        # are wrapped. If a hook is ever refactored to use pathlib.Path I/O or
        # shutil, extend this sandbox accordingly or it will write for real.
        if isinstance(s, str) and (
            s.startswith("/tmp/claude_hooks")  # nosec B108
            or s.startswith("/tmp/codex_hooks")  # nosec B108
        ):
            return str(fake_tmp) + s.removeprefix("/tmp")  # nosec B108
        return path

    def fake_expanduser(path: str) -> str:
        if path == "~" or path.startswith("~/"):
            return str(home) + path[1:]
        return path

    def _run(hook: str, payload: dict, *, urlopen=None, run=None, env=None):
        hook_path = REPO_ROOT / hook
        code = compile(hook_path.read_text(encoding="utf-8"), str(hook_path), "exec")

        def deny_urlopen(*args, **kwargs):
            raise AssertionError("unexpected network access: urllib.request.urlopen")

        def deny_run(cmd, **kwargs):
            raise AssertionError(f"unexpected subprocess.run: {cmd}")

        mp = pytest.MonkeyPatch()
        capsys.readouterr()  # drop anything captured before this run
        exit_code = None
        hook_globals = {"__name__": "__main__", "__file__": str(hook_path)}
        try:
            stdin = types.SimpleNamespace(
                buffer=io.BytesIO(json.dumps(payload).encode("utf-8"))
            )
            mp.setattr(sys, "stdin", stdin)
            mp.setattr("platform.system", lambda: "TestOS")
            mp.setattr("urllib.request.urlopen", urlopen or deny_urlopen)
            mp.setattr(subprocess, "run", run or deny_run)
            mp.setattr(os.path, "expanduser", fake_expanduser)
            mp.setattr(os, "makedirs", lambda p, **kw: real_makedirs(remap(p), **kw))
            mp.setattr(os, "listdir", lambda p=".": real_listdir(remap(p)))
            mp.setattr(os, "remove", lambda p: real_remove(remap(p)))
            mp.setattr(
                "builtins.open",
                lambda file, *a, **kw: real_open(remap(file), *a, **kw),
            )
            for key, value in {**DEFAULT_HOOK_ENV, **(env or {})}.items():
                if value is None:
                    mp.delenv(key, raising=False)
                else:
                    mp.setenv(key, value)
            try:
                # Hooks are top-level scripts; exec of local repo code only.
                exec(code, hook_globals)  # noqa: S102  # nosec B102
            except SystemExit as e:
                exit_code = e.code if e.code is not None else 0
        finally:
            mp.undo()

        captured = capsys.readouterr()
        return HookResult(
            exit_code=exit_code,
            stdout=captured.out,
            stderr=captured.err,
            globals=hook_globals,
            home=home,
            fake_tmp=fake_tmp,
        )

    return _run


@dataclass
class ShellEnv:
    home: Path
    stub_bin: Path
    calls_file: Path
    env: dict = field(default_factory=dict)

    def stub(self, name: str, body: str = "", exit_code: int = 0) -> Path:
        """Create a fake executable that logs its invocation."""
        script = (
            "#!/bin/bash\n"
            f'echo "{name} $*" >> "{self.calls_file}"\n'
            f"{body}\n"
            f"exit {exit_code}\n"
        )
        path = self.stub_bin / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        return path

    def hide(self, name: str) -> None:
        """Make a host-installed executable invisible to the script under test.

        The stub dir is only PREPENDED to the real PATH, so a test premised on
        a tool being *absent* silently depends on the host lacking it — the
        moment a CI runner image or dev machine ships the tool for real,
        `command -v` finds it and the premise breaks (this bit us when runner
        images gained phpstan). Stubbing cannot express absence, so instead
        every PATH entry containing `name` is replaced by a symlink-farm clone
        of that directory minus the entry.
        """
        clones_root = self.stub_bin.parent / "hidden-path"
        rebuilt: list[str] = []
        for idx, entry in enumerate(self.env["PATH"].split(os.pathsep)):
            src = Path(entry)
            try:
                offending = bool(entry) and (src / name).exists()
            except OSError:
                offending = False
            if not offending:
                rebuilt.append(entry)
                continue
            clone = clones_root / f"{name}-{idx}"
            clone.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.name == name:
                    continue
                link = clone / item.name
                if not os.path.lexists(link):
                    link.symlink_to(item)
            rebuilt.append(str(clone))
        self.env["PATH"] = os.pathsep.join(rebuilt)

    @property
    def calls(self) -> list[str]:
        if not self.calls_file.exists():
            return []
        return self.calls_file.read_text(encoding="utf-8").splitlines()

    def run(self, script: Path, *args: str, stdin: str = "", cwd: Path | None = None):
        return subprocess.run(
            ["bash", str(script), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self.env,
            cwd=cwd,
            timeout=60,
        )


@pytest.fixture
def shell_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    calls_file = tmp_path / "stub-calls.log"

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{stub_bin}:{env['PATH']}"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull

    se = ShellEnv(home=home, stub_bin=stub_bin, calls_file=calls_file, env=env)
    # Backstop: no test may ever fire a real desktop notification.
    se.stub("terminal-notifier")
    se.stub("osascript")
    # Backstop: no test may ever reach a real system-mutating tool. The stub
    # dir only PREPENDS to the host PATH, so any code path that escapes its
    # per-test stubs runs the real binary: a mid-development version of
    # install.sh's dry-run gate once reached the host `brew`, which
    # "upgraded" the font-ubuntu-mono cask by relocating the user's real
    # font files into the doomed pytest tmp HOME. Individual tests override
    # these freely with their own stub(); absence-testing via
    # _without_commands-style PATH surgery is unaffected (none of these
    # names are exercised as "absent" today).
    for tool in ("brew", "apt-get", "sudo", "chsh", "npm"):
        se.stub(tool)
    return se


def run_git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def git_repo(tmp_path):
    """A throwaway git repository with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-q", "-m", "initial commit")
    return repo
