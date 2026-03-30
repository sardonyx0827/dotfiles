--- @diagnostic disable: undefined-global
local builtin = require("telescope.builtin")
vim.keymap.set("n", "<leader>sf", builtin.find_files, { desc = "Find Files" })
vim.keymap.set("n", "<leader>sg", builtin.git_files, { desc = "Search Git Files" })
vim.keymap.set("n", "<leader>h/", builtin.search_history, { desc = "Search History" })
vim.keymap.set({"n", "v"}, "<leader>h:", builtin.command_history, { desc = "Command History" })
vim.keymap.set("n", "<leader>gf", builtin.git_files, { desc = "Git Files" })
vim.keymap.set("n", "<leader>gs", builtin.git_status, { desc = "Git Status" })
vim.keymap.set("n", "<leader>gl", builtin.git_commits, { desc = "Git Commits" })
vim.keymap.set("n", "<leader>of", builtin.oldfiles, { desc = "Old Files" })
vim.keymap.set("n", "<leader>ls", builtin.buffers, { desc = "Buffers" })
vim.keymap.set("n", "<leader>sl", builtin.buffers, { desc = "Buffers" })
vim.keymap.set("n", "<leader>ll", builtin.buffers, { desc = "Buffers" })
vim.keymap.set("n", "<leader>la", ":ls!<CR>", { desc = "Buffers" })
vim.keymap.set("n", "<leader>jl", builtin.jumplist, { desc = "Jump List" })
vim.keymap.set("n", "<leader>he", builtin.help_tags, { desc = "Help Tags" })
vim.keymap.set("n", "<leader>rg", builtin.registers, { desc = "Registers" })
vim.keymap.set("n", "<leader>sO", builtin.lsp_workspace_symbols, { desc = "LSP Workspace Symbols" })
vim.keymap.set("n", "<leader>so", builtin.treesitter, { desc = "Treesitter Symbols" })


-- using ripgrep. "sudo apt install ripgrep" or "brew install ripgrep"
vim.keymap.set("n", "<leader>gr", builtin.live_grep, { desc = "Live Grep" })
vim.keymap.set("n", "<leader>gw", builtin.grep_string, { desc = "Grep String" })
