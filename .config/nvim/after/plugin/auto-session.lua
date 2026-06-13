--- @diagnostic disable: undefined-global
local function close_all_buffers()
  vim.cmd("bufdo bd")
end
vim.keymap.set("n", "<leader>qq", close_all_buffers,
  { noremap = true, silent = true, desc = "Close All Buffers" })
local function close_all_buffers_and_exit()
  -- delete all sessions
  close_all_buffers()
  vim.cmd("q!")
end
vim.keymap.set("n", "<leader>qa", close_all_buffers_and_exit,
  { noremap = true, silent = true, desc = "Close All Buffers and Exit" })
