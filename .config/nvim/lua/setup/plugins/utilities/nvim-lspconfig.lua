--- @diagnostic disable: undefined-global
--- @diagnostic disable: different-requires
-- LSP: mason + mason-lspconfig + lspconfig (formerly after/plugin/lsp.lua).
-- Loads when a file is opened rather than at startup.
return {
  'neovim/nvim-lspconfig',
  event = { "BufReadPre", "BufNewFile" },
  dependencies = {
    'williamboman/mason.nvim',
    'williamboman/mason-lspconfig.nvim',
  },
  config = function()
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
        local client = vim.lsp.get_client_by_id(ev.data.client_id)
        local opts = { buf = bufnr, remap = false }

        vim.keymap.set("n", "<C-t>", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "jump to definition" }))
        vim.keymap.set("n", "gd", vim.lsp.buf.definition, vim.tbl_extend("force", opts, { desc = "jump to definition" }))
        vim.keymap.set("n", "K", vim.lsp.buf.hover, vim.tbl_extend("force", opts, { desc = "show definition" }))
        vim.keymap.set("n", "<leader>ra", vim.lsp.buf.rename,
          vim.tbl_extend("force", opts, { desc = "rename all file in workspace" }))
        vim.keymap.set("n", "<leader>ca", vim.lsp.buf.code_action, vim.tbl_extend("force", opts, { desc = "code action" }))
        vim.keymap.set("n", "gr", vim.lsp.buf.references, vim.tbl_extend("force", opts, { desc = "references" }))
        vim.keymap.set("n", "gl", vim.diagnostic.open_float, vim.tbl_extend("force", opts, { desc = "show diagnostic" }))
        vim.keymap.set("n", "[d", function() vim.diagnostic.jump({ count = -1 }) end,
          vim.tbl_extend("force", opts, { desc = "prev diagnostic" }))
        vim.keymap.set("n", "]d", function() vim.diagnostic.jump({ count = 1 }) end,
          vim.tbl_extend("force", opts, { desc = "next diagnostic" }))

        -- Native LSP document highlight
        if client and client:supports_method("textDocument/documentHighlight", bufnr) then
          local hl_group = vim.api.nvim_create_augroup("LspDocumentHighlight_" .. bufnr, { clear = true })
          vim.api.nvim_create_autocmd({ "CursorHold", "CursorHoldI" }, {
            group = hl_group,
            buffer = bufnr,
            callback = vim.lsp.buf.document_highlight,
          })
          vim.api.nvim_create_autocmd({ "CursorMoved", "CursorMovedI" }, {
            group = hl_group,
            buffer = bufnr,
            callback = vim.lsp.buf.clear_references,
          })
        end
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
        source = true,
        header = '',
        prefix = '',
      },
    })
  end,
}
