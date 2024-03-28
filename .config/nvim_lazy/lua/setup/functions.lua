---------------------------------------------------------
-- create filepath list from current directory
---------------------------------------------------------
local create_file_path_list_from_current_dir = function()
  cmd = "find * -type f"
  -- write it down to the current buffer
  vim.cmd("normal! i" .. cmd)
  vim.cmd(".!sh")

  print("execute command: " .. cmd)
end
vim.keymap.set("n", "<leader>lb", create_file_path_list_from_current_dir, { desc = 'create file path list from current directory' })


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
    vim.api.nvim_win_set_cursor(0, {between_line, 0})
  else
    print(message)
    vim.api.nvim_win_set_cursor(0, {current_line_number, 0})

  end

end
vim.keymap.set("n", "<leader><leader>n", function() move_to_codeblock("next") end, {desc = "move to next codeblock text", noremap = true})
vim.keymap.set("n", "<leader><leader>p", function() move_to_codeblock("prev") end, {desc = "move to prev codeblock text", noremap = true})

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
    vim.api.nvim_win_set_cursor(0, {start_line, 0})
    vim.cmd("normal! V")
    vim.api.nvim_win_set_cursor(0, {end_line, 0})
  else
    print("No code block found. Please move to a codeblock and try again.")
    vim.api.nvim_win_set_cursor(0, {cursor_position, 0})
  end

end
vim.keymap.set("n", "<leader><leader>s", select_codeblock_text, {desc = "Select codeblock text", noremap = true})

