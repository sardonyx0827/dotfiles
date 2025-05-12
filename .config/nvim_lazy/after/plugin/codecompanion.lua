-- Call the chat agent action
vim.keymap.set("n", "<leader>ca", "<cmd>CodeCompanionChat<cr>", { noremap = true, silent = true, desc = "Open CodeCompanionChat" })
vim.keymap.set("n", "<leader>co", "<cmd>CodeCompanionActions<cr>", { noremap = true, silent = true, desc = "Open CodeCompanionActions" })
vim.keymap.set("v", "<leader>ce", ":CodeCompanion ", { noremap = true, silent = true, desc = "Edit with CodeCompanion" })
