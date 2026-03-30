-- show git diff
return {
  "FabijanZulj/blame.nvim",
  lazy = true,
  cmd = "BlameToggle",
  config = function()
    require("blame").setup()
  end
}
