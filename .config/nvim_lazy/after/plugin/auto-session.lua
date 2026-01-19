--- @diagnostic disable: undefined-global
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


-- close other buffers except current one
local function close_other_buffers()
  local current_buf = vim.api.nvim_get_current_buf()
  local buffers = vim.api.nvim_list_bufs()
  for _, buf in ipairs(buffers) do
    if buf ~= current_buf and vim.api.nvim_buf_is_loaded(buf) then
      vim.api.nvim_buf_delete(buf, { force = true })
    end
  end
end
vim.keymap.set("n", "<leader>bo", close_other_buffers, { noremap = true, silent = true, desc = "Close Other Buffers" })

-- close buffers to the right of current buffer
local function close_buffers_to_right()
  local current_buf = vim.api.nvim_get_current_buf()
  local buffers = vim.api.nvim_list_bufs()
  local found_current = false
  for _, buf in ipairs(buffers) do
    if buf == current_buf then
      found_current = true
    elseif found_current and vim.api.nvim_buf_is_loaded(buf) then
      vim.api.nvim_buf_delete(buf, { force = true })
    end
  end
end
vim.keymap.set("n", "<leader>br", close_buffers_to_right, { noremap = true, silent = true, desc = "Close Buffers to Right" })
