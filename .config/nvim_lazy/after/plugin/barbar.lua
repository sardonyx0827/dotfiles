--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>jb", "<cmd>BufferPick<cr>", { noremap = true, silent = true, desc = "Barbar - Jump Buffer" })

-- close all buffer and exit nvim
local function close_all_buffers_and_exit()
  -- Get a list of all buffers
  local buffers = vim.api.nvim_list_bufs()
  -- Iterate through the buffers and delete them
  for _, buf in ipairs(buffers) do
    if vim.api.nvim_buf_is_loaded(buf) then
      vim.api.nvim_buf_delete(buf, { force = true })
    end
  end
  -- Exit Neovim
  vim.cmd("qa")
end
vim.keymap.set("n", "<leader>qa", close_all_buffers_and_exit, { noremap = true, silent = true, desc = "Close All Buffers and Exit" })
vim.keymap.set("n", "<leader>q", "<cmd>q<cr>", { noremap = true, silent = true, desc = "Exit Neovim" })

