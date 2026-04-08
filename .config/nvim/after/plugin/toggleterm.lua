--- @diagnostic disable: undefined-global
--- @diagnostic disable: duplicate-set-field
--- @diagnostic disable: lowercase-global
function _G.set_terminal_keymaps()
  local opts = { buf = 0 }
  vim.keymap.set("t", "<esc>", [[<C-\><C-n>]], opts)
end

-- if you only want these mappings for toggle term use term://*toggleterm#* instead
vim.cmd("autocmd! TermOpen term://* lua set_terminal_keymaps()")
function _G._toggleterm_open(count)
  return function()
    require("toggleterm").toggle(count, 12)
  end
end

vim.keymap.set("n", "<leader>sh", ":ToggleTerm 1direction=vertical size=80<cr>")
vim.keymap.set("n", "<leader>sh1", ":ToggleTerm<cr>", { desc = "ToggleTerm - toggle session 1" })
vim.keymap.set("n", "<leader>sh2", ":ToggleTerm 2direction=horizontal<cr>", { desc = "ToggleTerm - toggle session 2" })
vim.keymap.set("n", "<leader>sh3", ":ToggleTerm 3direction=horizontal<cr>", { desc = "ToggleTerm - toggle session 3" })
vim.keymap.set("n", "<leader>sh4", ":ToggleTerm 4direction=horizontal<cr>", { desc = "ToggleTerm - toggle session 4" })
vim.keymap.set("n", "<leader>sh9", ":ToggleTerm 9direction=vertical size=80<cr>",
  { desc = "ToggleTerm - toggle session 9" })
vim.keymap.set("n", "<leader>sh8", ":ToggleTerm 8direction=vertical size=80<cr>",
  { desc = "ToggleTerm - toggle session 8" })
vim.keymap.set("n", "<leader>sh7", ":ToggleTerm 7direction=vertical size=80<cr>",
  { desc = "ToggleTerm - toggle session 7" })
vim.keymap.set("n", "<leader>sh6", ":ToggleTerm 6direction=vertical size=80<cr>",
  { desc = "ToggleTerm - toggle session 6" })
vim.keymap.set("n", "<leader>shf", ":ToggleTerm direction=float<cr>", { desc = "ToggleTerm - toggle session 1" })
vim.keymap.set("n", "<leader>shb", ":ToggleTerm direction=horizontal<cr>", { desc = "ToggleTerm - toggle session 1" })
local Terminal = require("toggleterm.terminal").Terminal
local toggle_docker = Terminal:new({
  cmd = "lazydocker",
  direction = "float",
  hidden = true
})

function _docker_toggle()
  toggle_docker:toggle()
end

vim.keymap.set("n", "<leader>td", "<cmd>lua _docker_toggle()<CR>",
  { silent = true, desc = "docker - CUI tool" })

-- claude code
local toggle_claude = Terminal:new({
  cmd = "claude",
  direction = "float",
  hidden = true
})
function _claude_toggle()
  toggle_claude:toggle()
end

vim.keymap.set("n", "<leader>cc", "<cmd>lua _claude_toggle()<CR>",
  { silent = true, desc = "claude - CUI tool" })

-- gemini cli
local toggle_gemini = Terminal:new({
  cmd = "gemini",
  direction = "float",
  hidden = true
})

function _gemini_toggle()
  toggle_gemini:toggle()
end

vim.keymap.set("n", "<leader>tg", "<cmd>lua _gemini_toggle()<CR>",
  { silent = true, desc = "gemini cli - CUI tool" })

-- codex (OpenAI)
local toggle_codex = Terminal:new({
  cmd = "codex",
  direction = "float",
  hidden = true
})

function _codex_toggle()
  toggle_codex:toggle()
end

vim.keymap.set("n", "<leader>to", "<cmd>lua _codex_toggle()<CR>",
  { silent = true, desc = "codex (OpenAI) - CUI tool" })
