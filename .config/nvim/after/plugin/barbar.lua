--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>jb", "<cmd>BufferPick<cr>", { noremap = true, silent = true, desc = "Barbar - Jump Buffer" })

---------------------------------------------------------
-- close other buffers except current one
---------------------------------------------------------
vim.keymap.set("n", "<leader>bo", "<cmd>BufferCloseAllButCurrent<cr>",
  { noremap = true, silent = true, desc = "Close Other Buffers" })

---------------------------------------------------------
-- close buffers to the right of current buffer
---------------------------------------------------------
vim.keymap.set("n", "<leader>br", "<cmd>BufferCloseBuffersRight<cr>",
  { noremap = true, silent = true, desc = "Close Buffers to Right" })

