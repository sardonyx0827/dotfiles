---------------------------------------------------------
-- ai.ui
-- A single floating-window driver shared by every AI feature.
--
-- run_multi(opts) handles both layouts and any number of tools (single tool is
-- just a one-element tab set):
--   mode = "popup": one centred window; accept = copy to clipboard (+paste).
--   mode = "diff":  Original | Response side-by-side with diffthis; accept =
--                   hand the chosen lines back to the caller for replacement.
--
-- The UI never invokes a tool itself: `opts.start(tool, done)` is called for
-- each tab and is expected to kick off the async job and call done(ok,lines,err).
--
-- open_report(opts) is the other driver: a single result streamed into a real
-- vertical split on the right (not a float), with wrap toggling, for long prose
-- reports you want to keep open and scroll alongside the source buffer.
---------------------------------------------------------
local M = {}

-- Status highlight groups for the title tabs. default=true preserves user themes.
local function ensure_highlights()
  vim.api.nvim_set_hl(0, "AskAiTabDone",
    { fg = "#a6e3a1", bold = true, default = true })
  vim.api.nvim_set_hl(0, "AskAiTabFailed",
    { fg = "#f38ba8", bold = true, default = true })
  vim.api.nvim_set_hl(0, "AskAiTabPending",
    { link = "FloatTitle", default = true })
end

local function close_window(win)
  if win and vim.api.nvim_win_is_valid(win) then
    vim.api.nvim_win_close(win, true)
  end
end

-- Copy text to clipboard (and the tmux buffer when running inside tmux).
local function copy_to_clipboard(msg)
  vim.fn.setreg("+", msg)
  vim.fn.setreg('"', msg)
  if vim.env.TMUX then
    vim.fn.system("tmux load-buffer -", msg)
  end
end

--- Drive a multi-tab AI result window.
--- @param opts table {
---   mode = "popup"|"diff",
---   tools = string[],            -- one or more; first is active initially
---   filetype = string|nil,       -- ft for result buffers
---   original = string[]|nil,     -- diff mode: left pane content
---   footer = string|nil,
---   copy_notify = string|nil,    -- popup mode: notify text on accept
---   start = fun(tool, done),     -- start a tool's job; done(ok,lines,err)
---   on_accept = fun(tool, lines),-- diff mode: y pressed
---   on_accept_merged = fun(lines)-- diff mode: Y pressed
--- }
function M.run_multi(opts)
  ensure_highlights()

  local mode = opts.mode
  local tools = opts.tools
  local state = {
    bufs = {},
    jobs = {},
    status = {},
    active = 1,
    closed = false,
    win = nil,
    original_win = nil,
    original_buf = nil,
  }

  -- Pre-create per-tool response buffers so the UI renders before any job ends.
  for _, t in ipairs(tools) do
    local buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
      string.format("[%s: waiting for response...]", t),
    })
    vim.bo[buf].filetype = opts.filetype or ""
    vim.bo[buf].modifiable = false
    state.bufs[t] = buf
    state.status[t] = "pending"
  end

  -- Title shows every tool as a tab with a status marker; the active tab is
  -- bracketed when there is more than one.
  local function build_title()
    local parts = { { " ", "AskAiTabPending" } }
    for i, t in ipairs(tools) do
      if i > 1 then
        table.insert(parts, { " | ", "AskAiTabPending" })
      end
      local st = state.status[t]
      local hl = "AskAiTabPending"
      if st == "done" then
        hl = "AskAiTabDone"
      elseif st == "failed" or st == "cancelled" then
        hl = "AskAiTabFailed"
      end
      local marker = ""
      if st == "pending" then
        marker = " (loading)"
      elseif st == "failed" then
        marker = " (failed)"
      elseif st == "cancelled" then
        marker = " (cancelled)"
      end
      local label = t .. marker
      if i == state.active and #tools > 1 then
        label = "[" .. label .. "]"
      end
      table.insert(parts, { label, hl })
    end
    table.insert(parts, { " ", "AskAiTabPending" })
    return parts
  end

  -- Layout.
  if mode == "diff" then
    local total_width = math.min(vim.o.columns - 4, 200)
    local pane_width = math.floor((total_width - 2) / 2)
    local height = math.min(math.max(#opts.original + 2, 24), vim.o.lines - 4)
    local row = math.floor((vim.o.lines - height) / 2)
    local left_col = math.floor((vim.o.columns - total_width) / 2)
    local right_col = left_col + pane_width + 2

    local original_buf = vim.api.nvim_create_buf(false, true)
    vim.api.nvim_buf_set_lines(original_buf, 0, -1, false, opts.original)
    vim.bo[original_buf].filetype = opts.filetype or ""
    state.original_buf = original_buf

    state.original_win = vim.api.nvim_open_win(original_buf, false, {
      relative = "editor",
      width = pane_width,
      height = height,
      row = row,
      col = left_col,
      border = "rounded",
      title = " Original ",
      title_pos = "center",
    })

    state.win = vim.api.nvim_open_win(state.bufs[tools[1]], true, {
      relative = "editor",
      width = pane_width,
      height = height,
      row = row,
      col = right_col,
      border = "rounded",
      title = build_title(),
      title_pos = "center",
      footer = opts.footer,
      footer_pos = "center",
    })
    vim.cmd("stopinsert")
  else -- popup
    local width = math.min(80, vim.o.columns - 4)
    local height = math.min(20, vim.o.lines - 4)
    state.win = vim.api.nvim_open_win(state.bufs[tools[1]], true, {
      relative = "editor",
      width = width,
      height = height,
      row = math.floor((vim.o.lines - height) / 2),
      col = math.floor((vim.o.columns - width) / 2),
      style = "minimal",
      border = "rounded",
      title = build_title(),
      title_pos = "center",
      footer = opts.footer,
      footer_pos = "center",
    })
  end

  local function update_title()
    if not vim.api.nvim_win_is_valid(state.win) then return end
    local cfg = vim.api.nvim_win_get_config(state.win)
    cfg.title = build_title()
    pcall(vim.api.nvim_win_set_config, state.win, cfg)
  end

  -- Diff only when the active response is real content; loading/failed
  -- placeholders would otherwise mark every line as changed.
  local function update_diff()
    if mode ~= "diff" then return end
    local on = state.status[tools[state.active]] == "done"
    if vim.api.nvim_win_is_valid(state.original_win) then
      vim.api.nvim_win_call(state.original_win,
        function() vim.cmd(on and "diffthis" or "diffoff") end)
    end
    if vim.api.nvim_win_is_valid(state.win) then
      vim.api.nvim_win_call(state.win,
        function() vim.cmd(on and "diffthis" or "diffoff") end)
    end
  end

  local function switch_to(idx)
    if idx < 1 or idx > #tools then return end
    -- Tear down diff before the buffer swap; otherwise the Original pane keeps
    -- highlights derived from the previous comparison.
    if mode == "diff" then
      if vim.api.nvim_win_is_valid(state.original_win) then
        vim.api.nvim_win_call(state.original_win, function() vim.cmd("diffoff") end)
      end
      if vim.api.nvim_win_is_valid(state.win) then
        vim.api.nvim_win_call(state.win, function() vim.cmd("diffoff") end)
      end
    end
    state.active = idx
    local buf = state.bufs[tools[idx]]
    if vim.api.nvim_buf_is_valid(buf) and vim.api.nvim_win_is_valid(state.win) then
      vim.api.nvim_win_set_buf(state.win, buf)
    end
    update_title()
    update_diff()
  end

  local function switch_offset(offset)
    switch_to(((state.active - 1 + offset) % #tools) + 1)
  end

  local function focus_original()
    if vim.api.nvim_win_is_valid(state.original_win) then
      vim.api.nvim_set_current_win(state.original_win)
    end
  end
  local function focus_response()
    if vim.api.nvim_win_is_valid(state.win) then
      vim.api.nvim_set_current_win(state.win)
    end
  end

  local group = vim.api.nvim_create_augroup("AiUi_" .. state.win, { clear = true })

  local function close_all()
    if state.closed then return end
    state.closed = true
    pcall(vim.api.nvim_del_augroup_by_id, group)
    for t, job in pairs(state.jobs) do
      if state.status[t] == "pending" and job and job > 0 then
        pcall(vim.fn.jobstop, job)
        state.status[t] = "cancelled"
      end
    end
    close_window(state.win)
    close_window(state.original_win)
    for _, buf in pairs(state.bufs) do
      if vim.api.nvim_buf_is_valid(buf) then
        pcall(vim.api.nvim_buf_delete, buf, { force = true })
      end
    end
    if state.original_buf and vim.api.nvim_buf_is_valid(state.original_buf) then
      pcall(vim.api.nvim_buf_delete, state.original_buf, { force = true })
    end
  end

  local close_patterns = { tostring(state.win) }
  if mode == "diff" then
    table.insert(close_patterns, tostring(state.original_win))
  end
  vim.api.nvim_create_autocmd("WinClosed", {
    group = group,
    pattern = close_patterns,
    callback = close_all,
  })

  -- Trap focus inside the window cluster: snap back if the user moves away.
  local cluster = { [state.win] = true }
  if mode == "diff" then
    cluster[state.original_win] = true
  end
  local last_focused = state.win
  vim.api.nvim_create_autocmd("WinEnter", {
    group = group,
    callback = function()
      local cur = vim.api.nvim_get_current_win()
      if cluster[cur] then
        last_focused = cur
      elseif vim.api.nvim_win_is_valid(last_focused) then
        vim.schedule(function()
          if vim.api.nvim_win_is_valid(last_focused) then
            vim.api.nvim_set_current_win(last_focused)
          end
        end)
      end
    end,
  })

  -- Pin each window to its expected buffer so :bnext / :bprev / <C-^> can't
  -- replace the contents.
  vim.api.nvim_create_autocmd("BufWinEnter", {
    group = group,
    callback = function()
      if mode == "diff" and vim.api.nvim_win_is_valid(state.original_win)
        and state.original_buf and vim.api.nvim_buf_is_valid(state.original_buf)
        and vim.api.nvim_win_get_buf(state.original_win) ~= state.original_buf then
        vim.schedule(function()
          if vim.api.nvim_win_is_valid(state.original_win)
            and vim.api.nvim_buf_is_valid(state.original_buf) then
            vim.api.nvim_win_set_buf(state.original_win, state.original_buf)
          end
        end)
      end
      if vim.api.nvim_win_is_valid(state.win) then
        local exp = state.bufs[tools[state.active]]
        if exp and vim.api.nvim_buf_is_valid(exp)
          and vim.api.nvim_win_get_buf(state.win) ~= exp then
          vim.schedule(function()
            if vim.api.nvim_win_is_valid(state.win)
              and vim.api.nvim_buf_is_valid(exp) then
              vim.api.nvim_win_set_buf(state.win, exp)
            end
          end)
        end
      end
    end,
  })

  -- Return the active tab's lines, or notify and return nil if not ready.
  local function active_lines()
    local t = tools[state.active]
    if state.status[t] == "pending" then
      vim.notify(t .. " response is still loading.", vim.log.levels.WARN)
      return nil
    end
    if state.status[t] ~= "done" then
      vim.notify(t .. " response is not available.", vim.log.levels.WARN)
      return nil
    end
    return vim.api.nvim_buf_get_lines(state.bufs[t], 0, -1, false), t
  end

  local function popup_accept(paste)
    local lines = active_lines()
    if not lines then return end
    copy_to_clipboard(table.concat(lines, "\n"))
    close_all()
    vim.notify(opts.copy_notify or "Copied to clipboard.")
    if paste then
      vim.cmd("normal! p")
    end
  end

  local function diff_accept()
    local lines, t = active_lines()
    if not lines then return end
    close_all()
    if opts.on_accept then opts.on_accept(t, lines) end
  end

  -- Accept the (possibly dp/do-merged) Original buffer.
  local function diff_accept_merged()
    local lines = vim.api.nvim_buf_get_lines(state.original_buf, 0, -1, false)
    close_all()
    if opts.on_accept_merged then opts.on_accept_merged(lines) end
  end

  local function setup_keymaps(buf)
    if mode == "popup" then
      vim.keymap.set("n", "y", function() popup_accept(false) end,
        { buffer = buf, desc = "Yank active response" })
      vim.keymap.set("n", "p", function() popup_accept(true) end,
        { buffer = buf, desc = "Yank and paste active response" })
      vim.keymap.set("n", "q", close_all,
        { buffer = buf, desc = "Close window" })
    else
      vim.keymap.set("n", "y", diff_accept,
        { buffer = buf, desc = "Replace selection with active response" })
      vim.keymap.set("n", "Y", diff_accept_merged,
        { buffer = buf, desc = "Replace selection with merged original buffer" })
      vim.keymap.set("n", "q", close_all,
        { buffer = buf, desc = "Cancel and close" })
      vim.keymap.set("n", "<C-w>h", focus_original,
        { buffer = buf, desc = "Focus original pane" })
      vim.keymap.set("n", "<C-w><C-h>", focus_original,
        { buffer = buf, desc = "Focus original pane" })
      vim.keymap.set("n", "<C-w>l", focus_response,
        { buffer = buf, desc = "Focus response pane" })
      vim.keymap.set("n", "<C-w><C-l>", focus_response,
        { buffer = buf, desc = "Focus response pane" })
      vim.keymap.set("n", "<C-w>j", function() switch_offset(1) end,
        { buffer = buf, desc = "Next response" })
      vim.keymap.set("n", "<C-w><C-j>", function() switch_offset(1) end,
        { buffer = buf, desc = "Next response" })
      vim.keymap.set("n", "<C-w>k", function() switch_offset(-1) end,
        { buffer = buf, desc = "Prev response" })
      vim.keymap.set("n", "<C-w><C-k>", function() switch_offset(-1) end,
        { buffer = buf, desc = "Prev response" })
    end
    vim.keymap.set("n", "<Tab>", function() switch_offset(1) end,
      { buffer = buf, desc = "Next response" })
    vim.keymap.set("n", "<S-Tab>", function() switch_offset(-1) end,
      { buffer = buf, desc = "Prev response" })
    for i = 1, #tools do
      vim.keymap.set("n", tostring(i), function() switch_to(i) end,
        { buffer = buf, desc = "Switch to response " .. i })
    end
  end

  for _, t in ipairs(tools) do
    setup_keymaps(state.bufs[t])
  end
  if mode == "diff" then
    setup_keymaps(state.original_buf)
  end

  -- Launch each tool's job. The done callback fills that tool's buffer in place.
  for _, t in ipairs(tools) do
    local current = t
    state.jobs[current] = opts.start(current, function(ok, lines, err)
      if state.closed then return end
      local buf = state.bufs[current]
      if not vim.api.nvim_buf_is_valid(buf) then return end

      vim.bo[buf].modifiable = true
      if ok and lines and #lines > 0 then
        state.status[current] = "done"
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
      else
        state.status[current] = "failed"
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
          string.format("[%s failed: %s]", current, err or "unknown error"),
        })
        vim.bo[buf].modifiable = false
      end

      update_title()
      if tools[state.active] == current then
        update_diff()
      end
    end)
  end
end

--- Open a single AI result in a vertical split on the right of the current
--- window and stream one async job's output into it. Unlike run_multi (a
--- floating, multi-tool comparison), this is a real window: it stays put,
--- scrolls, and wraps long lines so prose reports stay readable. `tw` toggles
--- wrap; `y` yanks the whole report; `q` closes it (cancelling a pending job).
--- @param opts table {
---   name = string|nil,         -- buffer name (shown in the statusline)
---   filetype = string|nil,     -- ft for the result buffer
---   winbar = string|nil,       -- header/help line pinned above the buffer
---   wrap = boolean|nil,        -- initial wrap state (default true)
---   copy_notify = string|nil,  -- notify text on yank
---   keymaps = table[]|nil,     -- extra buffer keymaps: { key, desc?, fn(ctx) }
---                              -- ctx = { buf, win, close, status }
---   start = fun(done),         -- start the job; done(ok, lines, err)
--- }
--- @return table { win, buf, close }
function M.open_report(opts)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].filetype = opts.filetype or ""
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "[checking buffer...]" })
  vim.bo[buf].modifiable = false
  pcall(vim.api.nvim_buf_set_name, buf, opts.name or "[AI Report]")

  -- Open to the right of the current window and show our buffer there.
  vim.cmd("rightbelow vsplit")
  local win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(win, buf)
  pcall(vim.api.nvim_win_set_width, win, math.max(40, math.floor(vim.o.columns * 0.4)))

  vim.wo[win].wrap = opts.wrap ~= false
  vim.wo[win].linebreak = true
  vim.wo[win].breakindent = true
  vim.wo[win].number = false
  vim.wo[win].relativenumber = false
  vim.wo[win].signcolumn = "no"
  vim.wo[win].spell = false
  if opts.winbar then
    vim.wo[win].winbar = opts.winbar
  end

  local state = { closed = false, job = nil, status = "pending" }
  local group = vim.api.nvim_create_augroup("AiReport_" .. win, { clear = true })

  local function close()
    if state.closed then return end
    state.closed = true
    pcall(vim.api.nvim_del_augroup_by_id, group)
    if state.status == "pending" and state.job and state.job > 0 then
      pcall(vim.fn.jobstop, state.job)
    end
    close_window(win) -- buffer is bufhidden=wipe, so it goes with the window
  end

  vim.api.nvim_create_autocmd("WinClosed", {
    group = group,
    pattern = tostring(win),
    callback = close,
  })

  local function yank()
    copy_to_clipboard(table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n"))
    vim.notify(opts.copy_notify or "Copied to clipboard.")
  end

  local function toggle_wrap()
    if vim.api.nvim_win_is_valid(win) then
      vim.wo[win].wrap = not vim.wo[win].wrap
      vim.notify("wrap " .. (vim.wo[win].wrap and "on" or "off"))
    end
  end

  vim.keymap.set("n", "q", close, { buffer = buf, desc = "Close report" })
  vim.keymap.set("n", "y", yank, { buffer = buf, desc = "Yank report to clipboard" })
  vim.keymap.set("n", "tw", toggle_wrap, { buffer = buf, desc = "Toggle line wrap" })

  -- Caller-supplied keymaps. Each fn gets a context with the report handle and
  -- the current job status so it can act only once the report is ready.
  for _, km in ipairs(opts.keymaps or {}) do
    vim.keymap.set("n", km.key, function()
      km.fn({ buf = buf, win = win, close = close, status = state.status })
    end, { buffer = buf, desc = km.desc })
  end

  -- Stream the job's result into the buffer.
  state.job = opts.start(function(ok, lines, err)
    if state.closed or not vim.api.nvim_buf_is_valid(buf) then return end
    vim.bo[buf].modifiable = true
    if ok and lines and #lines > 0 then
      state.status = "done"
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    else
      state.status = "failed"
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
        string.format("[check failed: %s]", err or "unknown error"),
      })
    end
    vim.bo[buf].modifiable = false
  end)

  return { win = win, buf = buf, close = close }
end

return M
