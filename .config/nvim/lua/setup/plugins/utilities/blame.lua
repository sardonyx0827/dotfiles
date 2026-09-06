-- show git blame
return {
  "FabijanZulj/blame.nvim",
  lazy = true,
  cmd = "BlameToggle",
  keys = {
    {
      "<leader>gb",
      "<cmd>BlameToggle window<cr>",
      desc = "Toggle Blame - toggle git comments on line.",
    },
  },
  config = function()
    require("blame").setup()
  end
}
