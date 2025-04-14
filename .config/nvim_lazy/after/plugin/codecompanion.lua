local codecompanion = require("codecompanion")

codecompanion.setup({
  language = "Japanese",
  strategies = {
    chat = {
      adapter = "copilot",
      tools = {
        ["mcp"] = {
          -- Prevent mcphub from loading before needed
          callback = function()
            return require("mcphub.extensions.codecompanion")
          end,
          description = "Call tools and resources from the MCP Servers"
        }
      }
    },
    inline = {
      adapter = "copilot",
    },
  },
})

-- Call the chat agent action
vim.keymap.set("n", "<leader>ca", "<cmd>CodeCompanionChat<cr>", { noremap = true, silent = true, desc = "Open CodeCompanionChat" })
vim.keymap.set("n", "<leader>co", "<cmd>CodeCompanionActions<cr>", { noremap = true, silent = true, desc = "Open CodeCompanionActions" })
vim.keymap.set("v", "<leader>ce", ":CodeCompanion ", { noremap = true, silent = true, desc = "Edit with CodeCompanion" })
