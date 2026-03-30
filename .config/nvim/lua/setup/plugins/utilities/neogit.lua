-- git client
return {
  "NeogitOrg/neogit",
  lazy = true,
  cmd = "Neogit",
  dependencies = {
    { "nvim-lua/plenary.nvim" },   -- required
    {
      "sindrets/diffview.nvim",
      lazy = true,
      cmd = { "DiffviewOpen", "DiffviewClose", "DiffviewToggleFiles", "DiffviewFocusFiles", "DiffviewRefresh", "Neogit" },
    },   -- optional but recommended
  },
  config = function()
    require("neogit").setup()
  end,
}
