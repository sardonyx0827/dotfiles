-- Drive setup.functions.undotree_vimdiff's tab lifecycle and report the result
-- as JSON. Run as `nvim -l tests/lua/undotree_tabs.lua <repo-root> <scenario>`;
-- see tests/test_nvim_undotree_tabs.py, the only caller.
--
-- Why a scenario script rather than tests/lua/nvim_call.lua: the behaviour under
-- test is not a return value. It is what `TabClosed` does to *other* tabs, so a
-- test has to build a real tab layout, close one tab, and then look at which
-- tabs survived. nvim_call.lua deliberately calls one function and exits.
--
-- `nvim -l` does not load the user's init.lua, so the undotree plugin is absent.
-- That does not matter here: open_vimdiff's only call into it
-- (`require("undotree").close()`) is already pcall-wrapped, and the tab logic
-- under test does not touch the plugin at all.

local repo_root = arg[1]
local scenario = arg[2]
if not repo_root or not scenario then
  io.stderr:write("usage: nvim -l undotree_tabs.lua <repo-root> <scenario>\n")
  os.exit(2)
end

-- dofile, NOT require. Prepending to package.path does not decide anything here:
-- Neovim installs its own runtimepath loader ahead of Lua's path searcher, so
-- `require("setup.functions.undotree_vimdiff")` resolves under
-- $XDG_CONFIG_HOME/nvim (verified: debug.getinfo reported
-- @~/.config/nvim/lua/... even with repo_root prepended). On the primary
-- checkout ~/.config/nvim symlinks this repo, so the right file loaded by
-- accident; from a `git worktree` or a second clone the test would silently
-- exercise the OTHER tree and report green for an unfixed file.
-- dofile takes the path literally, so repo_root actually decides.
--
-- Safe because this module's only require is `require("undotree")`, which is
-- already pcall-wrapped for exactly the "plugin absent" case we are in.
local M = dofile(repo_root .. "/.config/nvim/lua/setup/functions/undotree_vimdiff.lua")

local function emit(value)
  io.stdout:write(vim.json.encode(value), "\n")
end

-- A buffer with two distinct undo states, so open_vimdiff has something to diff.
-- Two nvim_buf_set_lines calls land in ONE undo block under `-l` (there is no
-- keystroke to break the sequence), which would make seq_cur == 1 and the
-- requested seq identical -- open_vimdiff then returns early and the scenario
-- never gets a diff tab. Touching 'undolevels' is the documented way to force a
-- new undo block from a script.
local function make_target()
  vim.cmd("enew")
  local buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "line 1 alpha" })
  vim.cmd("let &undolevels = &undolevels")
  vim.api.nvim_buf_set_lines(buf, 1, -1, false, { "beta" })
  -- Cursor on a line whose first number is 1 -> get_seq_from_line() == 1,
  -- which differs from seq_cur (2), so open_vimdiff proceeds.
  vim.api.nvim_win_set_cursor(0, { 1, 0 })
  return buf
end

local target = make_target()
local user_tab = vim.api.nvim_get_current_tabpage()
M.open_vimdiff()
local diff_tab = vim.api.nvim_get_current_tabpage()

if diff_tab == user_tab then
  emit({ ok = false, err = "open_vimdiff did not create a diff tab" })
  os.exit(0)
end

local other_tab

-- The user's own :tabclose must not raise. Capture rather than propagate: the
-- buggy version could surface E937 out of the cascade it set off (its own
-- `:tabclose` wiped the scratch buffer whose BufWipeout handler was still
-- unwinding), and a harness that dies there cannot report which tabs survived.
local close_err
local function close(cmd)
  local ok, err = pcall(vim.cmd, cmd)
  if not ok then
    close_err = tostring(err)
  end
end

if scenario == "close_unrelated_from_user_tab" then
  -- Tabs: 1=user work, 2=diff, 3=unrelated. The user is sitting in their own
  -- tab and closes ONLY the unrelated one.
  vim.cmd("tabnew")
  other_tab = vim.api.nvim_get_current_tabpage()
  vim.api.nvim_set_current_tabpage(user_tab)
  close("tabclose " .. vim.api.nvim_tabpage_get_number(other_tab))
elseif scenario == "close_unrelated_from_that_tab" then
  -- Same layout, but the close happens from inside the unrelated tab.
  vim.cmd("tabnew")
  other_tab = vim.api.nvim_get_current_tabpage()
  close("tabclose")
elseif scenario == "close_the_diff_tab" then
  -- The supported way out: close the diff tab itself. Cleanup must still run.
  vim.api.nvim_set_current_tabpage(diff_tab)
  close("tabclose")
elseif scenario == "wipe_scratch_from_user_tab" then
  -- Same wipe, but issued from the USER's tab -- `:bwipeout <n>` / `:%bwipeout`
  -- is how a buffer normally gets wiped, and nothing says you must be looking
  -- at it. This is the only scenario where the tab that needs closing is NOT
  -- the current one, so it is the only one that can tell `tabclose <n>` apart
  -- from a bare `tabclose`. Without it, reverting to a bare tabclose passes
  -- every other scenario -- i.e. the original bug could come back unnoticed.
  local scratch
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(diff_tab)) do
    local b = vim.api.nvim_win_get_buf(win)
    if b ~= target then
      scratch = b
    end
  end
  if not scratch then
    emit({ ok = false, err = "could not find the scratch buffer" })
    os.exit(0)
  end
  vim.api.nvim_set_current_tabpage(user_tab)
  close("bwipeout! " .. scratch)
elseif scenario == "wipe_the_scratch_buffer" then
  -- The case the BufWipeout autocmd exists for: the scratch buffer goes away
  -- on its own while the diff tab is still open. Cleanup has to close the tab
  -- here -- this is the one entry point where `close_tab` genuinely has work to
  -- do while `from_wipeout` is set, so it is the only exercise of that pair.
  local scratch
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(diff_tab)) do
    local b = vim.api.nvim_win_get_buf(win)
    if b ~= target then
      scratch = b
    end
  end
  if not scratch then
    emit({ ok = false, err = "could not find the scratch buffer" })
    os.exit(0)
  end
  close("bwipeout! " .. scratch)
else
  emit({ ok = false, err = "unknown scenario: " .. scenario })
  os.exit(0)
end

-- Let any vim.schedule'd work from the autocmd run before we look.
vim.cmd("sleep 100m")

local diffs = {}
for _, win in ipairs(vim.api.nvim_list_wins()) do
  if vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == target then
    table.insert(diffs, vim.api.nvim_win_call(win, function()
      return vim.o.diff
    end))
  end
end

emit({
  ok = true,
  scenario = scenario,
  close_err = close_err or false,
  tab_count = #vim.api.nvim_list_tabpages(),
  user_tab_valid = vim.api.nvim_tabpage_is_valid(user_tab),
  diff_tab_valid = vim.api.nvim_tabpage_is_valid(diff_tab),
  other_tab_valid = other_tab ~= nil and vim.api.nvim_tabpage_is_valid(other_tab) or false,
  target_buf_valid = vim.api.nvim_buf_is_valid(target),
  target_still_in_diff_mode = diffs,
})
