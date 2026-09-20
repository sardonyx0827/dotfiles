"""The close-buffer mapping in `.config/nvim/lua/setup/functions/ai/init.lua`.

`ai/init.lua` is the entry point that wires the AI features to keys; its
siblings each already have a file here (test_nvim_ai_{backend,context,prompt}).
This is the gap, and it opens on the one mapping in that file that can destroy
work the user has not written yet: `close_current_buffer`, bound to both
`<C-q>` and `<leader>bc`.

It used to call `nvim_buf_delete(buf, { force = true })`, i.e. `:bd!`. On a
modified buffer that discards the edits *and* the undo history with no prompt,
no error and no notification -- one stray `<C-q>` next to `<C-w>` and the work
is gone with nothing to undo it back from. The tell was the asymmetry: the
function checked `nvim_buf_is_loaded` and not `modified`, which reads as an
omission rather than a decision.

Mechanism: the callback actually installed on the key, looked up with `maparg`
and invoked, under `nvim -l` -- the shape test_nvim_file_functions.py uses, and
for the same reason (feedkeys would swallow an error raised inside it). `-l`
does not source the user's init.lua, so the mapping under test is the only one
installed; see tests/lua/nvim_call.lua for the full argument. The scenario is
written to tmp_path rather than tests/lua/ so the fixture buffer's *name* can
live under tmp_path too: nothing here may touch the user's real files, swap
files or sessions.

Both keys are exercised separately rather than asserting they share a function
value. They are meant to stay interchangeable, and running the scenario twice
says so without depending on whether `maparg` hands back the same reference.
"""

import json
import os
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

NVIM = shutil.which("nvim")
pytestmark = pytest.mark.skipif(NVIM is None, reason="nvim not installed")

CLOSE_KEYS = ("<C-q>", "<leader>bc")

SCENARIO = r"""
-- Put one buffer in a known state, press the close key, report what survived.
-- Request on stdin: { "key": "<C-q>", "modified": true, "path": "..." }

-- Nothing in this run may leave a .swp beside the fixture path.
vim.o.swapfile = false

package.path = table.concat({
  "__REPO__/.config/nvim/lua/?.lua",
  "__REPO__/.config/nvim/lua/?/init.lua",
  package.path,
}, ";")

-- The lhs is expanded against mapleader when the map is registered, which is at
-- require time -- so this has to come first or `<leader>bc` lands on a
-- different key than the one the test asks maparg for.
vim.g.mapleader = ","

-- vim.notify is the only channel the refusal has to reach the user, so capture
-- it instead of letting it drain into the message area where nothing can read
-- it back.
local notified = {}
vim.notify = function(msg, _level)
  notified[#notified + 1] = tostring(msg)
end

require("setup.functions.ai")

local req = vim.json.decode(io.read("*a"))

local buf = vim.api.nvim_create_buf(true, false)
vim.api.nvim_set_current_buf(buf)
if req.kind == "terminal" then
  -- A terminal whose job is still running is the case that a blanket
  -- `force = false` would have broken: `:bd` refuses it (E89 "will be
  -- killed") even though 'modified' is false and there is no text to lose.
  vim.fn.jobstart({ "sleep", "30" }, { term = true })
  vim.wait(300)
else
  vim.api.nvim_buf_set_name(buf, req.path)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "keep me" })
  if not req.modified then
    vim.bo[buf].modified = false
  end
end

-- Emitted so the Python side can reject a vacuous pass. close_current_buffer
-- acts on the *current* buffer and returns early on an unloaded one: a fixture
-- that was neither would survive `force = true` for reasons that have nothing
-- to do with the guard under test, and the whole file would go green against
-- the bug.
local pre = {
  current = buf == vim.api.nvim_get_current_buf(),
  loaded = vim.api.nvim_buf_is_loaded(buf),
  modified = vim.bo[buf].modified,
  buftype = vim.bo[buf].buftype,
}

local m = vim.fn.maparg(req.key, "n", false, true)
assert(m and m.callback, "no mapping for " .. req.key)
local ok, err = pcall(m.callback)

-- A deleted buffer is an invalid one, so the lines only exist on the survival
-- path. Read them anyway where they do: "still valid" alone would also be true
-- of a buffer that survived because the callback threw before touching it.
local valid = vim.api.nvim_buf_is_valid(buf)
io.stdout:write(vim.json.encode({
  pre = pre,
  ok = ok,
  err = ok and vim.NIL or tostring(err),
  valid_after = valid,
  lines_after = valid and vim.api.nvim_buf_get_lines(buf, 0, -1, false) or vim.NIL,
  -- Joined rather than an array: vim.json.encode renders an empty Lua table as
  -- `{}`, and "nothing was notified" is the expected outcome on the happy path.
  notified = table.concat(notified, "\n"),
}), "\n")
"""


def press_close(tmp_path, key, *, modified, kind="file"):
    """Press `key` on a fixture buffer and return the scenario's reply."""
    script = tmp_path / "close_buffer_scenario.lua"
    script.write_text(SCENARIO.replace("__REPO__", str(REPO_ROOT)), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    request = {
        "key": key,
        "modified": modified,
        "kind": kind,
        # Named but never created: the buffer needs a path to be an ordinary
        # file buffer rather than a scratch one, and it must not be a real file
        # of the user's.
        "path": str(tmp_path / "doomed.txt"),
    }
    proc = subprocess.run(
        [NVIM, "-l", str(script)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
        # PATH is emptied so nothing the mapping might shell out to can be
        # picked up from the host -- except for the terminal fixture, which
        # needs a real `sleep` to have a job to keep running.
        env={
            "PATH": os.defpath if kind == "terminal" else "",
            "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    # E89 and friends go to stderr under `-l`, but pick the reply defensively
    # rather than trusting stdout to hold nothing else.
    replies = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert replies, f"scenario emitted no reply\nstderr: {proc.stderr}"
    res = json.loads(replies[-1])
    # The premise of every assertion below. Checked here so a fixture that
    # drifted out of shape fails as a broken test, not as a passing one.
    expected_buftype = "terminal" if kind == "terminal" else ""
    assert res["pre"] == {
        "current": True,
        "loaded": True,
        "modified": modified,
        "buftype": expected_buftype,
    }, f"fixture buffer was not in the asserted state: {res['pre']}"
    return res


@pytest.mark.parametrize("key", CLOSE_KEYS)
def test_a_modified_buffer_is_not_discarded(tmp_path, key):
    """The regression: `force = true` wiped unsaved edits without asking.

    Survival is asserted through the contents, not just validity -- a callback
    that threw before doing anything would leave a valid buffer too.
    """
    res = press_close(tmp_path, key, modified=True)
    assert res["valid_after"], "the modified buffer was deleted"
    assert res["lines_after"] == ["keep me"]


@pytest.mark.parametrize("key", CLOSE_KEYS)
def test_an_unmodified_buffer_still_closes(tmp_path, key):
    """The other half, and the harness canary.

    Refusing everything would satisfy the test above, and a scenario that never
    reached the mapping would too. This is the case that fails if either
    happens.
    """
    res = press_close(tmp_path, key, modified=False)
    assert res["ok"], res["err"]
    assert not res["valid_after"], "an unmodified buffer should still close"


@pytest.mark.parametrize("key", CLOSE_KEYS)
def test_the_refusal_says_how_to_override(tmp_path, key):
    """A silent refusal is the same surprise as a silent discard, inverted.

    Only the override hint is pinned, not the prose: the user has to be able to
    get out of the refusal deliberately.
    """
    res = press_close(tmp_path, key, modified=True)
    assert "bd!" in res["notified"], f"no override hint: {res['notified']!r}"


@pytest.mark.parametrize("key", CLOSE_KEYS)
def test_a_running_terminal_still_closes(tmp_path, key):
    """The cost the fix must not pay.

    Dropping force outright also refuses a terminal whose job is alive -- `:bd`
    reports E89 "will be killed" there, with 'modified' false and no text at
    stake -- which would have stopped <C-q> closing a toggleterm window. Keying
    force on 'modified' keeps that working, and this pins it.
    """
    res = press_close(tmp_path, key, modified=False, kind="terminal")
    assert res["ok"], res["err"]
    assert not res["valid_after"], "a running terminal should still close"
