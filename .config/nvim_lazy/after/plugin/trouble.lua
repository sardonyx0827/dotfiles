--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>xw", ":Trouble diagnostics toggle<CR>", {desc="Workspace Diagnostics"})
vim.keymap.set("n", "<leader>xn", vim.diagnostic.goto_next, {desc="Jump to Next Error/Warn"})
