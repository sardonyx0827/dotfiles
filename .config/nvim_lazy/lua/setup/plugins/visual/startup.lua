return {
  "startup-nvim/startup.nvim",
  -- lazy = true,
  -- event = "VimEnter",
  dependencies = {
    { "nvim-telescope/telescope.nvim" },
    { "nvim-lua/plenary.nvim" }
  },
  config = function()
    require "startup".setup({ theme = "dashboard" })   -- dashboard(default), evil, startify
  end
}
