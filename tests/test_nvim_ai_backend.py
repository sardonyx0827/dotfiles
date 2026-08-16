"""Unit tests for `.config/nvim/lua/setup/functions/ai/backend.lua`.

The second slice of the Neovim AI tests (see test_nvim_ai_prompt.py for the
mechanism and for why only `functions/ai/` is tested at all). Two things in
this module are worth pinning:

- `scan_payload` is the pre-send credential gate. Every AI request funnels
  through it, and a false negative sends a live credential to an external
  tool. It is also the one place where the Lua side and `scripts/secret_scan.py`
  have to agree on a wire contract (exit 0/1/2), which nothing checked.
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


def _lua_probe(tmp_path, body):
    """Run `body` in a bare `nvim -l` with the repo's lua tree on package.path.

    For invariants that only nvim itself can decide (does this API accept these
    arguments?) rather than ones a returned value can show.
    """
    binroot = make_bin(tmp_path, "probebin")
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

    Nothing previously checked that the Lua reader implements the same
    contract the Python writer promises; these run a stub scanner that exits
    with each documented code.
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
        """The `gemini` CLI is gone; the REST API is reached via a helper.

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

        `--tools ''` drops only the BUILT-IN tools. Measured against claude
        2.1.228: with that flag alone, a run whose Stop hook blocked went on to
        edit the working tree through mcp__serena__replace_content -- an MCP
        tool, untouched by --tools. --strict-mcp-config with no --mcp-config
        alongside it leaves the session zero MCP servers, which is the other
        half. A future edit that keeps one and drops the other reads as a
        harmless simplification and silently reopens the hole, so pin both.
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
    """Gemini reaches Google over HTTPS now, not through the `gemini` CLI.

    Three properties survive that move and none of them is visible in the
    command string alone: the helper is found next to the config symlink, the
    model comes from the same variable the rest of the repo uses, and the
    payload is still scanned for credentials before it leaves the editor.
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

        Same resolution scan_payload has always relied on, now shared with the
        Gemini helper -- so a broken lookup takes out both, and pinning it once
        covers both.
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
        """The gate stays armed for gemini after the transport change.

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
    """When every tool in a chain fails, the report must name every reason.

    Keeping only the LAST error was survivable while gemini was a CLI that
    usually worked. It stopped being so once gemini can fail for a reason of its
    own that says nothing about the request: with GEMINI_API_KEY unset -- the
    normal state of a GUI-launched editor -- every claude outage in the
    claude -> gemini chains (`<leader>qf`, `<leader>qh`) surfaced as
    "GEMINI_API_KEY is not set" and threw away what claude had said.

    Driven with unknown tool names because M.run rejects those SYNCHRONOUSLY:
    `nvim -l` never runs the event loop, so a chain of real tools would park in
    jobstart and report nothing at all.
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
        one-attempt case has to keep the bare reason it always had.
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

    Two things were wrong. `exit code 0` was reported when the tool succeeded
    but printed nothing -- stating a success as the cause of a failure. And
    with no on_stderr handler the CLI's own diagnosis was thrown away: a
    missing binary exits 127 with "command not found" on stderr, so the reader
    saw a bare number and no way to tell that the tool simply is not installed.
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
    """run_ollama never got the fix run_cli did, so its reason was always wrong.

    `done(false, {}, err or exit_code)` prefers the parse error, and parse_ollama
    returns a non-nil error for any unusable body -- including the empty string an
    unreachable server produces. The exit_code branch was therefore unreachable, and
    "could not connect to Ollama" always surfaced as "invalid JSON response". run_ollama
    also registered no on_stderr at all, so the transport's own diagnosis was discarded.

    Routed through the same cli_failure_reason run_cli uses, so both backends describe a
    failure the same way: the transport's exit code and stderr when the transport failed,
    the parse error only when it genuinely succeeded and returned something unusable.
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

    While the reason was always `exit code N` this could not happen. Quoting
    the tool's stderr made multi-line reasons the normal case -- a traceback is
    exactly what MAX_STDERR_CHARS was sized for -- so the display has to split
    the message instead of handing it over as one line.
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
