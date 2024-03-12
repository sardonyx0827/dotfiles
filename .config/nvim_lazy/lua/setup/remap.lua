vim.g.mapleader = ","

-- Explorer
vim.keymap.set("n", "<leader>p", ":Ex<CR>", {desc = "open file explorer"})
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
-- change directory to current file
vim.keymap.set("n", "<leader>cd", ":cd %:h<CR>", {desc = "change directory to current file"})
vim.keymap.set("n", "<leader>cu", ":cd ..<CR>", {desc = "change up directory"})

-- move cursor in insert mode
vim.keymap.set("i", "<C-j>", "<C-o>gj")
vim.keymap.set("i", "<C-k>", "<C-o>gk")
vim.keymap.set("i", "<C-h>", "<C-o>h")
vim.keymap.set("i", "<C-l>", "<C-o>l")

-- example
-- write "find * -type f -name "*.lua"
-- and execute this command ":.!sh"
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
end

vim.keymap.set("n", "<leader>bl", load_buffers_from_file_list, { desc = 'load buffers from file list' })

