return {
  "folke/tokyonight.nvim",
  name = "tokyonight",
  event = "VeryLazy",
  config = function()
    require("tokyonight").setup({
      transparent = true,
      styles = {
        -- Background styles. Can be "dark", "transparent" or "normal"
        sidebars = "transparent",
        floats = "transparent",
      },
      on_colors = function(colors)
        colors.border = "#565f89"
      end
    })
  end
}
