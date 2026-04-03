--- @diagnostic disable: undefined-global
--- @diagnostic disable: different-requires

require('mason').setup()
require('mason-lspconfig').setup({
  ensure_installed = {},
  handlers = {
    function(server_name)
      require('lspconfig')[server_name].setup({})
    end,
    lua_ls = function()
      require('lspconfig').lua_ls.setup({
        settings = {
          Lua = {
            runtime = { version = 'LuaJIT' },
            workspace = {
              checkThirdParty = false,
              library = { vim.env.VIMRUNTIME },
            },
          },
        },
      })
    end,
  }
})

vim.api.nvim_create_autocmd('LspAttach', {
  group = vim.api.nvim_create_augroup('UserLspConfig', {}),
  callback = function(ev)
    local bufnr = ev.buf
    local opts = { buffer = bufnr, remap = false }

    vim.keymap.set("n", "<C-t>", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "jump to definition" }))
    vim.keymap.set("n", "gd", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "jump to definition" }))
    vim.keymap.set("n", "K", vim.lsp.buf.hover, vim.tbl_extend("force", opts, { desc = "show definition" }))
    vim.keymap.set("n", "<leader>ra", vim.lsp.buf.rename, vim.tbl_extend("force", opts, { desc = "rename all file in workspace" }))
    vim.keymap.set("n", "<leader>ca", vim.lsp.buf.code_action, vim.tbl_extend("force", opts, { desc = "code action" }))
    vim.keymap.set("n", "gr", vim.lsp.buf.references, vim.tbl_extend("force", opts, { desc = "references" }))
    vim.keymap.set("n", "gl", vim.diagnostic.open_float, vim.tbl_extend("force", opts, { desc = "show diagnostic" }))
    vim.keymap.set("n", "[d", vim.diagnostic.goto_prev, vim.tbl_extend("force", opts, { desc = "prev diagnostic" }))
    vim.keymap.set("n", "]d", vim.diagnostic.goto_next, vim.tbl_extend("force", opts, { desc = "next diagnostic" }))
  end,
})

vim.diagnostic.config({
  virtual_text = true,
  severity_sort = true,
  signs = {
    text = {
      [vim.diagnostic.severity.ERROR] = '✘',
      [vim.diagnostic.severity.WARN] = '▲',
      [vim.diagnostic.severity.HINT] = '⚑',
      [vim.diagnostic.severity.INFO] = '',
    },
  },
  float = {
    style = 'minimal',
    border = 'rounded',
    source = 'always',
    header = '',
    prefix = '',
  },
})
