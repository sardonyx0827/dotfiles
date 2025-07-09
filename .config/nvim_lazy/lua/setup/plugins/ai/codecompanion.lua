-- AI Agent
return {
  "olimorris/codecompanion.nvim",
  dependencies = {
    "nvim-lua/plenary.nvim",
    "nvim-treesitter/nvim-treesitter",
  },
  opts = {
    language = "Japanese",
    -- adapters = {
    --   gemini = function()
    --     return require("codecompanion.adapters").extend("gemini", {
    --       env = {
    --         -- 環境変数のAPIキーを設定
    --         api_key = vim.env.GEMINI_API_KEY,
    --       },
    --     })
    --   end,
    -- },
    strategies = {
      -- Change the default chat adapter
      chat = {
        adapter = "copilot",
        -- adapter = "gemini",
        model = "gpt-4.1",
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
    },
    schema = {
      model = {
        order = 1,
        mapping = "parameters",
        type = "enum",
        desc =
        "ID of the model to use. See the model endpoint compatibility table for details on which models work with the Chat API.",
        ---@type string|fun(): string
        default = "claude-3.7-sonnet",
        choices = {
          ["o3-mini-2025-01-31"] = { opts = { can_reason = true } },
          ["o1-2024-12-17"] = { opts = { can_reason = true } },
          ["o1-mini-2024-09-12"] = { opts = { can_reason = true } },
          "claude-3.5-sonnet",
          "claude-3.7-sonnet",
          "claude-3.7-sonnet-thought",
          "gpt-4o-2024-08-06",
          "gemini-2.0-flash-001",
        },
      },
    },
    opts = {
      language = "Japanese",
    }
  }
}
