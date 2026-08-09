return {
  "selimacerbas/markdown-preview.nvim",
  keys = {
    { "<leader>mp", "<cmd>MarkdownPreview<cr>", desc = "Markdown Preview" },
  },
  dependencies = { "selimacerbas/live-server.nvim" },
  config = function()
    require("markdown_preview").setup({
      instance_mode = "takeover",
      default_theme = "dark",
      debounce_ms = 300,
    })
  end,
}
