function _G.set_terminal_keymaps()
  local opts = { buffer = 0 }
  vim.keymap.set("t", "<esc>", [[<C-\><C-n>]], opts)
  vim.keymap.set("t", "<C-h>", [[<Cmd>wincmd h<CR>]], opts)
  vim.keymap.set("t", "<C-j>", [[<Cmd>wincmd j<CR>]], opts)
  vim.keymap.set("t", "<C-k>", [[<Cmd>wincmd k<CR>]], opts)
  vim.keymap.set("t", "<C-l>", [[<Cmd>wincmd l<CR>]], opts)
  vim.keymap.set("t", "<C-s>", [[<C-\><C-n><C-w>]], opts)
end

-- if you only want these mappings for toggle term use term://*toggleterm#* instead
vim.cmd("autocmd! TermOpen term://* lua set_terminal_keymaps()")
vim.keymap.set("n", "<leader>sh", "<cmd>ToggleTerm<cr>")
function _G._toggleterm_open(count)
  return function()
    require("toggleterm").toggle(count, 12)
  end
end
vim.keymap.set("n", "<leader>sh1", "<cmd>ToggleTerm<cr>")
vim.keymap.set("n", "<leader>sh2", "<cmd>ToggleTerm 2direction=horizontal<cr>")
vim.keymap.set("n", "<leader>sh3", "<cmd>ToggleTerm 3direction=horizontal<cr>")
vim.keymap.set("n", "<leader>sh4", "<cmd>ToggleTerm 4direction=horizontal<cr>")
vim.keymap.set("n", "<leader>sh9", "<cmd>ToggleTerm 9direction=vertical<cr>")
vim.keymap.set("n", "<leader>sh8", "<cmd>ToggleTerm 8direction=vertical<cr>")
vim.keymap.set("n", "<leader>sh7", "<cmd>ToggleTerm 7direction=vertical<cr>")
vim.keymap.set("n", "<leader>sh6", "<cmd>ToggleTerm 6direction=vertical<cr>")

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
vim.api.nvim_set_keymap("n", "<leader>dt", "<cmd>lua _docui_toggle()<CR>", { noremap = true, silent = true })

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
