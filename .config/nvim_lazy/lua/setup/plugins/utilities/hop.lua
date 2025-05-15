return {
  'phaazon/hop.nvim',
  version = 'v2',
  config = function()
    local hop = require('hop')
    local directions = require('hop.hint').HintDirection
    vim.keymap.set("n", '<leader><leader>hw', ':HopWord<CR>',
      { remap = true, silent = true, desc = "hop - move to any word" })
    vim.keymap.set("n", '<leader>jj', ':HopWord<CR>', { remap = true, silent = true, desc = "hop - move to any word" })
    require("hop").setup({
    })
  end
}
