--- @diagnostic disable: undefined-global
local function close_all_buffers_and_exit()
  -- delete all sessions
  vim.cmd("SessionDelete")
  vim.cmd("q!")
end
vim.keymap.set("n", "<leader>qa", close_all_buffers_and_exit,
  { noremap = true, silent = true, desc = "Close All Buffers and Exit" })
