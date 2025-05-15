-- A high-performance color highlighter. show color in code, like #ffffff
return {
  "norcalli/nvim-colorizer.lua",
  event = "VeryLazy",
  config = function()
    require("colorizer").setup(config, {
      RRGGBBAA = true,
      rgb_fn = true,
      hsl_fn = true,
    })
  end,
}
