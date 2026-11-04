return {
  "selimacerbas/mdkite.nvim",
  keys = {
    { "<leader>mp", "<cmd>MdKite start<cr>", desc = "Markdown Preview" },
  },
  dependencies = { "selimacerbas/kitehost.nvim" },
  config = function()
    require("mdkite").setup({
      instance_mode = "takeover",
      default_theme = "dark",
      debounce_ms = 300,
    })
  end,
}
