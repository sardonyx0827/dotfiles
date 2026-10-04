"""lazy.lua's plugin discovery must survive a config path with Lua magic chars.

The spec loader maps each file path to a module name by stripping
`stdpath("config") .. "/lua/"`. Done with `string.gsub` that prefix is read as a
PATTERN: a `-` in the path (`/Users/jane-doe/.config`, a worktree under
`dotfiles-main`) is a lazy quantifier, the prefix never matches, every `require`
fails, and the editor starts with zero plugins and a "skipped plugin spec"
notification per file. Not visible on the primary checkout, whose path happens
to contain no magic characters. See the comment in lazy.lua's file scan.

Driven through the real lazy.lua with lazy.nvim itself stubbed out: the file
bootstraps lazy.nvim from git when it is absent, so the probe pre-creates the
directory it checks and hands it a `lazy` module that only records what it
was given.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

NVIM = shutil.which("nvim")
pytestmark = pytest.mark.skipif(NVIM is None, reason="nvim not installed")

PLUGIN_DIR = REPO_ROOT / ".config/nvim/lua/setup/plugins"

PROBE = r"""
local handed = nil
package.preload["lazy"] = function()
  return { setup = function(plugins, _opts) handed = plugins end }
end
local skipped = {}
vim.notify = function(msg, _level) table.insert(skipped, msg) end
dofile(vim.fn.stdpath("config") .. "/lua/setup/lazy.lua")
io.stdout:write(vim.json.encode({
  loaded = handed and #handed or -1,
  skipped = skipped,
}), "\n")
"""


def run_loader(tmp_path, xdg_dirname):
    xdg = tmp_path / xdg_dirname
    xdg.mkdir()
    (xdg / "nvim").symlink_to(REPO_ROOT / ".config/nvim")
    data = tmp_path / "data"
    # lazy.lua clones lazy.nvim unless this directory exists; an empty PATH
    # below makes sure the clone could not run even if the check regressed.
    (data / "nvim/lazy/lazy.nvim").mkdir(parents=True)
    binroot = tmp_path / "bin"
    binroot.mkdir()
    probe = tmp_path / "probe.lua"
    probe.write_text(PROBE, encoding="utf-8")
    proc = subprocess.run(
        [NVIM, "-l", str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(binroot),
            "HOME": str(tmp_path),
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_DATA_HOME": str(data),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def spec_file_count() -> int:
    return sum(1 for _ in PLUGIN_DIR.rglob("*.lua"))


@pytest.mark.parametrize("xdg_dirname", ["plain", "with-dash", "dots.and.more-dash"])
def test_every_spec_is_handed_to_lazy_whatever_the_config_path(tmp_path, xdg_dirname):
    res = run_loader(tmp_path, xdg_dirname)
    assert res["skipped"] == [], "specs were skipped: " + "; ".join(res["skipped"])
    assert res["loaded"] == spec_file_count()
