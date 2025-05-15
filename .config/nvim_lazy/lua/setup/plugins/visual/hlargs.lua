-- change args color
return {
  "m-demare/hlargs.nvim",
  event = "BufWinEnter",
  config = function()
    require("hlargs").setup({
      color = "#ef9123",
      performance = {
        max_iterations = 400,
      },
    })
  end,
}
