"""The close-buffer mapping in `.config/nvim/lua/setup/functions/ai/init.lua`.

`ai/init.lua` is the entry point that wires the AI features to keys; its
siblings each have a file here (test_nvim_ai_{backend,context,prompt}). This
one covers the mapping in that file that can destroy work the user has not
written yet: `close_current_buffer`, bound to both `<C-q>` and `<leader>bc`.

`nvim_buf_delete(buf, { force = true })`, i.e. `:bd!`, on a modified buffer
discards the edits *and* the undo history with no prompt, no error and no
notification -- one stray `<C-q>` next to `<C-w>` and the work is gone with
nothing to undo it back from. So a modified buffer must be refused, not forced.

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
    """A modified buffer survives the key: `force = true` would wipe unsaved edits.

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
    """Refusing a modified buffer must not also refuse a live terminal.

    Dropping force outright would refuse a terminal whose job is alive -- `:bd`
    reports E89 "will be killed" there, with 'modified' false and no text at
    stake -- and stop <C-q> closing a toggleterm window. Keying force on
    'modified' keeps that working, and this pins it.
    """
    res = press_close(tmp_path, key, modified=False, kind="terminal")
    assert res["ok"], res["err"]
    assert not res["valid_after"], "a running terminal should still close"


# --------------------------------------------------------------------------
# Sensitive source paths reach the send gate from every feature.
#
# backend.lua asks before a payload read from a sensitive path goes out
# (tests/test_nvim_ai_backend.py pins that, with specs written by hand). What
# only THIS file can pin is that each feature actually hands its source over:
# a `source_paths` that one entry point forgets to pass is a silent hole, and
# every backend test would stay green around it.
#
# Each case presses the real key (the callback `maparg` finds), with confirm()
# stubbed and every external CLI replaced on a sealed PATH by a stub that only
# records that it ran. "Sent" is therefore observed, not inferred: the marker
# file either has the tool's name in it or it does not.
# --------------------------------------------------------------------------

AI_SCENARIO = r"""
-- Press one AI key against a fixture and report what the gate and the tools saw.
-- Request on stdin: see run_ai_key() on the Python side.
vim.o.swapfile = false
package.path = table.concat({
  "__REPO__/.config/nvim/lua/?.lua",
  "__REPO__/.config/nvim/lua/?/init.lua",
  package.path,
}, ";")
vim.g.mapleader = ","

local req = vim.json.decode(io.read("*a"))

local confirms = {}
vim.fn.confirm = function(msg, choices, default)
  confirms[#confirms + 1] = { msg = msg, choices = choices, default = default }
  local answer = req.answers[#confirms]
  if answer == nil then return default end
  return answer
end
vim.notify = function() end

require("setup.functions.ai")

local function press(mode, key)
  local m = vim.fn.maparg(key, mode, false, true)
  assert(m and m.callback, "no " .. mode .. "-mode mapping for " .. key)
  m.callback()
end

local function marker_tools()
  local f = io.open(req.marker, "r")
  if not f then return {} end
  local out = {}
  for line in f:lines() do out[#out + 1] = line end
  f:close()
  return out
end

if req.kind == "commit" then
  vim.cmd("cd " .. vim.fn.fnameescape(req.repo))
  press("n", req.key)
else
  local buf = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_buf_set_name(buf, req.path)
  vim.bo[buf].buftype = ""
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, req.lines)
  vim.bo[buf].filetype = req.filetype or ""
  vim.bo[buf].modified = false
  if req.kind == "check" then
    press("n", "<leader>qf")
  elseif req.kind == "hint" then
    vim.api.nvim_win_set_cursor(0, { req.cursor, 0 })
    press("n", req.key)
  elseif req.kind == "replace" then
    vim.api.nvim_buf_set_mark(buf, "<", 1, 0, {})
    vim.api.nvim_buf_set_mark(buf, ">", #req.lines, 0, {})
    press("x", req.key)
    -- The selection text was captured when the prompt opened; whatever happens
    -- to the buffer now must not change which path that text is attributed to.
    if req.between == "rename" then
      vim.api.nvim_buf_set_name(buf, req.rename_to)
    elseif req.between == "wipe" then
      -- bwipeout, not nvim_buf_delete: :bdelete leaves an unlisted buffer that
      -- still has its name; a wiped one is gone and its number is invalid. The
      -- window showing it is pointed at another buffer first -- a displayed
      -- buffer is silently left alone while a float holds the focus.
      local win = vim.fn.win_findbuf(buf)[1]
      vim.api.nvim_win_call(win, function() vim.cmd("enew") end)
      vim.cmd("bwipeout! " .. buf)
      assert(not vim.api.nvim_buf_is_valid(buf), "the fixture buffer was not wiped")
    end
    -- The prompt window is now current; type the instruction and submit.
    vim.api.nvim_buf_set_lines(0, 0, -1, false, { "rewrite this" })
    press("n", "<C-s>")
  end
end

-- Let any job the gate allowed run to completion: poll until the expected
-- number of stubs have reported, however long a loaded machine takes. When none
-- is expected there is nothing to wait FOR, so give a wrongly-started one a
-- fixed grace period to show up instead.
if req.expect > 0 then
  vim.wait(10000, function() return #marker_tools() >= req.expect end, 25)
else
  vim.wait(600, function() return false end, 25)
end

io.stdout:write(vim.json.encode({
  confirms = #confirms == 0 and vim.empty_dict() or confirms,
  ran = marker_tools(),
}), "\n")
"""


def make_ai_bin(tmp_path):
    """A sealed PATH whose AI CLIs are recording stubs; nothing real is reachable."""
    binroot = tmp_path / "aibin"
    binroot.mkdir(exist_ok=True)
    for tool in ("sh", "cat", "git", "python3"):
        real = shutil.which(tool)
        assert real, f"{tool} is required by this test but is not installed"
        (binroot / tool).write_text(
            f'#!/bin/sh\nexec "{real}" "$@"\n', encoding="utf-8"
        )
    for tool in ("claude", "codex", "copilot"):
        # claude and codex get the payload on stdin and drain it. copilot takes
        # it in argv and never reads stdin, so a stub that did would block on the
        # job's open stdin pipe and never report.
        drain = "" if tool == "copilot" else "cat >/dev/null\n"
        (binroot / tool).write_text(
            f'#!/bin/sh\n{drain}echo {tool} >> "$AI_MARKER"\necho stub-reply\n',
            encoding="utf-8",
        )
    for entry in binroot.iterdir():
        entry.chmod(0o755)
    return binroot


def make_git_repo(tmp_path, *, staged=(), committed=(), modified=(), config=()):
    """A throwaway repo. `committed` files are committed, `modified` ones are then
    changed without staging, `staged` ones are new files added to the index.
    `config` is (key, value) pairs written to the repo's LOCAL git config -- the
    user's colour and external-diff settings, which `git diff` honours."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }

    def git(*args):
        subprocess.run(  # noqa: S603
            ["git", *args], cwd=repo, env=env, check=True, capture_output=True
        )

    git("init", "-q")
    for key, value in config:
        git("config", "--local", key, value)
    for name in committed:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("DB_HOST=db.internal\n", encoding="utf-8")
    if committed:
        git("add", "--", *committed)
        git("commit", "-q", "-m", "init")
    for name in modified:
        (repo / name).write_text(
            "DB_HOST=db.internal\nDB_PORT=5432\n", encoding="utf-8"
        )
    for name in staged:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text("DB_HOST=db.internal\n", encoding="utf-8")
    if staged:
        git("add", "--", *staged)
    return repo


def run_ai_key(tmp_path, request, *, answers=(), expect=0):
    """Run AI_SCENARIO for `request`; return {"confirms": [...], "ran": [...]}.

    `expect` is how many stub tools should have run once the gate lets the
    request through (0 when it should not have gone out at all).
    """
    script = tmp_path / "ai_key_scenario.lua"
    script.write_text(AI_SCENARIO.replace("__REPO__", str(REPO_ROOT)), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    xdg = tmp_path / "xdg"
    xdg.mkdir(exist_ok=True)
    if not (xdg / "nvim").exists():
        # scan_payload finds scripts/secret_scan.py through this symlink, the way
        # it does through the real ~/.config/nvim.
        (xdg / "nvim").symlink_to(REPO_ROOT / ".config/nvim")
    marker = tmp_path / "ran.txt"
    request = {
        **request,
        "answers": list(answers),
        "marker": str(marker),
        "expect": expect,
    }
    proc = subprocess.run(  # noqa: S603
        [NVIM, "-l", str(script)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": str(make_ai_bin(tmp_path)),
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "AI_MARKER": str(marker),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    assert proc.returncode == 0, proc.stderr
    replies = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert replies, (
        f"scenario emitted no reply\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    res = json.loads(replies[-1])
    for key in ("confirms", "ran"):
        if res[key] == {}:
            res[key] = []
    return res


class TestEveryFeaturePassesItsSourceToTheGate:
    """One test pair per feature: the sensitive file asks, the ordinary one does not."""

    def buffer_request(self, tmp_path, kind, name, **extra):
        return {
            "kind": kind,
            "path": str(tmp_path / name),
            "lines": ["DB_HOST=db.internal", "DB_PORT=5432"],
            **extra,
        }

    # -- proofreading (<leader>qf) ------------------------------------------

    def test_proofreading_a_sensitive_buffer_asks_and_no_sends_nothing(self, tmp_path):
        res = run_ai_key(tmp_path, self.buffer_request(tmp_path, "check", ".env"))
        assert len(res["confirms"]) == 1
        assert res["confirms"][0]["choices"] == "&No\n&Yes"
        assert res["confirms"][0]["default"] == 1
        assert str(tmp_path / ".env") in res["confirms"][0]["msg"]
        assert res["ran"] == [], "the buffer was sent without an answer"

    def test_proofreading_a_sensitive_buffer_sends_after_yes(self, tmp_path):
        res = run_ai_key(
            tmp_path,
            self.buffer_request(tmp_path, "check", ".env"),
            answers=[2],
            expect=1,
        )
        assert len(res["confirms"]) == 1
        assert res["ran"] == ["claude"]

    def test_proofreading_an_ordinary_buffer_asks_nothing(self, tmp_path):
        res = run_ai_key(
            tmp_path, self.buffer_request(tmp_path, "check", "notes.txt"), expect=1
        )
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    # -- cursor hint (<leader>qh) -------------------------------------------

    def hint_request(self, tmp_path, name):
        return self.buffer_request(
            tmp_path,
            "hint",
            name,
            key="<leader>qh",
            filetype="lua",
            lines=["-- Adds one.", "local function add(x)", "  return x + 1", "end"],
            cursor=2,
        )

    def test_a_hint_in_a_sensitive_buffer_asks_and_no_sends_nothing(self, tmp_path):
        res = run_ai_key(tmp_path, self.hint_request(tmp_path, "kubeconfig"))
        assert len(res["confirms"]) == 1
        assert res["ran"] == []

    def test_a_hint_in_an_ordinary_buffer_asks_nothing(self, tmp_path):
        res = run_ai_key(tmp_path, self.hint_request(tmp_path, "add.lua"), expect=1)
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    # -- selection rewrite (<C-c> / <C-l>) ----------------------------------

    def replace_request(self, tmp_path, name, key="<C-c>"):
        return self.buffer_request(tmp_path, "replace", name, key=key)

    def test_rewriting_a_selection_of_a_sensitive_buffer_asks_and_no_sends_nothing(
        self, tmp_path
    ):
        res = run_ai_key(tmp_path, self.replace_request(tmp_path, "prod.tfvars"))
        assert len(res["confirms"]) == 1
        assert res["ran"] == []

    def test_rewriting_a_selection_of_an_ordinary_buffer_asks_nothing(self, tmp_path):
        res = run_ai_key(tmp_path, self.replace_request(tmp_path, "main.lua"), expect=1)
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    def test_all_mode_rewrite_asks_once_for_four_tools(self, tmp_path):
        res = run_ai_key(
            tmp_path, self.replace_request(tmp_path, "prod.tfvars", key="<C-l>")
        )
        assert len(res["confirms"]) == 1, "one dialog per request, not per tool"
        assert res["ran"] == []

    def test_all_mode_rewrite_yes_runs_the_cli_tools_after_one_dialog(self, tmp_path):
        res = run_ai_key(
            tmp_path,
            self.replace_request(tmp_path, "prod.tfvars", key="<C-l>"),
            answers=[2],
            expect=3,
        )
        assert len(res["confirms"]) == 1
        # gemini has no stub here (it needs a key and the network) and fails on
        # its own; the three CLI tools all started.
        assert sorted(res["ran"]) == ["claude", "codex", "copilot"]

    def test_local_ollama_rewrite_is_exempt(self, tmp_path):
        res = run_ai_key(
            tmp_path, self.replace_request(tmp_path, "prod.tfvars", key="<C-o>")
        )
        assert res["confirms"] == []

    # -- commit message (<leader>cm / <leader>cl / <leader>co) ---------------

    def commit_request(self, repo, key="<leader>cm"):
        return {"kind": "commit", "repo": str(repo), "key": key}

    def test_a_staged_sensitive_file_name_asks_and_no_sends_nothing(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["kubeconfig"])
        res = run_ai_key(tmp_path, self.commit_request(repo))
        assert len(res["confirms"]) == 1
        assert "kubeconfig" in res["confirms"][0]["msg"]
        assert res["ran"] == []

    def test_a_staged_sensitive_file_is_sent_after_yes(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["kubeconfig"])
        res = run_ai_key(tmp_path, self.commit_request(repo), answers=[2], expect=1)
        assert res["ran"] == ["claude"]

    def test_a_staged_ordinary_file_asks_nothing(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["main.lua"])
        res = run_ai_key(tmp_path, self.commit_request(repo), expect=1)
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    def test_the_unstaged_fallback_diff_is_checked_too(self, tmp_path):
        """Nothing staged: the code sends the unstaged diff, so that is what counts."""
        repo = make_git_repo(tmp_path, committed=[".env"], modified=[".env"])
        res = run_ai_key(tmp_path, self.commit_request(repo))
        assert len(res["confirms"]) == 1
        assert res["ran"] == []

    def test_only_the_diff_that_is_sent_is_checked(self, tmp_path):
        """A sensitive file that is NOT in the staged diff must not raise a dialog.

        The paths come from the payload, not from the working tree: an unstaged
        `.env` change is not part of what gets sent while something else is staged.
        """
        repo = make_git_repo(
            tmp_path, committed=[".env"], modified=[".env"], staged=["main.lua"]
        )
        res = run_ai_key(tmp_path, self.commit_request(repo), expect=1)
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    def test_all_mode_commit_asks_once_for_three_tools(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["kubeconfig"])
        res = run_ai_key(tmp_path, self.commit_request(repo, "<leader>cl"))
        assert len(res["confirms"]) == 1, "one dialog per request, not per tool"
        assert res["ran"] == []

    def test_all_mode_commit_yes_runs_every_tool_after_one_dialog(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["kubeconfig"])
        res = run_ai_key(
            tmp_path, self.commit_request(repo, "<leader>cl"), answers=[2], expect=3
        )
        assert len(res["confirms"]) == 1
        assert sorted(res["ran"]) == ["claude", "codex", "copilot"]

    def test_commit_message_with_local_ollama_is_exempt(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["kubeconfig"])
        res = run_ai_key(tmp_path, self.commit_request(repo, "<leader>co"))
        assert res["confirms"] == []

    def test_a_sensitive_file_past_the_truncation_point_still_asks(self, tmp_path):
        """A large diff is cut to a stat plus a partial patch; the check is not.

        The names are read from the full diff the same git call returned, so a
        credentials file that sorts after the cut still raises the dialog even
        though only its line in the stat summary goes out.
        """
        repo = make_git_repo(tmp_path, staged=["a_big.txt", "z.tfvars"])
        (repo / "a_big.txt").write_text("x\n" * 40_000, encoding="utf-8")
        subprocess.run(  # noqa: S603
            ["git", "add", "a_big.txt"],
            cwd=repo,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull},
        )
        res = run_ai_key(tmp_path, self.commit_request(repo))
        assert len(res["confirms"]) == 1
        assert "z.tfvars" in res["confirms"][0]["msg"]

    # -- repo-relative names ---------------------------------------------------

    @pytest.mark.parametrize(
        "name", [".kube/config", ".ssh/config", ".aws/config", "gcloud/x.json"]
    )
    def test_a_staged_file_in_a_credentials_directory_at_the_repo_root_asks(
        self, tmp_path, name
    ):
        """A diff names `.kube/config`, not `/.kube/config`: no leading slash to match."""
        repo = make_git_repo(tmp_path, staged=[name])
        res = run_ai_key(tmp_path, self.commit_request(repo))
        assert len(res["confirms"]) == 1, "the file went out with no dialog"
        assert name in res["confirms"][0]["msg"]
        assert res["ran"] == []

    def test_a_staged_notes_file_that_merely_mentions_kube_asks_nothing(self, tmp_path):
        repo = make_git_repo(tmp_path, staged=["docs/kube-notes.md"])
        res = run_ai_key(tmp_path, self.commit_request(repo), expect=1)
        assert res["confirms"] == []
        assert res["ran"] == ["claude"]

    # -- the user's git configuration must not change what is parsed -----------

    @pytest.mark.parametrize(
        "setting", ["color.ui=always", "color.diff=always", "diff.external=<script>"]
    )
    def test_the_users_diff_settings_do_not_hide_the_file_names(
        self, tmp_path, setting
    ):
        """`color.*=always` puts SGR codes on every header; `diff.external` replaces
        the diff text altogether. Either one used to make the parser see no file
        names, and the commit went out without a dialog."""
        key, value = setting.split("=")
        if value == "<script>":
            script = tmp_path / "extdiff.sh"
            script.write_text(
                "#!/bin/sh\necho EXTERNAL-DIFF-OUTPUT\n", encoding="utf-8"
            )
            script.chmod(0o755)
            value = str(script)
        repo = make_git_repo(tmp_path, staged=["kubeconfig"], config=[(key, value)])
        res = run_ai_key(tmp_path, self.commit_request(repo))
        assert len(res["confirms"]) == 1, "a local git setting defeated the check"
        assert res["ran"] == []

    def test_a_coloured_large_diff_still_asks_about_a_file_past_the_cut(self, tmp_path):
        """The `--stat` call is configured the same way as the diff itself."""
        repo = make_git_repo(
            tmp_path, staged=["a_big.txt", "z.tfvars"], config=[("color.ui", "always")]
        )
        (repo / "a_big.txt").write_text("x\n" * 40_000, encoding="utf-8")
        subprocess.run(  # noqa: S603
            ["git", "add", "a_big.txt"],
            cwd=repo,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull},
        )
        res = run_ai_key(tmp_path, self.commit_request(repo), answers=[2], expect=1)
        assert len(res["confirms"]) == 1
        assert "z.tfvars" in res["confirms"][0]["msg"]

    # -- the source path of a rewrite is captured when the prompt opens --------

    def test_a_buffer_renamed_while_the_prompt_is_open_keeps_its_source(self, tmp_path):
        res = run_ai_key(
            tmp_path,
            {
                **self.replace_request(tmp_path, ".env"),
                "between": "rename",
                "rename_to": str(tmp_path / "plain.txt"),
            },
        )
        assert len(res["confirms"]) == 1, "the .env text went out under a new name"
        assert str(tmp_path / ".env") in res["confirms"][0]["msg"]
        assert res["ran"] == []

    def test_a_buffer_wiped_while_the_prompt_is_open_keeps_its_source(self, tmp_path):
        res = run_ai_key(
            tmp_path, {**self.replace_request(tmp_path, ".env"), "between": "wipe"}
        )
        assert len(res["confirms"]) == 1, "the .env text went out with no path at all"
        assert res["ran"] == []
