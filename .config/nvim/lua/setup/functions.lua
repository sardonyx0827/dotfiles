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
  if tool ~= "claude" and tool ~= "codex" and tool ~= "gemini" and tool ~= "copilot" and tool ~= "all" then
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
      elseif t == "copilot" then
        -- copilot CLI does not read stdin as context, so inline the selection into the prompt.
        local copilot_prompt = string.format(
          "%s\n\n## Selected %s code\n```%s\n%s\n```",
          system_prompt,
          lang,
          lang,
          table.concat(selected_lines, "\n")
        )
        return string.format("copilot --model gpt-5-mini -s -p %s",
          vim.fn.shellescape(copilot_prompt))
      else
        return string.format("cat %s | claude --model sonnet -p %s",
          vim.fn.shellescape(tmpfile),
          vim.fn.shellescape(system_prompt))
      end
    end

    if tool == "all" then
      local tools_order = { "claude", "codex", "gemini", "copilot" }
      local state = {
        buffers = {},
        jobs = {},
        status = {},
        active_idx = 1,
        closed = false,
      }
      local pending_jobs = #tools_order

      local function build_title()
        local parts = {}
        for i, t in ipairs(tools_order) do
          local marker = ""
          if state.status[t] == "pending" then
            marker = " (loading)"
          elseif state.status[t] == "failed" then
            marker = " (failed)"
          elseif state.status[t] == "cancelled" then
            marker = " (cancelled)"
          end
          local label = t .. marker
          if i == state.active_idx then
            table.insert(parts, "[" .. label .. "]")
          else
            table.insert(parts, label)
          end
        end
        return " " .. table.concat(parts, " | ") .. " "
      end

      -- Pre-create per-tool buffers so the UI can render before any job completes.
      for _, t in ipairs(tools_order) do
        local buf = vim.api.nvim_create_buf(false, true)
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
          string.format("[%s: waiting for response...]", t),
        })
        vim.bo[buf].filetype = filetype
        vim.bo[buf].modifiable = false
        state.buffers[t] = buf
        state.status[t] = "pending"
      end

      -- Fixed-size side-by-side panes for consistent comparison
      local total_width = math.min(vim.o.columns - 4, 200)
      local pane_width = math.floor((total_width - 2) / 2)
      local height = math.min(math.max(#selected_lines + 2, 24), vim.o.lines - 4)
      local row = math.floor((vim.o.lines - height) / 2)
      local left_col = math.floor((vim.o.columns - total_width) / 2)
      local right_col = left_col + pane_width + 2

      local original_buf = vim.api.nvim_create_buf(false, true)
      vim.api.nvim_buf_set_lines(original_buf, 0, -1, false, selected_lines)
      vim.bo[original_buf].filetype = filetype
      vim.bo[original_buf].modifiable = false

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

      local response_win = vim.api.nvim_open_win(state.buffers[tools_order[1]], true, {
        relative = "editor",
        width = pane_width,
        height = height,
        row = row,
        col = right_col,
        border = "rounded",
        title = build_title(),
        title_pos = "center",
        footer = " y:replace  q:cancel  <Tab>/<S-Tab>:switch  1/2/3:jump ",
        footer_pos = "center",
      })

      vim.cmd("stopinsert")

      local function update_title()
        if not vim.api.nvim_win_is_valid(response_win) then return end
        local cfg = vim.api.nvim_win_get_config(response_win)
        cfg.title = build_title()
        pcall(vim.api.nvim_win_set_config, response_win, cfg)
      end

      -- Diff only when the active response is real content; loading/failed
      -- placeholders would otherwise mark every line as changed.
      local function update_diff()
        local active_t = tools_order[state.active_idx]
        if state.status[active_t] == "done" then
          if vim.api.nvim_win_is_valid(original_win) then
            vim.api.nvim_win_call(original_win, function() vim.cmd("diffthis") end)
          end
          if vim.api.nvim_win_is_valid(response_win) then
            vim.api.nvim_win_call(response_win, function() vim.cmd("diffthis") end)
          end
        else
          if vim.api.nvim_win_is_valid(original_win) then
            vim.api.nvim_win_call(original_win, function() vim.cmd("diffoff") end)
          end
          if vim.api.nvim_win_is_valid(response_win) then
            vim.api.nvim_win_call(response_win, function() vim.cmd("diffoff") end)
          end
        end
      end

      local function switch_to(idx)
        if idx < 1 or idx > #tools_order then return end
        -- Tear down diff before the buffer swap; otherwise the Original pane
        -- keeps highlights derived from the previous comparison.
        if vim.api.nvim_win_is_valid(original_win) then
          vim.api.nvim_win_call(original_win, function() vim.cmd("diffoff") end)
        end
        if vim.api.nvim_win_is_valid(response_win) then
          vim.api.nvim_win_call(response_win, function() vim.cmd("diffoff") end)
        end
        state.active_idx = idx
        local buf = state.buffers[tools_order[idx]]
        if vim.api.nvim_buf_is_valid(buf) and vim.api.nvim_win_is_valid(response_win) then
          vim.api.nvim_win_set_buf(response_win, buf)
        end
        update_title()
        update_diff()
      end

      local function switch_offset(offset)
        local n = #tools_order
        local new_idx = ((state.active_idx - 1 + offset) % n) + 1
        switch_to(new_idx)
      end

      local function cancel_pending_jobs()
        for t, job_id in pairs(state.jobs) do
          if state.status[t] == "pending" and job_id and job_id > 0 then
            pcall(vim.fn.jobstop, job_id)
            state.status[t] = "cancelled"
          end
        end
      end

      local group = vim.api.nvim_create_augroup(
        "AskAiAllTabs_" .. response_win, { clear = true })

      local function close_all()
        if state.closed then return end
        state.closed = true
        pcall(vim.api.nvim_del_augroup_by_id, group)
        cancel_pending_jobs()
        close_window(original_win)
        close_window(response_win)
        for _, buf in pairs(state.buffers) do
          if vim.api.nvim_buf_is_valid(buf) then
            pcall(vim.api.nvim_buf_delete, buf, { force = true })
          end
        end
        if vim.api.nvim_buf_is_valid(original_buf) then
          pcall(vim.api.nvim_buf_delete, original_buf, { force = true })
        end
      end

      vim.api.nvim_create_autocmd("WinClosed", {
        group = group,
        pattern = { tostring(original_win), tostring(response_win) },
        callback = close_all,
      })

      -- Trap focus inside the cluster
      local cluster = { [original_win] = true, [response_win] = true }
      local last_focused = response_win
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

      local function focus_original()
        if vim.api.nvim_win_is_valid(original_win) then
          vim.api.nvim_set_current_win(original_win)
        end
      end
      local function focus_response()
        if vim.api.nvim_win_is_valid(response_win) then
          vim.api.nvim_set_current_win(response_win)
        end
      end

      local function accept()
        local active_t = tools_order[state.active_idx]
        if state.status[active_t] == "pending" then
          vim.notify(active_t .. " response is still loading.", vim.log.levels.WARN)
          return
        end
        if state.status[active_t] ~= "done" then
          vim.notify(active_t .. " response is not available.", vim.log.levels.WARN)
          return
        end
        local active_buf = state.buffers[active_t]
        local lines = vim.api.nvim_buf_get_lines(active_buf, 0, -1, false)
        close_all()
        if vim.api.nvim_buf_is_valid(target_buf) then
          vim.api.nvim_buf_set_lines(target_buf, start_line - 1, end_line, false, lines)
          vim.notify(string.format("Selection replaced with %s's response.", active_t))
        else
          vim.notify("Target buffer no longer valid.", vim.log.levels.ERROR)
        end
      end

      local function setup_keymaps(buf)
        vim.keymap.set("n", "y", accept,
          { buffer = buf, desc = "Replace selection with active response" })
        vim.keymap.set("n", "q", close_all,
          { buffer = buf, desc = "Cancel and close" })
        vim.keymap.set("n", "<Tab>", function() switch_offset(1) end,
          { buffer = buf, desc = "Next AI response" })
        vim.keymap.set("n", "<S-Tab>", function() switch_offset(-1) end,
          { buffer = buf, desc = "Prev AI response" })
        vim.keymap.set("n", "<C-w>j", function() switch_offset(1) end,
          { buffer = buf, desc = "Next AI response" })
        vim.keymap.set("n", "<C-w><C-j>", function() switch_offset(1) end,
          { buffer = buf, desc = "Next AI response" })
        vim.keymap.set("n", "<C-w>k", function() switch_offset(-1) end,
          { buffer = buf, desc = "Prev AI response" })
        vim.keymap.set("n", "<C-w><C-k>", function() switch_offset(-1) end,
          { buffer = buf, desc = "Prev AI response" })
        vim.keymap.set("n", "<C-w>h", focus_original,
          { buffer = buf, desc = "Focus original pane" })
        vim.keymap.set("n", "<C-w><C-h>", focus_original,
          { buffer = buf, desc = "Focus original pane" })
        vim.keymap.set("n", "<C-w>l", focus_response,
          { buffer = buf, desc = "Focus response pane" })
        vim.keymap.set("n", "<C-w><C-l>", focus_response,
          { buffer = buf, desc = "Focus response pane" })
        for i = 1, #tools_order do
          vim.keymap.set("n", tostring(i), function() switch_to(i) end,
            { buffer = buf, desc = "Switch to response " .. i })
        end
      end

      setup_keymaps(original_buf)
      for _, t in ipairs(tools_order) do
        setup_keymaps(state.buffers[t])
      end

      -- Launch all jobs in parallel; each completion fills its own buffer in place.
      for _, t in ipairs(tools_order) do
        local current_t = t
        local result_lines = {}
        local job_id = vim.fn.jobstart({ "sh", "-c", build_cmd(current_t) }, {
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
            pending_jobs = pending_jobs - 1
            if pending_jobs == 0 then
              vim.fn.delete(tmpfile)
            end
            vim.schedule(function()
              if state.closed then return end
              local buf = state.buffers[current_t]
              if not vim.api.nvim_buf_is_valid(buf) then return end

              vim.bo[buf].modifiable = true
              if exit_code == 0 and #result_lines > 0 then
                state.status[current_t] = "done"
                vim.api.nvim_buf_set_lines(buf, 0, -1, false, result_lines)
              else
                state.status[current_t] = "failed"
                vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
                  string.format("[%s failed (exit code %d)]", current_t, exit_code),
                })
                vim.bo[buf].modifiable = false
              end

              update_title()
              if tools_order[state.active_idx] == current_t then
                update_diff()
              end
            end)
          end,
        })
        state.jobs[current_t] = job_id
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
vim.keymap.set("x", "<C-o>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'copilot')<CR>",
  { desc = "Ask AI(Copilot) and replace selection", noremap = true, silent = true })
vim.keymap.set("x", "<C-l>",
  ":<C-u>lua _G.ask_ai_and_replace_selection(vim.fn.line(\"'<\"), vim.fn.line(\"'>\"), 'all')<CR>",
  { desc = "Ask AI(All: Claude/Codex/Gemini) and replace selection", noremap = true, silent = true })
