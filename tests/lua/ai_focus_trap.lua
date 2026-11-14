-- Drive the AI focus trap(s) for real and report where focus ends up.
--
-- Run as `nvim --clean -l tests/lua/ai_focus_trap.lua <repo-root>` with
-- {"scenario": "<name>"} on stdin; see tests/test_nvim_ai_focus_trap.py, the
-- only caller. `--clean` keeps ~/.config/nvim (a symlink to the main checkout
-- on the author's machine) off the runtimepath: its loader runs BEFORE
-- package.path, so without it `require` would test that tree, not this one.
-- The reply carries the file each module was loaded from so the Python side
-- can refuse a run that tested something else.
--
-- Reply: { ...scenario fields..., "flips": <WinEnter count>,
--          "errmsg": <v:errmsg at the end>, "sources": { <name> = <path> } }
--     or { "loop": true, "flips": <n> } with exit code 3 when the WinEnter
--        storm cap trips -- the freeze this file exists to catch. The cap makes
--        the failure fast and legible; the outer timeout in the Python caller
--        is the backstop for a freeze that never reaches the cap.

local root = arg[1]
package.path = table.concat({
  root .. "/.config/nvim/lua/?.lua",
  root .. "/.config/nvim/lua/?/init.lua",
  package.path,
}, ";")

vim.o.swapfile = false
-- The lhs of every <leader> map is expanded when it is registered, so this has
-- to precede the require of setup.functions.ai.
vim.g.mapleader = ","

local req = vim.json.decode(io.read("*a"))

local function emit(value)
  io.stdout:write(vim.json.encode(value), "\n")
  io.stdout:flush()
end

-- A legitimate scenario produces a few dozen WinEnter events. Two traps
-- fighting for focus produce one per scheduled callback, without end.
local FLIP_CAP = 400
local flips = 0
vim.api.nvim_create_autocmd("WinEnter", {
  callback = function()
    flips = flips + 1
    if flips >= FLIP_CAP then
      emit({ loop = true, flips = flips })
      vim.cmd("cquit 3")
    end
  end,
})

-- Nothing here may reach a real notifier, and a refusal is not what is under
-- test.
vim.notify = function() end

local ui = require("setup.functions.ai.ui")

local function cur()
  return vim.api.nvim_get_current_win()
end

-- Spin the event loop so vim.schedule callbacks (what a trap runs on) fire.
local function settle(ms)
  vim.wait(ms or 150, function() return false end, 5)
end

-- Put focus on `win`, give any trap its chance to react, and report where focus
-- ended up.
local function go(win)
  vim.api.nvim_set_current_win(win)
  settle()
  return cur()
end

local function noop_start()
  return 0
end

local function open_popup(tag)
  ui.run_multi({
    mode = "popup", tools = { tag }, filetype = "text", footer = "f",
    start = noop_start,
  })
  return cur()
end

-- Returns the Original pane and the Response pane (the one that takes focus).
local function open_diff()
  local before = {}
  for _, w in ipairs(vim.api.nvim_list_wins()) do
    before[w] = true
  end
  ui.run_multi({
    mode = "diff", tools = { "claude" }, original = { "one", "two" },
    footer = "f", start = noop_start,
  })
  local response = cur()
  local original
  for _, w in ipairs(vim.api.nvim_list_wins()) do
    if not before[w] and w ~= response then
      original = w
    end
  end
  return original, response
end

local function open_float()
  return vim.api.nvim_open_win(vim.api.nvim_create_buf(false, true), true, {
    relative = "editor", width = 10, height = 3, row = 1, col = 1,
  })
end

-- The buffer-local `q` of whatever window has focus: how the user closes an AI
-- popup or its prompt.
local function press_q()
  local m = vim.fn.maparg("q", "n", false, true)
  assert(m and m.callback and m.buffer == 1, "no buffer-local q mapping here")
  m.callback()
end

-- The real x-mode <C-c> mapping, from the real keymap table: V selects the
-- current line, <C-c> asks Claude about it and opens the prompt window.
local function press_ai_replace()
  require("setup.functions.ai")
  vim.api.nvim_feedkeys(
    vim.api.nvim_replace_termcodes("V<C-c>", true, false, true), "x", false)
  return cur()
end

local scenarios = {}

-- Two run_multi clusters alive at once: every AI mapping is global, so opening a
-- second one from inside the first is one keypress.
function scenarios.two_popups()
  local main = cur()
  local a = open_popup("one")
  local b = open_popup("two")
  settle()
  local out = { main = main, a = a, b = b, after_open = cur() }
  out.leave_to_editor = go(main)
  out.enter_a = go(a)
  out.leave_from_a = go(main)
  return out
end

-- A two-window (diff) cluster plus a popup. Which pane of the diff cluster had
-- focus last is part of what the trap remembers.
function scenarios.diff_and_popup()
  local main = cur()
  local original, response = open_diff()
  local popup = open_popup("one")
  settle()
  local out = {
    main = main, original = original, response = response, popup = popup,
    after_open = cur(),
  }
  out.leave_to_editor = go(main)
  out.enter_original = go(original)
  out.leave_from_original = go(main)
  out.enter_response = go(response)
  out.leave_from_response = go(main)
  return out
end

-- The user's report: press `V` then `<C-c>` (or any other AI mapping) inside an
-- open AI popup.
function scenarios.prompt_over_popup()
  local main = cur()
  local a = open_popup("one")
  local prompt_win = press_ai_replace()
  settle()
  local out = { main = main, a = a, prompt = prompt_win }
  out.prompt_filetype = vim.bo[vim.api.nvim_win_get_buf(prompt_win)].filetype
  out.after_open = cur()
  out.leave_to_editor = go(main)
  out.enter_a = go(a)
  out.leave_from_a = go(main)
  -- Back into the prompt and cancel it: the popup is still alive and traps.
  go(prompt_win)
  press_q()
  settle()
  out.prompt_valid_after_q = vim.api.nvim_win_is_valid(prompt_win)
  out.after_q = cur()
  out.leave_after_q = go(main)
  return out
end

-- The prompt window on its own: its trap predates the shared registry and must
-- behave the same.
function scenarios.prompt_alone()
  local main = cur()
  vim.api.nvim_buf_set_lines(0, 0, -1, false, { "hello" })
  local prompt_win = press_ai_replace()
  settle()
  local out = { main = main, prompt = prompt_win, after_open = cur() }
  out.leave_to_editor = go(main)
  go(prompt_win)
  press_q()
  settle()
  out.prompt_valid_after_q = vim.api.nvim_win_is_valid(prompt_win)
  out.leave_after_q = go(main)
  return out
end

-- Close one of two clusters, then leave the other. The closed one must neither
-- keep pulling focus nor be a target that no longer exists.
function scenarios.close_one_leave_other()
  local main = cur()
  local a = open_popup("one")
  local b = open_popup("two")
  settle()
  local out = { main = main, a = a, b = b }
  press_q() -- b has focus
  settle()
  out.b_valid_after_q = vim.api.nvim_win_is_valid(b)
  out.leave_after_closing_b = go(main)
  go(a)
  press_q()
  settle()
  out.a_valid_after_q = vim.api.nvim_win_is_valid(a)
  out.leave_after_closing_all = go(main)
  out.window_count = #vim.api.nvim_list_wins()
  return out
end

-- The target of a pending snap-back is closed before the callback runs.
function scenarios.target_closed_before_callback()
  local main = cur()
  local a = open_popup("one")
  local b = open_popup("two")
  settle()
  local out = { main = main, a = a, b = b }
  vim.api.nvim_set_current_win(main) -- schedules a snap towards b ...
  vim.api.nvim_win_close(b, true) -- ... whose cluster is gone before it runs
  settle()
  out.after_closing_newest = cur()
  vim.api.nvim_set_current_win(main)
  vim.api.nvim_win_close(a, true)
  settle()
  out.after_closing_all = cur()
  return out
end

-- Focus is back inside a live cluster before the scheduled callback runs: the
-- callback must leave it alone instead of dragging it to the newest cluster.
function scenarios.refocus_before_callback()
  local main = cur()
  local a = open_popup("one")
  local b = open_popup("two")
  settle()
  local out = { main = main, a = a, b = b }
  vim.api.nvim_set_current_win(main)
  vim.api.nvim_set_current_win(a)
  settle()
  out.after_refocus = cur()
  out.leave_from_a = go(main)

  -- The same with a focus change no WinEnter announces (`:noautocmd`, as
  -- plugins use). Entering a cluster normally also makes it the newest, which
  -- would hide a missing re-check; here b is the newest and focus is on a.
  go(b)
  vim.api.nvim_set_current_win(main)
  vim.cmd("noautocmd call nvim_set_current_win(" .. a .. ")")
  settle()
  out.after_unannounced_refocus = cur()
  return out
end

-- Several focus changes in one tick must cost one snap-back, not one each.
function scenarios.burst_is_one_snap()
  local main = cur()
  local a = open_popup("one")
  settle()
  local scheduled, calls = 0, 0
  local real_schedule = vim.schedule
  local real_set = vim.api.nvim_set_current_win
  -- Only callbacks scheduled from the ai modules: nothing else is under test,
  -- and Neovim's own runtime schedules things now and then.
  vim.schedule = function(fn)
    if debug.getinfo(fn, "S").source:find("/ai/", 1, true) then
      scheduled = scheduled + 1
    end
    return real_schedule(fn)
  end
  vim.api.nvim_set_current_win(main)
  vim.cmd("new")
  vim.cmd("new")
  vim.api.nvim_set_current_win = function(win)
    calls = calls + 1
    return real_set(win)
  end
  settle()
  vim.schedule = real_schedule
  vim.api.nvim_set_current_win = real_set
  return { a = a, scheduled = scheduled, snaps = calls, after = cur() }
end

-- A snap-back that throws (E11 in the cmdwin, textlock) must neither surface as
-- an error nor leave the trap unable to fire again.
function scenarios.failed_snap_does_not_disarm()
  local main = cur()
  local a = open_popup("one")
  settle()
  local real = vim.api.nvim_set_current_win
  vim.api.nvim_set_current_win(main) -- schedules the snap-back ...
  -- ... and the first set_current_win after this line (the snap-back's) throws.
  vim.api.nvim_set_current_win = function()
    vim.api.nvim_set_current_win = real
    error("E11: simulated failure")
  end
  settle()
  vim.api.nvim_set_current_win = real
  local out = { main = main, a = a, after_failed_snap = cur() }
  vim.cmd("new")
  settle()
  out.after_second_try = cur()
  return out
end

-- One cluster, one popup: the behaviour that must not change.
function scenarios.single_popup()
  local main = cur()
  local a = open_popup("one")
  settle()
  local out = { main = main, a = a, after_open = cur() }
  out.leave = go(main)
  press_q()
  settle()
  out.a_valid_after_q = vim.api.nvim_win_is_valid(a)
  out.leave_after_close = go(main)
  return out
end

-- One cluster of two windows: panes are interchangeable, and the pane that last
-- had focus is where a stray focus change is sent back to.
function scenarios.single_diff()
  local main = cur()
  local original, response = open_diff()
  settle()
  local out = {
    main = main, original = original, response = response, after_open = cur(),
  }
  out.leave_from_response = go(main)
  out.enter_original = go(original)
  out.leave_from_original = go(main)
  go(response)
  press_q()
  settle()
  out.response_valid_after_q = vim.api.nvim_win_is_valid(response)
  out.original_valid_after_q = vim.api.nvim_win_is_valid(original)
  out.leave_after_close = go(main)
  return out
end

-- One cluster, then another tabpage: focus must not be pulled across tabs.
function scenarios.tab_single()
  local tab1 = vim.api.nvim_get_current_tabpage()
  local main = cur()
  local a = open_popup("one")
  vim.cmd("tabnew")
  settle()
  local out = {
    a = a,
    stayed_in_tab2 = vim.api.nvim_get_current_tabpage() ~= tab1,
    cur_is_a = cur() == a,
  }
  vim.cmd("tabnext")
  settle()
  out.back_in_tab1 = vim.api.nvim_get_current_tabpage() == tab1
  out.cur_after_return = cur()
  out.leave_in_tab1 = go(main)
  return out
end

-- Two clusters in two tabpages: the trap acts on the tab you are in, even when
-- the cluster in the other tab is the one that had focus most recently.
function scenarios.tabs_two_clusters()
  local tab1 = vim.api.nvim_get_current_tabpage()
  local main1 = cur()
  local a = open_popup("one")
  vim.cmd("tabnew")
  local tab2 = vim.api.nvim_get_current_tabpage()
  local main2 = cur()
  local b = open_popup("two")
  settle()
  local out = { a = a, b = b, main1 = main1, main2 = main2 }
  out.leave_b = go(main2)
  -- Make tab 1's own current window the editor, so that entering the tab is an
  -- ordinary "focus landed outside every cluster" event rather than a refocus
  -- of a.
  vim.api.nvim_tabpage_set_win(tab1, main1)
  vim.api.nvim_set_current_tabpage(tab1)
  settle()
  out.cur_after_tab1 = cur()
  out.in_tab1 = vim.api.nvim_get_current_tabpage() == tab1
  out.in_tab2 = vim.api.nvim_get_current_tabpage() == tab2
  return out
end

-- The registry's own contract, without ui.lua or init.lua in the way.
function scenarios.registry()
  local trap = require("setup.functions.ai.focus_trap")
  local main = cur()
  local out = { main = main }
  out.register_empty_is_nil = trap.register({}) == nil
  out.register_invalid_is_nil = trap.register({ 987654 }) == nil
  out.unregister_nil_ok = pcall(trap.unregister, nil)

  local w1 = open_float()
  local h1 = trap.register({ w1 })
  local w2 = open_float()
  local h2 = trap.register({ w2 })
  out.w1, out.w2 = w1, w2
  out.leave_both = go(main)
  trap.unregister(h2)
  out.leave_after_unregistering_newest = go(main)
  trap.unregister(h1)
  out.unregister_twice_ok = pcall(trap.unregister, h1)
  out.leave_after_unregistering_all = go(main)

  -- A window closed without being unregistered is dropped, not targeted.
  local w3 = open_float()
  trap.register({ w3 })
  vim.api.nvim_win_close(w3, true)
  out.leave_after_closing_registered = go(main)
  return out
end

local scenario = scenarios[req.scenario]
if not scenario then
  emit({ error = "unknown scenario " .. tostring(req.scenario) })
  vim.cmd("cquit 2")
end

local out = scenario()

local function source_of(fn)
  return type(fn) == "function" and debug.getinfo(fn, "S").source or vim.NIL
end

local m = vim.fn.maparg("<C-c>", "x", false, true)
local trap_mod = package.loaded["setup.functions.ai.focus_trap"]
out.sources = {
  ui = source_of(ui.run_multi),
  init = source_of(type(m) == "table" and m.callback or nil),
  focus_trap = source_of(type(trap_mod) == "table" and trap_mod.register or nil),
}
out.flips = flips
out.errmsg = vim.v.errmsg
emit(out)
