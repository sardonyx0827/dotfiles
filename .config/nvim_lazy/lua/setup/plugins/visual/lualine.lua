-- command line position when using lualine
vim.opt.cmdheight = 0
-- Show Statusline
local my_transparent_theme = {
  normal = {
    a = { fg = '#7aa2f7', bg = 'None', gui = 'bold' },
    b = { fg = '#c0caf5', bg = 'None' },
    c = { fg = '#a9b1d6', bg = 'None' },
    x = { fg = '#a9b1d6', bg = 'None' },
    y = { fg = '#c0caf5', bg = 'None' },
    z = { fg = '#7aa2f7', bg = 'None' },
  },
  inactive = {
    a = { fg = '#545c7e', bg = 'None', gui = 'bold' },
    b = { fg = '#727a9a', bg = 'None' },
    c = { fg = '#727a9a', bg = 'None' },
    x = { fg = '#727a9a', bg = 'None' },
    y = { fg = '#727a9a', bg = 'None' },
    z = { fg = '#545c7e', bg = 'None' },
  },
  insert = { a = { fg = '#9ece6a', bg = 'None', gui = 'bold' } },
  visual = { a = { fg = '#ff9e64', bg = 'None', gui = 'bold' } },
  replace = { a = { fg = '#f7768e', bg = 'None', gui = 'bold' } },
  command = { a = { fg = '#e0af68', bg = 'None', gui = 'bold' } },
}
return {
  "nvim-lualine/lualine.nvim",
  event = "VeryLazy",
  dependencies = {
    'AndreM222/copilot-lualine'
  },
  config = function()
  require("lualine").setup {
    options = {
      icons_enabled = true,
      -- theme = "auto",
      theme = my_transparent_theme,
      --color = { bg = "none" },
      component_separators = { left = "", right = "" },
      section_separators = { left = "", right = "" },
      disabled_filetypes = {
        statusline = {},
        winbar = {},
      },
      ignore_focus = {},
      always_divide_middle = true,
      globalstatus = true,
      refresh = {
        statusline = 1000,
        tabline = 1000,
        winbar = 1000,
      }
    },
    sections = {
      lualine_a = { "mode" },
      lualine_b = { "branch", "diff", "diagnostics" },
      lualine_c = { "filename",
        {
          require("noice").api.statusline.mode.get,
          cond = require("noice").api.statusline.mode.has,
          color = { fg = "#ff9e64" },
        }
      },
      lualine_x = {
        {
          'copilot',
          -- Default values
          symbols = {
            status = {
              icons = {
                enabled = " ",
                sleep = " ",   -- auto-trigger disabled
                disabled = " ",
                unknown = " ",
                warning = " ",
              },
              hl = {
                enabled = "#50FA7B",
                sleep = "#AEB7D0",
                disabled = "#6272A4",
                warning = "#FFB86C",
                unknown = "#FF5555"
              }
            },
            spinners = require("copilot-lualine.spinners").dots,
            spinner_color = "#6272A4"
          },
          show_colors = true,
          show_loading = true
        },
        {
          require('mcphub.extensions.lualine')
        },
        "encoding", "fileformat", "filetype" },
      lualine_y = { "progress" },
      lualine_z = { "location" }
    },
    inactive_sections = {
      lualine_a = {},
      lualine_b = {},
      lualine_c = { "filename" },
      lualine_x = { "location" },
      lualine_y = {},
      lualine_z = {}
    },
    tabline = {},
    winbar = {},
    inactive_winbar = {},
    extensions = {}
  }
    end
}
