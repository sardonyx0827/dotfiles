return {
  "rose-pine/neovim",
  name = "rose-pine",
  --event = "VeryLazy",
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
  end
}
