-- A high-performance color highlighter. show color in code, like #ffffff
return {
  "NvChad/nvim-colorizer.lua",
  event = "VeryLazy",
  config = function()
    require("colorizer").setup({
      user_default_options = {
        RRGGBBAA = true,
        rgb_fn = true,
        hsl_fn = true,
      },
    })
  end,
}
