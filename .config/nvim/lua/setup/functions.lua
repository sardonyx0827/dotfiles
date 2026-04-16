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
-- [AI solution] generate commit message with Claude Code
---------------------------------------------------------
local function generate_commit_message_with_claude()
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

  vim.notify("Generating commit message with Claude Code...", vim.log.levels.INFO)

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
          title = string.format(" Commit Message (%s) ", diff_type),
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

vim.keymap.set("n", "<leader>cm", generate_commit_message_with_claude,
  { desc = "Generate commit message with Claude Code", noremap = true })
