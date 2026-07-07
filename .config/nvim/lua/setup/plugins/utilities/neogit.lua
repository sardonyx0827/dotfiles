-- git client
return {
  "NeogitOrg/neogit",
  lazy = true,
  cmd = "Neogit",
  keys = {
    {
      "<leader>gc",
      "<cmd>Neogit<cr>",
      noremap = true,
      silent = true,
      desc = "Open Neogit - Git Client.",
    },
  },
  dependencies = {
    { "nvim-lua/plenary.nvim" },   -- required
    {
      "sindrets/diffview.nvim",
      lazy = true,
      cmd = { "DiffviewOpen", "DiffviewClose", "DiffviewToggleFiles", "DiffviewFocusFiles", "DiffviewRefresh", "Neogit" },
      keys = {
        {
          "<leader>do",
          "<cmd>DiffviewOpen<cr>",
          noremap = true,
          silent = true,
          desc = "Open Git Diffview with Explorer",
        },
        {
          "<leader>dc",
          "<cmd>DiffviewClose<cr>",
          noremap = true,
          silent = true,
          desc = "Close Git Diffview with Explorer",
        },
      },
    },   -- optional but recommended
  },
  config = function()
    require("neogit").setup({
      disable_insert_on_commit = true,
    })
  end,
}
