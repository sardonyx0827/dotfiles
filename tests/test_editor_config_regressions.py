"""Text-level guards for editor-config bugs that no linter in this repo can see.

luacheck reads names and scopes; it has no model of another plugin's argv grammar.
Nothing lints Vimscript -- `.vim/rc/` has no linter, so its checks live here. The bugs
pinned here pass every other gate while being plainly wrong at runtime.

The toggleterm and Copilot-guard checks are content assertions, not behavior tests:
driving real toggleterm keymaps or a real `:saveas` would need a live Neovim/Vim session
with plugins installed, which this suite deliberately does not build. The assertions are
written against the specific malformed shapes so they stay meaningful rather than merely
present. The 70-ai.vim port tests further down do drive a real Vim (no plugins needed)
and skip when none is available.
"""

import json
import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

TOGGLETERM_SPEC = REPO_ROOT / ".config/nvim/lua/setup/plugins/utilities/toggleterm.lua"
VIM_AI_RC = REPO_ROOT / ".vim/rc/70-ai.vim"
VIM_COMMANDS_RC = REPO_ROOT / ".vim/rc/50-commands-autocmds.vim"
VIM_CUSTOM_RC = REPO_ROOT / ".vim/rc/80-custom.vim"


# A bare `:%s/\s\+$//e` moves the cursor and clobbers the last search pattern that n/N
# read; s:TrimTrailingWhitespace() in 80-custom.vim wraps the save-time trim in
# winsaveview/keeppatterns/winrestview for exactly that reason. `:FixWhitespace` must
# reuse the same wrapper instead of running the bare substitution on its own.
def test_fixwhitespace_command_uses_the_wrapped_trim():
    text = (
        VIM_COMMANDS_RC.read_text(encoding="utf-8")
        + "\n"
        + VIM_CUSTOM_RC.read_text(encoding="utf-8")
    )
    command_lines = [
        line
        for line in text.splitlines()
        if line.lstrip().startswith("command!") and "FixWhitespace" in line
    ]
    assert command_lines, "the FixWhitespace command definition is gone"
    assert not any(r"%s/\s\+$//e" in line for line in command_lines), (
        "FixWhitespace still runs the bare substitution directly, which moves the "
        f"cursor and clobbers the search register n/N read: {command_lines}"
    )
    assert any("s:TrimTrailingWhitespace" in line for line in command_lines), (
        f"FixWhitespace does not call the wrapped trim helper: {command_lines}"
    )


# `:ToggleTerm 2direction=horizontal` parses as an unrecognized "2direction" key, not a
# count plus a `direction` option. See toggleterm.lua's own comment for why. Verified
# against the real module: parse("2direction=horizontal").direction == nil.
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


# The Copilot sensitive-path guard only re-runs on the events in its augroup, and a
# buffer renamed in place (`:saveas ~/.env`) fires only BufFilePre/BufFilePost, so
# BufFilePost has to be watched. See the AICopilotSensitiveGuard augroup's own comment
# in 70-ai.vim.
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


# The vim AI replace path is an independent port of the Neovim one and must report a
# failed run the same way. job_start() needs an err_cb (or err_io), or the tool's own
# stderr -- the part that says WHY, e.g. "command not found" or a connection error -- is
# discarded and every failure renders as a bare `[<tool> failed (exit code N)]`,
# including exit code 0 (ran fine, printed nothing), which states a success as the
# cause of a failure.
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

    The vim AI path is an independent port of the Neovim one and must keep the same
    precedence: a body that never arrived fails to parse just as surely as a malformed
    one, so a parse-error-first reason would report "could not reach the server" as a
    parse complaint. The nvim side pins this in TestOllamaFailureReason.

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

    A differing limit is harmless for short messages like "command not found" but
    matters once gemini surfaces Google's own error text -- its "models/X is not
    found for API version v1beta ..." runs past 200 characters, so one editor would
    show the half that says what to do and the other not.

    Compared mechanically rather than by reading both constants, because the
    two are independent ports and the numbers are what can drift. The markers
    make the boundary observable through the different wrappers each side adds.
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
    can drift apart.

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


# --------------------------------------------------------------------------
# The accept path, driven end to end: job exit -> the finish callback -> `y` ->
# the user's own buffer. See the class docstring for why a text-level assertion
# could not have caught this one.
# --------------------------------------------------------------------------

# Lines 2-3 are the "selection"; T1/T4/T5 are the bystanders that prove a
# deletion happened rather than a replacement.
TARGET_LINES = ["T1", "T2", "T3", "T4", "T5"]
SELECTION = (2, 3)


def _vim_list(items) -> str:
    """Render a Python string sequence as a Vimscript single-quoted list literal."""
    return "[" + ", ".join("'" + s.replace("'", "''") + "'" for s in items) + "]"


def _probe_epilogue(accept: str | None) -> str:
    """Press the accept key (when there is one) and record the user's buffer after."""
    press = (
        f"""
let s:p_msg = ''
try
  let s:p_msg = execute('call {accept}()')
catch
  let s:p_msg = v:exception
endtry
call add(g:R, 'MESSAGE=' . substitute(s:p_msg, '[\\r\\n]\\+', ' ', 'g'))
"""
        if accept
        else ""
    )
    return (
        press
        + """
call add(g:R, 'TARGET_AFTER=' . string(getbufline(s:p_target, 1, '$')))
"""
    )


def _single_scenario(
    *,
    tool: str = "claude",
    output=("R1", "R2"),
    finish: str = "s:AI_SingleFinish",
    exit_status: int = 0,
    vanish: str = "resp",
    accept: str = "s:AI_SingleAccept",
) -> str:
    """Vim source driving one single-mode accept, from job exit to the keypress.

    `vanish` names the window the user closes with a plain `<C-w>c` before the
    job returns -- "resp", "orig", or "" for the healthy control. Both buffers
    are `bufhidden=wipe`, so closing either takes its buffer with it while the
    state dict keeps the now-dangling buffer number.
    """
    close = {
        "resp": "call win_gotoid(s:p_resp_win)\nclose\ncall win_gotoid(s:p_orig_win)\n",
        "orig": "call win_gotoid(s:p_orig_win)\nclose\ncall win_gotoid(s:p_resp_win)\n",
        "": "call win_gotoid(s:p_orig_win)\n",
    }[vanish]
    return f"""
call setline(1, {_vim_list(TARGET_LINES)})
let s:p_target = bufnr('%')

" The diff tab exactly as s:AI_RunJob builds it: an Original window holding the
" selection, a response split, and b:ai_state (plus the maps) on both.
tabnew
setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
call setline(1, ['T2', 'T3'])
let s:p_orig_buf = bufnr('%')
let s:p_orig_win = win_getid()
rightbelow vnew
setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
call setline(1, ['[{tool}: waiting for response...]'])
setlocal nomodifiable
let s:p_resp_buf = bufnr('%')
let s:p_resp_win = win_getid()
let s:p_state = {{
      \\ 'mode': 'single', 'tool': '{tool}',
      \\ 'target_buf': s:p_target, 'start': {SELECTION[0]}, 'end': {SELECTION[1]},
      \\ 'changedtick': getbufvar(s:p_target, 'changedtick'),
      \\ 'orig_buf': s:p_orig_buf, 'orig_win': s:p_orig_win,
      \\ 'resp_buf': s:p_resp_buf, 'resp_win': s:p_resp_win,
      \\ 'status': 'pending', 'output': {_vim_list(output)}, 'errout': [],
      \\ 'closed': 0, 'tmpfile': '/nonexistent/ai-probe-tmpfile',
      \\ }}
call setbufvar(s:p_orig_buf, 'ai_state', s:p_state)
call setbufvar(s:p_resp_buf, 'ai_state', s:p_state)

{close}
call add(g:R, 'RESP_EXISTS=' . bufexists(s:p_resp_buf))
" The plugin's own `q` sets this; an ordinary window close cannot, which is why
" the `if l:s.closed | return` guard in every finish callback does not fire.
call add(g:R, 'CLOSED_FLAG=' . s:p_state.closed)

" The real callback runs from timer_start, where a throw is swallowed and the
" tab is left standing; try/catch models that rather than aborting the scenario.
let s:p_thrown = ''
try
  call {finish}(s:p_state, {exit_status}, 0)
catch
  let s:p_thrown = v:exception
endtry
call add(g:R, 'FINISH_THREW=' . s:p_thrown)
call add(g:R, 'STATUS=' . s:p_state.status)
{_probe_epilogue(accept)}"""


def _all_scenario(
    *,
    output=("R1", "R2"),
    vanish: str = "active",
    finish_idx: int = 1,
    accept: str | None = "s:AI_AllAccept",
) -> str:
    """The same drive for `all` mode, whose response buffers are bufhidden=hide.

    A plain window close therefore leaves them alive; it takes an explicit
    `:bwipeout` (or `:bd!`) to reach the same dangling-number state.

    `vanish` picks which of the two tabs the user destroys: "active" (the one on
    screen -- Vim closes the response window along with it, since every buffer
    here is `nobuflisted` and none is eligible to take its place), "inactive"
    (the window survives, so the tab list stays observable), or "" for the
    healthy control.
    """
    kill = {
        "active": "execute 'bwipeout! ' . s:p_buf1\n",
        "inactive": "execute 'bwipeout! ' . s:p_buf2\n",
        "": "",
    }[vanish]
    wiped = {"active": "s:p_buf1", "inactive": "s:p_buf2", "": "s:p_buf1"}[vanish]
    # The reply belongs to whichever job is being finished; the other tab is
    # left with nothing, which is a plain 'failed' and not what is under test.
    outs = {i: _vim_list(output) if i == finish_idx else "[]" for i in (1, 2)}
    return f"""
call setline(1, {_vim_list(TARGET_LINES)})
let s:p_target = bufnr('%')

tabnew
setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
call setline(1, ['T2', 'T3'])
let s:p_orig_buf = bufnr('%')
let s:p_orig_win = win_getid()
rightbelow vnew
let s:p_resp_win = win_getid()
setlocal buftype=nofile bufhidden=hide noswapfile nobuflisted
call setline(1, ['[claude: waiting for response...]'])
setlocal nomodifiable
let s:p_buf1 = bufnr('%')
enew
setlocal buftype=nofile bufhidden=hide noswapfile nobuflisted
call setline(1, ['[codex: waiting for response...]'])
setlocal nomodifiable
let s:p_buf2 = bufnr('%')
execute 'buffer ' . s:p_buf1

let s:p_state = {{
      \\ 'mode': 'all', 'tools': ['claude', 'codex'],
      \\ 'target_buf': s:p_target, 'start': {SELECTION[0]}, 'end': {SELECTION[1]},
      \\ 'changedtick': getbufvar(s:p_target, 'changedtick'),
      \\ 'orig_buf': s:p_orig_buf, 'orig_win': s:p_orig_win,
      \\ 'resp_win': s:p_resp_win,
      \\ 'bufs': {{1: s:p_buf1, 2: s:p_buf2}},
      \\ 'status': {{1: 'pending', 2: 'pending'}},
      \\ 'output': {{1: {outs[1]}, 2: {outs[2]}}},
      \\ 'errout': {{1: [], 2: []}}, 'jobs': {{}},
      \\ 'active': 1, 'pending': 2, 'closed': 0,
      \\ 'tmpfile': '/nonexistent/ai-probe-tmpfile',
      \\ }}
call setbufvar(s:p_orig_buf, 'ai_state', s:p_state)
call setbufvar(s:p_buf1, 'ai_state', s:p_state)
call setbufvar(s:p_buf2, 'ai_state', s:p_state)

{kill}call win_gotoid(s:p_orig_win)
call add(g:R, 'RESP_EXISTS=' . bufexists({wiped}))
call add(g:R, 'CLOSED_FLAG=' . s:p_state.closed)

let s:p_thrown = ''
try
  call s:AI_AllFinish(s:p_state, {finish_idx}, 0, 0)
catch
  let s:p_thrown = v:exception
endtry
call add(g:R, 'FINISH_THREW=' . s:p_thrown)
call add(g:R, 'STATUS=' . s:p_state.status[{finish_idx}])
" The tab list s:AI_AllStatus paints. Empty when the response window died with
" the buffer inside it, which is what happens for vanish="active".
call add(g:R, 'TABLINE=' . getwinvar(s:p_resp_win, '&statusline'))
{_probe_epilogue(accept)}"""


def _run_ai_scenario(vim: str, body: str) -> dict:
    """Run one scenario against a real Vim and return its `KEY=value` probe lines."""
    script = "let g:R = []\n" + body + "\ncall writefile(g:R, $PROBE_OUT)\nqa!\n"
    raw = _run_vim_script(
        vim, script, extra_source=VIM_AI_RC.read_text(encoding="utf-8")
    )
    got = {}
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        got[key] = value
    assert "TARGET_AFTER" in got, f"scenario did not reach the end: {raw!r}"
    return got


UNTOUCHED = str(TARGET_LINES)


class TestAcceptNeverDeletesTheSelection:
    """`y` must never hand the apply path a buffer that is no longer there.

    The response buffer is opened `bufhidden=wipe` (single/ollama) or
    `bufhidden=hide` (all), so closing the split with an ordinary `<C-w>c`,
    `:close` or `:q` -- rather than the plugin's own `q`, the only thing that
    sets `closed` -- destroys the buffer while the state dict keeps its number.
    Every write to that number then returns 1 (failure) and says nothing: no
    exception, no message, and `setbufvar` will not even resurrect it.

    Each finish callback must write `status = 'done'` only AFTER the render
    lands. Written first, a no-oped render leaves the tab claiming success, and
    `y` gates on `status ==# 'done'`. `getbufline()` on the dead number returns
    `[]`, which s:AI_Apply must refuse: left alone, `s:AI_SetLines` would take its
    `l:new < l:old` branch -- `deletebufline(target, start, end)` -- and the
    user's selected lines would be deleted with no replacement while the command
    line says 'Selection replaced.'

    Driven through a real Vim because not one step of that chain is visible in
    the source text: it is entirely about which of two writes happens first and
    what a silent return code means. The Neovim side pins the same ordering
    (TestFixFlowNeverAcceptsTheLoadingPlaceholder in test_nvim_ai_prompt.py);
    the two are independent ports.
    """

    @pytest.fixture(scope="class")
    def vim(self):
        binary = _real_vim()
        if binary is None:
            pytest.skip("no genuine Vim available (the `vim` on PATH may be Neovim)")
        return binary

    # ---- single mode ----------------------------------------------------
    def test_a_closed_response_split_does_not_delete_the_selection(self, vim):
        """The whole point: the user loses a window, never their text."""
        got = _run_ai_scenario(vim, _single_scenario())
        assert got["RESP_EXISTS"] == "0", "the scenario did not wipe the buffer"
        assert got["CLOSED_FLAG"] == "0", "an ordinary close must not set `closed`"
        assert got["TARGET_AFTER"] == UNTOUCHED, (
            "the selected lines were deleted with nothing put in their place"
        )

    def test_a_closed_response_split_is_not_reported_as_done(self, vim):
        """Asserting on the buffer alone would let the same hole reopen.

        `status` is what every accept path consults, so a 'done' written over a
        render that never landed is the defect itself -- the deletion is only
        its most expensive symptom.
        """
        got = _run_ai_scenario(vim, _single_scenario())
        assert got["STATUS"] != "done", got

    def test_an_empty_reply_stays_distinguishable_from_a_vanished_buffer(self, vim):
        """Both refuse to apply; they must not refuse for the same stated reason.

        A tool that ran fine and printed nothing is a failure of the tool. A
        response window the user closed is not a failure at all. Collapsing the
        two -- which any bare non-empty guard in s:AI_Apply would do -- leaves
        the reader of the status line unable to tell a broken tool from their
        own keypress.
        """
        vanished = _run_ai_scenario(vim, _single_scenario())
        empty = _run_ai_scenario(vim, _single_scenario(output=(), vanish=""))
        assert empty["STATUS"] == "failed", empty
        assert vanished["STATUS"] != empty["STATUS"], (vanished, empty)
        assert empty["TARGET_AFTER"] == UNTOUCHED, empty

    def test_a_healthy_single_response_is_still_applied(self, vim):
        """The regression guard. A fix that never writes 'done' passes everything
        above by breaking the feature outright."""
        got = _run_ai_scenario(vim, _single_scenario(vanish=""))
        assert got["FINISH_THREW"] == "", got
        assert got["STATUS"] == "done", got
        assert got["TARGET_AFTER"] == str(["T1", "R1", "R2", "T4", "T5"]), got

    def test_the_merged_accept_cannot_delete_the_selection_either(self, vim):
        """`Y` reads orig_buf, which is `bufhidden=wipe` for the same reasons.

        It has no status gate at all -- it hands whatever `getbufline` returns
        straight to s:AI_Apply -- so the guard that covers it has to live in
        s:AI_Apply, the one place all four accept paths pass through.
        """
        got = _run_ai_scenario(
            vim, _single_scenario(vanish="orig", accept="s:AI_SingleAcceptMerged")
        )
        assert got["TARGET_AFTER"] == UNTOUCHED, got

    # ---- ollama (shares the single-mode UI, accept and status machinery) --
    def test_a_wiped_ollama_response_does_not_delete_the_selection(self, vim):
        got = _run_ai_scenario(
            vim,
            _single_scenario(
                tool="gemma",
                output=('{"response": "R1\\nR2"}',),
                finish="s:AI_OllamaFinish",
            ),
        )
        assert got["STATUS"] != "done", got
        assert got["TARGET_AFTER"] == UNTOUCHED, got

    def test_a_healthy_ollama_response_is_still_applied(self, vim):
        got = _run_ai_scenario(
            vim,
            _single_scenario(
                tool="gemma",
                output=('{"response": "R1\\nR2"}',),
                finish="s:AI_OllamaFinish",
                vanish="",
            ),
        )
        assert got["STATUS"] == "done", got
        assert got["TARGET_AFTER"] == str(["T1", "R1", "R2", "T4", "T5"]), got

    # ---- all mode --------------------------------------------------------
    def test_a_wiped_all_mode_response_does_not_delete_the_selection(self, vim):
        got = _run_ai_scenario(vim, _all_scenario())
        assert got["RESP_EXISTS"] == "0", "the scenario did not wipe the buffer"
        assert got["STATUS"] != "done", got
        assert got["TARGET_AFTER"] == UNTOUCHED, got

    def test_a_wiped_all_mode_tab_is_not_painted_like_a_finished_one(self, vim):
        """all mode is the only place the mislabel is actually on screen.

        Wipe a tab that is NOT the one being displayed and the response window
        survives, so s:AI_AllStatus keeps painting the tab list. A status it has
        no marker for renders character-for-character like a finished tab, which
        is how the user is invited to press `y` on it in the first place.

        Compared against the healthy control rather than matched against a
        marker string, so this pins that the two are TOLD APART and not the
        particular word chosen to do it.
        """
        wiped = _run_ai_scenario(
            vim, _all_scenario(vanish="inactive", finish_idx=2, accept=None)
        )
        healthy = _run_ai_scenario(
            vim, _all_scenario(vanish="", finish_idx=2, accept=None)
        )
        assert wiped["RESP_EXISTS"] == "0", wiped
        assert healthy["TABLINE"] != "", "the response window died; nothing was painted"
        assert wiped["TABLINE"] != healthy["TABLINE"], (
            f"a tab whose buffer is gone is painted exactly like a finished one: "
            f"{wiped['TABLINE']!r}"
        )

    def test_a_healthy_all_mode_response_is_still_applied(self, vim):
        got = _run_ai_scenario(vim, _all_scenario(vanish=""))
        assert got["FINISH_THREW"] == "", got
        assert got["STATUS"] == "done", got
        assert got["TARGET_AFTER"] == str(["T1", "R1", "R2", "T4", "T5"]), got


def test_no_deprecated_vim_highlight_calls_in_the_lua_tree():
    """`vim.highlight` was deprecated in 0.11 and is scheduled for removal.

    The TextYankPost handler in setup/init.lua must call `vim.hl.on_yank`:
    `vim.highlight.on_yank` warns on every yank on 0.12 and will simply fail once
    the alias is removed. See tests/test_nvim_keymap_opts.py for the sibling
    deprecated-0.12 check.
    """
    offenders = sorted(
        str(path.relative_to(REPO_ROOT))
        for path in (REPO_ROOT / ".config/nvim").rglob("*.lua")
        if "vim.highlight." in path.read_text(encoding="utf-8")
    )
    assert offenders == [], f"deprecated vim.highlight used in: {offenders}"


# ---------------------------------------------------------------------------
# Neovim plugin specs, loaded for real under `nvim -l`.
#
# `nvim -l` runs a Lua script without the user's init.lua or any plugin manager
# (see tests/lua/nvim_call.lua), so each probe `require`s a spec module straight
# from the repo and stubs only the plugin modules the spec reaches into. Probes
# reduce everything to plain data before encoding: the spec tables hold
# functions, which vim.json cannot encode.
# ---------------------------------------------------------------------------
NVIM = shutil.which("nvim")
NVIM_LUA_ROOT = REPO_ROOT / ".config/nvim/lua"

_PROBE_PRELUDE = f"""
package.path = {str(NVIM_LUA_ROOT / "?.lua")!r} .. ";" .. package.path
local function emit(value)
  io.stdout:write(vim.json.encode(value), "\\n")
end
"""


def _nvim_probe(tmp_path, body: str) -> dict:
    if NVIM is None:
        pytest.skip("nvim not installed")
    probe = tmp_path / "probe.lua"
    probe.write_text(_PROBE_PRELUDE + body, encoding="utf-8")
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )
    # A crash is a broken probe, never a passing or failing assertion.
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_colorscheme_picker_registers_its_statusline_autocmd_once(tmp_path):
    """Each `<M-0>` press must not stack another ColorScheme autocmd.

    The handler keeps StatusLine transparent across colorscheme switches by
    hooking ColorScheme. Registering that hook on every press piles up one
    identical autocmd per use for the rest of the session. The hook is still
    registered by the handler itself, so three presses leave exactly one.
    """
    got = _nvim_probe(
        tmp_path,
        """
local picks = 0
package.loaded["telescope.builtin"] = {
  colorscheme = function() picks = picks + 1 end,
}
local spec = require("setup.plugins.utilities.telescope")
local handler
for _, key in ipairs(spec.keys) do
  if key[1] == "<M-0>" then handler = key[2] end
end
if not handler then
  emit({ found = false })
  return
end
local before = #vim.api.nvim_get_autocmds({ event = "ColorScheme" })
for _ = 1, 3 do handler() end
local after = #vim.api.nvim_get_autocmds({ event = "ColorScheme" })
vim.api.nvim_set_hl(0, "StatusLine", { fg = "#ff0000", blend = 50 })
vim.cmd("doautocmd ColorScheme")
local hl = vim.api.nvim_get_hl(0, { name = "StatusLine" })
-- nvim_get_hl omits `blend` once it is back to 0.
emit({ found = true, picks = picks, added = after - before, blend = hl.blend or 0 })
""",
    )
    assert got["found"], "telescope spec no longer maps <M-0>"
    assert got["picks"] == 3, got
    assert got["added"] == 1, f"{got['added']} ColorScheme autocmds after 3 presses"
    # The hook still does its job: a colorscheme change resets the blend.
    assert got["blend"] == 0, got


def test_trouble_lazy_loads_only_on_commands_it_defines(tmp_path):
    """trouble.nvim v2 defines a single user command, `:Trouble`.

    lazy.nvim turns every name in `cmd` into a stub command that loads the
    plugin and then re-runs the command, so a name the plugin does not define
    (the v1 `TroubleToggle` / `TroubleRefresh`) loads Trouble and then fails
    with "Not an editor command".
    """
    got = _nvim_probe(
        tmp_path,
        """
local cmd = require("setup.plugins.utilities.trouble").cmd
if type(cmd) == "string" then cmd = { cmd } end
emit({ cmd = cmd or {} })
""",
    )
    assert got["cmd"], "trouble spec no longer lazy-loads on any command"
    assert set(got["cmd"]) <= {"Trouble"}, got["cmd"]


def test_telescope_pickers_hold_only_option_tables(tmp_path):
    """Every `pickers` value is an option table for the picker it is keyed by.

    telescope looks options up as `pickers[<picker name>]`, so a bare option at
    this level (`pickers.show_all_buffers = true`) belongs to no picker and is
    silently ignored. Whether each key names a real picker is not checked:
    telescope itself is not installed where this suite runs.
    """
    got = _nvim_probe(
        tmp_path,
        """
local captured
package.loaded["telescope"] = {
  setup = function(opts) captured = opts end,
  load_extension = function() end,
}
require("setup.plugins.utilities.telescope").config()
local names, bad = {}, {}
for name, value in pairs((captured or {}).pickers or {}) do
  table.insert(names, name)
  if type(value) ~= "table" then table.insert(bad, name) end
end
table.sort(names)
table.sort(bad)
emit({ names = names, bad = bad })
""",
    )
    assert got["names"], "telescope.setup no longer receives any pickers"
    assert got["bad"] == [], f"not picker option tables: {got['bad']}"


def test_eager_plugin_specs_declare_no_lazy_load_triggers(tmp_path):
    """A `lazy = false` spec must not also declare `cmd` / `event` / `ft`.

    Those keys only tell lazy.nvim when to load a lazy plugin; on a plugin that
    loads at startup they do nothing and misstate how it is loaded. `keys` is
    exempt: lazy.nvim still creates those mappings for an eager plugin.
    """
    got = _nvim_probe(
        tmp_path,
        f"""
local root = {str(NVIM_LUA_ROOT)!r} .. "/"
local files = vim.fs.find(function(name) return name:match("%.lua$") end, {{
  path = root .. "setup/plugins", type = "file", limit = math.huge,
}})
table.sort(files)
local eager, offenders = 0, {{}}
local function check(spec, where)
  if type(spec) ~= "table" then return end
  -- A file may return a list of specs (nvim-lspconfig.lua does).
  if type(spec[1]) == "table" then
    for i, item in ipairs(spec) do check(item, where .. "[" .. i .. "]") end
    return
  end
  if spec.lazy == false then
    eager = eager + 1
    for _, key in ipairs({{ "cmd", "event", "ft" }}) do
      if spec[key] ~= nil then table.insert(offenders, where .. ": " .. key) end
    end
  end
  local deps = spec.dependencies or {{}}
  if type(deps) ~= "table" then deps = {{ deps }} end
  for _, dep in ipairs(deps) do
    check(dep, where .. " > " .. tostring(type(dep) == "table" and dep[1] or dep))
  end
end
for _, file in ipairs(files) do
  -- Plain prefix cut, not gsub: the path may hold Lua pattern characters.
  local module = file:sub(#root + 1):gsub("%.lua$", ""):gsub("/", ".")
  check(require(module), module)
end
emit({{ eager = eager, offenders = offenders }})
""",
    )
    assert got["eager"] > 0, "no `lazy = false` spec found -- did the probe break?"
    assert got["offenders"] == [], got["offenders"]


def test_mapleader_is_assigned_once_before_lazy_setup():
    """`vim.g.mapleader` is set in lazy.lua only, ahead of `lazy.setup`.

    lazy.nvim expands `<leader>` in spec `keys` when lazy.setup runs, and
    vim.keymap.set expands it when each mapping is made. A second assignment
    elsewhere is a second place to edit: change one copy and plugin keys and the
    remaining keymaps end up on different leaders. remap.lua relies on
    setup/init.lua loading lazy.lua first.
    """
    assignment = re.compile(r"\bvim\.g\.mapleader\s*=(?!=)")
    sites = [
        f"{path.relative_to(REPO_ROOT)}:{n}"
        for path in sorted((REPO_ROOT / ".config/nvim").rglob("*.lua"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("--") and assignment.search(line)
    ]
    lazy_lua = ".config/nvim/lua/setup/lazy.lua"
    assert len(sites) == 1 and sites[0].startswith(lazy_lua + ":"), sites

    lines = (REPO_ROOT / lazy_lua).read_text(encoding="utf-8").splitlines()
    setup_line = next(
        (n for n, line in enumerate(lines, 1) if "lazy.setup(" in line), None
    )
    assert setup_line, "lazy.lua no longer calls lazy.setup"
    assert int(sites[0].rsplit(":", 1)[1]) < setup_line

    init_lua = (REPO_ROOT / ".config/nvim/lua/setup/init.lua").read_text(
        encoding="utf-8"
    )
    lazy_at = init_lua.find('require("setup.lazy")')
    remap_at = init_lua.find('require("setup.remap")')
    assert 0 <= lazy_at < remap_at, "setup/init.lua must load lazy.lua before remap.lua"
