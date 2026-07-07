-- +-tree on redo/undo
return {
  "jiaoshijie/undotree",
  dependencies = "nvim-lua/plenary.nvim",
  keys = { -- load the plugin only when using its keybinding:
    { "<leader>u", "<cmd>lua require('undotree').toggle()<cr>" },
  },
  config = function()
    require("undotree").setup()
    -- vimdiff integration: <C-d> in the undotree panel (formerly after/plugin/undotree.lua)
    require("setup.functions.undotree_vimdiff").setup()
  end,
}
