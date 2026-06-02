--- @diagnostic disable: undefined-global
-- Prevent lualine from showing in dashboard-like filetypes by setting laststatus to 0 locally.
-- This is necessary because lualine's global statusline setting does not apply to special buffers like dashboard.
vim.api.nvim_create_autocmd("FileType", {
  pattern = { "dashboard", "alpha", "startify" },
  callback = function()
    vim.opt_local.laststatus = 0
  end,
})

-- Restore global statusline (laststatus=3) when leaving dashboard-like filetypes.
vim.api.nvim_create_autocmd("BufEnter", {
  callback = function()
    local ft = vim.bo.filetype
    local disabled_fts = { "dashboard", "alpha", "startify" }

    if not vim.tbl_contains(disabled_fts, ft) then
      vim.opt.laststatus = 3
    end
  end,
})

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
            cond = function()
              if not require("noice").api.statusline.mode.has() then
                return false
              end
              local mode_text = require("noice").api.statusline.mode.get()
              if mode_text and mode_text:find("recording") then
                return true
              end
              local current_mode = vim.api.nvim_get_mode().mode
              if current_mode == "n" or current_mode == "nt" then
                return false
              end
              return true
            end,
            color = { fg = "#ff9e64" },
          },
        },
        lualine_x = {
          {
            'copilot',
            -- Default values
            symbols = {
              status = {
                icons = {
                  enabled = " ",
                  sleep = " ", -- auto-trigger disabled
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
