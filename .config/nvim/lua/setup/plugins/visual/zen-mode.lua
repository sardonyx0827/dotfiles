-- focus
return {
  "folke/zen-mode.nvim",
  lazy = true,
  keys = {
    {
      "<leader>zz",
      function()
        local zen_mode = require("zen-mode")
        zen_mode.setup {
          window = {
            width = 90,
            options = {}
          },
        }
        zen_mode.toggle()
        vim.wo.wrap = false
        vim.wo.number = true
        vim.wo.rnu = true
      end,
      desc = "toggle zen mode",
    },
    {
      "<leader>zZ",
      function()
        local zen_mode = require("zen-mode")
        zen_mode.setup {
          window = {
            width = 80,
            options = {}
          },
        }
        zen_mode.toggle()
        vim.wo.wrap = false
        vim.wo.number = false
        vim.wo.rnu = false
        vim.opt.colorcolumn = "0"
      end,
      desc = "toggle zen mode - no number, no ruler, no colorcolumn",
    },
  },
}
