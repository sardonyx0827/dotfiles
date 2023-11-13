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
vim.keymap.set("n", "<leader>rn", [[:%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>]], {desc = "rename text this file"})
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

