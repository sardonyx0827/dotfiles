--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>do", "<cmd>DiffviewOpen<cr>", { noremap = true, silent = true, desc = "Open Git Diffview with Explorer" })
vim.keymap.set("n", "<leader>dc", "<cmd>DiffviewClose<cr>", { noremap = true, silent = true, desc = "Close Git Diffview with Explorer" })
