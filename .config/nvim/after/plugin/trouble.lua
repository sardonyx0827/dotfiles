--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>xw", ":Trouble diagnostics toggle<CR>", { desc = "Workspace Diagnostics" })
vim.keymap.set("n", "<leader>xn", function() vim.diagnostic.jump({ count = 1, on_jump = vim.diagnostic.open_float }) end, { desc = "Jump to Next Error/Warn" })
