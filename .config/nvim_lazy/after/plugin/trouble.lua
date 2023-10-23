vim.keymap.set("n", "<leader>xw", function() require("trouble").open("workspace_diagnostics") end, {desc="Workspace Diagnostics"})
vim.keymap.set("n", "<leader>xd", function() require("trouble").open("document_diagnostics") end, {desc="Document Diagnostics"})
vim.keymap.set("n", "<leader>xg", function() require("trouble").open("lsp_references") end, {desc="LSP References"})
