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

vim.keymap.set("n", "<leader>sh1", ":ToggleTerm<cr>", {desc = "ToggleTerm - toggle session 1"})
vim.keymap.set("n", "<leader>sh2", ":ToggleTerm 2direction=horizontal<cr>", {desc = "ToggleTerm - toggle session 2"})
vim.keymap.set("n", "<leader>sh3", ":ToggleTerm 3direction=horizontal<cr>", {desc = "ToggleTerm - toggle session 3"})
vim.keymap.set("n", "<leader>sh4", ":ToggleTerm 4direction=horizontal<cr>", {desc = "ToggleTerm - toggle session 4"})
vim.keymap.set("n", "<leader>sh9", ":ToggleTerm 9direction=vertical<cr>", {desc = "ToggleTerm - toggle session 9"})
vim.keymap.set("n", "<leader>sh8", ":ToggleTerm 8direction=vertical<cr>", {desc = "ToggleTerm - toggle session 8"})
vim.keymap.set("n", "<leader>sh7", ":ToggleTerm 7direction=vertical<cr>", {desc = "ToggleTerm - toggle session 7"})
vim.keymap.set("n", "<leader>sh6", ":ToggleTerm 6direction=vertical<cr>", {desc = "ToggleTerm - toggle session 6"})
vim.keymap.set("n", "<leader>shf", ":ToggleTerm direction=float<cr>", {desc = "ToggleTerm - toggle session 1"})
vim.keymap.set("n", "<leader>shb", ":ToggleTerm direction=horizontal<cr>", {desc = "ToggleTerm - toggle session 1"})

local Terminal = require("toggleterm.terminal").Terminal
local toggle_docker = Terminal:new({
	cmd = "lazydocker",
	direction = "float",
	hidden = true
})

function _docker_toggle()
	toggle_docker:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>td", "<cmd>lua _docker_toggle()<CR>", { noremap = true, silent = true, desc = "docker - CUI tool" })

-- todo (pip install dooit)
local dooit = Terminal:new({
	cmd = "dooit",
	direction = "float",
	hidden = true
})

function _dooit_toggle()
	dooit:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>to", "<cmd>lua _dooit_toggle()<CR>", { noremap = true, silent = true, desc = "toggle dooit - CUI tool" })

-- claude code
local toggle_claude = Terminal:new({
	cmd = "claude",
	direction = "float",
	hidden = true
})

function _claude_toggle()
	toggle_claude:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>tc", "<cmd>lua _claude_toggle()<CR>", { noremap = true, silent = true, desc = "claude code - CUI tool" })

-- codex (OpenAI)
local toggle_codex = Terminal:new({
  cmd = "codex",
  direction = "float",
  hidden = true
})

function _codex_toggle()
  toggle_codex:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>tx", "<cmd>lua _codex_toggle()<CR>", { noremap = true, silent = true, desc = "codex (OpenAI) - CUI tool" })

-- aider
local toggle_aider = Terminal:new({
  -- cmd = "aider --model gemini/gemini-2.5-pro-preview-03-25 --weak-model gemini/gemini-2.5-flash-preview-04-17 --no-auto-commits",
  cmd = "aider --model gemini/gemini-2.5-flash-preview-04-17 --no-auto-commits",
  direction = "float",
  hidden = true
})

function _aider_toggle()
  toggle_aider:toggle()
end
vim.api.nvim_set_keymap("n", "<leader>tg", "<cmd>lua _aider_toggle()<CR>", { noremap = true, silent = true, desc = "aider - CUI tool" })
