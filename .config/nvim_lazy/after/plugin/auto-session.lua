--- @diagnostic disable: undefined-global
local function close_all_buffers_and_exit()
  -- auto-sessionのセッションを削除
  vim.cmd("SessionDelete")
  -- 全バッファを削除して終了
  vim.cmd("q!")
end
vim.keymap.set("n", "<leader>qa", close_all_buffers_and_exit, { noremap = true, silent = true, desc = "Close All Buffers and Exit" })
