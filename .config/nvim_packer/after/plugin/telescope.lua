--- @diagnostic disable: undefined-global
require('telescope').setup({
  defaults = {
    file_ignore_patterns = { 'node_modules', 'vendor', 'dist', 'build' },
  },
  pickers = {
    live_grep = {
      --theme = "dropdown",
      additional_args = function()
        return { "--hidden" }
      end
    },
    find_files = {
      --theme = "dropdown"
    },
    git_files = {
      --theme = "dropdown",
    },
    buffers = {
      --theme = "dropdown",
    },
  },
})
local builtin = require('telescope.builtin')
vim.keymap.set('n', '<leader>sf', builtin.find_files, {})
vim.keymap.set('n', '<leader>gf', builtin.git_files, {})
vim.keymap.set('n', '<leader>gs', builtin.git_status, {})
vim.keymap.set('n', '<leader>b', builtin.buffers, {})
vim.keymap.set('n', '<leader>h', builtin.help_tags, {})
vim.keymap.set('n', '<leader>j', builtin.jumplist, {})
vim.keymap.set('n', '<leader>rg', builtin.registers, {})

-- using ripgrep. "sudo apt install ripgrep" or "brew install ripgrep"
vim.keymap.set('n', '<leader>gr', builtin.live_grep, {})
vim.keymap.set('n', '<leader>gw', builtin.grep_string, {})
