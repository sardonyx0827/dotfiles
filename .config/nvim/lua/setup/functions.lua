--- @diagnostic disable: undefined-global
---------------------------------------------------------
-- create filepath list from current directory
---------------------------------------------------------
local create_file_path_list_from_current_dir = function()
  local cmd = "find . -type f -print | sed 's|^./||'"
  -- ignore file path list
  local ignore_file_list = {
    ".git/",
    "node_modules/",
    "vendor/",
    "dist/",
    "build/",
    "__pycache__/",
    "env/",
    ".env/",
    "venv/",
    ".venv/",
    ".ruff_cache/",
  }
  -- delete ignored file paths
  for _, ignore_path in ipairs(ignore_file_list) do
    cmd = cmd .. " | grep -v '^" .. ignore_path .. "'"
  end
  -- write it down to the current buffer
  vim.cmd("normal! i" .. cmd)
  vim.cmd(".!sh")

  -- print("execute command: " .. cmd)
end
vim.keymap.set("n", "<leader>lb", create_file_path_list_from_current_dir,
  { desc = 'create file path list from current directory' })


---------------------------------------------------------
-- load buffers from filepath list
---------------------------------------------------------
-- example
-- write "find * -type f"
-- and execute this command ":.!sh"
-- then, execute this command ":lua load_buffers_from_file_list()"
local load_buffers_from_file_list = function()
  local buf = vim.api.nvim_get_current_buf()
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)

  for _, line in ipairs(lines) do
    -- if a buffer is already open with the same name, don't open it again
    local tmp_bufnr = vim.fn.bufnr(line)
    local opened_buffer_list = vim.api.nvim_list_bufs()
    local is_opened = false

    for _, opened_bufnr in ipairs(opened_buffer_list) do
      if opened_bufnr == tmp_bufnr then
        is_opened = true
        break
      end
    end

    if is_opened then
      goto continue
    end

    local bufnr = vim.api.nvim_create_buf(true, false)
    vim.api.nvim_buf_set_name(bufnr, line)
    vim.api.nvim_buf_call(bufnr, vim.cmd.edit)

    ::continue::
  end
  print("load buffers - Processing completed successfully.")
end

vim.keymap.set("n", "<leader>bl", load_buffers_from_file_list, { desc = 'load buffers from file list' })

---------------------------------------------------------
-- codeblock utilities
---------------------------------------------------------
-- Function to move to the next or previous code block
local function move_to_codeblock(direction)
  -- Check if the direction argument is valid
  if direction ~= "next" and direction ~= "prev" then
    print("Invalid direction argument. It should be either 'next' or 'prev'.")
    return
  end

  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local line_count = vim.api.nvim_buf_line_count(0)
  local between_line = line_count
  local step = direction == "next" and 1 or -1
  local limit = direction == "next" and line_count or 1
  local message = direction == "next" and "No next codeblock" or "No prev codeblock"

  -- Loop through the lines based on the direction
  for i = current_line_number, limit, step do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      local filetype = _line:match("^```(%w+)")
      if (direction == "next" and filetype ~= nil) or (direction == "prev" and filetype == nil) then
        between_line = i + step
        break
      end
    end
    if i == limit then
      between_line = limit
      break
    end
  end

  -- Set the cursor position or print a message if no code block is found
  if between_line ~= limit then
    vim.api.nvim_win_set_cursor(0, { between_line, 0 })
  else
    print(message)
    vim.api.nvim_win_set_cursor(0, { current_line_number, 0 })
  end
end
vim.keymap.set("n", "<leader><leader>n", function() move_to_codeblock("next") end,
  { desc = "move to next codeblock text", noremap = true })
vim.keymap.set("n", "<leader><leader>p", function() move_to_codeblock("prev") end,
  { desc = "move to prev codeblock text", noremap = true })

-- This function is used to select the text within a code block in a markdown file.
local function select_codeblock_text()
  local cursor_position = vim.api.nvim_win_get_cursor(0)[1]
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local max_line = vim.api.nvim_buf_line_count(0)
  local start_line, end_line

  -- search upwards for start of code block
  for i = current_line_number, 1, -1 do
    if string.match(vim.fn.getline(i), "^```") then
      start_line = i + 1
      break
    end
  end

  -- search downwards for end of code block
  for i = current_line_number, max_line do
    if string.match(vim.fn.getline(i), "^```") then
      end_line = i - 1
      break
    end
  end

  -- if start_line and end_line are found, select the text
  if start_line and end_line then
    vim.api.nvim_win_set_cursor(0, { start_line, 0 })
    vim.cmd("normal! V")
    vim.api.nvim_win_set_cursor(0, { end_line, 0 })
  else
    print("No code block found. Please move to a codeblock and try again.")
    vim.api.nvim_win_set_cursor(0, { cursor_position, 0 })
  end
end
vim.keymap.set("n", "<leader><leader>s", select_codeblock_text, { desc = "Select codeblock text", noremap = true })

-- copy last codeblock text to clipboard
local function copy_last_codeblock_text_to_clipboard(part)
  if part == nil then
    part = "last"
  end
  if string.match(part, "last") then
    vim.cmd('normal! G')
    move_to_codeblock("prev")
  else
    vim.cmd('normal! gg')
    move_to_codeblock("next")
  end
  select_codeblock_text()
  vim.cmd('normal! "+y')
end
vim.keymap.set("n", "<leader><leader>f", function() copy_last_codeblock_text_to_clipboard("first") end,
  { desc = "Copy first codeblock text to clipboard", noremap = true })
vim.keymap.set("n", "<leader><leader>l", function() copy_last_codeblock_text_to_clipboard("last") end,
  { desc = "Copy last codeblock text to clipboard", noremap = true })

---------------------------------------------------------
-- PWD command
---------------------------------------------------------
local function pwd_command()
  local pwd = vim.fn.getcwd()
  vim.api.nvim_put({ pwd }, 'l', true, true)
end
vim.keymap.set("n", "<leader>ws", pwd_command, { desc = "Put cwd result", noremap = true })

---------------------------------------------------------
-- Copy absolute file path to clipboard
---------------------------------------------------------
local function copy_file_absolute_path()
  local filepath = vim.fn.expand("%:p")
  vim.fn.setreg("+", filepath)
  vim.fn.setreg('"', filepath)
  -- Copy to tmux buffer if running in tmux
  if vim.env.TMUX then
    vim.fn.system("tmux load-buffer -", filepath)
  end
  print("Copied file absolute path to clipboard: " .. filepath)
end
vim.keymap.set("n", "<leader>cp", copy_file_absolute_path,
  { desc = "Copy file absolute path to clipboard", noremap = true })

---------------------------------------------------------
-- [AI solution] copy lsp diagnostics to clipboard for ai assistance
---------------------------------------------------------
local function copy_lsp_diagnostics()
  local diagnostics = vim.diagnostic.get(0)
  -- sort diagnostics by line number
  table.sort(diagnostics, function(a, b)
    return a.lnum < b.lnum
  end)

  -- get relative file path
  local filepath = vim.fn.expand("%:.")

  local lines = { "Can you help me fix the diagnostics in @" .. filepath .. "?" }
  for _, diagnostic in ipairs(diagnostics) do
    local severity_map = {
      [vim.diagnostic.severity.ERROR] = "ERROR",
      [vim.diagnostic.severity.WARN] = "WARN",
      [vim.diagnostic.severity.INFO] = "INFO",
      [vim.diagnostic.severity.HINT] = "HINT"
    }
    local severity = severity_map[diagnostic.severity] or "UNKNOWN"
    local line = diagnostic.lnum + 1
    local col_start = diagnostic.col + 1
    local col_end = diagnostic.end_col and (diagnostic.end_col + 1) or col_start

    table.insert(lines, string.format("[%s] %s @%s :L%d:C%d-C%d",
      severity, diagnostic.message, filepath, line, col_start, col_end))
  end
  if #diagnostics > 0 then
    local content = table.concat(lines, "\n")
    vim.fn.setreg("+", content)
    vim.fn.setreg('"', content)
    -- Copy to tmux buffer if running in tmux
    if vim.env.TMUX then
      vim.fn.system("tmux load-buffer -", content)
    end
    print("Copied LSP diagnostics to clipboard.")
  else
    print("No LSP diagnostics found.")
  end
end
vim.keymap.set("n", "<leader><leader>d", copy_lsp_diagnostics,
  { desc = "Copy LSP diagnostics to clipboard", noremap = true })


---------------------------------------------------------
-- [AI solution] copy all lsp diagnostics to clipboard for ai assistance
---------------------------------------------------------
local function copy_all_lsp_diagnostics()
  local lines = { "Can you help me fix the following diagnostics in my project?" }
  local buffers = vim.api.nvim_list_bufs()

  for _, bufnr in ipairs(buffers) do
    -- Skip invalid or deleted buffers
    if vim.api.nvim_buf_is_valid(bufnr) and vim.api.nvim_buf_is_loaded(bufnr) then
      -- get relative file path
      local filepath = vim.api.nvim_buf_get_name(bufnr)
      -- Skip unnamed buffers
      if filepath ~= "" then
        local relative_path = vim.fn.fnamemodify(filepath, ":.")
        local diagnostics = vim.diagnostic.get(bufnr)
        if #diagnostics > 0 then
          -- sort diagnostics by line number
          table.sort(diagnostics, function(a, b)
            return a.lnum < b.lnum
          end)
          for _, diagnostic in ipairs(diagnostics) do
            local severity_map = {
              [vim.diagnostic.severity.ERROR] = "ERROR",
              [vim.diagnostic.severity.WARN] = "WARN",
              [vim.diagnostic.severity.INFO] = "INFO",
              [vim.diagnostic.severity.HINT] = "HINT"
            }
            local severity = severity_map[diagnostic.severity] or "UNKNOWN"
            local line = diagnostic.lnum + 1
            local col_start = diagnostic.col + 1
            local col_end = diagnostic.end_col and (diagnostic.end_col + 1) or col_start
            table.insert(lines, string.format("[%s] %s @%s :L%d:C%d-C%d",
              severity, diagnostic.message, relative_path, line, col_start, col_end))
          end
        end
      end
    end
  end

  if #lines > 1 then
    local content = table.concat(lines, "\n")
    vim.fn.setreg("+", content)
    vim.fn.setreg('"', content)
    -- Copy to tmux buffer if running in tmux
    if vim.env.TMUX then
      vim.fn.system("tmux load-buffer -", content)
    end
    print("Copied all LSP diagnostics to clipboard.")
  else
    print("No LSP diagnostics found.")
  end
end
vim.keymap.set("n", "<leader><leader>a", copy_all_lsp_diagnostics,
  { desc = "Copy all LSP diagnostics to clipboard", noremap = true })

---------------------------------------------------------
-- [AI solution] get file and line info visual selection
---------------------------------------------------------
_G.get_file_line_info_visual = function(start_line, end_line)
  if not start_line or not end_line or start_line == 0 or end_line == 0 then
    print("No visual selection found.")
    return
  end

  if start_line > end_line then
    start_line, end_line = end_line, start_line
  end

  local filepath = vim.fn.expand("%:.")
  local content
  if start_line == end_line then
    content = string.format("@%s#L%d", filepath, start_line)
  else
    content = string.format("@%s#L%d-%d", filepath, start_line, end_line)
  end
  vim.fn.setreg("+", content)
  vim.fn.setreg('"', content)
  -- Copy to tmux buffer if running in tmux
  if vim.env.TMUX then
    vim.fn.system("tmux load-buffer -", content)
  end
  print("Copied file and line info to clipboard.")
end

vim.keymap.set("x", "<leader><leader>c",
  ":<C-u>lua get_file_line_info_visual(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"))<CR>",
  { desc = "Get file and line info from visual selection", noremap = true, silent = true })

---------------------------------------------------------
-- close current buffer
---------------------------------------------------------
local function close_current_buffer()
  local current_buf = vim.api.nvim_get_current_buf()
  if vim.api.nvim_buf_is_loaded(current_buf) then
    vim.api.nvim_buf_delete(current_buf, { force = true })
  end
end
vim.keymap.set("n", "<C-q>", close_current_buffer, { noremap = true, silent = true, desc = "Close Current Buffer" })
vim.keymap.set("n", "<leader>bc", close_current_buffer, { noremap = true, silent = true, desc = "Close Current Buffer" })

---------------------------------------------------------
-- [AI solution] generate commit message with Claude Code / Codex
---------------------------------------------------------
local function generate_commit_message(tool)
  if tool ~= "claude" and tool ~= "codex" then
    tool = "claude"
  end

  local diff = vim.fn.system("git diff --cached")
  if vim.v.shell_error ~= 0 then
    vim.notify("Not a git repository.", vim.log.levels.ERROR)
    return
  end

  local diff_type = "staged"
  if diff == "" then
    diff = vim.fn.system("git diff")
    diff_type = "unstaged"
  end
  if diff == "" then
    vim.notify("No changes detected.", vim.log.levels.WARN)
    return
  end

  vim.notify("Generating commit message with " .. tool .. "...", vim.log.levels.INFO)

  local tmpfile = vim.fn.tempname()
  vim.fn.writefile(vim.split(diff, "\n"), tmpfile)

  local prompt = "Generate a git commit message for the following diff. "
      .. "Follow Conventional Commits format (e.g. feat:, fix:, refactor:, docs:, test:, chore:). "
      .. "Reply ONLY with the commit message, no markdown formatting, no explanation, no surrounding quotes. "
      .. "Keep the summary line under 50 characters. Add a body separated by a blank line if the change is complex. "
      .. "Write in English."

  local result_lines = {}
  local cmd = string.format("cat %s | claude --model haiku -p %s",
    vim.fn.shellescape(tmpfile),
    vim.fn.shellescape(prompt))

  if tool ~= "claude" then
    cmd = string.format("cat %s | codex exec %s",
      vim.fn.shellescape(tmpfile),
      vim.fn.shellescape(prompt))
  end

  vim.fn.jobstart({ "sh", "-c", cmd }, {
    stdout_buffered = true,
    on_stdout = function(_, data)
      if data then
        -- Remove trailing empty string from buffered output
        if #data > 0 and data[#data] == "" then
          table.remove(data)
        end
        result_lines = data
      end
    end,
    on_exit = function(_, exit_code)
      vim.fn.delete(tmpfile)
      vim.schedule(function()
        if exit_code ~= 0 or #result_lines == 0 then
          vim.notify("Failed to generate commit message.", vim.log.levels.ERROR)
          return
        end

        local buf = vim.api.nvim_create_buf(false, true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, result_lines)

        local max_line_width = 0
        for _, l in ipairs(result_lines) do
          max_line_width = math.max(max_line_width, #l)
        end
        local width = math.min(math.max(60, max_line_width + 4), vim.o.columns - 4)
        local height = math.min(#result_lines + 2, vim.o.lines - 4)

        local win = vim.api.nvim_open_win(buf, true, {
          relative = "editor",
          width = width,
          height = height,
          row = math.floor((vim.o.lines - height) / 2),
          col = math.floor((vim.o.columns - width) / 2),
          style = "minimal",
          border = "rounded",
          title = string.format(" Commit Message (%s, %s) ", tool, diff_type),
          title_pos = "center",
          footer = " y:yank  p:paste  q:close ",
          footer_pos = "center",
        })

        vim.bo[buf].modifiable = true
        vim.bo[buf].filetype = "gitcommit"

        -- Accept: copy to clipboard and close
        vim.keymap.set("n", "y", function()
          local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
          local msg = table.concat(lines, "\n")
          vim.fn.setreg("+", msg)
          vim.fn.setreg('"', msg)
          if vim.env.TMUX then
            vim.fn.system("tmux load-buffer -", msg)
          end
          vim.api.nvim_win_close(win, true)
          vim.notify("Commit message copied to clipboard.")
        end, { buf = buf, desc = "Accept and copy commit message" })

        -- Accept: copy to clipboard and close and paste
        vim.keymap.set("n", "p", function()
          local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
          local msg = table.concat(lines, "\n")
          vim.fn.setreg("+", msg)
          vim.fn.setreg('"', msg)
          if vim.env.TMUX then
            vim.fn.system("tmux load-buffer -", msg)
          end
          vim.api.nvim_win_close(win, true)
          vim.notify("Commit message copied to clipboard.")
          vim.cmd("normal! p")
        end, { buf = buf, desc = "Accept and copy commit message" })

        -- Close without action
        vim.keymap.set("n", "q", function()
          vim.api.nvim_win_close(win, true)
        end, { buf = buf, desc = "Close commit message window" })
      end)
    end,
  })
end

vim.keymap.set("n", "<leader>cm", function() generate_commit_message("claude") end,
  { desc = "Generate commit message with Claude Code", noremap = true })
vim.keymap.set("n", "<leader>cx", function() generate_commit_message("codex") end,
  { desc = "Generate commit message with Codex", noremap = true })


---------------------------------------------------------
-- [AI solution] Select a range, open a prompt window to ask the AI(Claude Code / Codex / Gemini), and replace the selected range with the AI's response
---------------------------------------------------------
_G.ask_ai_and_replace_selection = function(start_line, end_line, tool)
  if not start_line or not end_line or start_line == 0 or end_line == 0 then
    vim.notify("No visual selection found.", vim.log.levels.ERROR)
    return
  end
  if start_line > end_line then
    start_line, end_line = end_line, start_line
  end
  if tool ~= "claude" and tool ~= "codex" and tool ~= "gemini" and tool ~= "all" then
    tool = "claude"
  end

  -- Capture target window/buffer so we can replace the range later
  local target_buf = vim.api.nvim_get_current_buf()
  local selected_lines = vim.api.nvim_buf_get_lines(target_buf, start_line - 1, end_line, false)
  local filetype = vim.bo[target_buf].filetype
  local lang = filetype ~= "" and filetype or "plain text"

  -- Open prompt window for user instruction
  local prompt_buf = vim.api.nvim_create_buf(false, true)
  local prompt_width = math.min(80, vim.o.columns - 4)
  local prompt_height = math.min(10, vim.o.lines - 4)
  local prompt_win = vim.api.nvim_open_win(prompt_buf, true, {
    relative = "editor",
    width = prompt_width,
    height = prompt_height,
    row = math.floor((vim.o.lines - prompt_height) / 2),
    col = math.floor((vim.o.columns - prompt_width) / 2),
    style = "minimal",
    border = "rounded",
    title = string.format(" Ask %s (lines %d-%d, %s) ", tool, start_line, end_line, lang),
    title_pos = "center",
    footer = " <C-s>:submit  q:cancel(normal) ",
    footer_pos = "center",
  })

  vim.bo[prompt_buf].modifiable = true
  vim.bo[prompt_buf].filetype = "markdown"
  vim.cmd("startinsert")

  local function close_window(win)
    if win and vim.api.nvim_win_is_valid(win) then
      vim.api.nvim_win_close(win, true)
    end
  end

  -- Trap focus inside the prompt window: snap back if the user moves away
  local prompt_group = vim.api.nvim_create_augroup(
    "AskAiPrompt_" .. prompt_win, { clear = true })
  vim.api.nvim_create_autocmd("WinClosed", {
    group = prompt_group,
    pattern = tostring(prompt_win),
    callback = function()
      pcall(vim.api.nvim_del_augroup_by_id, prompt_group)
    end,
  })
  vim.api.nvim_create_autocmd("WinEnter", {
    group = prompt_group,
    callback = function()
      if vim.api.nvim_get_current_win() ~= prompt_win
        and vim.api.nvim_win_is_valid(prompt_win) then
        vim.schedule(function()
          if vim.api.nvim_win_is_valid(prompt_win) then
            vim.api.nvim_set_current_win(prompt_win)
          end
        end)
      end
    end,
  })

  vim.keymap.set("n", "q", function() close_window(prompt_win) end,
    { buffer = prompt_buf, desc = "Cancel prompt" })

  local submit = function()
    local prompt_lines = vim.api.nvim_buf_get_lines(prompt_buf, 0, -1, false)
    local user_prompt = vim.trim(table.concat(prompt_lines, "\n"))
    if user_prompt == "" then
      vim.notify("Prompt is empty.", vim.log.levels.WARN)
      return
    end
    close_window(prompt_win)
    vim.notify("Asking " .. tool .. "...", vim.log.levels.INFO)

    local system_prompt = string.format(
      "You are an AI assistant integrated into a Neovim editor. "
        .. "The selected %s code/text is provided via stdin. "
        .. "Apply the user's request and reply ONLY with the resulting text that should replace the selection. "
        .. "Do NOT wrap the output in markdown code fences. "
        .. "Do NOT include explanations, preambles, or trailing commentary. "
        .. "Preserve the original indentation style of the input.\n\n"
        .. "## User Request\n%s",
      lang,
      user_prompt
    )

    local tmpfile = vim.fn.tempname()
    vim.fn.writefile(selected_lines, tmpfile)

    local function build_cmd(t)
      if t == "codex" then
        return string.format("cat %s | codex exec --skip-git-repo-check %s",
          vim.fn.shellescape(tmpfile),
          vim.fn.shellescape(system_prompt))
      elseif t == "gemini" then
        return string.format("cat %s | gemini -m gemini-3.1-flash-lite-preview -p %s",
          vim.fn.shellescape(tmpfile),
          vim.fn.shellescape(system_prompt))
      else
        return string.format("cat %s | claude --model sonnet -p %s",
          vim.fn.shellescape(tmpfile),
          vim.fn.shellescape(system_prompt))
      end
    end

    if tool == "all" then
      local tools_order = { "claude", "codex" }
      local results = {}
      local pending = #tools_order

      local function open_multi_panel()
        -- Substitute a placeholder for any tool that failed so the UI still renders
        for _, t in ipairs(tools_order) do
          local r = results[t]
          if r.exit_code ~= 0 or #r.lines == 0 then
            r.lines = { string.format("[%s failed (exit code %d)]", t, r.exit_code) }
          end
        end

        local total_width = math.min(vim.o.columns - 4, 200)
        local pane_width = math.floor((total_width - 2) / 2)
        local max_lines = #selected_lines
        for _, t in ipairs(tools_order) do
          max_lines = math.max(max_lines, #results[t].lines)
        end
        -- Ensure each stacked right panel has at least ~6 inner rows
        local height = math.min(math.max(max_lines + 2, 22), vim.o.lines - 4)
        local row = math.floor((vim.o.lines - height) / 2)
        local left_col = math.floor((vim.o.columns - total_width) / 2)
        local right_col = left_col + pane_width + 2

        local original_buf = vim.api.nvim_create_buf(false, true)
        vim.api.nvim_buf_set_lines(original_buf, 0, -1, false, selected_lines)
        local original_win = vim.api.nvim_open_win(original_buf, false, {
          relative = "editor",
          width = pane_width,
          height = height,
          row = row,
          col = left_col,
          border = "rounded",
          title = " Original ",
          title_pos = "center",
        })
        vim.bo[original_buf].filetype = filetype
        vim.bo[original_buf].modifiable = false

        -- Stack the response panels on the right. Match the left pane's outer span.
        local right_total_outer = height + 2
        local panel_outer = math.floor(right_total_outer / #tools_order)
        local panels = {}
        for i, t in ipairs(tools_order) do
          local outer = panel_outer
          if i == #tools_order then
            outer = right_total_outer - panel_outer * (i - 1)
          end
          local inner = math.max(outer - 2, 1)
          local panel_row = row + panel_outer * (i - 1)

          local buf = vim.api.nvim_create_buf(false, true)
          vim.api.nvim_buf_set_lines(buf, 0, -1, false, results[t].lines)

          local win = vim.api.nvim_open_win(buf, i == 1, {
            relative = "editor",
            width = pane_width,
            height = inner,
            row = panel_row,
            col = right_col,
            border = "rounded",
            title = string.format(" %s's Response ", t),
            title_pos = "center",
            footer = " y:replace  q:cancel  <C-w>j/k:next/prev ",
            footer_pos = "center",
          })

          vim.bo[buf].filetype = filetype
          vim.bo[buf].modifiable = true
          panels[i] = { buf = buf, win = win, tool = t }
        end

        vim.cmd("stopinsert")

        local function close_all()
          close_window(original_win)
          for _, p in ipairs(panels) do
            close_window(p.win)
          end
        end

        local close_patterns = { tostring(original_win) }
        for _, p in ipairs(panels) do
          table.insert(close_patterns, tostring(p.win))
        end
        local group = vim.api.nvim_create_augroup(
          "AskAiMultiDiff_" .. panels[1].win, { clear = true })
        vim.api.nvim_create_autocmd("WinClosed", {
          group = group,
          pattern = close_patterns,
          callback = function()
            close_all()
            pcall(vim.api.nvim_del_augroup_by_id, group)
          end,
        })

        -- Enable diff between Original and the initially focused response pane
        vim.api.nvim_win_call(original_win, function() vim.cmd("diffthis") end)
        vim.api.nvim_win_call(panels[1].win, function() vim.cmd("diffthis") end)

        -- Trap focus inside the cluster; remember the last visited response pane.
        -- Swap diff to whichever response pane currently has focus.
        local cluster = { [original_win] = true }
        for _, p in ipairs(panels) do
          cluster[p.win] = true
        end
        local last_panel_idx = 1
        local current_diff_idx = 1
        vim.api.nvim_create_autocmd("WinEnter", {
          group = group,
          callback = function()
            local cur = vim.api.nvim_get_current_win()
            if cluster[cur] then
              for i, p in ipairs(panels) do
                if p.win == cur then
                  if i ~= current_diff_idx then
                    local prev = panels[current_diff_idx]
                    if vim.api.nvim_win_is_valid(prev.win) then
                      vim.api.nvim_win_call(prev.win, function() vim.cmd("diffoff") end)
                    end
                    vim.api.nvim_win_call(p.win, function() vim.cmd("diffthis") end)
                    if vim.api.nvim_win_is_valid(original_win) then
                      vim.api.nvim_win_call(original_win, function() vim.cmd("diffthis") end)
                    end
                    current_diff_idx = i
                  end
                  last_panel_idx = i
                  break
                end
              end
            else
              vim.schedule(function()
                if vim.api.nvim_win_is_valid(panels[last_panel_idx].win) then
                  vim.api.nvim_set_current_win(panels[last_panel_idx].win)
                end
              end)
            end
          end,
        })

        local function focus_original()
          if vim.api.nvim_win_is_valid(original_win) then
            vim.api.nvim_set_current_win(original_win)
          end
        end
        local function focus_panel(idx)
          if panels[idx] and vim.api.nvim_win_is_valid(panels[idx].win) then
            vim.api.nvim_set_current_win(panels[idx].win)
          end
        end
        local function focus_panel_offset(offset)
          local cur = vim.api.nvim_get_current_win()
          for i, p in ipairs(panels) do
            if p.win == cur then
              local n = #panels
              local new_i = ((i - 1 + offset) % n) + 1
              focus_panel(new_i)
              return
            end
          end
        end

        for _, p in ipairs(panels) do
          local panel = p
          vim.keymap.set("n", "y", function()
            local lines = vim.api.nvim_buf_get_lines(panel.buf, 0, -1, false)
            close_all()
            if vim.api.nvim_buf_is_valid(target_buf) then
              vim.api.nvim_buf_set_lines(target_buf, start_line - 1, end_line, false, lines)
              vim.notify(string.format("Selection replaced with %s's response.", panel.tool))
            else
              vim.notify("Target buffer no longer valid.", vim.log.levels.ERROR)
            end
          end, { buffer = panel.buf, desc = "Replace selection with " .. panel.tool .. "'s response" })

          vim.keymap.set("n", "q", close_all,
            { buffer = panel.buf, desc = "Cancel replacement" })

          vim.keymap.set("n", "<C-w>h", focus_original,
            { buffer = panel.buf, desc = "Focus original pane" })
          vim.keymap.set("n", "<C-w><C-h>", focus_original,
            { buffer = panel.buf, desc = "Focus original pane" })
          vim.keymap.set("n", "<C-w>j", function() focus_panel_offset(1) end,
            { buffer = panel.buf, desc = "Focus next response pane" })
          vim.keymap.set("n", "<C-w><C-j>", function() focus_panel_offset(1) end,
            { buffer = panel.buf, desc = "Focus next response pane" })
          vim.keymap.set("n", "<C-w>k", function() focus_panel_offset(-1) end,
            { buffer = panel.buf, desc = "Focus prev response pane" })
          vim.keymap.set("n", "<C-w><C-k>", function() focus_panel_offset(-1) end,
            { buffer = panel.buf, desc = "Focus prev response pane" })
        end

        vim.keymap.set("n", "q", close_all,
          { buffer = original_buf, desc = "Cancel replacement" })
        vim.keymap.set("n", "<C-w>l", function() focus_panel(last_panel_idx) end,
          { buffer = original_buf, desc = "Focus response pane" })
        vim.keymap.set("n", "<C-w><C-l>", function() focus_panel(last_panel_idx) end,
          { buffer = original_buf, desc = "Focus response pane" })
      end

      for _, t in ipairs(tools_order) do
        local current_t = t
        local result_lines = {}
        vim.fn.jobstart({ "sh", "-c", build_cmd(current_t) }, {
          stdout_buffered = true,
          on_stdout = function(_, data)
            if data then
              if #data > 0 and data[#data] == "" then
                table.remove(data)
              end
              result_lines = data
            end
          end,
          on_exit = function(_, exit_code)
            results[current_t] = { exit_code = exit_code, lines = result_lines }
            pending = pending - 1
            if pending == 0 then
              vim.fn.delete(tmpfile)
              vim.schedule(open_multi_panel)
            end
          end,
        })
      end
      return
    end

    local cmd = build_cmd(tool)
    local result_lines = {}
    vim.fn.jobstart({ "sh", "-c", cmd }, {
      stdout_buffered = true,
      on_stdout = function(_, data)
        if data then
          if #data > 0 and data[#data] == "" then
            table.remove(data)
          end
          result_lines = data
        end
      end,
      on_exit = function(_, exit_code)
        vim.fn.delete(tmpfile)
        vim.schedule(function()
          if exit_code ~= 0 or #result_lines == 0 then
            vim.notify("Failed to get response from " .. tool, vim.log.levels.ERROR)
            return
          end

          -- Prepare two scratch buffers: left = original selection, right = response
          local original_buf = vim.api.nvim_create_buf(false, true)
          local preview_buf = vim.api.nvim_create_buf(false, true)
          vim.api.nvim_buf_set_lines(original_buf, 0, -1, false, selected_lines)
          vim.api.nvim_buf_set_lines(preview_buf, 0, -1, false, result_lines)

          -- Layout: side-by-side floating windows occupying most of the editor
          local total_width = math.min(vim.o.columns - 4, 200)
          local pane_width = math.floor((total_width - 2) / 2)
          local max_lines = math.max(#selected_lines, #result_lines)
          local height = math.min(max_lines + 2, vim.o.lines - 4)
          local row = math.floor((vim.o.lines - height) / 2)
          local left_col = math.floor((vim.o.columns - total_width) / 2)
          local right_col = left_col + pane_width + 2

          local original_win = vim.api.nvim_open_win(original_buf, false, {
            relative = "editor",
            width = pane_width,
            height = height,
            row = row,
            col = left_col,
            border = "rounded",
            title = " Original ",
            title_pos = "center",
          })

          local preview_win = vim.api.nvim_open_win(preview_buf, true, {
            relative = "editor",
            width = pane_width,
            height = height,
            row = row,
            col = right_col,
            border = "rounded",
            title = tool .. "'s Response ",
            title_pos = "center",
            footer = " y:replace  q:cancel ",
            footer_pos = "center",
          })

          vim.bo[original_buf].filetype = filetype
          vim.bo[preview_buf].filetype = filetype
          vim.bo[original_buf].modifiable = false
          vim.bo[preview_buf].modifiable = true

          -- Enable diff mode on both windows for inline change highlighting
          vim.api.nvim_win_call(original_win, function() vim.cmd("diffthis") end)
          vim.api.nvim_win_call(preview_win, function() vim.cmd("diffthis") end)

          -- Ensure normal mode in case the prompt window was submitted from insert mode
          vim.cmd("stopinsert")

          local function close_diff()
            close_window(original_win)
            close_window(preview_win)
          end

          -- If either window is closed externally, tear down the other too
          local group = vim.api.nvim_create_augroup(
            "AskAiDiff_" .. preview_win, { clear = true })
          vim.api.nvim_create_autocmd("WinClosed", {
            group = group,
            pattern = { tostring(original_win), tostring(preview_win) },
            callback = function()
              close_diff()
              pcall(vim.api.nvim_del_augroup_by_id, group)
            end,
          })

          -- Trap focus inside the diff pair: if the user moves out (e.g. <C-w>h to
          -- a background window), snap back to the last visited diff pane.
          local last_diff_win = preview_win
          vim.api.nvim_create_autocmd("WinEnter", {
            group = group,
            callback = function()
              local cur = vim.api.nvim_get_current_win()
              if cur == original_win or cur == preview_win then
                last_diff_win = cur
              elseif vim.api.nvim_win_is_valid(last_diff_win) then
                vim.schedule(function()
                  if vim.api.nvim_win_is_valid(last_diff_win) then
                    vim.api.nvim_set_current_win(last_diff_win)
                  end
                end)
              end
            end,
          })

          -- Accept: replace the original selected range with the (possibly edited) response
          local accept = function()
            local lines = vim.api.nvim_buf_get_lines(preview_buf, 0, -1, false)
            close_diff()
            if vim.api.nvim_buf_is_valid(target_buf) then
              vim.api.nvim_buf_set_lines(target_buf, start_line - 1, end_line, false, lines)
              vim.notify("Selection replaced with %s's response.", tool)
            else
              vim.notify("Target buffer no longer valid.", vim.log.levels.ERROR)
            end
          end
          local cancel = function() close_diff() end

          local focus_left = function()
            if vim.api.nvim_win_is_valid(original_win) then
              vim.api.nvim_set_current_win(original_win)
            end
          end
          local focus_right = function()
            if vim.api.nvim_win_is_valid(preview_win) then
              vim.api.nvim_set_current_win(preview_win)
            end
          end

          for _, buf in ipairs({ original_buf, preview_buf }) do
            vim.keymap.set("n", "y", accept,
              { buffer = buf, desc = "Replace selection with response" })
            vim.keymap.set("n", "q", cancel,
              { buffer = buf, desc = "Cancel replacement" })
            -- Constrain window movement to only the two diff panes
            vim.keymap.set("n", "<C-w>h", focus_left,
              { buffer = buf, desc = "Focus original pane" })
            vim.keymap.set("n", "<C-w><C-h>", focus_left,
              { buffer = buf, desc = "Focus original pane" })
            vim.keymap.set("n", "<C-w>l", focus_right,
              { buffer = buf, desc = "Focus response pane" })
            vim.keymap.set("n", "<C-w><C-l>", focus_right,
              { buffer = buf, desc = "Focus response pane" })
          end
        end)
      end,
    })
  end

  vim.keymap.set({ "n", "i" }, "<C-s>", submit,
    { buffer = prompt_buf, desc = "Submit prompt to " .. tool })
end

vim.keymap.set("x", "<C-c>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'claude')<CR>",
  { desc = "Ask AI(Claude) and replace selection", noremap = true, silent = true })
vim.keymap.set("x", "<C-x>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'codex')<CR>",
  { desc = "Ask AI(Codex) and replace selection", noremap = true, silent = true })
vim.keymap.set("x", "<C-g>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'gemini')<CR>",
  { desc = "Ask AI(Gemini) and replace selection", noremap = true, silent = true })
vim.keymap.set("x", "<C-l>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'all')<CR>",
  { desc = "Ask AI(All: Claude/Codex/Gemini) and replace selection", noremap = true, silent = true })
