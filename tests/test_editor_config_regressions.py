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
