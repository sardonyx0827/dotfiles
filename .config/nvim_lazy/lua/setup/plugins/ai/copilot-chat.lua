--- @diagnostic disable: undefined-global
-- copilot chat prompts
local prompts = {
  -- Code related prompts
  Explain = "/COPILOT_EXPLAIN 次のコードの動作を説明してください。",
  Review = "/COPILOT_REVIEW 次のコードをレビューし、改善の提案をしてください。",
  Fix = "/COPILOT_GENERATE このコードの問題を説明し、解決策を提供してください。",
  Optimize = "/COPILOT_GENERATE 選択したコードを最適化して、パフォーマンスと可読性を向上させてください。",
  Docs = "/COPILOT_GENERATE 次のコードに対するドキュメンテーションを提供してください。",
  Tests = "/COPILOT_GENERATE このコードのテストを生成してください。",
}

return {
  "CopilotC-Nvim/CopilotChat.nvim",
  event = "VeryLazy",
  branch = "main",
  dependencies = {
    -- Or { "github/copilot.vim" }
    "zbirenbaum/copilot.lua",
    "nvim-lua/plenary.nvim",
    "nvim-telescope/telescope.nvim"
  },
  opts = {
    mode = "newbuffer", -- newbuffer or split, default: newbuffer
    -- model = 'claude-3.5-sonnet',
    model = "gpt-4.1",
    -- model = "gpt-5-mini",
    show_help = "no", -- Show help text for CopilotChatInPlace, default: yes
    debug = false, -- Enable or disable debug mode, the log file will be in ~/.local/state/nvim/CopilotChat.nvim.log
    language = "Japanese",
    prompts = prompts,
    auto_follow_cursor = false, -- Don't follow the cursor after getting response
  },
  config = function(_, opts)
    local chat = require("CopilotChat")
    -- local select = require("CopilotChat.select")
    -- Use unnamed register for the selection
    -- opts.selection = select.unnamed
    chat.setup(opts)
    vim.api.nvim_create_user_command("CopilotChatInline", function(args)
      chat.open({
        model = "gpt-4.1",
        -- model = "gpt-5-mini",
        window = {
          title = "CopilotChatInline",
          layout = "float",
          relative = "cursor",
          width = 0.5,
          height = 0.4,
          row = 1,
        },
      })
    end, { nargs = "*", range = true })
  end,
}
