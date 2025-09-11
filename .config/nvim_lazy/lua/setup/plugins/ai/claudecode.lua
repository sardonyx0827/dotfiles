return {
  "coder/claudecode.nvim",
  dependencies = { "folke/snacks.nvim" },
  config = true,
  keys = {
    { "<leader>cc", "<cmd>ClaudeCode<cr>", mode="n", desc = "Toggle Claude" },
    { "<leader>cc", "<cmd>ClaudeCodeSend<cr>", mode = "v", desc = "Send to Claude" },
    { "<leader>cC", "<cmd>ClaudeCodeClose<cr>", desc = "Close Claude" },
    { "<leader>cc", "<cmd>ClaudeCodeClose<cr>", mode = "v", desc = "Close Claude" },
    {
      "<leader>ca",
      "<cmd>ClaudeCodeTreeAdd<cr>",
      desc = "Add file to Claude",
      ft = { "NvimTree", "neo-tree", "oil", "minifiles" },
    },
    -- Diff management
    { "<leader>da", "<cmd>ClaudeCodeDiffAccept<cr>", desc = "Accept diff" },
    { "<leader>dd", "<cmd>ClaudeCodeDiffDeny<cr>", desc = "Deny diff" },
  },
}
