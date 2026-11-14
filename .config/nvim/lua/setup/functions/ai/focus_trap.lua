---------------------------------------------------------
-- ai.focus_trap
-- One registry and one WinEnter handler for every AI window that traps focus.
--
-- The AI floating windows (a run_multi cluster, the replace-prompt window)
-- keep focus: leave them and you are sent back. Each used to install its OWN
-- WinEnter autocmd doing "if focus is outside me, vim.schedule() a jump back to
-- me". That is fine for one window and fatal for two: every AI mapping is
-- global, so pressing another one (`V` then `<C-c>`, `<leader>cx`) inside an
-- open popup opens a second trap, and from then on each trap sees focus
-- outside itself and pulls it back, which the other trap sees as focus outside
-- itself, and so on, forever, inside vim.schedule -- 100% CPU until the editor
-- is killed.
--
-- So the traps share state instead. A "cluster" is the set of windows that count
-- as one trap (run_multi's popup, or its Original + Response pair; the prompt
-- window alone). The single handler below treats focus on ANY live cluster's
-- window as home, and only when focus lands outside all of them sends it to the
-- cluster that had focus most recently.
--
-- Callers register right after opening their windows and unregister on their
-- close path; a window that closes without anyone unregistering is dropped
-- when the handler next runs, so a trap never targets a dead window.
---------------------------------------------------------
local M = {}

-- Live clusters, oldest first. { wins = win[], last = win, stamp = integer }
local clusters = {}
-- Bumped whenever a cluster takes focus (or is registered). The cluster with
-- the highest stamp is the one that had focus most recently.
local stamp = 0
local autocmd_id = nil
-- True while a snap-back is scheduled. Further focus changes before it runs
-- need no callback of their own: it decides from where focus is by then.
local snap_pending = false

local function owner_of(win)
  for _, c in ipairs(clusters) do
    for _, w in ipairs(c.wins) do
      if w == win then
        return c
      end
    end
  end
  return nil
end

-- The handler only has work while a cluster is alive; with none it is removed
-- so an idle session carries no global WinEnter autocmd at all.
local function disarm()
  if autocmd_id then
    pcall(vim.api.nvim_del_autocmd, autocmd_id)
    autocmd_id = nil
  end
end

-- Forget clusters that have no window left, and windows that have closed.
local function prune()
  for i = #clusters, 1, -1 do
    local c = clusters[i]
    local live = {}
    for _, w in ipairs(c.wins) do
      if vim.api.nvim_win_is_valid(w) then
        live[#live + 1] = w
      end
    end
    if #live == 0 then
      table.remove(clusters, i)
    else
      c.wins = live
      if not vim.api.nvim_win_is_valid(c.last) then
        c.last = live[1]
      end
    end
  end
  if #clusters == 0 then
    disarm()
  end
end

-- The window of `c` to return to on tabpage `tab`: where the cluster last had
-- focus, else any other of its windows there. nil when none is on that tab.
local function window_on(c, tab)
  if vim.api.nvim_win_get_tabpage(c.last) == tab then
    return c.last
  end
  for _, w in ipairs(c.wins) do
    if vim.api.nvim_win_get_tabpage(w) == tab then
      return w
    end
  end
  return nil
end

-- The most recently focused cluster ON THE CURRENT TABPAGE. Floating windows
-- belong to the tabpage they were opened in, and nvim_set_current_win on one
-- from another tab switches tab -- so a cluster elsewhere is never a target:
-- the trap holds you in its own tab and does not follow you out of it.
local function pick_target()
  local tab = vim.api.nvim_get_current_tabpage()
  local best, target = nil, nil
  for _, c in ipairs(clusters) do
    if not best or c.stamp > best.stamp then
      local win = window_on(c, tab)
      if win then
        best, target = c, win
      end
    end
  end
  return target
end

local function snap_back()
  snap_pending = false
  prune()
  -- Decided from where focus is NOW, not from the event that scheduled this:
  -- focus may have returned to a cluster (any cluster) in the meantime.
  if owner_of(vim.api.nvim_get_current_win()) then
    return
  end
  local target = pick_target()
  if target then
    -- E11 (command-line window), textlock and friends throw. A trap that
    -- cannot move focus right now has nothing better to do than leave it.
    pcall(vim.api.nvim_set_current_win, target)
  end
end

-- Must not return a value: a truthy return deletes the autocmd.
local function on_win_enter()
  local cur = vim.api.nvim_get_current_win()
  local owner = owner_of(cur)
  if owner then
    stamp = stamp + 1
    owner.stamp = stamp
    owner.last = cur
    return
  end
  if not snap_pending then
    snap_pending = true
    vim.schedule(snap_back)
  end
end

local function arm()
  if autocmd_id then
    return
  end
  autocmd_id = vim.api.nvim_create_autocmd("WinEnter", {
    group = vim.api.nvim_create_augroup("AiFocusTrap", { clear = true }),
    callback = on_win_enter,
  })
end

--- Start trapping focus inside `wins`, as one cluster.
---
--- Call it right after opening the windows. nvim_open_win(..., true) fires
--- WinEnter before it returns, i.e. before this can run, so a snap-back may
--- already be scheduled against the new window; registering makes the new
--- cluster the most recently focused one, which is what makes that pending
--- callback find focus at home and do nothing.
--- @param wins integer[] window ids; the first is where focus goes initially
--- @return table|nil handle for unregister(); nil when no window is valid
function M.register(wins)
  local live = {}
  for _, w in ipairs(wins) do
    if vim.api.nvim_win_is_valid(w) then
      live[#live + 1] = w
    end
  end
  if #live == 0 then
    return nil
  end
  stamp = stamp + 1
  local c = { wins = live, last = live[1], stamp = stamp }
  clusters[#clusters + 1] = c
  arm()
  return c
end

--- Stop trapping for a cluster. Safe on nil and on a handle already released.
--- @param handle table|nil what register() returned
function M.unregister(handle)
  for i, c in ipairs(clusters) do
    if c == handle then
      table.remove(clusters, i)
      break
    end
  end
  if #clusters == 0 then
    disarm()
  end
end

return M
