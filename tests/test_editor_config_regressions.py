"""Text-level guards for editor-config bugs that no linter in this repo can see.

luacheck reads names and scopes; it has no model of another plugin's argv grammar.
Nothing at all checks Vimscript -- `.vim/rc/` is the one tree in this repository with
neither a linter nor a test. Both bugs pinned here passed every gate while being plainly
wrong at runtime, and both were found by reading rather than by any automated check.

These are content assertions, not behavior tests: driving real toggleterm keymaps or a
real `:saveas` would need a live Neovim/Vim session with plugins installed, which this
suite deliberately does not build. The assertions are written against the specific
malformed shapes so they stay meaningful rather than merely present.
"""

import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

TOGGLETERM_SPEC = REPO_ROOT / ".config/nvim/lua/setup/plugins/utilities/toggleterm.lua"
VIM_AI_RC = REPO_ROOT / ".vim/rc/70-ai.vim"


# toggleterm's own commandline parser splits each space-separated token on "=" and takes
# the left side as the option name. `:ToggleTerm 2direction=horizontal` therefore parses
# as {["2direction"]="horizontal"} -- an unrecognized key -- and `.direction` comes back
# nil, so the terminal silently uses the setup default instead of the split the mapping's
# own `desc` promises. The count belongs on the command name (`:2ToggleTerm ...`), which
# is how Vim command counts work; glued to the option it is just a typo the parser cannot
# report. Verified against the real module: parse("2direction=horizontal").direction == nil.
_COUNT_GLUED_TO_OPTION = re.compile(r":ToggleTerm\s+\d+[a-z_]+=")


def test_toggleterm_count_prefix_is_not_glued_to_an_option_name():
    text = TOGGLETERM_SPEC.read_text(encoding="utf-8")
    # Positive anchor: a "must not match" assertion alone keeps passing against an
    # emptied or renamed file.
    assert ":ToggleTerm" in text, "toggleterm spec no longer maps :ToggleTerm at all"

    # Lua comments are skipped: the fix's own comment quotes the broken form as the
    # thing not to write, and a whole-file scan would flag the explanation forever.
    offenders = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if not line.lstrip().startswith("--") and _COUNT_GLUED_TO_OPTION.search(line)
    ]
    assert not offenders, (
        "a terminal count is glued to an option name, so toggleterm parses it as an "
        "unknown key and silently ignores the option; put the count on the command "
        f"instead (`:2ToggleTerm direction=horizontal`): {offenders}"
    )


# The Copilot sensitive-path guard decides whether to disable Copilot for a buffer by
# matching its name against a secret-path list. It only re-runs on the events in this
# augroup, so a buffer that BECOMES sensitive by being renamed in place -- `:saveas
# ~/.env`, `:file id_rsa` -- is never re-checked and keeps streaming to GitHub under its
# new name. Renames fire BufFilePre/BufFilePost and none of the three originally watched
# events, so the rename path had no coverage at all.
def test_copilot_sensitive_guard_rechecks_after_a_buffer_rename():
    text = VIM_AI_RC.read_text(encoding="utf-8")
    assert "AICopilotSensitiveGuard" in text, (
        "the Copilot sensitive-path guard augroup is gone"
    )

    guard_events = [
        line
        for line in text.splitlines()
        if "autocmd" in line and "AI_CopilotGuard" in line
    ]
    assert guard_events, "the guard augroup no longer registers any autocmd"

    watched = " ".join(guard_events)
    assert "BufFilePost" in watched, (
        "the Copilot sensitive-path guard does not re-run on rename; `:saveas ~/.env` "
        "fires BufFilePre/BufFilePost, so without one of those a buffer that becomes "
        f"sensitive keeps Copilot enabled for the rest of the session: {guard_events}"
    )


# The vim AI replace path is an independent port of the Neovim one, and it was left
# behind when 59cfcf9 fixed how a failed run is reported. job_start() registered out_cb
# but no err_cb, so the tool's own stderr -- the part that says WHY, e.g. "command not
# found" or a connection error -- was discarded, and every failure was rendered as a bare
# `[<tool> failed (exit code N)]`, including exit code 0 (ran fine, printed nothing),
# which states a success as the cause of a failure.
def test_vim_ai_jobs_capture_stderr():
    text = VIM_AI_RC.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Vimscript comments start with `"`; the file discusses job_start in prose too.
    starts = [
        n
        for n, line in enumerate(lines, 1)
        if "job_start(" in line and not line.lstrip().startswith('"')
    ]
    assert starts, "70-ai.vim no longer starts any AI job"
    for start in starts:
        # The options dict is a line-continued block; read to its closing brace.
        block = []
        for line in lines[start - 1 :]:
            block.append(line)
            if line.rstrip().endswith("})"):
                break
        joined = " ".join(block)
        assert "err_cb" in joined or "err_io" in joined, (
            f"the job_start at 70-ai.vim:{start} captures stdout but drops stderr, so a "
            f"failure is reported as a bare exit code with no reason: {joined.strip()}"
        )


def test_vim_ai_failure_message_can_carry_a_reason():
    """A bare `(exit code %d)` format string cannot say anything but the number."""
    text = VIM_AI_RC.read_text(encoding="utf-8")
    bare = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if "failed (exit code %d)" in line
    ]
    assert not bare, (
        "the failure message has no room for the captured stderr, so the reader still "
        f"sees only a number (and 'exit code 0' when the tool merely printed nothing): "
        f"{bare}"
    )


def _real_vim() -> str | None:
    """Path to a genuine Vim, or None.

    Must match 70-ai.vim's OWN guard, not just "is it Vim". The file is wrapped in
    `if !has('nvim') && has('job') && has('channel') && has('timers')`, so a build
    missing any of those (vim-tiny, and whatever a given CI image ships as `vim`)
    defines none of the functions under test -- the tests would fail rather than skip,
    for a reason that has nothing to do with the code. `vim` on PATH is also commonly
    Neovim here, since this repo aliases it.
    """
    for candidate in ("/usr/bin/vim", shutil.which("vim")):
        if not candidate:
            continue
        try:
            if _vim_guard_holds(candidate):
                return candidate
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def _vim_guard_holds(binary: str) -> bool:
    """True when `binary` satisfies every condition 70-ai.vim's own `if` requires."""
    script = (
        "call writefile([(!has('nvim') && has('job') && has('channel')"
        " && has('timers')) ? 'yes' : 'no'], $PROBE_OUT)\nqa!\n"
    )
    return _run_vim_script(binary, script, extra_source=None).strip() == "yes"


def _run_vim_script(binary: str, script: str, extra_source: str | None) -> str:
    """Run `script` under `binary`, optionally with 70-ai.vim prepended.

    Prepended rather than sourced: `s:` is per-script scope, so a separate file cannot
    reach 70-ai.vim's script-local functions at all.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        combined = os.path.join(tmp, "probe.vim")
        out = os.path.join(tmp, "probe.out")
        body = (extra_source + "\n" if extra_source else "") + script
        with open(combined, "w", encoding="utf-8") as fh:
            fh.write(body)
        subprocess.run(  # noqa: S603
            [binary, "-es", "-u", "NONE", "-N", "-S", combined],
            env={**os.environ, "PROBE_OUT": out},
            capture_output=True,
            text=True,
            timeout=60,
        )
        try:
            with open(out, encoding="utf-8") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""


class TestVimOllamaFailureOrdering:
    """The transport's exit code must win over the response-body parse error.

    The vim AI path is an independent port of the Neovim one and carried the same
    precedence bug: the parse error was preferred unconditionally, and a body that never
    arrived fails to parse just as surely as a malformed one, so "could not reach the
    server" always surfaced as a parse complaint. The nvim side has this pinned by
    TestOllamaFailureReason; the vim port originally got the fix with no test at all --
    reverting the ordering left every test green.

    Driven through a real Vim rather than asserted as source text, because the shape of
    the condition is not the behaviour: what matters is which reason comes out.
    """

    @pytest.fixture(scope="class")
    def vim(self):
        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    def reason(self, vim, status, errbuf, parse_err):
        source = VIM_AI_RC.read_text(encoding="utf-8")
        err_literal = "v:null" if parse_err is None else f"'{parse_err}'"
        script = (
            "let s:r = s:AI_OllamaFailureReason('gemma', {status}, {errbuf}, {err})\n"
            "call writefile([s:r], $PROBE_OUT)\nqa!\n"
        ).format(
            status=status,
            errbuf=repr(list(errbuf)).replace("'", "'"),
            err=err_literal,
        )
        return _run_vim_script(vim, script, extra_source=source).strip()

    def test_transport_failure_beats_the_parse_error(self, vim):
        out = self.reason(vim, 7, ["curl: (7) Failed to connect"], "invalid JSON")
        assert "7" in out, out
        assert "invalid JSON" not in out, (
            "a failed transport still reported the downstream parse error"
        )

    def test_transport_stderr_reaches_the_reason(self, vim):
        out = self.reason(vim, 7, ["curl: (7) Failed to connect"], "invalid JSON")
        assert "Failed to connect" in out, out

    def test_parse_error_survives_when_the_transport_succeeded(self, vim):
        out = self.reason(vim, 0, [], "invalid JSON")
        assert "invalid JSON" in out, out

    def test_exit_zero_is_never_stated_as_the_cause(self, vim):
        out = self.reason(vim, 0, [], None)
        assert "exit 0" not in out, out
        assert out.strip() != ""


class TestVimOneShotInvocationShape:
    """The vim port must not hand the agent a way to edit the repository.

    Its Neovim twin is pinned by TestBuildCliCmd in test_nvim_ai_backend.py; the
    same three properties are re-checked here because the two builders are
    independent ports, and this one expresses the empty tool list as `''''`
    inside a single-quoted Vimscript string -- a quoting form that is correct
    but easy to "tidy" into `''`, which would pass `-p` itself as the tool list.

    Driven through a real Vim rather than grepped out of the source: what
    matters is the string that reaches `sh -c`, and that is exactly what the
    quoting question is about.
    """

    @pytest.fixture(scope="class")
    def vim(self):
        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    def build(self, vim, tool):
        source = VIM_AI_RC.read_text(encoding="utf-8")
        script = (
            f"let s:c = s:AI_BuildCmd('{tool}', '/tmp/payload', 'SYS', ['x'])\n"  # nosec B108
            "call writefile([s:c], $PROBE_OUT)\nqa!\n"
        )
        return _run_vim_script(vim, script, extra_source=source).strip()

    def test_claude_gets_no_tools_from_either_source(self, vim):
        # --tools drops the built-in set only; MCP-provided tools survive it and
        # are just as able to write. Both flags or neither is worth having.
        cmd = self.build(vim, "claude")
        assert "--tools ''" in cmd, cmd
        assert "--strict-mcp-config" in cmd, cmd

    def test_the_hooked_tools_are_marked_as_editor_oneshot(self, vim):
        assert "EDITOR_AI_ONESHOT=1 claude" in self.build(vim, "claude")
        assert "EDITOR_AI_ONESHOT=1 codex" in self.build(vim, "codex")

    def test_codex_never_gets_a_writable_sandbox(self, vim):
        cmd = self.build(vim, "codex")
        assert "codex exec --sandbox read-only" in cmd, cmd

    def test_the_unhooked_tools_are_not_marked(self, vim):
        # gemini and copilot run no Stop hook; marking them would be cargo cult.
        assert "EDITOR_AI_ONESHOT" not in self.build(vim, "gemini")
        assert "EDITOR_AI_ONESHOT" not in self.build(vim, "copilot")


class TestFailureDetailClipParity:
    """Both ports must quote the same amount of a failing tool's stderr.

    They did not: Neovim allowed 500 characters and classic Vim 200. That cost
    nothing while every message was a short "command not found", and started to
    matter once gemini began surfacing Google's own error text -- its "models/X
    is not found for API version v1beta ..." runs past 200 characters, so one
    editor showed the half that says what to do and the other did not.

    Compared mechanically rather than by reading both constants, because the
    two are independent ports and the numbers are what drifted. The markers make
    the boundary observable through the different wrappers each side adds.
    """

    LIMIT = 500

    @pytest.fixture(scope="class")
    def vim(self):
        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    def detail(self):
        # "B" sits at the last kept index; "C" is the first that must be cut.
        return "A" * (self.LIMIT - 1) + "B" + "C" * 50

    def test_both_editors_cut_at_the_same_character(self, vim, tmp_path):
        from test_nvim_ai_backend import NVIM, backend_call, make_bin

        if NVIM is None:
            pytest.skip("nvim not installed")
        detail = self.detail()

        nvim_out = backend_call(
            "_internal.cli_failure_reason",
            1,
            [detail],
            binroot=make_bin(tmp_path, "bin"),
            tmp_path=tmp_path,
        ).only

        source = VIM_AI_RC.read_text(encoding="utf-8")
        script = (
            f"let s:r = s:AI_FailureReason('t', 1, ['{detail}'])\n"
            "call writefile([s:r], $PROBE_OUT)\nqa!\n"
        )
        vim_out = _run_vim_script(vim, script, extra_source=source).strip()

        for label, out in (("nvim", nvim_out), ("vim", vim_out)):
            assert "B" in out, f"{label} cut before the shared limit: {len(out)}"
            assert "C" not in out, f"{label} kept more than the shared limit"


class TestVimGeminiApiShape:
    """Gemini reaches the REST API through the shared helper, not the CLI.

    Its Neovim twin is pinned by TestGeminiApiPath in test_nvim_ai_backend.py.
    Re-checked here because the two command builders are independent ports and
    have drifted before: the failure-message formatting was fixed on the Neovim
    side and the identical bug sat in this file for another two commits.

    Driven through a real Vim rather than grepped out of the source, because
    what matters is the string that reaches `sh -c`.
    """

    @pytest.fixture(scope="class")
    def vim(self):
        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    def build(self, vim, tool):
        source = VIM_AI_RC.read_text(encoding="utf-8")
        script = (
            f"let s:c = s:AI_BuildCmd('{tool}', '/tmp/payload', 'SYS', ['x'])\n"  # nosec B108
            "call writefile([s:c], $PROBE_OUT)\nqa!\n"
        )
        return _run_vim_script(vim, script, extra_source=source).strip()

    def test_the_gemini_cli_is_no_longer_invoked(self, vim):
        cmd = self.build(vim, "gemini")
        assert " gemini -m " not in cmd, cmd
        assert " gemini -p " not in cmd, cmd

    def test_the_payload_still_arrives_on_stdin(self, vim):
        """The property everything downstream rests on.

        s:AI_RunAll hands ONE tmpfile to every tool in the 'all' list, and the
        ARG_MAX refusal in s:AI_CmdTooLarge assumes gemini keeps the command
        short however large the selection is. Inlining the payload the way
        copilot has to would break both at once.
        """
        cmd = self.build(vim, "gemini")
        assert cmd.startswith("cat '/tmp/payload' | python3 '"), cmd  # nosec B108
        assert cmd.endswith("/scripts/gemini_api.py' --system 'SYS'"), cmd

    def test_the_api_key_never_appears_in_the_command(self, vim):
        """The reason the request goes through a child process at all.

        The command is handed to `sh -c`, so a key interpolated into it -- the
        obvious `curl -H "x-goog-api-key: $GEMINI_API_KEY"` rewrite -- would be
        expanded into curl's argv and readable by every process on the machine
        through `ps aux`. The helper reads the variable from its own
        environment instead, so it must not be named here.
        """
        cmd = self.build(vim, "gemini")
        assert "GEMINI_API_KEY" not in cmd, cmd
        assert "x-goog-api-key" not in cmd, cmd

    def test_the_helper_path_resolves_to_the_repo_scripts_dir(self, vim, tmp_path):
        """Three `:h` up from .vim/rc/70-ai.vim must land on the repo root.

        Every other test in this class PREPENDS the rc to a probe in a temp
        directory, where `<sfile>` is the probe rather than the rc -- so they
        pin the command SHAPE while proving nothing about where the helper is
        looked for, and would pass just as happily against
        `/nonexistent/scripts/gemini_api.py`.

        Run the real file from a repo-shaped location instead. A wrong step
        count aims both python helpers at a path that does not exist (the
        credential scanner shares the expression), and the only symptom is
        every gemini request failing with "No such file or directory" while
        the scanner silently degrades to its fail-open branch.
        """
        import os

        root = tmp_path / "fakerepo"
        rc = root / ".vim/rc/70-ai.vim"
        rc.parent.mkdir(parents=True)
        # Appended to the rc itself rather than sourced from a sibling: `s:` is
        # per-script scope, so nothing outside this file can read the variables.
        rc.write_text(
            VIM_AI_RC.read_text(encoding="utf-8")
            + "\ncall writefile([s:ai_gemini_helper, s:ai_secret_scanner], $PROBE_OUT)"
            + "\nqa!\n",
            encoding="utf-8",
        )
        out = tmp_path / "probe.out"
        subprocess.run(  # noqa: S603
            [vim, "-es", "-u", "NONE", "-N", "-S", str(rc)],
            env={**os.environ, "PROBE_OUT": str(out)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        scripts = (root / "scripts").resolve()
        assert out.read_text(encoding="utf-8").splitlines() == [
            str(scripts / "gemini_api.py"),
            str(scripts / "secret_scan.py"),
        ]

    def test_the_instruction_is_still_shell_escaped(self, vim):
        """An unescaped instruction is a command injection into `sh -c`.

        The user types it into the prompt window, so a quote that closes early
        would turn the rest of their own text into commands.
        """
        source = VIM_AI_RC.read_text(encoding="utf-8")
        script = (
            "let s:c = s:AI_BuildCmd('gemini', '/tmp/payload', \"it's; rm -rf /\", ['x'])\n"  # nosec B108
            "call writefile([s:c], $PROBE_OUT)\nqa!\n"
        )
        cmd = _run_vim_script(vim, script, extra_source=source).strip()
        assert "it'\\''s" in cmd, cmd
        assert "it's" not in cmd, cmd
