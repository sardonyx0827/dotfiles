--- @diagnostic disable: undefined-global
return {
  'romgrk/barbar.nvim',
  dependencies = {
    'lewis6991/gitsigns.nvim',       -- OPTIONAL: for git status
    'nvim-tree/nvim-web-devicons',   -- OPTIONAL: for file icons
  },
  init = function() vim.g.barbar_auto_setup = false end,
  -- barbar renders the buffer tabline, so it must load eagerly (no lazy trigger).
  -- Keymaps live in config (not the `keys` field) to avoid turning the plugin lazy.
  config = function()
    require("barbar").setup({
      -- anything missing will use the default:
      -- animation = true,
      -- insert_at_start = true,
      -- …etc.
      insert_at_end = true,
    })

    -- formerly after/plugin/barbar.lua
    vim.keymap.set("n", "<leader>jb", "<cmd>BufferPick<cr>",
      { noremap = true, silent = true, desc = "Barbar - Jump Buffer" })
    -- close other buffers except current one
    vim.keymap.set("n", "<leader>bo", "<cmd>BufferCloseAllButCurrent<cr>",
      { noremap = true, silent = true, desc = "Close Other Buffers" })
    -- close buffers to the right of current buffer
    vim.keymap.set("n", "<leader>br", "<cmd>BufferCloseBuffersRight<cr>",
      { noremap = true, silent = true, desc = "Close Buffers to Right" })
  end,
  version = '^1.0.0',   -- optional: only update when a new 1.x version is released
}
