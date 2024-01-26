vim.g.mapleader = ","

-- best solutions for me
-- In visual mode, shift text left and reselect
vim.keymap.set("v", "<", "<gv")
-- In visual mode, shift text right and reselect
vim.keymap.set("v", ">", ">gv")
-- In visual mode, move selected lines down and reselect
vim.keymap.set("v", "J", ":m '>+1<CR>gv=gv")
-- In visual mode, move selected lines up and reselect
vim.keymap.set("v", "K", ":m '<-2<CR>gv=gv")
-- In normal mode, join lines and return to initial position
vim.keymap.set("n", "J", "mzJ`z")
-- In normal mode, scroll down half a page and center
vim.keymap.set("n", "<C-d>", "<C-d>zz")
-- In normal mode, scroll up half a page and center
vim.keymap.set("n", "<C-u>", "<C-u>zz")
-- In normal mode, find next match and center
vim.keymap.set("n", "n", "nzzzv")
-- In normal mode, find previous match and center
vim.keymap.set("n", "N", "Nzzzv")
-- In normal mode, rename text in this file
vim.keymap.set("n", "<leader>rn", [[:%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>]], {desc = "rename text in this file"})
-- In normal mode, clear search highlighting
vim.keymap.set("n", "<leader><Space>", function()
  vim.cmd("noh")
end)
-- In normal mode, save the current file
vim.keymap.set("n", "<C-s>", function()
  vim.cmd("w")
end)

-- count up/down. 'C-a' is already used in Tmux(prefix)
vim.keymap.set("v", "<C-k>", "<C-a>gv")
vim.keymap.set("v", "<C-j>", "<C-x>gv")
vim.keymap.set("n", "<C-k>", "<C-a>")
vim.keymap.set("n", "<C-j>", "<C-x>")

-- resize current window
-- make the window biger vertically
vim.keymap.set('n', '<C-up>', '1<C-w>+', { noremap = true, silent = true})
-- make the window smaller vertically
vim.keymap.set('n', '<C-Down>', '1<C-w>-', { noremap = true, silent = true})
-- make the window bigger horizontally by pressing shift and =
vim.keymap.set('n', '<C-Right>', '1<C-w>>', { noremap = true, silent = true})
-- make the window smaller horizontally by pressing shift and -
vim.keymap.set('n', '<C-Left>', '1<C-w><', { noremap = true, silent = true})

-- jump next/prev buffer
vim.keymap.set("n", "<M-j>", ":bnext<CR>", {desc = "next buffer"})
vim.keymap.set("n", "<M-k>", ":bprev<CR>", {desc = "previous buffer"})

-- select line the same indentation with current line
local function count_indent(line)
  local indent = string.match(line, "^%s+")
  if indent == nil then
    return 0
  end
  return string.len(indent)
end
local function select_line_same_indent()
  -- check current line indent
  local line = vim.fn.getline(".")
  local current_indent = count_indent(line)
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  -- search above the same indentation with current_indent
  local start_line = current_line_number
  local end_line = 0
  for i = current_line_number, 0, -1 do
    local _line = vim.fn.getline(i)
    local indent = count_indent(_line)
    if indent == current_indent then
      start_line = i
    else
      break
    end
    if i == 1 then
      start_line = 1
      break
    end
  end
  -- search below the same indentation with current_indent
  local max_line = vim.api.nvim_buf_line_count(0)
  for i = current_line_number, max_line do
    local _line = vim.fn.getline(i)
    local indent = count_indent(_line)
    if indent == current_indent then
      end_line = i
    else
      break
    end
  end
  -- select lines
  vim.api.nvim_win_set_cursor(0, {start_line, 0})
  vim.cmd("normal! V")
  vim.api.nvim_win_set_cursor(0, {end_line, 0})
end
vim.keymap.set("n", "<leader>vin", select_line_same_indent, {desc = "select lines - same indentation", noremap = true})

-- select codeblock text
local function move_cursor_to_above_codeblock()
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local start_line = current_line_number
  -- search above
  for i = current_line_number, 0, -1 do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      start_line = i - 1
      break
    end
    if i <= 1 then
      start_line = 1
      break
    end
  end
  vim.api.nvim_win_set_cursor(0, {start_line, 0})
end
local function select_codeblock_text(cursor_position)
  local current_line_number = vim.api.nvim_win_get_cursor(0)[1]
  local start_line = current_line_number
  local end_line = current_line_number
  local max_line = vim.api.nvim_buf_line_count(0)
  -- search above
  for i = current_line_number, 0, -1 do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      start_line = i + 1
      break
    end
    if i == 1 then
      start_line = 1
      break
    end
  end
  -- search below
  for i = current_line_number, max_line do
    local _line = vim.fn.getline(i)
    if string.match(_line, "^```") then
      end_line = i - 1
      break
    end
  end
  if start_line <= 1 then
    print("no codeblock text")
    -- restore cursor position
    vim.api.nvim_win_set_cursor(0, {cursor_position, 0})
  else
    -- select lines
    vim.api.nvim_win_set_cursor(0, {start_line, 0})
    vim.cmd("normal! V")
    vim.api.nvim_win_set_cursor(0, {end_line, 0})
  end
end
local function select_last_codeblock_text()
  -- save cursor position
  local cursor_position = vim.api.nvim_win_get_cursor(0)[1]
  vim.cmd("normal! G")
  move_cursor_to_above_codeblock()
  select_codeblock_text(cursor_position)
end
vim.keymap.set("n", "<leader>vmm", select_last_codeblock_text, {desc = "select codeblock text (last)", noremap = true})

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
local function diff_texts(path1, path2, filetype)
  -- open path2 text to new tab
  vim.cmd("tabnew " .. path2)
  vim.cmd("setlocal filetype=" .. filetype)
  vim.cmd("vertical diffsplit " .. path1)
  vim.cmd("setlocal filetype=" .. filetype)
end

local function get_filetype_from_codeblock()
  vim.cmd("normal! G")
  move_cursor_to_above_codeblock()
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
local function diff_codeblock_text()
  local path1 = "/tmp/_target_text"
  local path2 = "/tmp/_copilot_suggestion"
  local function save_and_check(path, register)
    local result = save_yanked_text(path, register)
    if not result then
      error("Failed to save yanked text to " .. path)
    end
  end
  save_and_check(path1, '"')
  local filetype = get_filetype_from_codeblock()
  select_last_codeblock_text()
  vim.cmd('normal! y')
  save_and_check(path2, '"')
  diff_texts(path1, path2, filetype)
end
vim.keymap.set("n", "<leader>vmd", diff_codeblock_text, {desc = "diff codeblock text", noremap = true})
vim.keymap.set("n", "<leader>vmc", ":tabclose<CR>", {desc = "diff codeblock text", noremap = true})
