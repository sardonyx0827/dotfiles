--- @diagnostic disable: undefined-global
return {
  'smoka7/hop.nvim',
  version = 'v2',
  config = function()
    vim.keymap.set("n", '<leader>jj', ':HopWord<CR>', { remap = true, silent = true, desc = "hop - move to any word" })
    vim.keymap.set("n", '<leader>ss', ':HopPattern<CR>', { remap = true, silent = true, desc = "hop - move to pattern" })
    require("hop").setup({
    })
  end
}
