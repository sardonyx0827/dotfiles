require("toggleterm").setup{
  -- "vertical" | "horizontal" | "tab" | "float"
  direction = "float"
}
function _G.set_terminal_keymaps()
  local opts = { buffer = 0 }
  vim.keymap.set("t", "<esc>", [[<C-\><C-n>]], opts)
  vim.keymap.set("t", "jk", [[<C-\><C-n>]], opts)
  vim.keymap.set("t", "<C-h>", [[<Cmd>wincmd h<CR>]], opts)
  vim.keymap.set("t", "<C-j>", [[<Cmd>wincmd j<CR>]], opts)
  vim.keymap.set("t", "<C-k>", [[<Cmd>wincmd k<CR>]], opts)
  vim.keymap.set("t", "<C-l>", [[<Cmd>wincmd l<CR>]], opts)
  vim.keymap.set("t", "<C-s>", [[<C-\><C-n><C-w>]], opts)
end

-- if you only want these mappings for toggle term use term://*toggleterm#* instead
vim.cmd("autocmd! TermOpen term://* lua set_terminal_keymaps()")
vim.keymap.set("n", "<leader>sh", "<cmd>ToggleTerm<cr>")

local Terminal = require("toggleterm.terminal").Terminal
-- use docui https://github.com/skanehira/docui
local docui = Terminal:new({
	cmd = "docui",
	direction = "float",
	hidden = true
})

function _docui_toggle()
	docui:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>do", "<cmd>lua _docui_toggle()<CR>", { noremap = true, silent = true })

-- todo (pip install dooit)
local dooit = Terminal:new({
	cmd = "dooit",
	direction = "float",
	hidden = true
})

function _dooit_toggle()
	dooit:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>to", "<cmd>lua _dooit_toggle()<CR>", { noremap = true, silent = true })
