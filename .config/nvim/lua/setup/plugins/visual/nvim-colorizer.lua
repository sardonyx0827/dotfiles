-- A high-performance color highlighter. show color in code, like #ffffff
return {
  "catgoose/nvim-colorizer.lua",
  event = "VeryLazy",
  config = function()
    require("colorizer").setup({
      options = {
        parsers = { css = true, rgb = { enable = false } },
      },
    })
  end,
}
