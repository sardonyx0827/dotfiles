return {
  "coder/claudecode.nvim",
  dependencies = { "folke/snacks.nvim" },
  config = true,
  keys = {
    { "<leader>cc", "<cmd>ClaudeCode<cr>",     mode = "n", desc = "ClaudeCode - Toggle Claude" },
    { "<leader>cc", "<cmd>ClaudeCodeSend<cr>", mode = "v", desc = "ClaudeCode - Send to Claude" },
    {
      "<leader>ca",
      "<cmd>ClaudeCodeTreeAdd<cr>",
      desc = "ClaudeCode - Add file to Claude",
      ft = { "NvimTree", "neo-tree", "oil", "minifiles" },
    },
    -- Diff management
    { "<leader>da", "<cmd>ClaudeCodeDiffAccept<cr>", desc = "ClaudeCode - Accept diff" },
    { "<leader>dd", "<cmd>ClaudeCodeDiffDeny<cr>",   desc = "ClaudeCode - Deny diff" },
  },
}
