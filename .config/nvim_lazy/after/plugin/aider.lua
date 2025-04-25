vim.api.nvim_set_keymap('n', '<leader>ao', ':AiderOpen aider --model gemini/gemini-2.5-flash-preview-04-17 --no-auto-commits<CR>', {noremap = true, silent = true})
vim.api.nvim_set_keymap('n', '<leader>am', ':AiderAddModifiedFiles<CR>', {noremap = true, silent = true})
