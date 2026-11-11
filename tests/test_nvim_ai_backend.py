"""Unit tests for `.config/nvim/lua/setup/functions/ai/backend.lua`.

The second slice of the Neovim AI tests (see test_nvim_ai_prompt.py for the
mechanism and for why only `functions/ai/` is tested at all). Two things in
this module are worth pinning:

- `scan_payload` is the pre-send credential gate. Every AI request funnels
  through it, and a false negative sends a live credential to an external
  tool. It is also the one place where the Lua side and `scripts/secret_scan.py`
  have to agree on a wire contract (exit 0/1/2).
- `build_cli_cmd` decides what lands in argv, and `run_cli` refuses a payload
  that would blow past ARG_MAX. copilot has no stdin path, so its payload goes
  into the command line -- the one place in this codebase that knowingly breaks
  the "payload on stdin, never argv" rule.

Both are `local`s; backend.lua exposes them through an `M._internal` test seam
(see the comment there for why that rather than debug.getupvalue).

Hermetic by construction: every nvim here runs with a PATH containing only
what the case under test needs, so no AI CLI can be reached even if a code
path tries. `sh` is a stub that exits immediately -- `M.run` really does call
`jobstart` once a payload clears the size check, and with the host PATH that
would exec the real `copilot`.
"""

import json
import os
import shutil
import subprocess

import gemini_api
import pytest
from conftest import REPO_ROOT

# The scanner is the shared one the bash-review hooks use; reuse a payload that
# suite already asserts is detected, so this proves the two sides agree rather
# than proving a hand-rolled string happens to match.
from test_bash_review import FAKE_AWS_KEY

HARNESS = REPO_ROOT / "tests/lua/nvim_call.lua"
BACKEND_MODULE = "setup.functions.ai.backend"
UI_MODULE = "setup.functions.ai.ui"
NVIM = shutil.which("nvim")

pytestmark = pytest.mark.skipif(NVIM is None, reason="nvim not installed")

# Exceeds MAX_CMD_BYTES (256 KB) even before shellescape inflates it. Hardcoded
# rather than read from the module: moving that bound should break this test.
OVERSIZED_INPUT = "x" * (256 * 1024 + 1)


class LuaResult:
    def __init__(self, payload):
        self.ret = payload["ret"][: payload["n"]]
        self.calls = payload["calls"]

    @property
    def only(self):
        assert len(self.ret) == 1, f"expected one return value, got {self.ret}"
        return self.ret[0]


CALLBACK = {"__callback": True}


def _lua_probe(tmp_path, body, binroot=None):
    """Run `body` in a bare `nvim -l` with the repo's lua tree on package.path.

    For invariants that only nvim itself can decide (does this API accept these
    arguments?) rather than ones a returned value can show.

    `binroot` overrides the PATH the probe runs under. The default one cannot
    execute anything (see make_bin), which is right for every probe that only
    asks nvim a question; a probe that has to let a job actually RUN passes its
    own (see make_running_bin) and stays sealed by holding nothing else.
    """
    binroot = binroot or make_bin(tmp_path, "probebin")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    probe = tmp_path / "probe.lua"
    probe.write_text(
        f'package.path = "{REPO_ROOT}/.config/nvim/lua/?.lua;"\n'
        f'  .. "{REPO_ROOT}/.config/nvim/lua/?/init.lua;" .. package.path\n' + body,
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": str(binroot), "HOME": str(home)},
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def make_bin(tmp_path, name, tools=("sh",)):
    """A PATH directory holding only `tools`, as wrappers around the real ones.

    Wrappers rather than symlinks so python3 keeps resolving its own prefix.
    `sh` is special-cased to a no-op: it must exist for vim.fn.jobstart to
    accept the command at all, but must never run it.
    """
    binroot = tmp_path / name
    binroot.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        script = binroot / tool
        if tool == "sh":
            script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        else:
            real = shutil.which(tool)
            assert real, f"{tool} is required by this test but is not installed"
            script.write_text(f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8")
        script.chmod(0o755)
    return binroot


def make_running_bin(tmp_path, marker, lead="hang"):
    """A PATH directory whose commands can actually RUN, unlike make_bin's.

    make_bin's `sh` is a no-op on purpose -- those tests must never execute the
    command they build. The cancellation defect only exists once a real
    subprocess is in flight and then gets stopped, so here `sh` and `cat` are
    wrappers around the real binaries and the two tools of the chain are stubs.

    Sealed exactly the same way regardless: this directory is the WHOLE PATH, so
    no real AI CLI is reachable however the chain behaves, and there is
    deliberately no python3 -- the gemini transport (scripts/gemini_api.py)
    could not run even if a future edit put gemini in the chain.

    `lead` is the leading tool's stub. "hang" loops until it is stopped -- a
    shell loop rather than a bare `sleep`, so SIGTERM is answered promptly and
    no surviving child keeps the job's pipes open, which is what would otherwise
    hold on_exit back past any timeout worth writing. "fail" exits non-zero at
    once. The trailing tool records that it ran by creating `marker`, which is
    the only way to tell "the chain advanced" apart from "the chain reported a
    failure".
    """
    binroot = tmp_path / "chainbin"
    binroot.mkdir(parents=True, exist_ok=True)
    for tool in ("sh", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} is required by this test but is not installed"
        (binroot / tool).write_text(
            f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8"
        )
    lead_body = {"hang": "while true; do sleep 0.2; done", "fail": "exit 1"}[lead]
    (binroot / "claude").write_text(f"#!/bin/sh\n{lead_body}\n", encoding="utf-8")
    (binroot / "copilot").write_text(
        f'#!/bin/sh\necho ran > "{marker}"\nexit 1\n', encoding="utf-8"
    )
    for entry in binroot.iterdir():
        entry.chmod(0o755)
    return binroot


def make_xdg(tmp_path, config_target):
    """An XDG_CONFIG_HOME whose `nvim` entry is a symlink to `config_target`.

    This is what makes secret_scanner_path() testable without stubbing any
    vim.fn: it resolves stdpath("config"), steps up two directories and looks
    for scripts/secret_scan.py, exactly as it does against the real
    ~/.config/nvim symlink into this repo.
    """
    xdg = tmp_path / "xdg"
    xdg.mkdir(parents=True, exist_ok=True)
    link = xdg / "nvim"
    if not link.exists():
        link.symlink_to(config_target)
    return xdg


def fake_repo(tmp_path, scanner_body=None):
    """A repo-shaped tree: `<root>/.config/nvim` plus an optional stub scanner.

    Omitting `scanner_body` produces a tree with no scripts/secret_scan.py,
    which is the "scanner missing" case.
    """
    root = tmp_path / "fakerepo"
    (root / ".config/nvim").mkdir(parents=True, exist_ok=True)
    if scanner_body is not None:
        scripts = root / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "secret_scan.py").write_text(scanner_body, encoding="utf-8")
    return root


def backend_call(
    fn, *args, binroot, tmp_path, xdg=None, module=BACKEND_MODULE, extra_env=None
):
    """Call `ai.<module>.<fn>` (dotted paths allowed) in a sealed headless nvim.

    `extra_env` adds variables the module under test reads through vim.env
    (GEMINI_MODEL). The base environment stays deliberately bare -- the point of
    this harness is that nothing leaks in from the developer's shell.
    """
    request = {
        "module": module,
        "fn": fn,
        "args": list(args),
        "nargs": len(args),
    }
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": str(binroot),
        "HOME": str(home),
        # Keep nvim's state out of the developer's real ~/.local/share/nvim.
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_CACHE_HOME": str(home / "cache"),
    }
    if xdg is not None:
        env["XDG_CONFIG_HOME"] = str(xdg)
    env.update(extra_env or {})
    proc = subprocess.run(
        [NVIM, "-l", str(HARNESS), str(REPO_ROOT)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, (
        f"nvim exited {proc.returncode} calling {fn}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"], f"{BACKEND_MODULE}.{fn} raised: {payload.get('err')}"
    return LuaResult(payload)


class TestScanPayloadContract:
    """The exit-code contract scripts/secret_scan.py documents in its docstring.

    Pins that the Lua reader implements the contract the Python writer
    promises; each case runs a stub scanner that exits with a documented code.
    """

    def scan(self, tmp_path, text, scanner_body=None, tools=("sh", "python3")):
        root = fake_repo(tmp_path, scanner_body)
        return backend_call(
            "_internal.scan_payload",
            text,
            binroot=make_bin(tmp_path, "bin", tools),
            tmp_path=tmp_path,
            xdg=make_xdg(tmp_path, root / ".config/nvim"),
        )

    def test_exit_0_is_clean(self, tmp_path):
        res = self.scan(tmp_path, "hello", "import sys; sys.stdin.read()\n")
        assert res.ret == ["clean"]

    def test_exit_1_is_a_detection_carrying_the_label(self, tmp_path):
        body = "import sys\nsys.stdin.read()\nprint('aws access key')\nraise SystemExit(1)\n"
        res = self.scan(tmp_path, "hello", body)
        assert res.ret == ["secret", "aws access key"]

    def test_exit_2_is_unavailable_not_clean(self, tmp_path):
        """A scanner that could not import must never read as "no secret"."""
        body = "import sys; sys.stdin.read(); raise SystemExit(2)\n"
        res = self.scan(tmp_path, "hello", body)
        assert res.ret == ["unavailable"]

    def test_any_other_exit_code_is_unavailable(self, tmp_path):
        body = "import sys; sys.stdin.read(); raise SystemExit(9)\n"
        res = self.scan(tmp_path, "hello", body)
        assert res.ret == ["unavailable"]

    def test_missing_scanner_is_unavailable(self, tmp_path):
        res = self.scan(tmp_path, "hello", scanner_body=None)
        assert res.ret == ["unavailable"]

    def test_missing_python3_is_unavailable_and_does_not_throw(self, tmp_path):
        """A GUI-launched nvim without the shell's PATH must degrade, not throw.

        A list-form vim.fn.system on a missing binary raises E475 rather than
        setting v:shell_error, and backend.lua defends that twice: the
        executable() guard up front and the pcall around the call. This pins
        the outcome, not which of the two produced it -- removing the guard
        leaves the pcall to catch the same error and yield the same answer, so
        no test can tell them apart from out here.
        """
        body = "import sys; sys.stdin.read()\n"
        res = self.scan(tmp_path, "hello", body, tools=("sh",))
        assert res.ret == ["unavailable"]

    def test_nil_payload_is_sent_as_an_empty_string(self, tmp_path):
        body = "import sys\nassert sys.stdin.read() == ''\n"
        res = self.scan(tmp_path, None, body)
        assert res.ret == ["clean"]

    def test_payload_goes_on_stdin_and_never_on_argv(self, tmp_path):
        """The rule the whole scanner design rests on.

        A secret in argv is readable by any process on the machine via
        `ps aux`, which would leak exactly what this gate exists to protect.
        The stub asserts on its own argv and reports a mismatch as exit 2, so a
        regression shows up as "unavailable" rather than passing quietly.
        """
        body = (
            "import sys\n"
            "payload = sys.stdin.read()\n"
            "if payload != 'TOP-SECRET-VALUE':\n"
            "    raise SystemExit(2)\n"
            "if any('TOP-SECRET-VALUE' in a for a in sys.argv):\n"
            "    raise SystemExit(2)\n"
        )
        res = self.scan(tmp_path, "TOP-SECRET-VALUE", body)
        assert res.ret == ["clean"]


class TestScanPayloadAgainstTheRealScanner:
    """End-to-end against scripts/secret_scan.py, not a stub.

    The contract tests above would still pass if the real scanner disagreed
    with them; these two close that gap by resolving the same path production
    resolves and running the real thing.
    """

    def scan(self, tmp_path, text):
        return backend_call(
            "_internal.scan_payload",
            text,
            binroot=make_bin(tmp_path, "bin", ("sh", "python3")),
            tmp_path=tmp_path,
            xdg=make_xdg(tmp_path, REPO_ROOT / ".config/nvim"),
        )

    def test_a_real_credential_is_detected(self, tmp_path):
        res = self.scan(tmp_path, f"aws_access_key_id = {FAKE_AWS_KEY}")
        status, label = res.ret
        assert status == "secret"
        assert label
        assert FAKE_AWS_KEY not in label, "the label must never echo the value"

    def test_ordinary_code_is_clean(self, tmp_path):
        res = self.scan(tmp_path, "local function add(a, b)\n  return a + b\nend\n")
        assert res.ret == ["clean"]


class TestBuildCliCmd:
    """What each tool is actually handed.

    Pure apart from vim.fn.shellescape and, for gemini, repo_script -- which
    reads stdpath("config") to find the shared python helper. That is why the
    gemini cases resolve their expected path through repo_script rather than
    rebuilding it: the harness runs nvim with HOME under tmp_path, so a
    hardcoded expectation would be asserting against the developer's machine.
    """

    def payload_file(self, tmp_path):
        """Stand-in for run_cli's vim.fn.tempname(); only its text matters here."""
        return str(tmp_path / "payload")

    def build(self, tmp_path, tool, model, instruction, inp=None, skip_git=False):
        return backend_call(
            "_internal.build_cli_cmd",
            tool,
            model,
            instruction,
            self.payload_file(tmp_path),
            inp,
            skip_git,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        ).only

    def test_codex_pipes_the_tempfile_in(self, tmp_path):
        cmd = self.build(tmp_path, "codex", None, "INSTR")
        assert cmd == (
            f"cat '{self.payload_file(tmp_path)}' "
            "| EDITOR_AI_ONESHOT=1 codex exec --sandbox read-only 'INSTR'"
        )

    def test_codex_skip_git_check_is_opt_in(self, tmp_path):
        off = self.build(tmp_path, "codex", None, "I", skip_git=False)
        on = self.build(tmp_path, "codex", None, "I", skip_git=True)
        assert "--skip-git-repo-check" not in off
        assert "--skip-git-repo-check 'I'" in on

    def test_codex_never_gets_a_writable_sandbox(self, tmp_path):
        """The backstop behind the Stop-hook marker, for both skip_git modes.

        Weaker than the claude branch's `--tools '' --strict-mcp-config`: the
        sandbox governs model-run shell commands, not MCP-provided tools, and an
        MCP tool is what got through when only the built-ins were taken away.
        Asserted anyway because losing it would leave codex with no second layer
        at all.
        """
        for skip_git in (False, True):
            cmd = self.build(tmp_path, "codex", None, "I", skip_git=skip_git)
            assert "codex exec --sandbox read-only" in cmd, cmd
            assert "workspace-write" not in cmd
            assert "danger-full-access" not in cmd

    def helper_path(self, tmp_path, name):
        """Where backend.lua will look for a shared python helper.

        Derived through the module's own repo_script rather than rebuilt here,
        so this pins the COMMAND shape without also re-deriving (and possibly
        disagreeing about) the path resolution it depends on.
        """
        return backend_call(
            "_internal.repo_script",
            name,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        ).only

    def test_gemini_goes_through_the_shared_api_helper_not_the_cli(self, tmp_path):
        """Gemini goes through the REST API helper, not the `gemini` CLI.

        Pinned as a whole string because the property that matters is
        positional: the payload still arrives on STDIN, which is what lets the
        API path keep run_cli's job handling, stderr capture and ARG_MAX guard
        unchanged.
        """
        helper = self.helper_path(tmp_path, "gemini_api.py")
        cmd = self.build(tmp_path, "gemini", None, "INSTR")
        assert cmd == (
            f"cat '{self.payload_file(tmp_path)}' | python3 '{helper}' --system 'INSTR'"
        )

    def test_an_unpinned_gemini_model_leaves_the_flag_off_entirely(self, tmp_path):
        """The helper must be free to read $GEMINI_MODEL at request time.

        Passing a model resolved when backend.lua was `require`d would freeze
        that variable at startup AND split the two editors -- the VimScript port
        pins no model at all, so the command for the one feature they share must
        come out the same. A `--model ''` would be worse than useless: an empty
        id is not a model, and the helper would have to special-case it.
        """
        assert "--model" not in self.build(tmp_path, "gemini", None, "INSTR")

    def test_a_pinned_gemini_model_still_reaches_the_helper(self, tmp_path):
        """The nvim-only flows (check / fix / hint) may still pin one."""
        cmd = self.build(tmp_path, "gemini", "gemini-pro-latest", "INSTR")
        assert "--model 'gemini-pro-latest' --system 'INSTR'" in cmd

    def test_the_api_key_never_appears_in_the_gemini_command(self, tmp_path):
        """The whole reason the request goes through a child process.

        The command string is handed to `sh -c`, so a key interpolated into it
        -- `curl -H "x-goog-api-key: $GEMINI_API_KEY"` being the obvious
        rewrite -- would be expanded into curl's argv and readable by every
        process on the machine via `ps aux` for the life of the request. The
        helper reads GEMINI_API_KEY out of its OWN environment instead, so the
        variable must not be named here at all.
        """
        cmd = self.build(tmp_path, "gemini", "flash", "INSTR")
        assert "GEMINI_API_KEY" not in cmd
        assert "x-goog-api-key" not in cmd

    def test_claude_is_the_default_branch(self, tmp_path):
        cmd = self.build(tmp_path, "claude", "sonnet", "INSTR")
        expected = (
            f"cat '{self.payload_file(tmp_path)}' | EDITOR_AI_ONESHOT=1 "
            "claude --model 'sonnet' --tools '' --strict-mcp-config -p 'INSTR'"
        )
        assert cmd == expected

    def test_claude_gets_no_tools_from_either_source(self, tmp_path):
        """Both halves, or the write path stays open.

        `--tools ''` drops only the BUILT-IN tools; an MCP tool such as
        mcp__serena__replace_content can still edit the working tree.
        --strict-mcp-config with no --mcp-config alongside it leaves the
        session zero MCP servers, which is the other half. An edit that keeps
        one and drops the other reads as a harmless simplification and silently
        reopens the hole, so pin both.
        """
        cmd = self.build(tmp_path, "claude", "haiku", "I")
        assert "--tools ''" in cmd, "built-in tools still available"
        assert "--strict-mcp-config" in cmd, "MCP tools still available"

    def test_only_the_hooked_tools_are_marked_as_editor_oneshot(self, tmp_path):
        """gemini and copilot run no Stop hook, so the marker would be noise."""
        for tool, model in (("claude", "sonnet"), ("codex", None)):
            assert "EDITOR_AI_ONESHOT=1" in self.build(tmp_path, tool, model, "I")
        for tool, model in (("gemini", "flash"), ("copilot", "gpt-5-mini")):
            assert "EDITOR_AI_ONESHOT" not in self.build(
                tmp_path, tool, model, "I", inp="X"
            )

    @pytest.mark.parametrize("tool,model", [("claude", "sonnet"), ("codex", None)])
    def test_the_marker_reaches_the_tool_and_not_the_pipe_head(
        self, tmp_path, tool, model
    ):
        """Run the built string through a real `sh -c` and ask the tool itself.

        Containing the right substring is not the property that matters; the
        property is that the AGENT's process has the variable, because that is
        what its Stop hook inherits. The two come apart under an edit that reads
        as pure tidying -- hoisting the assignment to the front of the pipeline
        (`EDITOR_AI_ONESHOT=1 cat X | claude ...`) marks `cat` and leaves the
        agent unmarked, and every substring assertion above still passes.
        """
        binroot = make_bin(tmp_path, "shbin")
        marker = tmp_path / "seen-env"
        stub = binroot / tool
        stub.write_text(
            f'#!/bin/sh\nprintf "%s" "${{EDITOR_AI_ONESHOT-unset}}" > "{marker}"\n',
            encoding="utf-8",
        )
        stub.chmod(0o755)
        # A real sh, not make_bin's no-op stand-in: the pipeline has to run.
        real_sh = shutil.which("sh")
        assert real_sh
        (binroot / "sh").write_text(
            f'#!/bin/sh\nexec "{real_sh}" "$@"\n', encoding="utf-8"
        )
        (binroot / "sh").chmod(0o755)
        (tmp_path / "payload").write_text("payload", encoding="utf-8")

        cmd = self.build(tmp_path, tool, model, "INSTR")
        subprocess.run(  # noqa: S603
            ["sh", "-c", cmd],
            env={"PATH": str(binroot)},
            capture_output=True,
            timeout=60,
            check=False,
        )
        assert marker.read_text(encoding="utf-8") == "1", (
            f"{tool} did not receive EDITOR_AI_ONESHOT; its Stop hook will audit"
        )

    def test_an_unknown_tool_falls_through_to_claude(self, tmp_path):
        """Documenting the `else` branch: it is not a rejection path.

        M.run refuses unknown tools before reaching here, so this only fires
        for a tool added to TOOLS without a branch of its own -- in which case
        it silently runs as claude.
        """
        cmd = self.build(tmp_path, "brand-new", "m", "I")
        assert cmd.startswith(
            f"cat '{self.payload_file(tmp_path)}' | EDITOR_AI_ONESHOT=1 claude "
        )

    def test_copilot_inlines_the_payload_instead_of_piping_it(self, tmp_path):
        cmd = self.build(tmp_path, "copilot", "gpt", "INSTR", inp="SEL")
        assert "cat " not in cmd, "copilot has no stdin path; nothing to pipe"
        assert self.payload_file(tmp_path) not in cmd
        assert "copilot --model 'gpt' -s -p " in cmd
        assert "## Input" in cmd
        assert "SEL" in cmd

    @pytest.mark.parametrize(
        "tool", ["codex", "gemini", "claude", "copilot"], ids=lambda t: t
    )
    def test_shell_metacharacters_in_the_instruction_are_quoted(self, tmp_path, tool):
        """An unescaped instruction would be a command injection into `sh -c`.

        The whole string is handed to jobstart({"sh", "-c", cmd}), so a quote
        that closes early turns the rest of the user's prompt into commands.
        """
        cmd = self.build(tmp_path, tool, "m", "it's; rm -rf /", inp="SEL")
        # The quote must come back as the POSIX '\'' sequence, which cannot
        # terminate the surrounding single-quoted string. Asserting the raw
        # text is absent is the half that would catch a regression: copilot
        # wraps the instruction in a larger prompt before escaping, so a
        # positive-only check on the exact quoted form differs per tool.
        assert "it'\\''s" in cmd
        assert "it's" not in cmd

    def test_copilot_escapes_the_inlined_payload_as_well(self, tmp_path):
        """The instruction is not the only attacker-influenced part.

        For copilot the selected text itself goes into argv, so a quote in the
        buffer would break out just as an unescaped instruction would.
        """
        cmd = self.build(tmp_path, "copilot", "gpt", "INSTR", inp="pay'; rm -rf /")
        assert "pay'\\''; rm -rf /" in cmd
        assert "pay';" not in cmd


class TestGeminiApiPath:
    """Gemini reaches Google over HTTPS through a helper, not the `gemini` CLI.

    Three properties are not visible in the command string alone: the helper is
    found next to the config symlink, the model comes from the same variable the
    rest of the repo uses, and the payload is scanned for credentials before it
    leaves the editor.
    """

    def probe(self, tmp_path, fn, *args, xdg=None, extra_env=None, tools=("sh",)):
        return backend_call(
            fn,
            *args,
            binroot=make_bin(tmp_path, "bin", tools),
            tmp_path=tmp_path,
            xdg=xdg,
            extra_env=extra_env,
        )

    def test_helpers_resolve_next_to_the_config_symlink(self, tmp_path):
        """~/.config/nvim is a symlink into the repo; step up two levels.

        The same resolution scan_payload and the Gemini helper share, so a
        broken lookup takes out both and pinning it once covers both.
        """
        root = fake_repo(tmp_path)
        got = self.probe(
            tmp_path,
            "_internal.repo_script",
            "gemini_api.py",
            xdg=make_xdg(tmp_path, root / ".config/nvim"),
        ).only
        assert got == str(root / "scripts" / "gemini_api.py")

    def test_the_registry_pins_no_gemini_model(self, tmp_path):
        """Resolution belongs to the helper, at request time.

        A model captured when backend.lua was `require`d would ignore a
        $GEMINI_MODEL set later in the session -- and the VimScript port, which
        pins nothing, would then be using a different model than Neovim for the
        same keystroke.
        """
        assert self.probe(tmp_path, "gemini_model").only == gemini_api.DEFAULT_MODEL
        probe = tmp_path / "registry.lua"
        probe.write_text(
            f'package.path = "{REPO_ROOT}/.config/nvim/lua/?.lua;"\n'
            f'  .. "{REPO_ROOT}/.config/nvim/lua/?/init.lua;" .. package.path\n'
            'local tools = require("setup.functions.ai.backend").TOOLS\n'
            'io.stdout:write(tostring(tools.gemini.default_model), "\\n")\n',
            encoding="utf-8",
        )
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        proc = subprocess.run(  # noqa: S603
            [NVIM, "-l", str(probe)],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": str(make_bin(tmp_path, "bin")), "HOME": str(home)},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "nil"

    def test_the_reported_default_matches_the_python_helper(self, tmp_path):
        """The one literal that is deliberately written down twice.

        backend.lua repeats it only so ai/init.lua can DISPLAY the model in a
        report header -- "(default)" is not something a reader can act on --
        while scripts/gemini_api.py owns the value the request actually uses.
        Nothing else would notice the two drifting apart, and the symptom would
        be a header confidently naming the wrong model.
        """
        assert self.probe(tmp_path, "gemini_model").only == gemini_api.DEFAULT_MODEL

    def test_gemini_model_env_var_is_read_on_every_call(self, tmp_path):
        got = self.probe(
            tmp_path, "gemini_model", extra_env={"GEMINI_MODEL": "  pro  "}
        ).only
        assert got == "pro", "surrounding whitespace must not reach the request path"

    def test_an_empty_gemini_model_falls_back_to_the_default(self, tmp_path):
        """`export GEMINI_MODEL=` is a common shell accident, not a model id."""
        got = self.probe(
            tmp_path, "gemini_model", extra_env={"GEMINI_MODEL": "   "}
        ).only
        assert got == gemini_api.DEFAULT_MODEL

    def test_the_registry_entry_is_not_the_local_transport(self, tmp_path):
        """The distinction the credential gate keys off.

        M.run exempts `kind == "ollama"` from the pre-send scan because Ollama
        is localhost. Gemini talks to Google, so it must not share that kind --
        an edit that folded the two "non-CLI" transports together would send
        buffers out unscanned while every command-shape test stayed green.
        """
        probe = tmp_path / "kinds.lua"
        probe.write_text(
            f'package.path = "{REPO_ROOT}/.config/nvim/lua/?.lua;"\n'
            f'  .. "{REPO_ROOT}/.config/nvim/lua/?/init.lua;" .. package.path\n'
            'local tools = require("setup.functions.ai.backend").TOOLS\n'
            'io.stdout:write(tools.gemini.kind, ",", tools.gemma.kind, "\\n")\n',
            encoding="utf-8",
        )
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        proc = subprocess.run(  # noqa: S603
            [NVIM, "-l", str(probe)],
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": str(make_bin(tmp_path, "bin")), "HOME": str(home)},
        )
        assert proc.returncode == 0, proc.stderr
        gemini_kind, gemma_kind = proc.stdout.strip().split(",")
        assert gemma_kind == "ollama"
        assert gemini_kind != "ollama"

    def test_a_credential_in_a_gemini_payload_is_refused_before_any_job(self, tmp_path):
        """The credential gate stays armed for gemini.

        Driven through the public M.run with the real scanner, so this fails if
        the exemption is ever widened from "the local transport" to "anything
        that is not a CLI" -- the shape of edit that reads as tidying and
        quietly ships buffers to Google unscanned.
        """
        res = backend_call(
            "run",
            {"tool": "gemini", "prompt": "I", "input": f"aws_key = {FAKE_AWS_KEY}"},
            CALLBACK,
            binroot=make_bin(tmp_path, "bin", ("sh", "python3")),
            tmp_path=tmp_path,
            xdg=make_xdg(tmp_path, REPO_ROOT / ".config/nvim"),
        )
        assert res.only is None, "a refused request must not return a job id"
        assert res.calls == [
            [False, [], "credential detected in payload; not sent to AI"]
        ]


class TestPayloadSizeRefusal:
    """The ARG_MAX guard, exercised through the public M.run.

    Reaching it through the real entry point also proves the ordering: the
    refusal happens before jobstart, so an oversized payload never reaches a
    process at all.
    """

    def run(self, tmp_path, spec):
        return backend_call(
            "run",
            spec,
            CALLBACK,
            True,  # _skip_scan: the credential gate is covered above
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        )

    def test_oversized_copilot_payload_is_refused_before_any_job_starts(self, tmp_path):
        res = self.run(
            tmp_path,
            {
                "tool": "copilot",
                "prompt": "INSTR",
                "input": OVERSIZED_INPUT,
                "model": "gpt",
            },
        )
        assert res.only is None, "a refused request must not return a job id"
        assert len(res.calls) == 1
        ok, lines, err = res.calls[0]
        assert ok is False
        assert lines == []
        assert "payload too large for copilot" in err
        assert "limit 256 KB" in err
        # The message must say what to do instead, not just that it failed.
        assert "claude / codex / gemini" in err

    def test_the_same_payload_is_fine_for_a_stdin_based_tool(self, tmp_path):
        """The limit is about argv, not about payload size as such.

        claude pipes from a tempfile, so the command stays small however big
        the input is; the request gets as far as jobstart. Its callbacks are
        deferred and never fire under `nvim -l`, so the observable difference
        is a job id and no synchronous refusal -- which is exactly the
        distinction being asserted.
        """
        res = self.run(
            tmp_path,
            {
                "tool": "claude",
                "prompt": "INSTR",
                "input": OVERSIZED_INPUT,
                "model": "sonnet",
            },
        )
        assert res.calls == [], "no synchronous refusal for a stdin-based tool"
        assert isinstance(res.only, int) and res.only > 0

    def test_unknown_tool_is_refused_by_name(self, tmp_path):
        res = self.run(tmp_path, {"tool": "nope", "prompt": "I", "input": "x"})
        assert res.only is None
        assert res.calls == [[False, [], "unknown tool: nope"]]


class TestRunWithFallbackReporting:
    """When every tool in a chain fails, the report must name every reason, not just the last.

    Driven with unknown tool names because M.run rejects those SYNCHRONOUSLY:
    `nvim -l` never runs the event loop, so a chain of real tools would park in
    jobstart and report nothing at all.

    See the comment on `failures` inside `M.run_with_fallback` in backend.lua
    for why every failure is accumulated rather than only the last.
    """

    def chain(self, tmp_path, *tools):
        specs = [{"tool": t, "prompt": "I", "input": "x"} for t in tools]
        return backend_call(
            "run_with_fallback",
            specs,
            CALLBACK,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        )

    def test_every_attempt_is_named_when_the_whole_chain_fails(self, tmp_path):
        res = self.chain(tmp_path, "no-such-a", "no-such-b")
        assert len(res.calls) == 1, "the caller must be told exactly once"
        ok, lines, err, tool = res.calls[0]
        assert (ok, lines) == (False, [])
        assert "no-such-a: unknown tool: no-such-a" in err
        assert "no-such-b: unknown tool: no-such-b" in err
        assert tool == "no-such-b", "the reported tool is still the last tried"

    def test_a_single_step_chain_is_not_prefixed_with_its_own_name(self, tmp_path):
        """ui.failure_lines already renders "[<tool> failed: <err>]".

        Prefixing here too would produce "[gemini failed: gemini: ...]", so the
        one-attempt case has to keep the bare reason.
        """
        res = self.chain(tmp_path, "no-such-a")
        _, _, err, _ = res.calls[0]
        assert err == "unknown tool: no-such-a"

    def test_a_later_success_still_reports_no_error(self, tmp_path):
        """Accumulating failures must not leak into a successful run.

        claude failing and gemini answering is the chain working as designed;
        the collected reason belongs nowhere near that callback.
        """
        res = self.chain(tmp_path, "no-such-a", "claude")
        assert res.calls == [], "claude reaches jobstart, whose callback is deferred"
        assert res.only is not None, "the handle must track the in-flight attempt"

    def test_a_synchronous_first_failure_does_not_lose_the_live_job(self, tmp_path):
        """`handle.job` must name the attempt in flight, not the one that failed.

        A spec that fails synchronously (unknown tool, oversized payload, a
        refused jobstart) runs its callback -- which advances to the next spec
        and stores THAT job -- before `handle.job = M.run(...)` assigns. The
        assignment must not clobber the live id with nil, or ui.lua's
        cancel_job has nothing to stop. See the `handle.attempt` comment in
        `M.run_with_fallback`.
        """
        res = self.chain(tmp_path, "no-such-a", "claude")
        assert isinstance(res.only, dict), res.only
        job = res.only.get("job")
        assert isinstance(job, int) and job > 0, f"the live job was lost: {res.only}"


# Drives one fallback chain through a REAL ui driver and reports what happened.
#
# __DRIVER__ picks which of ui.lua's two close paths cancels it ("report" ->
# open_report's `close`, i.e. what `q` and WinClosed call; "multi" ->
# run_multi's close_all, reached by closing the popup window). __CANCEL__ says
# whether to cancel at all -- with it false the same probe pins the behaviour
# that must NOT change: a genuine failure still falls back.
CHAIN_PROBE = r"""
local backend = require("setup.functions.ai.backend")
local ui = require("setup.functions.ai.ui")

-- Two "cli" tools, chosen only for what their stubs can do: claude leads and is
-- the one stopped mid-flight, copilot trails and its stub is what records that
-- the chain advanced.
local SPECS = {
  { tool = "claude", prompt = "I", input = "x" },
  { tool = "copilot", prompt = "I", input = "x" },
}

local reported = {}
local handle
-- The chain reports through the UI driver's own `done`, exactly as ai/init.lua's
-- callers wire it; this only tees off the tool name on the way past, so the
-- driver still sees the callback it would see in production.
local function track(done)
  return function(ok, lines, err, tool)
    reported[#reported + 1] = tostring(tool)
    done(ok, lines, err)
  end
end

local cancel
if "__DRIVER__" == "report" then
  local report = ui.open_report({
    start = function(done)
      handle = backend.run_with_fallback(SPECS, track(done))
      return handle
    end,
  })
  cancel = report.close
else
  ui.run_multi({
    mode = "popup",
    tools = { "claude" },
    -- nvim_open_win rejects footer_pos without a footer, and run_multi always
    -- passes footer_pos; every real caller supplies one.
    footer = " q:cancel ",
    start = function(_, done)
      handle = backend.run_with_fallback(SPECS, track(done))
      return handle
    end,
  })
  -- run_multi opens its popup with enter = true, so it is the current window,
  -- and closing a window is what fires the WinClosed autocmd close_all hangs
  -- off. Going through the window rather than calling close_all directly is the
  -- point: it is the path a user takes.
  local win = vim.api.nvim_get_current_win()
  cancel = function() vim.api.nvim_win_close(win, true) end
end

-- The first attempt has to be genuinely in flight before it can be cancelled.
local started = vim.wait(10000, function()
  return handle ~= nil and handle.job ~= nil and handle.job > 0
end, 20)

if __CANCEL__ then
  cancel()
end

-- Wait for a POSITIVE settle signal -- the chain reported to its caller --
-- rather than sampling the marker after a fixed sleep: "the fallback has not
-- started yet" and "the fallback never runs" are the same picture to a timer.
local settled = vim.wait(10000, function() return #reported > 0 end, 20)
-- ...then a grace window, during which a chain that was still going to advance
-- would have started its subprocess.
vim.wait(500)

io.stdout:write(vim.json.encode({
  started = started,
  settled = settled,
  reported = reported,
}), "\n")
"""


class TestCancellingStopsTheFallbackChain:
    """A cancelled request must not go on to start the next tool in the chain.

    A cancellation is vim.fn.jobstop (ui.lua's `close` and `close_all`, i.e.
    closing the window), and the stopped job's on_exit reports SIGTERM as
    "exit code 143" -- indistinguishable from a tool failing on its own. Only
    the `cancelled` flag tells them apart; without it the chain starts the next
    tool after the window is gone (for gemini, a live request to Google). See
    the `handle.cancelled` comment in `attempt` in backend.lua.

    Unlike every other test in this file these need the event loop. The
    behaviour lives in a job callback, and `nvim -l` runs those only while something waits
    (TestRunWithFallbackReporting is built the other way round -- on tools that
    fail SYNCHRONOUSLY -- for exactly that reason), so these drive real
    subprocesses under vim.wait. They also cancel the way production does,
    through the ui driver's own close path rather than by calling jobstop here:
    the wiring between the two files is half of what is being tested.
    """

    def probe(self, tmp_path, *, driver="report", cancel=True, lead="hang"):
        marker = tmp_path / "fallback-ran"
        out = _lua_probe(
            tmp_path,
            CHAIN_PROBE.replace("__DRIVER__", driver).replace(
                "__CANCEL__", "true" if cancel else "false"
            ),
            binroot=make_running_bin(tmp_path, marker, lead),
        )
        payload = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
        # Both are premises, not results: a probe whose first attempt never
        # started, or which never heard back at all, cannot say anything about
        # what the chain did next -- and would otherwise report the absence of
        # the marker as a pass.
        assert payload["started"], "the leading attempt never reached jobstart"
        assert payload["settled"], "the chain never reported back to its caller"
        payload["fallback_ran"] = marker.exists()
        return payload

    def test_closing_the_report_window_does_not_start_the_next_tool(self, tmp_path):
        res = self.probe(tmp_path, driver="report")
        assert res["reported"] == ["claude"], (
            "the cancelled chain advanced to the next tool and reported "
            f"{res['reported']}"
        )
        # The corroborating half, and the one that says why this matters: the
        # tool name shows the chain advanced, the marker shows a real subprocess
        # ran after the user had closed the window.
        assert not res["fallback_ran"], (
            "the next tool's subprocess ran after the request was cancelled"
        )

    def test_closing_the_multi_window_does_not_start_the_next_tool(self, tmp_path):
        """The other close path, which cancels every pending tool at once.

        run_multi's close_all is a separate loop over state.jobs; both it and
        open_report's `close` have to raise the cancel signal, so both are
        driven here rather than trusting that they share a helper today.
        """
        res = self.probe(tmp_path, driver="multi")
        assert res["reported"] == ["claude"], (
            "the cancelled chain advanced to the next tool and reported "
            f"{res['reported']}"
        )
        assert not res["fallback_ran"], (
            "the next tool's subprocess ran after the window was closed"
        )

    def test_a_genuine_failure_still_falls_back(self, tmp_path):
        """The invariant the cancel guard must not break -- falling back IS the feature.

        Same probe, nothing cancelled, leading tool failing on its own: the
        chain has to advance, run the next tool for real, and report under that
        tool's name. A `cancelled` flag that defaulted to true, or a cancel
        signal raised on an ordinary failure, would pass the two tests above
        and fail here.
        """
        res = self.probe(tmp_path, cancel=False, lead="fail")
        assert res["reported"] == ["copilot"], (
            f"the chain did not fall back; it reported {res['reported']}"
        )
        assert res["fallback_ran"], "the next tool never actually ran"


def test_the_test_seam_exposes_exactly_what_these_tests_use(tmp_path):
    """Keep M._internal from growing into a second public API.

    It exists so two private functions can be tested; anything else added to
    it is either dead weight or a sign something belongs on M proper.
    """
    binroot = make_bin(tmp_path, "bin")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    probe = tmp_path / "probe.lua"
    probe.write_text(
        f'package.path = "{REPO_ROOT}/.config/nvim/lua/?.lua;"\n'
        f'  .. "{REPO_ROOT}/.config/nvim/lua/?/init.lua;" .. package.path\n'
        "local names = {}\n"
        'for k in pairs(require("setup.functions.ai.backend")._internal) do\n'
        "  names[#names + 1] = k\n"
        "end\n"
        "table.sort(names)\n"
        'io.stdout:write(table.concat(names, ","), "\\n")\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": str(binroot), "HOME": str(home)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        "build_cli_cmd,cli_failure_reason,ollama_failure_reason,repo_script,"
        "scan_payload"
    )


def test_nvim_l_does_not_load_the_user_config(tmp_path):
    """The premise the whole harness rests on, asserted rather than assumed.

    ~/.config/nvim is a symlink into this repo, so if `nvim -l` ever started
    honouring user config these tests would silently begin bootstrapping
    lazy.nvim and downloading plugins instead of testing a module.

    This is the one case that keeps the real $HOME: both discovery routes have
    to come up empty, the XDG one and the ~/.config fallback. The state / data
    / cache trio is still redirected into tmp_path, so a future Neovim that
    does write on `-l` cannot touch the developer's own nvim state.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    probe = tmp_path / "probe.lua"
    probe.write_text(
        'io.stdout:write(tostring(package.loaded["lazy"] ~= nil), " ",\n'
        '  tostring(vim.o.runtimepath:find("lazy") ~= nil), "\\n")\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(make_bin(tmp_path, "bin")),
            "HOME": os.path.expanduser("~"),
            "XDG_CONFIG_HOME": str(make_xdg(tmp_path, REPO_ROOT / ".config/nvim")),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "false false"


class TestCliFailureReason:
    """What the hint report says when a CLI run does not produce an answer.

    `exit code 0` must never be stated as the cause of a failure (the tool
    succeeded but printed nothing), and the CLI's own stderr must reach the
    reason: a missing binary exits 127 with "command not found", which a bare
    number would hide. See `cli_failure_reason` in backend.lua.
    """

    def reason(self, tmp_path, exit_code, stderr):
        return backend_call(
            "_internal.cli_failure_reason",
            exit_code,
            stderr,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        ).only

    def test_exit_zero_is_never_reported_as_an_exit_code(self, tmp_path):
        assert "exit code 0" not in self.reason(tmp_path, 0, [])

    def test_exit_zero_reports_an_empty_response(self, tmp_path):
        assert "empty" in self.reason(tmp_path, 0, []).lower()

    def test_stderr_is_carried_into_the_reason(self, tmp_path):
        r = self.reason(tmp_path, 127, ["sh: gemini: command not found"])
        assert "127" in r
        assert "command not found" in r

    def test_bare_exit_code_when_the_tool_said_nothing(self, tmp_path):
        assert self.reason(tmp_path, 2, []) == "exit code 2"

    def test_blank_stderr_does_not_leave_a_dangling_separator(self, tmp_path):
        assert self.reason(tmp_path, 2, ["", ""]) == "exit code 2"

    def test_truncation_does_not_split_a_multibyte_character(self, tmp_path):
        # CLIs localise their errors. Cutting the quote at a byte offset lands
        # mid-character for anything outside ASCII and emits invalid UTF-8.
        r = self.reason(tmp_path, 1, ["エラー" * 400])
        assert "�" not in r, "truncated mid-character"
        assert r.endswith("...")


class TestOllamaFailureReason:
    """run_ollama's failure reason: the transport's exit code and stderr win over the parse error.

    parse_ollama returns a non-nil error for any unusable body, including the empty
    string an unreachable server produces, so a parse-error-first reason would report
    "could not connect to Ollama" as "invalid JSON response". The parse error is
    reported only when the transport succeeded. Goes through the same
    cli_failure_reason run_cli uses, so both backends describe a failure the same way.
    See `ollama_failure_reason` in backend.lua.
    """

    def reason(self, tmp_path, exit_code, stderr, parse_err):
        return backend_call(
            "_internal.ollama_failure_reason",
            exit_code,
            stderr,
            parse_err,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        ).only

    def test_transport_failure_reports_the_exit_code_not_the_parse_error(
        self, tmp_path
    ):
        r = self.reason(tmp_path, 7, [], "invalid JSON response")
        assert "7" in r, r
        assert "invalid JSON" not in r, (
            "a failed transport still reported the downstream parse error"
        )

    def test_transport_stderr_reaches_the_reason(self, tmp_path):
        r = self.reason(
            tmp_path, 7, ["curl: (7) Failed to connect"], "invalid JSON response"
        )
        assert "Failed to connect" in r, r

    def test_parse_error_survives_when_the_transport_succeeded(self, tmp_path):
        r = self.reason(tmp_path, 0, [], "invalid JSON response")
        assert "invalid JSON response" in r, r

    def test_success_with_no_parse_error_still_says_something(self, tmp_path):
        assert self.reason(tmp_path, 0, [], None).strip() != ""


class TestFailureMessageReachesTheBuffer:
    """A failure reason is rendered with nvim_buf_set_lines, which REJECTS an
    item containing a newline ('replacement string' item contains newlines).

    Quoting the tool's stderr makes multi-line reasons the normal case -- a
    traceback is exactly what MAX_STDERR_CHARS is sized for -- so the display
    has to split the message instead of handing it over as one line.
    """

    def lines(self, tmp_path, label, err):
        return backend_call(
            "_internal.failure_lines",
            label,
            err,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
            module=UI_MODULE,
        ).ret[0]

    def test_no_item_contains_a_newline(self, tmp_path):
        got = self.lines(tmp_path, "gemini", "exit code 1: Traceback\n  File x\nBoom")
        assert got, "expected at least one line"
        assert all("\n" not in item for item in got), got

    def test_a_single_line_reason_stays_one_line(self, tmp_path):
        got = self.lines(tmp_path, "gemini", "exit code 2")
        assert len(got) == 1
        assert "gemini" in got[0] and "exit code 2" in got[0]

    def test_a_nil_reason_is_still_reported(self, tmp_path):
        got = self.lines(tmp_path, "gemini", None)
        assert len(got) == 1
        assert "unknown error" in got[0]

    def test_every_stderr_line_survives(self, tmp_path):
        got = self.lines(tmp_path, "codex", "exit code 1: first\nsecond\nthird")
        joined = "\n".join(got)
        for part in ("first", "second", "third"):
            assert part in joined, f"{part} missing from {got}"

    def test_nvim_actually_accepts_the_result(self, tmp_path):
        # The property that matters is not "no newline in a string" -- that is
        # the test above -- but "nvim_buf_set_lines takes these lines", so push
        # them through it for real rather than restating the same assertion.
        out = _lua_probe(
            tmp_path,
            'local ui = require("setup.functions.ai.ui")\n'
            'local lines = ui._internal.failure_lines("gemini", "exit code 1: a\\nb\\nc")\n'
            "local buf = vim.api.nvim_create_buf(false, true)\n"
            "local ok, err = pcall("
            "vim.api.nvim_buf_set_lines, buf, 0, -1, false, lines)\n"
            'io.stdout:write(ok and "ok" or ("ERR: " .. tostring(err)), "\\n")\n',
        )
        assert out == "ok", out


def test_the_ui_test_seam_exposes_exactly_what_these_tests_use(tmp_path):
    """Same guard as the backend seam: keep `_internal` from growing into a
    second public API. Add a name here only together with the test that uses it.
    """
    out = _lua_probe(
        tmp_path,
        "local names = {}\n"
        'for k in pairs(require("setup.functions.ai.ui")._internal) do\n'
        "  names[#names + 1] = k\n"
        "end\n"
        "table.sort(names)\n"
        'io.stdout:write(table.concat(names, ","), "\\n")\n',
    )
    assert out == "failure_lines"


# A CLI that exits 0 having printed nothing but a newline. Real: a tool whose
# model answered with an empty message, or one that swallowed the prompt.
EMPTY_REPLY_PROBE = r"""
local backend = require("setup.functions.ai.backend")
local reported
backend.run({ tool = "codex", prompt = "I", input = "x" }, function(ok, lines, err)
  reported = { ok = ok, lines = lines, err = err }
end, true)
vim.wait(10000, function() return reported ~= nil end, 20)
io.stdout:write(vim.json.encode(reported or { timeout = true }), "\n")
"""


def make_empty_reply_bin(tmp_path, output):
    """A sealed PATH whose `codex` prints `output` (printf-escaped) and exits 0."""
    binroot = tmp_path / "emptybin"
    binroot.mkdir(parents=True, exist_ok=True)
    for tool in ("sh", "cat"):
        real = shutil.which(tool)
        assert real, f"{tool} is required by this test but is not installed"
        (binroot / tool).write_text(
            f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8"
        )
    (binroot / "codex").write_text(
        f"#!/bin/sh\ncat >/dev/null\nprintf '{output}'\nexit 0\n", encoding="utf-8"
    )
    for entry in binroot.iterdir():
        entry.chmod(0o755)
    return binroot


class TestWhitespaceOnlyReplies:
    """Exit 0 with nothing but whitespace on stdout is an empty response.

    `"\\n"` splits into {"", ""} and clean_cli_lines drops one trailing empty
    string, so a bare `#result > 0` would count the survivor as an answer: the
    report tab would turn green, `y` would replace the selection with a blank
    line, and cli_failure_reason's "empty response" branch would be unreachable.
    The other transports apply the same rule (parse_ollama / parse_edits treat
    whitespace-only as empty).
    """

    @pytest.mark.parametrize("output", ["\\n", "\\n\\n", "   \\n\\t\\n"])
    def test_a_whitespace_only_reply_is_reported_as_empty(self, tmp_path, output):
        out = _lua_probe(
            tmp_path, EMPTY_REPLY_PROBE, binroot=make_empty_reply_bin(tmp_path, output)
        )
        res = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
        assert "timeout" not in res, "the job never reported back"
        assert res["ok"] is False, f"a blank reply was accepted as an answer: {res}"
        assert res["lines"] == []
        assert "empty response" in res["err"]

    def test_a_reply_with_content_is_still_accepted(self, tmp_path):
        out = _lua_probe(
            tmp_path,
            EMPTY_REPLY_PROBE,
            binroot=make_empty_reply_bin(tmp_path, "hello\\n"),
        )
        res = json.loads([ln for ln in out.splitlines() if ln.startswith("{")][-1])
        assert res["ok"] is True, res
        assert res["lines"] == ["hello"]


# --------------------------------------------------------------------------
# The sensitive-PATH guard.
#
# The credential gate above looks at VALUES. A `.env` full of `DB_HOST=...`
# lines holds nothing credential-shaped, so it sailed through to an external
# model; the path list that already kept Copilot COMPLETION out of such files
# was applied nowhere else. These tests pin the shared list, the confirm dialog
# in the one choke point every send passes, and the commit-diff path
# derivation. See `confirm_send` in backend.lua.
# --------------------------------------------------------------------------

SENSITIVE_MODULE = "setup.functions.ai.sensitive"

# (path, sensitive?). Absolute, like the buffer names the callers pass and like
# the `bufname` copilot.lua's should_attach receives. The same table drives the
# Vim port's check, so the two lists cannot be given different expectations.
PATH_CASES = [
    pytest.param("/work/app/.env", True, id="dotenv"),
    pytest.param("/work/app/.env.production", True, id="dotenv-suffixed"),
    pytest.param("/work/app/.envrc", True, id="envrc"),
    pytest.param("/home/u/.ssh/config", True, id="ssh-dir"),
    pytest.param("/home/u/keys/id_ed25519", True, id="ssh-key-basename"),
    pytest.param("/work/certs/server.PEM", True, id="pem-case-insensitive"),
    pytest.param("/work/infra/prod.tfvars", True, id="tfvars"),
    pytest.param("/work/infra/terraform.tfstate.backup", True, id="tfstate"),
    pytest.param("/home/u/.kube/config", True, id="kube-dir"),
    pytest.param("/work/ops/kubeconfig", True, id="kubeconfig"),
    pytest.param("/home/u/.docker/config.json", True, id="docker-config-json"),
    pytest.param("/work/app/docker/compose.json", False, id="docker-other-json"),
    pytest.param("/work/gcp/my-service-account.json", True, id="service-account"),
    pytest.param("/work/gcp/Credentials.json", True, id="credentials-word"),
    pytest.param("/work/app/db_password.txt", True, id="password-word"),
    pytest.param("/work/app/main.lua", False, id="ordinary-source"),
    pytest.param("/work/app/README.md", False, id="ordinary-doc"),
    pytest.param("/work/app/environment.py", False, id="env-prefix-is-not-dotenv"),
    pytest.param("/work/app/envoy.yaml", False, id="envoy-is-not-dotenv"),
    pytest.param("", False, id="empty-path-no-name-buffer"),
    # Repo-relative names, as a diff or a relative buffer name gives them: the
    # directory entries need a leading `/`, which a root-level `.kube/config`
    # does not have. A relative name is judged as if it began with one.
    pytest.param(".kube/config", True, id="relative-kube-dir"),
    pytest.param(".ssh/config", True, id="relative-ssh-dir"),
    pytest.param(".aws/config", True, id="relative-aws-dir"),
    pytest.param(".gnupg/pubring.kbx", True, id="relative-gnupg-dir"),
    pytest.param(".azure/azureProfile.json", True, id="relative-azure-dir"),
    pytest.param("gcloud/x.json", True, id="relative-gcloud-dir"),
    pytest.param("docs/kube-notes.md", False, id="relative-kube-in-a-word"),
    pytest.param("docs/gcloud-notes.md", False, id="relative-gcloud-in-a-word"),
    pytest.param("docs/ssh-notes.md", False, id="relative-ssh-in-a-word"),
]


class TestSensitivePathList:
    """The one shared definition of "a path that must not go out unasked"."""

    @pytest.mark.parametrize(("path", "expected"), PATH_CASES)
    def test_is_sensitive_path(self, tmp_path, path, expected):
        got = backend_call(
            "is_sensitive_path",
            path,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
            module=SENSITIVE_MODULE,
        ).only
        assert got is expected

    def test_a_missing_path_is_not_sensitive_and_does_not_throw(self, tmp_path):
        got = backend_call(
            "is_sensitive_path",
            None,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
            module=SENSITIVE_MODULE,
        ).only
        assert got is False

    def test_sensitive_paths_keeps_only_the_sensitive_ones_once_each(self, tmp_path):
        got = backend_call(
            "sensitive_paths",
            ["/a/main.lua", "/a/.env", "/a/.env", "", "/a/kubeconfig"],
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
            module=SENSITIVE_MODULE,
        ).only
        assert got == ["/a/.env", "/a/kubeconfig"]

    def attach_decisions(self, tmp_path, spec_path):
        """Run copilot.lua's real `config` against a stand-in `copilot` module.

        The spec is loaded from `spec_path` so the same probe can be pointed at a
        pre-refactor copy: a characterization test is only worth anything if the
        old code gives the same answers.
        """
        probe = f"""
        local captured
        package.loaded["copilot"] = {{ setup = function(opts) captured = opts end }}
        package.loaded["copilot.command"] = {{ toggle = function() end }}
        local spec = dofile("{spec_path}")
        spec.config()
        local function decide(name, opts)
          local buf = vim.api.nvim_create_buf(opts.listed, false)
          if opts.buftype then vim.bo[buf].buftype = opts.buftype end
          return captured.should_attach(buf, name)
        end
        local out = {{
          sensitive = decide("/work/app/.env", {{ listed = true }}),
          kube = decide("/home/u/.kube/config", {{ listed = true }}),
          ordinary = decide("/work/app/main.lua", {{ listed = true }}),
          unnamed = decide(nil, {{ listed = true }}),
          unlisted = decide("/work/app/main.lua", {{ listed = false }}),
          special = decide("/work/app/main.lua", {{ listed = true, buftype = "nofile" }}),
        }}
        io.stdout:write(vim.json.encode(out))
        """
        return json.loads(_lua_probe(tmp_path, probe))

    EXPECTED_ATTACH = {
        "sensitive": False,
        "kube": False,
        "ordinary": True,
        "unnamed": True,
        "unlisted": False,
        "special": False,
    }

    def test_copilot_attach_decisions_are_unchanged(self, tmp_path):
        spec = REPO_ROOT / ".config/nvim/lua/setup/plugins/ai/copilot.lua"
        assert self.attach_decisions(tmp_path, spec) == self.EXPECTED_ATTACH

    def test_copilot_attach_uses_the_shared_list_and_keeps_none_of_its_own(self):
        """copilot.lua must not grow a second copy of the list.

        Its attach behaviour is unchanged (same patterns, same call), so the
        test is about where the list lives: two copies is how the gate and the
        completion guard would drift apart.
        """
        src = (REPO_ROOT / ".config/nvim/lua/setup/plugins/ai/copilot.lua").read_text(
            encoding="utf-8"
        )
        assert 'require("setup.functions.ai.sensitive")' in src
        assert "SENSITIVE_PATH" not in src
        assert "should_attach" in src

    def test_the_vim_list_has_the_same_content_as_the_lua_list(self, tmp_path):
        lua = json.loads(
            _lua_probe(
                tmp_path,
                f'io.stdout:write(vim.json.encode(require("{SENSITIVE_MODULE}").PATTERNS))',
            )
        )
        assert len(lua) > 20, "the Lua list is suspiciously short"
        assert [_lua_pattern_as_vim(p) for p in lua] == _vim_sensitive_list()

    def test_vim_agrees_with_lua_on_every_sample_path(self, tmp_path):
        from test_editor_config_regressions import _real_vim, _run_vim_script

        vim = _real_vim()
        if vim is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        source = (REPO_ROOT / ".vim/rc/70-ai.vim").read_text(encoding="utf-8")
        paths = [case.values[0] for case in PATH_CASES]
        script = (
            f"let s:paths = json_decode('{json.dumps(paths)}')\n"
            "call writefile([json_encode(map(copy(s:paths),"
            " {_, p -> s:AI_IsSensitivePath(p) ? v:true : v:false}))], $PROBE_OUT)\n"
            "qa!\n"
        )
        out = _run_vim_script(vim, script, extra_source=source).strip()
        assert out, "the Vim probe wrote nothing: is s:AI_IsSensitivePath defined?"
        assert json.loads(out) == [case.values[1] for case in PATH_CASES]


def _lua_pattern_as_vim(pat):
    """Translate one pattern of the shared list into the Vim regex form.

    Only the constructs the list uses are known: anything else raises, so a
    pattern that needs a smarter translation fails here instead of comparing
    unequal for a reason nobody can read.
    """
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "%":
            nxt = pat[i + 1]
            assert nxt in ".-", f"unhandled Lua escape %{nxt} in {pat!r}"
            out.append("\\." if nxt == "." else "-")
            i += 2
            continue
        out.append("\\?" if c == "?" else c)
        i += 1
    return "".join(out)


def _vim_sensitive_list():
    """The single-quoted entries of 70-ai.vim's sensitive-path list, in order."""
    import re

    src = (REPO_ROOT / ".vim/rc/70-ai.vim").read_text(encoding="utf-8")
    start = src.index("let s:ai_sensitive_paths = [")
    block = src[start : src.index("\\ ]", start)]
    return re.findall(r"'((?:[^']|'')*)'", block)


class TestDiffPaths:
    """File names of the diff that is actually sent, read from the diff itself.

    Deriving them from the text (rather than from a second `git` call) means the
    check cannot disagree with the payload about which files are in it.
    """

    def paths(self, tmp_path, diff):
        return backend_call(
            "diff_paths",
            diff,
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
            module=SENSITIVE_MODULE,
        ).only

    def test_a_modified_file(self, tmp_path):
        diff = (
            "diff --git a/.env b/.env\nindex 1..2 100644\n--- a/.env\n+++ b/.env\n"
            "@@ -1 +1,2 @@\n DB_HOST=db\n+DB_PORT=5432\n"
        )
        assert self.paths(tmp_path, diff) == [".env"]

    def test_several_files_in_order_once_each(self, tmp_path):
        diff = (
            "diff --git a/src/a.lua b/src/a.lua\n--- a/src/a.lua\n+++ b/src/a.lua\n"
            "@@ -1 +1 @@\n-x\n+y\n"
            "diff --git a/kubeconfig b/kubeconfig\n--- a/kubeconfig\n+++ b/kubeconfig\n"
            "@@ -1 +1 @@\n-x\n+y\n"
        )
        assert self.paths(tmp_path, diff) == ["src/a.lua", "kubeconfig"]

    def test_a_new_file(self, tmp_path):
        diff = (
            "diff --git a/deploy/prod.tfvars b/deploy/prod.tfvars\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/deploy/prod.tfvars\n@@ -0,0 +1 @@\n+region = 1\n"
        )
        assert self.paths(tmp_path, diff) == ["deploy/prod.tfvars"]

    def test_a_deleted_file_is_still_a_source_of_content(self, tmp_path):
        diff = (
            "diff --git a/old/.env b/old/.env\ndeleted file mode 100644\n"
            "--- a/old/.env\n+++ /dev/null\n@@ -1 +0,0 @@\n-A=1\n"
        )
        assert self.paths(tmp_path, diff) == ["old/.env"]

    def test_a_pure_rename_names_both_ends(self, tmp_path):
        diff = (
            "diff --git a/plain.txt b/prod.tfvars\nsimilarity index 100%\n"
            "rename from plain.txt\nrename to prod.tfvars\n"
        )
        got = self.paths(tmp_path, diff)
        assert "plain.txt" in got and "prod.tfvars" in got

    def test_a_binary_file_has_no_content_headers_but_is_still_named(self, tmp_path):
        diff = "diff --git a/keys/server.pem b/keys/server.pem\nBinary files differ\n"
        assert self.paths(tmp_path, diff) == ["keys/server.pem"]

    def test_a_mode_only_change_is_still_named(self, tmp_path):
        diff = "diff --git a/id_rsa b/id_rsa\nold mode 100644\nnew mode 100600\n"
        assert self.paths(tmp_path, diff) == ["id_rsa"]

    def test_a_name_with_a_space_keeps_the_space(self, tmp_path):
        diff = (
            "diff --git a/my notes/.env b/my notes/.env\n"
            "--- a/my notes/.env\t\n+++ b/my notes/.env\t\n@@ -1 +1 @@\n-a\n+b\n"
        )
        assert self.paths(tmp_path, diff) == ["my notes/.env"]

    def test_a_quoted_name_is_unquoted(self, tmp_path):
        diff = (
            'diff --git "a/caf\\303\\251/.env" "b/caf\\303\\251/.env"\n'
            '--- "a/caf\\303\\251/.env"\n+++ "b/caf\\303\\251/.env"\n'
            "@@ -1 +1 @@\n-a\n+b\n"
        )
        got = self.paths(tmp_path, diff)
        assert len(got) == 1 and got[0].endswith("/.env") and '"' not in got[0]

    def test_hunk_lines_that_look_like_headers_are_not_file_names(self, tmp_path):
        """`-- credentials` removed from a Lua file renders as `--- credentials`.

        Only the header block (between `diff --git` and the first `@@`) names
        files. Reading body lines as names would raise a dialog for a diff that
        touches nothing sensitive.
        """
        diff = (
            "diff --git a/x.lua b/x.lua\n--- a/x.lua\n+++ b/x.lua\n@@ -1,2 +1,2 @@\n"
            "--- credentials\n+++ password\n context\n"
        )
        assert self.paths(tmp_path, diff) == ["x.lua"]

    def test_a_repo_root_name_keeps_the_leading_directory(self, tmp_path):
        """`a/.kube/config` loses `a/` and nothing else: the dir stays in the name."""
        diff = (
            "diff --git a/.kube/config b/.kube/config\n--- a/.kube/config\n"
            "+++ b/.kube/config\n@@ -1 +1 @@\n-a\n+b\n"
        )
        assert self.paths(tmp_path, diff) == [".kube/config"]

    COLOURED = (
        "\x1b[1mdiff --git a/.env b/.env\x1b[m\n"
        "\x1b[1mindex 1..2 100644\x1b[m\n"
        "\x1b[1m--- a/.env\x1b[m\n\x1b[1m+++ b/.env\x1b[m\n"
        "\x1b[36m@@ -1 +1,2 @@\x1b[m\n DB_HOST=db\n\x1b[32m+\x1b[mDB_PORT=5432\n"
    )

    def test_ansi_escapes_around_the_headers_do_not_hide_the_names(self, tmp_path):
        """`color.ui=always` wraps every header in SGR codes; `^diff` must still match."""
        assert self.paths(tmp_path, self.COLOURED) == [".env"]

    def test_a_coloured_hunk_header_still_ends_the_header_block(self, tmp_path):
        diff = (
            "\x1b[1mdiff --git a/x.lua b/x.lua\x1b[m\n\x1b[1m--- a/x.lua\x1b[m\n"
            "\x1b[1m+++ b/x.lua\x1b[m\n\x1b[36m@@ -1 +1 @@\x1b[m\n"
            "\x1b[31m--- credentials\x1b[m\n\x1b[32m+++ password\x1b[m\n"
        )
        assert self.paths(tmp_path, diff) == ["x.lua"]

    @pytest.mark.parametrize("flag", ["--cc", "--combined"])
    def test_a_merge_conflict_header_names_its_one_path(self, tmp_path, flag):
        diff = (
            f"diff {flag} kubeconfig\nindex 1,2..3\n--- a/kubeconfig\n"
            "+++ b/kubeconfig\n@@@ -1,1 -1,1 +1,5 @@@\n++<<<<<<< HEAD\n"
        )
        assert self.paths(tmp_path, diff) == ["kubeconfig"]

    def test_a_merge_conflict_header_keeps_a_path_that_starts_with_a_slash_letter(
        self, tmp_path
    ):
        """No `a/` `b/` prefix exists in a combined header, so none is stripped."""
        diff = "diff --cc b/.ssh/id_rsa\nindex 1,2..3\n@@@ -1 -1 +1 @@@\n"
        assert self.paths(tmp_path, diff) == ["b/.ssh/id_rsa"]

    def test_a_quoted_merge_conflict_header_is_unquoted(self, tmp_path):
        diff = 'diff --cc "caf\\303\\251/.env"\nindex 1,2..3\n@@@ -1 -1 +1 @@@\n'
        got = self.paths(tmp_path, diff)
        assert len(got) == 1 and got[0].endswith("/.env") and '"' not in got[0]

    def test_no_diff_and_non_diff_text_name_nothing(self, tmp_path):
        assert self.paths(tmp_path, "") == []
        assert self.paths(tmp_path, "fatal: not a git repository\n") == []


GATE_PREAMBLE = """
package.path = "{repo}/.config/nvim/lua/?.lua;"
  .. "{repo}/.config/nvim/lua/?/init.lua;" .. package.path

-- confirm() stand-in. A real one returns the default button when the user just
-- presses Enter, which is what a missing answer models: the dialog defaults to No.
local answers = vim.json.decode('{answers}')
local confirms = {{}}
vim.fn.confirm = function(msg, choices, default, kind)
  confirms[#confirms + 1] = {{ msg = msg, choices = choices, default = default, kind = kind }}
  local a = answers[#confirms]
  if a == nil then return default end
  return a
end

-- Nothing may actually be spawned: a started job IS the send.
local jobs = {{}}
vim.fn.jobstart = function(cmd, _opts)
  jobs[#jobs + 1] = cmd
  return 100 + #jobs
end

local backend = require("setup.functions.ai.backend")
local function emit(v) io.stdout:write(vim.json.encode(v), "\\n") end
"""


def gate_probe(tmp_path, body, *, answers=(), tools=("python3",)):
    """Run `body` (Lua) against the real backend with confirm() and jobstart() stubbed.

    The scanner is the real one: `XDG_CONFIG_HOME` points at this repo's nvim
    config the way ~/.config/nvim does, so scan_payload finds
    scripts/secret_scan.py. Pass `tools` without python3 for the
    scanner-unavailable case. Returns the JSON object the body emitted.
    """
    binroot = make_bin(tmp_path, "gatebin", tools)
    xdg = make_xdg(tmp_path, REPO_ROOT / ".config/nvim")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    script = tmp_path / "gate_probe.lua"
    script.write_text(
        GATE_PREAMBLE.format(repo=REPO_ROOT, answers=json.dumps(list(answers))) + body,
        encoding="utf-8",
    )
    proc = subprocess.run(  # noqa: S603
        [NVIM, "-l", str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(binroot),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    replies = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    assert replies, (
        f"probe emitted nothing\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    return json.loads(replies[-1])


def _listify(value):
    """An empty Lua table crosses vim.json as `{}`; the Python side wants `[]`."""
    return [] if value == {} else value


SENSITIVE_PATH = "/work/app/.env"
SENSITIVE_REFUSAL = "sensitive file path in payload; not sent to AI"

# `all` mode in the editor: one request per tool, same payload, same tick.
ALL_TOOLS = '{"claude", "codex", "gemini", "copilot"}'

# Shared Lua: a spec factory and a recorder that collects done() callbacks.
GATE_HELPERS = """
local function spec(tool, paths)
  return {
    tool = tool,
    prompt = "INSTR",
    -- Not credential-shaped: the whole point is what value scanning cannot see.
    input = "DB_HOST=db.internal\\nDB_PORT=5432",
    source_paths = paths,
  }
end
local reported = {}
local function done(ok, lines, err, tool)
  reported[#reported + 1] = { ok = ok, err = err or vim.NIL, tool = tool or vim.NIL }
end
local function result(extra)
  local out = { confirms = confirms, jobs = jobs, reported = reported }
  for k, v in pairs(extra or {}) do out[k] = v end
  emit(out)
end
"""


class TestSensitivePathGateInRun:
    """backend.run: a payload read from a sensitive path asks first, default No."""

    def run(self, tmp_path, body, **kw):
        res = gate_probe(tmp_path, GATE_HELPERS + body, **kw)
        for key in ("confirms", "jobs", "reported"):
            res[key] = _listify(res[key])
        return res

    def test_a_sensitive_path_raises_a_dialog_that_defaults_to_no(self, tmp_path):
        res = self.run(
            tmp_path,
            f'backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done); result()',
        )
        assert len(res["confirms"]) == 1
        dialog = res["confirms"][0]
        assert dialog["choices"] == "&No\n&Yes"
        assert dialog["default"] == 1, "Enter / Esc must abort the send"
        assert dialog["kind"] == "Warning"
        assert SENSITIVE_PATH in dialog["msg"]

    def test_no_sends_nothing_and_says_why(self, tmp_path):
        res = self.run(
            tmp_path,
            f'backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done); result()',
            answers=[1],
        )
        assert res["jobs"] == [], "a refused request started a process"
        assert res["reported"] == [
            {"ok": False, "err": SENSITIVE_REFUSAL, "tool": None}
        ]

    def test_yes_sends(self, tmp_path):
        res = self.run(
            tmp_path,
            f'backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done); result()',
            answers=[2],
        )
        assert len(res["confirms"]) == 1
        assert len(res["jobs"]) == 1
        assert res["reported"] == [], "an approved request must not be refused"

    def test_an_ordinary_path_asks_nothing(self, tmp_path):
        res = self.run(
            tmp_path,
            'backend.run(spec("claude", {"/work/app/main.lua"}), done); result()',
        )
        assert res["confirms"] == []
        assert len(res["jobs"]) == 1

    def test_a_caller_that_names_no_source_is_not_asked(self, tmp_path):
        """Not every request has a file behind it (a bare spec, a scratch buffer)."""
        res = self.run(tmp_path, 'backend.run(spec("claude", nil), done); result()')
        assert res["confirms"] == []
        assert len(res["jobs"]) == 1

    def test_the_path_gate_covers_the_gemini_api_transport_too(self, tmp_path):
        """Same trap as the credential gate: `kind ~= "cli"` would exempt gemini."""
        res = self.run(
            tmp_path,
            f'backend.run(spec("gemini", {{"{SENSITIVE_PATH}"}}), done); result()',
        )
        assert len(res["confirms"]) == 1
        assert res["jobs"] == []

    def test_local_ollama_is_exempt(self, tmp_path):
        res = self.run(
            tmp_path,
            f'backend.run(spec("gemma", {{"{SENSITIVE_PATH}"}}), done); result()',
        )
        assert res["confirms"] == [], "nothing leaves the machine, so nothing to ask"
        assert len(res["jobs"]) == 1

    def test_all_mode_asks_once_and_no_cancels_every_tool(self, tmp_path):
        res = self.run(
            tmp_path,
            f"""
            for _, t in ipairs({ALL_TOOLS}) do
              backend.run(spec(t, {{"{SENSITIVE_PATH}"}}), done)
            end
            result()
            """,
            answers=[1],
        )
        assert len(res["confirms"]) == 1, "one dialog per request, not per tool"
        assert res["jobs"] == []
        assert [r["err"] for r in res["reported"]] == [SENSITIVE_REFUSAL] * 4

    def test_all_mode_yes_starts_every_tool_after_one_dialog(self, tmp_path):
        res = self.run(
            tmp_path,
            f"""
            for _, t in ipairs({ALL_TOOLS}) do
              backend.run(spec(t, {{"{SENSITIVE_PATH}"}}), done)
            end
            result()
            """,
            answers=[2],
        )
        assert len(res["confirms"]) == 1
        assert len(res["jobs"]) == 4
        assert res["reported"] == []

    def test_an_approval_does_not_outlive_the_tick(self, tmp_path):
        """The same-tick dedupe must not turn into a standing permission."""
        res = self.run(
            tmp_path,
            f"""
            backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done)
            vim.wait(50)
            backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done)
            result()
            """,
            answers=[2, 2],
        )
        assert len(res["confirms"]) == 2

    def test_a_cached_clean_decision_does_not_wave_a_sensitive_path_through(
        self, tmp_path
    ):
        """The dedupe key must include the paths, not only the payload text.

        Same text, same tick: an ordinary file asked first caches "fine". Keyed
        on the text alone, a `.env` carrying the same bytes asked next would
        read that entry and go out without a dialog.
        """
        res = self.run(
            tmp_path,
            f"""
            backend.run(spec("claude", {{"/work/app/main.lua"}}), done)
            backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done)
            result()
            """,
        )
        assert len(res["confirms"]) == 1
        assert len(res["jobs"]) == 1, "only the ordinary file's request went out"
        assert [r["err"] for r in res["reported"]] == [SENSITIVE_REFUSAL]

    def test_a_credential_and_a_sensitive_path_share_one_dialog(self, tmp_path):
        res = self.run(
            tmp_path,
            f"""
            local s = spec("claude", {{"{SENSITIVE_PATH}"}})
            s.input = "aws_key = {FAKE_AWS_KEY}"
            backend.run(s, done)
            result()
            """,
        )
        assert len(res["confirms"]) == 1
        msg = res["confirms"][0]["msg"]
        assert "Possible credential" in msg and SENSITIVE_PATH in msg
        assert res["jobs"] == []
        assert "credential" in res["reported"][0]["err"]
        assert "sensitive file path" in res["reported"][0]["err"]

    def test_the_credential_only_dialog_is_unchanged(self, tmp_path):
        res = self.run(
            tmp_path,
            f"""
            local s = spec("claude", {{"/work/app/main.lua"}})
            s.input = "aws_key = {FAKE_AWS_KEY}"
            backend.run(s, done)
            result()
            """,
        )
        assert len(res["confirms"]) == 1
        assert res["confirms"][0]["msg"].endswith(
            "detected in the AI payload.\nSend it to the AI tool anyway?"
        )
        assert res["reported"][0]["err"] == (
            "credential detected in payload; not sent to AI"
        )

    def test_the_path_dialog_does_not_depend_on_the_scanner(self, tmp_path):
        """No python3 means no value scan (fail open); the path check is local."""
        res = self.run(
            tmp_path,
            f'backend.run(spec("claude", {{"{SENSITIVE_PATH}"}}), done); result()',
            tools=(),
        )
        assert len(res["confirms"]) == 1
        assert res["jobs"] == []


class TestSensitivePathGateInFallback:
    """run_with_fallback gates once up front; an attempt must not re-ask or bypass."""

    # The lead tool refuses the oversized payload synchronously, so the chain
    # advances to the second within the same call (`nvim -l` runs no event loop).
    CHAIN = f"""
        local big = spec("copilot", {{"{SENSITIVE_PATH}"}})
        big.input = string.rep("x", 256 * 1024 + 1)
        big.model = "gpt"
        local nxt = spec("claude", {{"{SENSITIVE_PATH}"}})
        nxt.input = big.input
        local handle = backend.run_with_fallback({{ big, nxt }}, done)
        result({{ handle = handle == nil and "nil" or "handle" }})
    """

    def run(self, tmp_path, body, **kw):
        res = gate_probe(tmp_path, GATE_HELPERS + body, **kw)
        for key in ("confirms", "jobs", "reported"):
            res[key] = _listify(res[key])
        return res

    def test_the_fallback_attempt_is_asked_once_not_twice(self, tmp_path):
        res = self.run(tmp_path, self.CHAIN, answers=[2])
        assert len(res["confirms"]) == 1, "the fallback attempt re-prompted"
        assert len(res["jobs"]) == 1, "the chain never reached its second tool"
        assert "claude" in res["jobs"][0][-1], res["jobs"]

    def test_no_stops_the_whole_chain(self, tmp_path):
        res = self.run(tmp_path, self.CHAIN, answers=[1])
        assert len(res["confirms"]) == 1
        assert res["jobs"] == [], "a No was bypassed by the fallback attempt"
        assert res["handle"] == "nil"
        assert [r["err"] for r in res["reported"]] == [SENSITIVE_REFUSAL]

    def test_an_all_ollama_chain_is_exempt_like_run(self, tmp_path):
        res = self.run(
            tmp_path,
            f"""
            backend.run_with_fallback({{ spec("gemma", {{"{SENSITIVE_PATH}"}}) }}, done)
            result()
            """,
        )
        assert res["confirms"] == []
        assert len(res["jobs"]) == 1

    def test_an_ordinary_path_asks_nothing(self, tmp_path):
        res = self.run(
            tmp_path,
            """
            backend.run_with_fallback({ spec("claude", {"/work/app/main.lua"}) }, done)
            result()
            """,
        )
        assert res["confirms"] == []
        assert len(res["jobs"]) == 1


class TestVimSensitivePathGate:
    """classic Vim's select-and-replace asks before sending a sensitive file.

    The Vim port of the gate: `s:AI_Submit` checks the TARGET buffer's path
    against `s:ai_sensitive_paths`, one confirm that defaults to No, local Ollama
    exempt. Driven through a real Vim (the rc prepended to a probe, as the other
    70-ai.vim port tests do). confirm() cannot be shadowed in Vim, but under
    `-es` it answers with the default button without blocking -- which is
    exactly the property under test: left alone, the dialog means No. The
    job-starting functions are redefined in the probe (same script scope) so
    nothing is ever spawned and "was anything sent" is a plain list.
    """

    @pytest.fixture(scope="class")
    def vim(self):
        from test_editor_config_regressions import _real_vim

        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    def probe(self, vim, script):
        from test_editor_config_regressions import _run_vim_script

        source = (REPO_ROOT / ".vim/rc/70-ai.vim").read_text(encoding="utf-8")
        out = _run_vim_script(vim, script, extra_source=source).strip()
        assert out, (
            "the Vim probe wrote nothing (a function missing, or a script error)"
        )
        return json.loads(out)

    def confirm_send(self, vim, paths):
        return self.probe(
            vim,
            f"let s:r = s:AI_ConfirmSend('DB_HOST=db', json_decode('{json.dumps(paths)}'))\n"
            "call writefile([json_encode(s:r)], $PROBE_OUT)\nqa!\n",
        )

    def submit(self, vim, tool, target_name, between=""):
        """Open the prompt with s:AI_Start, optionally disturb the target, submit.

        Goes through the real s:AI_Start so the context carries whatever it
        captures at prompt-open time. `between` is Vim script run after that and
        before the submit (`s:target` is the target buffer's number): it models
        the user wiping or renaming the buffer while the prompt window is open.
        """
        name = f"file {target_name}" if target_name else ""
        res = self.probe(
            vim,
            f"""
let s:log = []
function! s:AI_RunSingle(ctx, sys, tmpfile) abort
  call add(s:log, 'single')
endfunction
function! s:AI_RunAll(ctx, sys, tmpfile) abort
  call add(s:log, 'all')
endfunction
function! s:AI_RunOllama(ctx, tmpfile) abort
  call add(s:log, 'ollama')
endfunction
enew
{name}
call setline(1, ['DB_HOST=db'])
let s:target = bufnr('%')
call setpos("'<", [s:target, 1, 1, 0])
call setpos("'>", [s:target, 1, 1, 0])
let s:err = ''
try
  call s:AI_Start('{tool}')
  call setline(1, 'rewrite it')
  {between}
  call s:AI_Submit()
catch
  let s:err = v:exception . ' @ ' . v:throwpoint
endtry
call writefile([json_encode({{'log': s:log, 'err': s:err,
      \\ 'messages': execute('messages')}})], $PROBE_OUT)
qa!
""",
        )
        # A throw inside s:AI_Submit would leave `log` empty -- which reads as
        # "nothing was sent" and would make every refusal test pass vacuously.
        assert res["err"] == "", res["err"]
        return res

    def test_a_sensitive_path_is_refused_by_the_default_answer(self, vim):
        assert self.confirm_send(vim, ["/work/app/.env"]) == [
            0,
            "sensitive file path in payload",
        ]

    def test_an_ordinary_path_and_no_path_go_through(self, vim):
        assert self.confirm_send(vim, ["/work/app/main.lua"]) == [1, ""]
        assert self.confirm_send(vim, []) == [1, ""]

    def test_submit_sends_nothing_from_a_sensitive_buffer(self, vim):
        res = self.submit(vim, "claude", "/work/app/.env")
        assert res["log"] == [], "the selection was sent without an answer"
        assert "cancelled (sensitive file path in payload)" in res["messages"]

    def test_submit_checks_the_target_buffer_not_the_prompt_window(self, vim):
        """The current buffer at submit time is the unnamed prompt, never the source."""
        res = self.submit(vim, "claude", "/work/app/prod.tfvars")
        assert res["log"] == []

    def test_submit_sends_from_an_ordinary_buffer_without_asking(self, vim):
        res = self.submit(vim, "claude", "/work/app/main.lua")
        assert res["log"] == ["single"]

    def test_an_unnamed_target_buffer_names_no_source(self, vim):
        """fnamemodify('', ':p') is the cwd; it must not be mistaken for a path."""
        res = self.submit(vim, "claude", "")
        assert res["log"] == ["single"]

    def test_all_mode_is_refused_as_a_whole(self, vim):
        res = self.submit(vim, "all", "/work/app/.env")
        assert res["log"] == []

    def test_all_mode_from_an_ordinary_buffer_still_runs(self, vim):
        res = self.submit(vim, "all", "/work/app/main.lua")
        assert res["log"] == ["all"]

    def test_local_ollama_is_exempt(self, vim):
        res = self.submit(vim, "gemma", "/work/app/.env")
        assert res["log"] == ["ollama"]

    def test_a_relative_buffer_name_is_judged_by_its_absolute_path(self, vim):
        """`:file .kube/config` (relative) must not slip past the `/.kube/` entry."""
        res = self.submit(vim, "claude", ".kube/config")
        assert res["log"] == []

    # The selection text is captured when the prompt opens; the path has to be
    # captured then too. Read at submit time, a wiped or renamed target would send
    # the text with an empty or different path and no dialog.

    def test_a_target_wiped_while_the_prompt_is_open_is_still_asked_about(self, vim):
        res = self.submit(
            vim, "claude", "/work/app/.env", between="execute 'bwipeout! ' . s:target"
        )
        assert res["log"] == [], "the text of a wiped .env went out with no path"
        assert "cancelled (sensitive file path in payload)" in res["messages"]

    def test_a_target_renamed_while_the_prompt_is_open_is_still_asked_about(self, vim):
        res = self.submit(
            vim,
            "claude",
            "/work/app/.env",
            between=(
                "call win_execute(bufwinid(s:target), 'silent file /work/app/plain.txt')"
            ),
        )
        assert res["log"] == [], "the text of a .env went out under a different name"

    def test_a_target_wiped_while_the_prompt_is_open_does_not_throw_when_ordinary(
        self, vim
    ):
        res = self.submit(
            vim,
            "claude",
            "/work/app/main.lua",
            between="execute 'bwipeout! ' . s:target",
        )
        assert res["log"] == ["single"]
