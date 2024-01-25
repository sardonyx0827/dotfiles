vim.g.mapleader = ","

-- best solutions for me
vim.keymap.set("v", "<", "<gv")
vim.keymap.set("v", ">", ">gv")
vim.keymap.set("v", "J", ":m '>+1<CR>gv=gv")
vim.keymap.set("v", "K", ":m '<-2<CR>gv=gv")
vim.keymap.set("n", "J", "mzJ`z")
vim.keymap.set("n", "<C-d>", "<C-d>zz")
vim.keymap.set("n", "<C-u>", "<C-u>zz")
vim.keymap.set("n", "n", "nzzzv")
vim.keymap.set("n", "N", "Nzzzv")
vim.keymap.set("n", "<leader>rn", [[:%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>]], {desc = "rename text in this file"})
vim.keymap.set("n", "<leader><Space>", function()
  vim.cmd("noh")
end)
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
  print(current_indent)
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
vim.keymap.set("n", "<leader>vi", select_line_same_indent, {desc = "select lines - same indentation", noremap = true})

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
vim.keymap.set("n", "<leader>vm", select_last_codeblock_text, {desc = "select codeblock text (last)", noremap = true})
