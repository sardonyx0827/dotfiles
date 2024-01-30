--vim.keymap.set("i", "<C-j>", "<Plug>(copilot-next)")
--vim.keymap.set("i", "<C-k>", "<Plug>(copilot-previous)")
require("copilot").setup({
  suggestion = {
    --enabled = true,
    enabled = false,
    auto_trigger = false,
    debounce = 75,
    keymap = {
      accept = "<TAB>",
      accept_word = false,
      accept_line = false,
      next = "<c-j>",
      prev = "<c-k>",
      dismiss = "<C-]>",
    },
  },
  panel = {
    enabled = true,
    auto_refresh = true,
    keymap = {
      jump_prev = "[[",
      jump_next = "]]",
      accept = "<CR>",
      refresh = "gr",
      open = "<M-CR>"
    },
    layout = {
      position = "right", -- | top | left | right
      ratio = 0.5
    },
  },
  --panel = { enabled = false },

})
require("copilot_cmp").setup()
vim.keymap.set("n", "<c-p>", ":Copilot panel<CR>", { silent = true })
vim.keymap.set("i", "<c-l>", "<ESC>:Copilot panel<CR>", { silent = true })


-- Copilot Chat
vim.keymap.set("n", "<leader>cc", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<leader>cc", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>co", ":CopilotChat ", { desc = "Copilot Chat - ongoing" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<C-M-i>", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>ce", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("v", "<leader>ce", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("n", "<leader>cf", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat - /fix" })
vim.keymap.set("v", "<leader>cf", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat - /fix" })
vim.keymap.set("n", "<leader>ct", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("v", "<leader>ct", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("n", "<leader>cj", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("v", "<leader>cj", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("n", "<leader>cs", "{V}y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - yank surround" })
vim.keymap.set("n", "<leader>cl", "50kV100j50ky:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - yank 100lines" })

-- jump to next error/warn and fix with Copilot Chat
local function quick_fix_next_error_with_ai()
  local diagnostics = vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})
  if #diagnostics == 0 then
    print("No errors found.")
    return
  end

  -- jump to next error/warn
  vim.diagnostic.goto_next({severity = vim.diagnostic.severity.ERROR})

  -- get diagnostic message and current line
  local diagnostic_message = diagnostics[1].message:gsub("\n", "\\n")

  -- get 5 lines above and 5 lines below
  local current_line = vim.api.nvim_win_get_cursor(0)[1]
  local start = math.max(0, current_line - 5)
  local finish = math.min(vim.api.nvim_buf_line_count(0), current_line + 5)

  local lines_above = vim.api.nvim_buf_get_lines(0, start, finish, false)
  local lines_text = table.concat(lines_above, "\\n")

  -- open Copilot chat window
  vim.cmd("vertical rightbelow new")
  vim.cmd("setlocal filetype=markdown")
  vim.cmd("CopilotChat ".. "error message : " .. diagnostic_message .. " | current line text : " .. lines_text .. " | your job : how to fix it?")
end

vim.keymap.set("n", "<leader>qf", quick_fix_next_error_with_ai, {desc="Jump to Next Error and fix with Copilot"})

-- select codeblock text
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
    print("No code block found.")
    vim.api.nvim_win_set_cursor(0, {cursor_position, 0})
  end
end

vim.keymap.set("n", "<leader>vmm", select_codeblock_text, {desc = "select codeblock text (between codeblock)", noremap = true})

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
  local message = direction == "next" and "no next codeblock" or "no prev codeblock"

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
vim.keymap.set("n", "<leader>vmn", function() move_to_codeblock("next") end, {desc = "move to next codeblock text", noremap = true})
vim.keymap.set("n", "<leader>vmp", function() move_to_codeblock("prev") end, {desc = "move to prev codeblock text", noremap = true})

local function save_yanked_text(path, reg)
  local text = vim.fn.getreg(reg)
  if text == nil or text == "" then
    print("no text in register")
    return false
  end
  local file = io.open(path, "w")
  if file == nil then
    print("cannot open file")
    return false
  end
  file:write(text)
  file:close()
  return true
end

local function get_filetype_from_codeblock()
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local block_line = current_line_number
  for i = current_line_number, 0, -1 do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      block_line = i
      break
    end
  end
  vim.api.nvim_win_set_cursor(0, {block_line, 0})

  local filetype = vim.api.nvim_get_current_line()
  -- extract filetype from codeblock
  filetype = filetype:match("^```(%w+)")
  return filetype
end

local function find_start_line()
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  for i = current_line_number, 0, -1 do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      return i + 1
    end
    if i == 1 then
      return 1
    end
  end
end

local function save_and_check(path, register)
  local result = save_yanked_text(path, register)
  if not result then
    error("Failed to save yanked text to " .. path)
  end
end

local function diff_texts(target_text, copilot_text, filetype)
  if filetype == nil then
    filetype = "text"
  end
  vim.cmd("tabnew " .. copilot_text)
  vim.cmd("setlocal filetype=" .. filetype)
  vim.cmd("vertical diffsplit " .. target_text)
  vim.cmd("setlocal filetype=" .. filetype)
end

-- tmp file path
local target_text = "/tmp/_target_text"
local copilot_text = "/tmp/_copilot_suggestion"
local function compare_code_block()
  local cursor_position = vim.api.nvim_win_get_cursor(0)[1]
  local start_line = find_start_line()

  if start_line <= 1 then
    print("no codeblock in this buffer")
    vim.api.nvim_win_set_cursor(0, {cursor_position, 0})
    return
  end

  -- save target text using yank register
  save_and_check(target_text, '0')
  local filetype = get_filetype_from_codeblock()

  print(vim.api.nvim_win_get_cursor(0)[1])
  -- move cursor +1
  vim.cmd("normal! j")
  select_codeblock_text()
  -- save to register c
  vim.cmd('normal! "cy')
  save_and_check(copilot_text, 'c')
  diff_texts(target_text, copilot_text, filetype)
end

local function close_diff_tab()
  local wins = vim.api.nvim_list_wins()
  for _, w in ipairs(wins) do
    local bufname = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w))
    if bufname:match("/tmp/_") ~= nil then
      vim.api.nvim_win_close(w, true)
    end
  end
end
vim.keymap.set("n", "<leader>vmd", compare_code_block, {desc = "diff codeblock text(between)", noremap = true})
vim.keymap.set("n", "<leader>vmc", close_diff_tab, {desc = "close diff tab", noremap = true})

local function show_diff_files()
  diff_texts(target_text, copilot_text)
end
vim.keymap.set("n", "<leader>vms", show_diff_files, {desc = "show old diff files", noremap = true})

-- search target text in buffer (compare opening buffer and _target_text)
local function search_target_text_in_buffer()
  vim.cmd("normal! gg")
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local start_line = 1
  local max_line = vim.api.nvim_buf_line_count(0)
  local target_line_list = {}

  -- Improvement 1: Enhanced error handling
  local file, err = io.open(target_text, "r")
  if err then
    print("Error opening target file: " .. err)
    return false
  end

  if file then
    for line in file:lines() do
      table.insert(target_line_list, line)
    end
    file:close()
  end

  local buffer_lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
  local target_line_number = 1
  for i = current_line_number, max_line do
    local _line = buffer_lines[i]
    if _line == target_line_list[target_line_number] then
      if target_line_number == #target_line_list then
        start_line = i - target_line_number + 1
        break
      end
      target_line_number = target_line_number + 1
    else
      target_line_number = 1
    end
  end
  vim.api.nvim_win_set_cursor(0, {start_line, 0})
end

-- reflect Copilot suggestion
local function reflect_copilot_suggestion()
  -- read file text in _target_text
  local target_line_list = {}
  local file = io.open(target_text, "r")
  if file then
    for line in file:lines() do
      table.insert(target_line_list, line)
    end
    file:close()
  else
    print("Cannot open target file: " .. target_text)
    return false
  end

  -- read file text in _copilot_suggestion
  local suggested_lines = ""
  local file2 = io.open(copilot_text, "r")
  if file2 then
    suggested_lines = file2:read("a")
    file2:close()
  else
    print("Cannot open suggestion file: " .. copilot_text)
    return false
  end

  close_diff_tab()
  vim.cmd("wincmd h")

  search_target_text_in_buffer()
  -- delete lines in target buffer
  vim.cmd("normal! V" .. #target_line_list - 1 .. "j")
  -- save suggested_lines to clipboard and paste
  vim.fn.setreg('"', suggested_lines)
  vim.cmd("normal! P")

end
vim.keymap.set("n", "<leader>vma", reflect_copilot_suggestion, {desc = "close diff tab and accept copilot suggestion (after diff)", noremap = true})

-- compare texts, yanked text and copilot suggestion
local function obtain_copilot_suggestion()
  local cursor_position = vim.api.nvim_win_get_cursor(0)[1]
  local start_line = find_start_line()

  if start_line <= 1 then
    print("no codeblock in this buffer")
    vim.api.nvim_win_set_cursor(0, {cursor_position, 0})
    return
  end

  -- save target text using yank register
  save_and_check(target_text, '0')
  compare_code_block()
  -- save to register c
  vim.cmd('normal! "cy')
  save_and_check(copilot_text, 'c')
  reflect_copilot_suggestion()
end

vim.keymap.set("n", "<leader>vmo", obtain_copilot_suggestion, {desc = "obtain copilot suggestion (no diff, between codeblock)", noremap = true})
