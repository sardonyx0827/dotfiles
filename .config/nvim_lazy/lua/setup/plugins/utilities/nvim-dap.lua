--- @diagnostic disable: undefined-global
-- DAP for Debugging
return {
  'mfussenegger/nvim-dap',
  lazy = true,
  keys = {
    { "<F5>", mode = "n", },
  },
  dependencies = {
    'rcarriga/nvim-dap-ui',
    'jay-babu/mason-nvim-dap.nvim',
    "nvim-neotest/nvim-nio",
    'mfussenegger/nvim-dap-python',
  },
}
