return {
  "stevearc/conform.nvim",
  event = { "BufWritePre" },
  keys = {
    {
      "<leader>ff",
      function()
        require("conform").format({ async = true, lsp_format = "fallback" })
      end,
      desc = "Format buffer (conform)",
    },
  },
  config = function()
    require("conform").setup({
      formatters_by_ft = {
        javascript      = { "prettier" },
        typescript      = { "prettier" },
        javascriptreact = { "prettier" },
        typescriptreact = { "prettier" },
        json            = { "prettier" },
        jsonc           = { "prettier" },
        sh              = { 'shfmt' },
      },
      -- format_on_save = {
      --   timeout_ms = 500,
      --   lsp_format = "fallback",
      -- },
    })
  end,
}
