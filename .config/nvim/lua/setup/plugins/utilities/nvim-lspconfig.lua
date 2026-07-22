--- @diagnostic disable: different-requires
-- LSP: mason + mason-lspconfig + lspconfig (formerly after/plugin/lsp.lua).
-- mason loads after startup (VeryLazy) so :Mason is available without opening a file;
-- mason-lspconfig + lspconfig load lazily when a file is opened.
local mason = {
  'williamboman/mason.nvim',
  event = "VeryLazy",
  config = function()
    require('mason').setup()
  end,
}

local lspconfig = {
  'neovim/nvim-lspconfig',
  event = { "BufReadPre", "BufNewFile" },
  dependencies = {
    'williamboman/mason.nvim',
    'williamboman/mason-lspconfig.nvim',
  },
  config = function()
    -- mason-lspconfig v2 は v1 の `handlers` を撤廃し、インストール済みサーバは
    -- automatic_enable (既定 on) が vim.lsp.enable() で有効化する。旧 `handlers`
    -- を setup() に渡しても黙って無視され、下の lua_ls 設定が dead code 化していた。
    -- サーバ個別設定は Neovim 0.11+ の vim.lsp.config で宣言し、nvim-lspconfig の
    -- lsp/<server>.lua 既定へマージする (enable より前に登録しておく)。
    vim.lsp.config('lua_ls', {
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

    require('mason-lspconfig').setup({
      ensure_installed = {},
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

return { mason, lspconfig }
