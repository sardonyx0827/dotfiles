-- undotree vimdiff integration
-- Opens a vimdiff tab comparing a selected undo state with the current buffer
-- for jiaoshijie/undotree. Supports do (obtain) / dp (put) operations.
--
-- Relocated from after/plugin/undotree.lua so it only loads when undotree does.
-- The undotree plugin spec calls M.setup() from its config.

local M = {}

--- Extract the undo seq number from the current line in the undotree buffer.
--- Supports both compact and legacy parsers of jiaoshijie/undotree.
---@return number|nil seq number, or nil if not found
local function get_seq_from_line()
  local line = vim.api.nvim_get_current_line()
  -- Skip tree decoration chars (>, <, {, }, [, ], s, S, etc.) and grab the first number
  for num in line:gmatch("%d+") do
    return tonumber(num)
  end
  return nil
end

--- Find the editing target buffer in the same tab as the undotree panel.
---@return number|nil buf buffer number
local function find_target_buf()
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
    local buf = vim.api.nvim_win_get_buf(win)
    local ft = vim.bo[buf].filetype
    -- Exclude undotree-related buffers
    if ft ~= "undotree" and ft ~= "undotreeDiff"
       and ft ~= "Undotree" and ft ~= "UndotreeDiff" then
      return buf
    end
  end
  return nil
end

--- Get the buffer contents at a specific undo seq.
--- The buffer state is restored after retrieval.
---@param buf number buffer number
---@param seq number target undo seq number
---@return string[]|nil lines array of lines, or nil on failure
---@return number current_seq the original seq number
local function get_undo_state_lines(buf, seq)
  local lines, current_seq

  local ok, err = pcall(function()
    vim.api.nvim_buf_call(buf, function()
      local ut = vim.fn.undotree()
      current_seq = ut.seq_cur

      -- Move to the specified undo state
      vim.cmd("silent undo " .. seq)
      lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)

      -- Restore to the original state
      if current_seq and current_seq > 0 then
        vim.cmd("silent undo " .. current_seq)
      else
        -- seq_cur is 0 (initial state): return to it. `undo 0` moves to the
        -- state before the first change (i.e. seq 0). Using `later 9999` here
        -- would instead redo everything and silently leave the target buffer
        -- at the LATEST state, not where the user actually was.
        vim.cmd("silent undo 0")
      end
    end)
  end)

  if not ok then
    vim.notify("undotree vimdiff: failed to retrieve undo state: " .. tostring(err), vim.log.levels.ERROR)
    return nil, 0
  end

  return lines, current_seq or 0
end

--- Open a vimdiff in a new tab.
--- Left: past undo state (scratch, read-only)
--- Right: actual editing buffer (editable, supports do/dp)
function M.open_vimdiff()
  local seq = get_seq_from_line()
  if not seq then
    vim.notify("undotree vimdiff: no undo state found on the current line", vim.log.levels.WARN)
    return
  end

  local target_buf = find_target_buf()
  if not target_buf then
    vim.notify("undotree vimdiff: target buffer not found", vim.log.levels.ERROR)
    return
  end

  local buf_ft = vim.bo[target_buf].filetype
  local buf_name = vim.api.nvim_buf_get_name(target_buf)
  local short_name = vim.fn.fnamemodify(buf_name, ":t")
  if short_name == "" then short_name = "[No Name]" end

  -- Skip if the selected seq is the same as the current state
  local ut = vim.api.nvim_buf_call(target_buf, function()
    return vim.fn.undotree()
  end)
  if ut.seq_cur == seq then
    vim.notify("undotree vimdiff: selected state is the same as the current state", vim.log.levels.INFO)
    return
  end

  local old_lines, _ = get_undo_state_lines(target_buf, seq)
  if not old_lines then
    return
  end

  pcall(function()
    require("undotree").close()
  end)

  vim.cmd("tabnew")

  -- Left side: past state (scratch buffer)
  local old_buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(old_buf, 0, -1, false, old_lines)

  vim.bo[old_buf].buftype = "nofile"
  vim.bo[old_buf].bufhidden = "wipe"
  vim.bo[old_buf].swapfile = false
  vim.bo[old_buf].filetype = buf_ft

  -- Set buffer name (avoid duplicates)
  pcall(vim.api.nvim_buf_set_name, old_buf, "undo#" .. seq .. " " .. short_name)

  -- Enable syntax highlighting, then make read-only
  vim.bo[old_buf].modifiable = false
  vim.cmd("diffthis")

  -- Right side: actual editing buffer
  vim.cmd("vsplit")
  vim.api.nvim_win_set_buf(0, target_buf)
  vim.cmd("diffthis")

  -- Focus on the right side (real buffer)
  -- so that do (obtain) is immediately usable

  -- The group name is per-invocation, and the suffix is the whole point.
  --
  -- `clear = true` on a FIXED name is what made this matter: a second undo-diff
  -- recreating "UndotreeVimdiffCleanup" DELETED the first tab's BufWipeout and
  -- TabClosed handlers, leaving a diff that was still open with nothing armed
  -- to unwind it. Wiping that tab's scratch buffer then took the scratch window
  -- and stopped there -- the tab stayed standing, showing the user's real
  -- buffer still in diff mode, with no handler left to close it. Measured:
  -- after the second open, the first call's two autocmds were simply gone.
  --
  -- Keyed on old_buf rather than a module-level counter, and that is not a
  -- style preference. A counter lives in this module's table, so re-sourcing
  -- the file (:Lazy reload, :luafile) restarts it at 1 while a diff opened
  -- under the previous table is still open -- and the next open_vimdiff then
  -- recreates THAT tab's group name with clear = true, which is the original
  -- bug with extra steps. Buffer handles come from nvim, not from us: they are
  -- never reused within a session (verified -- wiping a buffer does not hand
  -- its number back), so old_buf stays unique across a reload.
  local augroup = vim.api.nvim_create_augroup(
    "UndotreeVimdiffCleanup_" .. old_buf, { clear = true })
  local diff_tab = vim.api.nvim_get_current_tabpage()
  local cleaning_up = false -- re-entrancy guard

  --- Unwind the diff state.
  ---   close_tab    : the diff tab still needs closing (false when it is
  ---                  already gone and we are only cleaning up after it).
  ---   from_wipeout : we were entered from `old_buf`'s own BufWipeout, so that
  ---                  buffer is mid-wipe and must not be touched again.
  local function cleanup_diff(close_tab, from_wipeout)
    if cleaning_up then return end
    cleaning_up = true

    -- Remove the augroup first to prevent recursive triggers.
    --
    -- Now that the names are per-invocation this also has to happen for its own
    -- sake, on every exit path: no later call will ever reclaim this name by
    -- recreating it, so an invocation that did not delete its group would leave
    -- it behind -- with its autocmds still armed on a diff that no longer
    -- exists -- for the rest of the session. By id, so it is unambiguously
    -- OURS and not whatever currently answers to that name.
    pcall(vim.api.nvim_del_augroup_by_id, augroup)

    -- Run diffoff on all windows showing the target buffer
    for _, win in ipairs(vim.api.nvim_list_wins()) do
      if vim.api.nvim_win_is_valid(win)
         and vim.api.nvim_win_get_buf(win) == target_buf then
        vim.api.nvim_win_call(win, function()
          vim.cmd("diffoff")
        end)
      end
    end

    -- Remove temporary keymaps from the target buffer
    pcall(vim.keymap.del, "n", "<C-w>q", { buf = target_buf })
    pcall(vim.keymap.del, "n", "<C-w><C-q>", { buf = target_buf })

    -- Explicitly wipe the scratch buffer -- unless its own BufWipeout is what
    -- brought us here, in which case it is already being wiped.
    --
    -- Deleting it from inside that handler is not merely redundant, it breaks
    -- the close: nvim_buf_delete fails with "Failed to unload buffer" (E937,
    -- the buffer is in use), and although the pcall catches the Lua error, the
    -- autocmd has still raised a Vim error, which makes the COMMAND THAT
    -- TRIGGERED IT fail. So `:tabclose` in the diff tab reported
    -- `E937: Attempt to delete a buffer that is in use` to the user -- on the
    -- documented way out. (`<C-w>q` routed around it and looked fine, which is
    -- why this survived.) Verified with a standalone bufhidden=wipe repro:
    -- skipping the delete makes the outer close succeed.
    if not from_wipeout and vim.api.nvim_buf_is_valid(old_buf) then
      pcall(vim.api.nvim_buf_delete, old_buf, { force = true })
    end

    -- Close the diff tab if it still exists.
    --
    -- BY NUMBER, not a bare `:tabclose`. A bare tabclose closes whatever tab is
    -- CURRENT, and the only guard here used to be that diff_tab is *valid* --
    -- never that it is the tab we are standing in. Combined with the
    -- patternless TabClosed autocmd below, closing any unrelated tab ran this
    -- code from some other tab and took that tab out instead: with the user
    -- sitting in their own working tab, that tab is what disappeared.
    --
    -- Deferred through vim.schedule, and that is load-bearing rather than
    -- defensive. One caller is BufWipeout: when the scratch buffer is wiped on
    -- its own (`:bwipeout`), the diff tab is still open and genuinely needs
    -- closing -- but a `:tabclose` issued from inside that handler fails while
    -- the wipe is still unwinding, the pcall swallows it, and the user is left
    -- with a stranded half-diffed tab. Measured: without the schedule that
    -- scenario ends with diff_tab still valid.
    -- Running after the wipe completes also lets the validity check mean
    -- something: if the tab was what closed in the first place (`:tabclose` in
    -- the diff tab, which wipes the buffer as a side effect), diff_tab is
    -- already invalid by then and this is correctly a no-op.
    if close_tab then
      vim.schedule(function()
        if vim.api.nvim_tabpage_is_valid(diff_tab)
           and #vim.api.nvim_list_tabpages() > 1 then
          pcall(vim.cmd, "tabclose " .. vim.api.nvim_tabpage_get_number(diff_tab))
        end
      end)
    end
  end

  --- Close the diff tab and clean up (the keymap / BufWipeout entry point).
  local function close_diff_tab()
    cleanup_diff(true)
  end

  -- Left side (scratch buffer): simple mapping
  vim.keymap.set("n", "<C-w>q", close_diff_tab, {
    buf = old_buf, silent = true, noremap = true,
    desc = "undotree vimdiff: close diff tab",
  })
  vim.keymap.set("n", "<C-w><C-q>", close_diff_tab, {
    buf = old_buf, silent = true, noremap = true,
    desc = "undotree vimdiff: close diff tab",
  })

  -- Right side (real buffer): only act as tabclose when in the diff tab
  -- (falls back to normal :quit in other tabs)
  local function close_if_in_diff_tab()
    if vim.api.nvim_get_current_tabpage() == diff_tab then
      close_diff_tab()
    else
      -- Fall back to normal behavior outside the diff tab
      vim.cmd("quit")
    end
  end

  vim.keymap.set("n", "<C-w>q", close_if_in_diff_tab, {
    buf = target_buf, silent = true, noremap = true,
    desc = "undotree vimdiff: close diff tab",
  })

  -- Clean up if the scratch buffer is wiped by other means.
  -- from_wipeout = true: old_buf is mid-wipe right now, so cleanup_diff must
  -- not try to delete it again (see the comment at that branch -- doing so
  -- fails the very command that triggered this autocmd).
  vim.api.nvim_create_autocmd("BufWipeout", {
    group = augroup,
    buffer = old_buf,
    callback = function()
      cleanup_diff(true, true)
    end,
  })

  -- Clean up diffoff when the diff tab is closed by other means (:tabclose,
  -- :q on both windows).
  --
  -- TabClosed cannot be narrowed with `pattern`: it fires for EVERY tab close
  -- in the session and its <amatch> is a tab NUMBER, which shifts as tabs come
  -- and go, so it never reliably identifies this tab. Identify by handle
  -- instead -- if diff_tab is no longer valid, the tab that just closed was
  -- ours and the diff state needs unwinding. Any other tab closing is none of
  -- our business, and returning early is the whole point: without this check
  -- the callback ran on every tab close and its `:tabclose` destroyed whatever
  -- tab the user happened to be in.
  --
  -- `once` is deliberately NOT set. The callback now no-ops for unrelated
  -- closes, so it has to stay armed until our own tab actually goes; with
  -- `once` the first unrelated close would consume it and the real cleanup
  -- would never run. It still self-removes, because cleanup_diff deletes the
  -- augroup on the pass that matters.
  vim.api.nvim_create_autocmd("TabClosed", {
    group = augroup,
    callback = function()
      if vim.api.nvim_tabpage_is_valid(diff_tab) then
        return
      end
      -- Our tab is already gone: clean up, but do not close another one.
      cleanup_diff(false)
    end,
  })

  vim.notify(
    "undotree vimdiff: undo#" .. seq .. " vs current  |  "
    .. "do=obtain  dp=put  ]c/[c=next/prev hunk  <C-w>q=quit",
    vim.log.levels.INFO
  )
end

--- Automatically set the <C-d> keymap in undotree buffers
function M.setup()
  vim.api.nvim_create_autocmd("FileType", {
    pattern = { "undotree", "Undotree" },
    callback = function(ev)
      vim.keymap.set("n", "<C-d>", M.open_vimdiff, {
        buf = ev.buf,
        silent = true,
        noremap = true,
        desc = "undotree: open vimdiff comparison",
      })
    end,
  })
end

return M
