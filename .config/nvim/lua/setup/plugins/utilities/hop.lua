--- @diagnostic disable: undefined-global
return {
  'smoka7/hop.nvim',
  version = 'v2',
  config = function()
    vim.keymap.set("n", '<leader>jj', ':HopWord<CR>', { remap = true, silent = true, desc = "hop - move to any word" })
    vim.keymap.set("n", '<leader>js', ':HopPattern<CR>', { remap = true, silent = true, desc = "hop - move to searched pattern" })
    vim.keymap.set("n", '<leader>jc', ':HopChar1<CR>', { remap = true, silent = true, desc = "hop - move to two character" })
    require("hop").setup({
    })
  end
}
