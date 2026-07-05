--- @diagnostic disable: undefined-global
-- undotree vimdiff integration
-- Opens a vimdiff tab comparing a selected undo state with the current buffer
-- for jiaoshijie/undotree. Supports do (obtain) / dp (put) operations.

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
        -- seq_cur is 0 (initial state), redo to the latest
        vim.cmd("silent later 9999")
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

  local augroup = vim.api.nvim_create_augroup("UndotreeVimdiffCleanup", { clear = true })
  local diff_tab = vim.api.nvim_get_current_tabpage()
  local cleaning_up = false -- re-entrancy guard

  --- Close the entire diff tab and clean up
  local function close_diff_tab()
    if cleaning_up then return end
    cleaning_up = true

    -- Remove the augroup first to prevent recursive triggers
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

    -- Explicitly wipe the scratch buffer
    if vim.api.nvim_buf_is_valid(old_buf) then
      pcall(vim.api.nvim_buf_delete, old_buf, { force = true })
    end

    -- Close the diff tab if it still exists
    if vim.api.nvim_tabpage_is_valid(diff_tab)
       and #vim.api.nvim_list_tabpages() > 1 then
      pcall(vim.cmd, "tabclose")
    end
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

  -- Clean up if the scratch buffer is wiped by other means
  vim.api.nvim_create_autocmd("BufWipeout", {
    group = augroup,
    buffer = old_buf,
    callback = close_diff_tab,
  })

  -- Clean up diffoff when the tab is closed
  vim.api.nvim_create_autocmd("TabClosed", {
    group = augroup,
    callback = close_diff_tab,
    once = true,
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

-- Auto-setup
M.setup()

return M
