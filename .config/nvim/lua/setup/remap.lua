--- @diagnostic disable: undefined-global
vim.g.mapleader = ","

-- move text: keep the visual selection after shifting
vim.keymap.set("v", "<", "<gv")
vim.keymap.set("v", ">", ">gv")

-- search: keep the match centered
vim.keymap.set("n", "n", "nzzzv")
vim.keymap.set("n", "N", "Nzzzv")
vim.keymap.set("n", "<leader><Space>", function()
  vim.cmd("noh")
end)
vim.keymap.set("n", "<C-s>", function()
  vim.cmd("w")
end)
vim.keymap.set("n", "<leader>rn", [[:%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>]],
  { desc = "rename text in this file" })
-- vimgrep and open quickfix window
vim.keymap.set("n", "<leader>vg", function()
  vim.cmd("vimgrep /" .. vim.fn.input("Grep For > ") .. "/ **/*")
  vim.cmd("copen")
end, { desc = "vimgrep and open quickfix window" })

-- edit block, add String to each line
vim.keymap.set("v", "<leader>eb", [[:s/\(\w.*\)/\1<Left><Left>]],
  { desc = "edit block, add String to each line" })

--insert tab character in insert mode
vim.keymap.set('i', '<C-t>', '<C-v><Tab>', { noremap = true, silent = true })

-- window
-- make the window biger vertically
vim.keymap.set('n', '<C-up>', '1<C-w>+', { noremap = true, silent = true })
-- make the window smaller vertically
vim.keymap.set('n', '<C-Down>', '1<C-w>-', { noremap = true, silent = true })
-- make the window bigger horizontally
vim.keymap.set('n', '<C-Right>', '1<C-w>>', { noremap = true, silent = true })
-- make the window smaller horizontally
vim.keymap.set('n', '<C-Left>', '1<C-w><', { noremap = true, silent = true })
-- jump next/prev buffer
vim.keymap.set("n", "<C-l>", ":bnext<CR>", { noremap = true, silent = true })
vim.keymap.set("n", "<C-h>", ":bprev<CR>", { noremap = true, silent = true })
vim.keymap.set('n', 'gt', ':bnext<CR>', { noremap = true, silent = true })
vim.keymap.set('n', 'gT', ':bprev<CR>', { noremap = true, silent = true })
-- close all buffers
vim.keymap.set("n", "<leader>cb", "<cmd>%bdelete<cr>", { noremap = true, silent = true, desc = "Close All Buffers" })
-- change directory to current file
vim.keymap.set("n", "<leader>cd", ":cd %:h<CR>", { desc = "change directory to current file" })
vim.keymap.set("n", "<leader>cu", ":cd ..<CR>", { desc = "change up directory" })

-- move cursor in insert mode
-- allow <Left>/<Right> to wrap across line boundaries in insert mode
vim.opt.whichwrap:append("[,]")
vim.keymap.set("i", "<C-b>", "<Left>")
vim.keymap.set("i", "<C-f>", "<Right>")

-- move cursor in command mode
vim.keymap.set("c", "<C-b>", "<Left>")
vim.keymap.set("c", "<C-f>", "<Right>")

-- toggle mouse
vim.keymap.set('n', '<leader>tm', function()
  if vim.o.mouse == 'a' then
    vim.opt.mouse = ''
  else
    vim.opt.mouse = 'a'
  end
end, { desc = 'Toggle mouse' })

-- close all buffers (formerly after/plugin/auto-session.lua)
local function close_all_buffers()
  vim.cmd("bufdo bd")
end
vim.keymap.set("n", "<leader>qq", close_all_buffers,
  { noremap = true, silent = true, desc = "Close All Buffers" })
vim.keymap.set("n", "<leader>qa", function()
  close_all_buffers()
  vim.cmd("q!")
end, { noremap = true, silent = true, desc = "Close All Buffers and Exit" })

-- native buffer list (formerly after/plugin/telescope.lua <leader>la)
vim.keymap.set("n", "<leader>la", ":ls!<CR>", { desc = "List Buffers" })

-- jump to next diagnostic (core diagnostics; formerly after/plugin/trouble.lua <leader>xn)
vim.keymap.set("n", "<leader>xn", function()
  vim.diagnostic.jump({ count = 1, on_jump = vim.diagnostic.open_float })
end, { desc = "Jump to Next Error/Warn" })
