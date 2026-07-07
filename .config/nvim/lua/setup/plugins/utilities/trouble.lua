--- @diagnostic disable: undefined-doc-name
-- find Trouble in my code
return {
  "folke/trouble.nvim",
  lazy = true,
  cmd = { "TroubleToggle", "Trouble", "TroubleRefresh" },
  keys = {
    {
      "<leader>xX",
      "<cmd>Trouble diagnostics toggle<cr>",
      desc = "Workspace Diagnostics (Trouble)",
    },
    {
      "<leader>xw",
      "<cmd>Trouble diagnostics toggle<cr>",
      desc = "Workspace Diagnostics (Trouble)",
    },
    {
      "<leader>xx",
      "<cmd>Trouble diagnostics toggle filter.buf=0<cr>",
      desc = "Buffer Diagnostics (Trouble)",
    },
    {
      "<leader>xs",
      "<cmd>Trouble symbols toggle focus=false<cr>",
      desc = "Symbols (Trouble)",
    },
    {
      "<leader>xl",
      "<cmd>Trouble lsp toggle focus=false win.position=right<cr>",
      desc = "LSP Definitions / references / ... (Trouble)",
    },
    {
      "<leader>xL",
      "<cmd>Trouble loclist toggle<cr>",
      desc = "Location List (Trouble)",
    },
    {
      "<leader>xQ",
      "<cmd>Trouble qflist toggle<cr>",
      desc = "Quickfix List (Trouble)",
    },
  },
  config = function()
    require("trouble").setup {
      -- icons = false,
      -- your configuration comes here
      -- or leave it empty to use the default settings
      -- refer to the configuration section below
      icons = {
        ---@type trouble.Indent.symbols
        indent        = {
          top         = "│ ",
          middle      = "├╴",
          last        = "└╴",
          -- last          = "-╴",
          -- last       = "╰╴", -- rounded
          fold_open   = " ",
          fold_closed = " ",
          ws          = "  ",
        },
        folder_closed = " ",
        folder_open   = " ",
        kinds         = {
          Array         = " ",
          Boolean       = "󰨙 ",
          Class         = " ",
          Constant      = "󰏿 ",
          Constructor   = " ",
          Enum          = " ",
          EnumMember    = " ",
          Event         = " ",
          Field         = " ",
          File          = " ",
          Function      = "󰊕 ",
          Interface     = " ",
          Key           = " ",
          Method        = "󰊕 ",
          Module        = " ",
          Namespace     = "󰦮 ",
          Null          = " ",
          Number        = "󰎠 ",
          Object        = " ",
          Operator      = " ",
          Package       = " ",
          Property      = " ",
          String        = " ",
          Struct        = "󰆼 ",
          TypeParameter = " ",
          Variable      = "󰀫 ",
        },
      },
    }
  end
}
