"""Edge cases of the codeblock helpers in `.config/nvim/lua/setup/functions/file.lua`.

The mappings are exercised through the callbacks actually installed on
`,,n` / `,,p` / `,,s` (mapleader is `,`), looked up with maparg so an error
raised inside one is observable -- feedkeys would swallow it.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

NVIM = shutil.which("nvim")
pytestmark = pytest.mark.skipif(NVIM is None, reason="nvim not installed")

PROBE = r"""
vim.g.mapleader = ","
dofile("__REPO__/.config/nvim/lua/setup/functions/file.lua")
local req = vim.json.decode(io.read("*a"))
vim.api.nvim_buf_set_lines(0, 0, -1, false, req.lines)
vim.api.nvim_win_set_cursor(0, { req.cursor, 0 })
local m = vim.fn.maparg(req.key, "n", false, true)
assert(m and m.callback, "no mapping for " .. req.key)
local ok, err = pcall(m.callback)
io.stdout:write(vim.json.encode({
  ok = ok,
  err = ok and vim.NIL or tostring(err),
  cursor = vim.api.nvim_win_get_cursor(0)[1],
  mode = vim.api.nvim_get_mode().mode,
}), "\n")
"""


def press(tmp_path, key, lines, cursor):
    probe = tmp_path / "probe.lua"
    probe.write_text(PROBE.replace("__REPO__", str(REPO_ROOT)), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        input=json.dumps({"key": key, "lines": lines, "cursor": cursor}),
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "", "HOME": str(home), "XDG_DATA_HOME": str(home / "data")},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestMoveToCodeblock:
    def test_an_unclosed_fence_on_the_last_line_does_not_throw(self, tmp_path):
        # `between_line = i + 1` is one past the end while the fence is still
        # being typed; nvim_win_set_cursor rejected it with "out of range".
        res = press(tmp_path, ",,n", ["text", "```lua"], cursor=1)
        assert res["ok"], res["err"]
        assert 1 <= res["cursor"] <= 2

    def test_a_closing_fence_on_the_first_line_does_not_throw(self, tmp_path):
        # The mirror image: searching backwards lands on line 0.
        res = press(tmp_path, ",,p", ["```", "text"], cursor=1)
        assert res["ok"], res["err"]
        assert res["cursor"] >= 1

    def test_the_happy_path_still_lands_inside_the_block(self, tmp_path):
        res = press(tmp_path, ",,n", ["a", "```lua", "print(1)", "```"], cursor=1)
        assert res["ok"], res["err"]
        assert res["cursor"] == 3


class TestSelectCodeblockText:
    def test_a_cursor_on_the_fence_line_does_not_leave_visual_mode_behind(
        self, tmp_path
    ):
        # Both scans start at the cursor line, so a cursor ON the fence matches
        # it twice: start = 2, end = 0, the guard passed, `normal! V` ran, and
        # set_cursor(0) threw mid-selection.
        res = press(tmp_path, ",,s", ["```lua", "print(1)", "```"], cursor=1)
        assert res["ok"], res["err"]
        assert res["mode"] == "n"
        assert res["cursor"] == 1

    def test_inside_a_block_the_body_is_selected(self, tmp_path):
        res = press(tmp_path, ",,s", ["```lua", "a", "b", "```"], cursor=2)
        assert res["ok"], res["err"]
        assert res["mode"] == "V"
        assert res["cursor"] == 3
