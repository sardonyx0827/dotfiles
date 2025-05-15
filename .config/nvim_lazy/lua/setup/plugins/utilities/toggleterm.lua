-- Terminal
return {
  "akinsho/toggleterm.nvim",
  version = "*",
  lazy = true,
  cmd = { "ToggleTerm" },
  config = function()
    require("toggleterm").setup {
      -- "vertical" | "horizontal" | "tab" | "float"
      direction = "tab"
    }
  end,
}
