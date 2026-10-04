return {
  "Mofiqul/vscode.nvim",
  name = "vscode",
  event = "VeryLazy",
  config = function()
    local c = require('vscode.colors').get_colors()
    require("vscode").setup({
      style = 'dark',
      -- Enable transparent background
      transparent = true,
      -- Disable italic comment
      italic_comments = false,
      -- Disable nvim-tree background color
      disable_nvimtree_bg = true,
      -- Override highlight groups (see ./lua/vscode/theme.lua)
      group_overrides = {
        Cursor = { fg = c.vscDarkBlue, bg = c.vscLightGreen, bold = true },
      }
    })
  end
}
