return {
  "rose-pine/neovim",
  name = "rose-pine",
  -- Active theme: load first among start plugins and be the only one to call
  -- :colorscheme at startup, so the result is deterministic.
  lazy = false,
  priority = 1000,
  config = function()
    require("rose-pine").setup({
      highlight_groups = {
        -- default
        -- TelescopeNormal = { fg = "subtle", bg = "overlay" },
        -- TelescopeSelection = { fg = "text", bg = "highlight_med" },
        -- TelescopeSelectionCaret = { fg = "love", bg = "highlight_med" },
        -- TelescopeMultiSelection = { fg = "text", bg = "highlight_high" },
        --
        -- TelescopeTitle = { fg = "base", bg = "love" },
        -- TelescopePromptTitle = { fg = "base", bg = "pine" },
        -- TelescopePreviewTitle = { fg = "base", bg = "iris" },
        --
        -- TelescopePromptNormal = { fg = "text", bg = "surface" },
        -- TelescopePromptBorder = { fg = "surface", bg = "surface" },

        -- bg none settings
        TelescopeBorder = { fg = "overlay", bg = "none" },
        TelescopeNormal = { fg = "subtle", bg = "none" },
        TelescopeSelection = { fg = "text", bg = "highlight_med" },
        TelescopeSelectionCaret = { fg = "love", bg = "highlight_med" },
        TelescopeMultiSelection = { fg = "text", bg = "highlight_high" },

        TelescopeTitle = { fg = "base", bg = "love" },
        TelescopePromptTitle = { fg = "base", bg = "pine" },
        TelescopePreviewTitle = { fg = "base", bg = "iris" },

        TelescopePromptNormal = { fg = "text", bg = "none" },
        TelescopePromptBorder = { fg = "surface", bg = "none" },

        Normal = { bg = "none" },
        NormalNC = { bg = "none" },
        NormalFloat = { bg = "none" },
        FloatBorder = { bg = "none" },
      },
    })

    ------------------------------------------------------------------
    -- Global appearance (formerly after/plugin/colorscheme.lua).
    -- rose-pine is the active theme and loads eagerly, so this base
    -- appearance is established here. The ColorScheme autocmds below
    -- keep transparency correct if the theme is later switched.
    ------------------------------------------------------------------

    -- default color scheme
    vim.cmd("colorscheme rose-pine-main")
    -- vim.cmd("colorscheme kanagawa-dragon")
    -- vim.cmd("colorscheme vscode")
    -- vim.cmd("colorscheme tokyonight-night")

    -- clear bg color
    vim.api.nvim_set_hl(0, "Normal", { bg = "none" })
    vim.api.nvim_set_hl(0, "NormalNC", { bg = "none" })
    vim.api.nvim_set_hl(0, "NormalFloat", { bg = "none" })
    vim.api.nvim_set_hl(0, "FloatBorder", { bg = "none" })
    vim.api.nvim_set_hl(0, "StatusLine", { blend = 0 })
    vim.api.nvim_set_hl(0, "TabLineFill", { blend = 0 })

    -- barbar.nvim transparency configuration
    local function set_barbar_transparent()
      local hl = vim.api.nvim_set_hl

      -- Combination of buffer states and parts
      local states = { 'Current', 'Inactive', 'Visible', 'Alternate' }
      local parts = {
        '', 'Btn', 'Icon', 'Index', 'Mod', 'ModBtn', 'Number',
        'Pin', 'PinBtn', 'Sign', 'SignRight', 'Target',
        'ADDED', 'CHANGED', 'DELETED', 'ERROR', 'HINT', 'INFO', 'WARN'
      }

      for _, state in ipairs(states) do
        for _, part in ipairs(parts) do
          local group = 'Buffer' .. state .. part
          local ok, existing = pcall(vim.api.nvim_get_hl, 0, { name = group, link = false })
          if ok and existing then
            existing.bg = 'NONE'
            hl(0, group, existing)
          end
        end
      end

      -- Special highlight groups (e.g., tabbar empty areas)
      local special_groups = {
        'BufferTabpageFill', -- Space between buffer and tabpage
        'BufferTabpages',    -- Tabpage indicator
        'BufferTabpagesSep', -- Tabpage separator
        'BufferScrollArrow', -- Scroll arrows
        'BufferOffset',      -- Sidebar offset
      }

      for _, group in ipairs(special_groups) do
        hl(0, group, { bg = 'NONE' })
      end
    end

    -- Apply when colorscheme changes
    vim.api.nvim_create_autocmd('ColorScheme', {
      callback = set_barbar_transparent,
    })

    -- Initial execution (call after colorscheme is set)
    vim.defer_fn(set_barbar_transparent, 0)

    -- Snacks.nvim specific highlights
    vim.api.nvim_create_autocmd('VimEnter', {
      callback = function()
        Snacks.util.set_hl({
          PickerDir = { link = 'Text' },
          PickerPathHidden = { link = 'Text' },
        }, { prefix = 'Snacks' })
      end,
    })
  end
}
