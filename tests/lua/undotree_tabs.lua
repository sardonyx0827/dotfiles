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

-- Ids of the cleanup autocmds currently registered by the module.
--
-- Matched on the group-name PREFIX and reported as ids, not counts, because
-- both halves of the bug this catches are invisible to a name lookup. The
-- group name is per-invocation, so `{ group = "UndotreeVimdiffCleanup" }`
-- raises once the name carries a suffix; and back when the name was a fixed
-- literal, a second open_vimdiff registered its autocmds under that SAME name,
-- so a by-name query returned two entries either way -- the first call's, or
-- the second call's standing on their grave. Ids are unique per autocmd and
-- never reused, so "are call 1's still there" is only answerable through them.
local function cleanup_autocmd_ids()
  local ids = {}
  for _, ac in ipairs(vim.api.nvim_get_autocmds({ event = { "BufWipeout", "TabClosed" } })) do
    if type(ac.group_name) == "string" and ac.group_name:find("UndotreeVimdiffCleanup", 1, true) == 1 then
      table.insert(ids, ac.id)
    end
  end
  return ids
end

--- The scratch (past-state) buffer of a diff tab: the one that is not `target`.
local function find_scratch(tab, target)
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
    local b = vim.api.nvim_win_get_buf(win)
    if b ~= target then
      return b
    end
  end
  return nil
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
local first_call_autocmds = cleanup_autocmd_ids()

if diff_tab == user_tab then
  emit({ ok = false, err = "open_vimdiff did not create a diff tab" })
  os.exit(0)
end

local other_tab
local second_diff_tab
local second_call_autocmds
local first_scratch

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

--- Press the buffer-local `<C-w>q` the module maps on the user's REAL buffer,
--- standing in `tab` in the window that shows that buffer.
---
--- Through feedkeys, i.e. the key the user presses resolved by the mapping that
--- is actually installed -- not the callback this harness could have looked up
--- with maparg. Which invocation's callback occupies that one shared
--- {buf, mode, lhs} slot IS the question these scenarios ask, and looking the
--- callback up here would answer it on the module's behalf.
---
--- Mode "x" so the keys are consumed before this returns; note that an error
--- raised inside the mapping's callback is swallowed there rather than reaching
--- `close_err`, so these scenarios are judged on the layout they leave behind.
local function press_close_in(tab, target_buf)
  vim.api.nvim_set_current_tabpage(tab)
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
    if vim.api.nvim_win_get_buf(win) == target_buf then
      vim.api.nvim_set_current_win(win)
    end
  end
  local ok, err = pcall(vim.api.nvim_feedkeys,
    vim.api.nvim_replace_termcodes("<C-w>q", true, false, true), "x", false)
  if not ok then
    close_err = tostring(err)
  end
end

--- Open a SECOND diff on the same target while the first one is still open.
---
--- Driven from the user's tab because that is where the undotree panel lives:
--- find_target_buf() scans the CURRENT tab, and the cursor has to sit on a line
--- whose first number is a seq that differs from seq_cur (see make_target).
--- Every caller depends on two diffs being open at once, so failing to get a
--- tab of its own is reported here rather than surfacing as a puzzling result.
local function open_second_diff()
  vim.api.nvim_set_current_tabpage(user_tab)
  vim.api.nvim_win_set_cursor(0, { 1, 0 })
  M.open_vimdiff()
  local tab = vim.api.nvim_get_current_tabpage()
  if tab == user_tab or tab == diff_tab then
    emit({ ok = false, err = "the second open_vimdiff did not create its own tab" })
    os.exit(0)
  end
  return tab
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
elseif scenario == "close_first_diff_after_second_open"
    or scenario == "wipe_first_scratch_after_second_open" then
  -- TWO diffs open at once, which no other scenario builds -- and that is the
  -- whole point. The cleanup augroup used to be a fixed literal created with
  -- `clear = true`, so opening a second undo-diff DELETED the first tab's
  -- BufWipeout and TabClosed handlers. Every scenario above calls open_vimdiff
  -- exactly once, so all of them pass with that defect in place.
  --
  -- The second call is driven from the user's tab because that is where the
  -- undotree panel lives: find_target_buf() scans the CURRENT tab, and the
  -- cursor has to sit on a line whose first number is a seq that differs from
  -- seq_cur (see make_target).
  first_scratch = find_scratch(diff_tab, target)
  if not first_scratch then
    emit({ ok = false, err = "could not find the first scratch buffer" })
    os.exit(0)
  end
  second_diff_tab = open_second_diff()
  second_call_autocmds = cleanup_autocmd_ids()

  if scenario == "close_first_diff_after_second_open" then
    -- The documented way out of the FIRST diff, taken while the second is open.
    vim.api.nvim_set_current_tabpage(diff_tab)
    close("tabclose")
  else
    -- The first diff's scratch buffer is wiped while its tab is still open --
    -- the case BufWipeout exists for, now aimed at the invocation whose
    -- handlers the second call used to erase. Without them nothing closes that
    -- tab: the wipe takes the scratch window with it and leaves the first diff
    -- tab standing, showing the real buffer still in diff mode.
    vim.api.nvim_set_current_tabpage(user_tab)
    close("bwipeout! " .. first_scratch)
  end
elseif scenario == "close_first_diff_via_target_keymap_after_second_open"
    or scenario == "close_both_diffs_via_target_keymap" then
  -- `<C-w>q` pressed on the user's REAL buffer, in the FIRST diff tab, while a
  -- second diff is open. Nothing before this ever pressed it: the two-diff
  -- scenarios above leave through :tabclose or :bwipeout, and the mappings on
  -- old_buf are not at risk because old_buf is a fresh scratch buffer per call.
  --
  -- That mapping is set with { buf = target_buf }, and target_buf is the user's
  -- file buffer -- shared by every invocation. The second open re-registers the
  -- same {buf, mode, lhs} slot and vim.keymap.set REPLACES what was there
  -- (measured: one mapping on target_buf after two opens), so the surviving
  -- callback belonged to the SECOND invocation and closed over ITS diff_tab.
  -- Pressed in the first diff tab, that tab-identity check failed and the
  -- handler fell through to the documented fallback, a bare `:quit`: one window
  -- closed, the tab left standing half-diffed with its augroup and its
  -- BufWipeout / TabClosed autocmds still armed.
  first_scratch = find_scratch(diff_tab, target)
  if not first_scratch then
    emit({ ok = false, err = "could not find the first scratch buffer" })
    os.exit(0)
  end
  second_diff_tab = open_second_diff()
  second_call_autocmds = cleanup_autocmd_ids()
  press_close_in(diff_tab, target)

  if scenario == "close_both_diffs_via_target_keymap" then
    -- The second diff now leaves the same documented way, which is what pins
    -- the other half: the mapping lives on a buffer that is STILL hosting a
    -- live diff, so the first cleanup must not delete it and strand this tab
    -- with no documented way out. Sleep first, so the press lands in a settled
    -- layout rather than mid-unwind (the first cleanup's tabclose is deferred
    -- through vim.schedule).
    vim.cmd("sleep 100m")
    press_close_in(second_diff_tab, target)
  end
elseif scenario == "target_keymap_outside_a_diff_tab" then
  -- The documented fallback, which per-diff dispatch must not eat: pressed
  -- anywhere that is not a diff tab, `<C-w>q` stays an ordinary :quit. The
  -- user's own tab is split first so there is a window to close without taking
  -- the tab down with it -- `:quit` in a tab's last window closes the tab.
  vim.api.nvim_set_current_tabpage(user_tab)
  vim.cmd("split")
  press_close_in(user_tab, target)
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
  second_diff_tab_valid = second_diff_tab ~= nil
    and vim.api.nvim_tabpage_is_valid(second_diff_tab) or false,
  -- Windows left in the first diff tab. 0 when it closed properly; the strand
  -- this catches leaves exactly one (the scratch window, still in diff mode),
  -- which is what makes a failure message say what actually happened.
  first_diff_win_count = vim.api.nvim_tabpage_is_valid(diff_tab)
    and #vim.api.nvim_tabpage_list_wins(diff_tab) or 0,
  first_scratch_valid = first_scratch ~= nil
    and vim.api.nvim_buf_is_valid(first_scratch) or false,
  user_tab_win_count = vim.api.nvim_tabpage_is_valid(user_tab)
    and #vim.api.nvim_tabpage_list_wins(user_tab) or 0,
  first_call_autocmd_count = #first_call_autocmds,
  first_call_autocmds_survived = second_call_autocmds ~= nil
    and (function()
      local alive = {}
      for _, id in ipairs(second_call_autocmds) do alive[id] = true end
      local n = 0
      for _, id in ipairs(first_call_autocmds) do
        if alive[id] then n = n + 1 end
      end
      return n
    end)() or vim.NIL,
})
