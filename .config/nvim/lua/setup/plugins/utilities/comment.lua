-- Toggle comments numToStr/Comment.nvim
return {
  "numToStr/Comment.nvim",
  event = "BufWinEnter",
  config = function()
    require("Comment").setup()
  end,
}
