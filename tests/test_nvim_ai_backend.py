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


def backend_call(fn, *args, binroot, tmp_path, xdg=None, module=BACKEND_MODULE):
    """Call `ai.<module>.<fn>` (dotted paths allowed) in a sealed headless nvim."""
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
    """What each tool is actually handed. Pure apart from vim.fn.shellescape."""

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
        assert cmd == f"cat '{self.payload_file(tmp_path)}' | codex exec 'INSTR'"

    def test_codex_skip_git_check_is_opt_in(self, tmp_path):
        off = self.build(tmp_path, "codex", None, "I", skip_git=False)
        on = self.build(tmp_path, "codex", None, "I", skip_git=True)
        assert "--skip-git-repo-check" not in off
        assert "codex exec --skip-git-repo-check 'I'" in on

    def test_gemini_carries_the_model(self, tmp_path):
        cmd = self.build(tmp_path, "gemini", "flash", "INSTR")
        assert (
            cmd == f"cat '{self.payload_file(tmp_path)}' | gemini -m 'flash' -p 'INSTR'"
        )

    def test_claude_is_the_default_branch(self, tmp_path):
        cmd = self.build(tmp_path, "claude", "sonnet", "INSTR")
        expected = (
            f"cat '{self.payload_file(tmp_path)}' | claude --model 'sonnet' -p 'INSTR'"
        )
        assert cmd == expected

    def test_an_unknown_tool_falls_through_to_claude(self, tmp_path):
        """Documenting the `else` branch: it is not a rejection path.

        M.run refuses unknown tools before reaching here, so this only fires
        for a tool added to TOOLS without a branch of its own -- in which case
        it silently runs as claude.
        """
        cmd = self.build(tmp_path, "brand-new", "m", "I")
        assert cmd.startswith(f"cat '{self.payload_file(tmp_path)}' | claude ")

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
        "build_cli_cmd,cli_failure_reason,ollama_failure_reason,scan_payload"
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
