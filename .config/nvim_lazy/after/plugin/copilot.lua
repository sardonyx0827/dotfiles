--vim.keymap.set("i", "<C-j>", "<Plug>(copilot-next)")
--vim.keymap.set("i", "<C-k>", "<Plug>(copilot-previous)")
require("copilot").setup({
  suggestion = {
    --enabled = true,
    enabled = false,
    auto_trigger = false,
    debounce = 75,
    keymap = {
      accept = "<TAB>",
      accept_word = false,
      accept_line = false,
      next = "<c-j>",
      prev = "<c-k>",
      dismiss = "<C-]>",
    },
  },
  panel = { enabled = false },
})
require("copilot_cmp").setup()
vim.keymap.set("n", "<c-p>", ":Copilot panel<CR>", { silent = true })
vim.keymap.set("n", "<c-l>", ":Copilot panel accept<CR>", { silent = true })
vim.keymap.set("i", "<c-l>", "<ESC>:Copilot panel<CR>", { silent = true })
