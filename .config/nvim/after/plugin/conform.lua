--- @diagnostic disable: undefined-global
vim.keymap.set("n", "<leader>ff", function()
  require("conform").format({ async = true, lsp_format = "fallback" })
end)
