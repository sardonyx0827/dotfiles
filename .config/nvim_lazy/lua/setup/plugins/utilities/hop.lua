--- @diagnostic disable: undefined-global
return {
  'smoka7/hop.nvim',
  version = 'v2',
  config = function()
    vim.keymap.set("n", '<leader>jj', ':HopWord<CR>', { remap = true, silent = true, desc = "hop - move to any word" })
    require("hop").setup({
    })
  end
}
