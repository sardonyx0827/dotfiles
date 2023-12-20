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
vim.keymap.set("n", "<C-p>", ":Copilot panel open<CR>", { silent = true })
