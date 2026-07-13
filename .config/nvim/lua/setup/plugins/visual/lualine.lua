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
-- Rosé Pine (main) palette — kept in sync with the active colorscheme
-- (rose-pine.lua) so the statusline matches the rest of the UI. bg is 'None'
-- on purpose: the statusline stays transparent like the terminal.
local my_transparent_theme = {
  normal = {
    a = { fg = '#c4a7e7', bg = 'None', gui = 'bold' }, -- iris
    b = { fg = '#e0def4', bg = 'None' },               -- text
    c = { fg = '#908caa', bg = 'None' },               -- subtle
    x = { fg = '#908caa', bg = 'None' },               -- subtle
    y = { fg = '#e0def4', bg = 'None' },               -- text
    z = { fg = '#c4a7e7', bg = 'None' },               -- iris
  },
  inactive = {
    a = { fg = '#6e6a86', bg = 'None', gui = 'bold' }, -- muted
    b = { fg = '#6e6a86', bg = 'None' },
    c = { fg = '#6e6a86', bg = 'None' },
    x = { fg = '#6e6a86', bg = 'None' },
    y = { fg = '#6e6a86', bg = 'None' },
    z = { fg = '#6e6a86', bg = 'None' },
  },
  insert = { a = { fg = '#9ccfd8', bg = 'None', gui = 'bold' } },  -- foam
  visual = { a = { fg = '#f6c177', bg = 'None', gui = 'bold' } },  -- gold
  replace = { a = { fg = '#eb6f92', bg = 'None', gui = 'bold' } }, -- love
  command = { a = { fg = '#ebbcba', bg = 'None', gui = 'bold' } }, -- rose
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
            color = { fg = "#f6c177" }, -- Rosé Pine gold
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
                  enabled = "#9ccfd8",  -- foam
                  sleep = "#908caa",    -- subtle
                  disabled = "#6e6a86", -- muted
                  warning = "#f6c177",  -- gold
                  unknown = "#eb6f92"   -- love
                }
              },
              spinners = require("copilot-lualine.spinners").dots,
              spinner_color = "#6e6a86" -- muted
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
