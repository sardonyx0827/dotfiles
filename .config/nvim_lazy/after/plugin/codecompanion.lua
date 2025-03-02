local codecompanion =require("codecompanion")

codecompanion.setup({
  strategies = {
    chat = {
      adapter = "copilot",
    },
    inline = {
      adapter = "copilot",
    },
  },
})

-- Call the chat agent action
vim.keymap.set("n", "<leader>ca", "<cmd>CodeCompanionChat<cr>", { noremap = true, silent = true, desc = "Open CodeCompanionChat" })

