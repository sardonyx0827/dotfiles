--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>gb", "<cmd>BlameToggle window<cr>",
  { noremap = true, silent = true, desc = "Toggle Blame - toggle git comments on line." })
