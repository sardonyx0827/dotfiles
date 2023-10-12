-- disable netrw at the very start of your init.lua
--vim.g.loaded_netrw = 1
--vim.g.loaded_netrwPlugin = 1
vim.keymap.set("n", "<leader>e", "<cmd>NvimTreeToggle<CR>")

-- set termguicolors to enable highlight groups
vim.opt.termguicolors = true

--setup with some options
require("nvim-tree").setup({
  sort_by = "case_sensitive",
  view = {
    width = 30,
    --float = { enable = true },
    --side = "left",
  },
  renderer = {
    group_empty = true,
    icons = {
      glyphs = {
        git = {
          unstaged = '!',
          renamed = '»',
          untracked = '?',
          deleted = '✘',
          staged = '✓',
          unmerged = '',
          ignored = '◌',
        },
      },
    },
  },
  filters = {
    dotfiles = false,
    custom = { 'node_modules', '.git' },
  },
})


-- close nvim-tree when all windows are closed
vim.api.nvim_create_autocmd("QuitPre", {
  callback = function()
    local invalid_win = {}
    local wins = vim.api.nvim_list_wins()
    for _, w in ipairs(wins) do
      local bufname = vim.api.nvim_buf_get_name(vim.api.nvim_win_get_buf(w))
      if bufname:match("NvimTree_") ~= nil then
        table.insert(invalid_win, w)
      end
    end
    if #invalid_win == #wins - 1 then
      -- Should quit, so we close all invalid windows.
      for _, w in ipairs(invalid_win) do vim.api.nvim_win_close(w, true) end
    end
  end
})
