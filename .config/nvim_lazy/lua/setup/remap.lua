--- @diagnostic disable: undefined-global
vim.g.mapleader = ","

-- Explorer
vim.keymap.set("n", "<leader>p", ":Ex<CR>", {desc = "open file explorer"})
-- In visual mode, shift text left and reselect
vim.keymap.set("v", "<", "<gv")
-- In visual mode, shift text right and reselect
vim.keymap.set("v", ">", ">gv")
-- In normal mode, join lines and return to initial position
vim.keymap.set("n", "J", "mzJ`z")
-- In normal mode, scroll down half a page and center
vim.keymap.set("n", "<C-d>", "<C-d>zz")
-- In normal mode, scroll up half a page and center
vim.keymap.set("n", "<C-u>", "<C-u>zz")

-- search
-- In normal mode, find next match and center
vim.keymap.set("n", "n", "nzzzv")
-- In normal mode, find previous match and center
vim.keymap.set("n", "N", "Nzzzv")
-- In normal mode, clear search highlighting
vim.keymap.set("n", "<leader><Space>", function()
  vim.cmd("noh")
end)
-- In normal mode, save the current file
vim.keymap.set("n", "<C-s>", function()
  vim.cmd("w")
end)
-- In normal mode, rename text in this file
vim.keymap.set("n", "<leader>rn", [[:%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>]], {desc = "rename text in this file"})
-- vimgrep and open quickfix window
vim.keymap.set("n", "<leader>vg", function()
  vim.cmd("vimgrep /" .. vim.fn.input("Grep For > ") .. "/ **/*")
  vim.cmd("copen")
end, {desc = "vimgrep and open quickfix window"})

-- window
-- make the window biger vertically
vim.keymap.set('n', '<C-up>', '1<C-w>+', { noremap = true, silent = true})
-- make the window smaller vertically
vim.keymap.set('n', '<C-Down>', '1<C-w>-', { noremap = true, silent = true})
-- make the window bigger horizontally by pressing shift and =
vim.keymap.set('n', '<C-Right>', '1<C-w>>', { noremap = true, silent = true})
-- make the window smaller horizontally by pressing shift and -
vim.keymap.set('n', '<C-Left>', '1<C-w><', { noremap = true, silent = true})
-- jump next/prev buffer
vim.keymap.set("n", "<C-l>", ":bnext<CR>", { noremap = true, silent = true})
vim.keymap.set("n", "<C-h>", ":bprev<CR>", { noremap = true, silent = true})
-- change directory to current file
vim.keymap.set("n", "<leader>cd", ":cd %:h<CR>", {desc = "change directory to current file"})
vim.keymap.set("n", "<leader>cu", ":cd ..<CR>", {desc = "change up directory"})
-- move cursor in insert mode
vim.keymap.set("i", "<C-j>", "<C-o>gj")
vim.keymap.set("i", "<C-k>", "<C-o>gk")
vim.keymap.set("i", "<C-h>", "<C-o>h")
vim.keymap.set("i", "<C-l>", "<C-o>l")

