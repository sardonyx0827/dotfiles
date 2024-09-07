local lsp_zero = require('lsp-zero')

lsp_zero.on_attach(function(_, bufnr)
  -- see :help lsp-zero-keybindings
  -- to learn the available actions
  lsp_zero.default_keymaps({buffer = bufnr})
  -- jump to definition
  vim.keymap.set("n", "<C-t>", function() vim.lsp.buf.definition() end, { buffer = bufnr, remap = false, desc = "jump to definition" })
  -- show definition in a split
  vim.keymap.set("n", "K", function() vim.lsp.buf.hover() end, { buffer = bufnr, remap = false, desc = "show definition" })
  vim.keymap.set("n", "<leader>ca", function() vim.lsp.buf.code_action() end, { buffer = bufnr, remap = false, desc = "code action" })
  vim.keymap.set("n", "<leader>ff", function() vim.lsp.buf.format { async = true } end, { buffer = bufnr, remap = false, desc = "format this file" })
  vim.keymap.set("n", "<leader>ra", function() vim.lsp.buf.rename() end, {desc = "rename all file in workspace"})
end)

require('mason').setup({})
local null_ls = require("null-ls")
require('mason-lspconfig').setup({
  ensure_installed = {},
  handlers = {
    -- lsp_zero.default_setup,
    lua_ls = function()
      local lua_opts = lsp_zero.nvim_lua_ls()
      require('lspconfig').lua_ls.setup(lua_opts)
    end,
    function (server_name)
      if server_name == "tsserver" then
        if server_name == 'tsserver' then
          server_name = 'ts_ls'
        else
          lsp_zero.default_setup(server_name)
        end
      end
      -- highlight error for definition
      local opts = {
        capabilities = require("cmp_nvim_lsp").default_capabilities(),
        on_attach = function(client, bufnr)
          if client.supports_method "textDocument/documentHighlight" then
            local lsp_document_highlight = vim.api.nvim_create_augroup("lsp_document_highlight", {})
            vim.api.nvim_create_autocmd({ "CursorHold", "CursorHoldI" }, {
              group = lsp_document_highlight,
              buffer = bufnr,
              callback = function()
                vim.lsp.buf.document_highlight()
              end,
            })
            vim.api.nvim_create_autocmd({ "CursorMoved", "CursorMovedI" }, {
              group = lsp_document_highlight,
              buffer = bufnr,
              callback = function()
                vim.lsp.buf.clear_references()
              end,
            })
          end
        end
      }
      require("lspconfig")[server_name].setup(opts)
    end
  }
})

-- setup null-ls for formatting and linting
local mason_package = require("mason-core.package")
local mason_registry = require("mason-registry")
local null_sources = {}
for _, package in ipairs(mason_registry.get_installed_packages()) do
  local package_categories = package.spec.categories[1]
  if package_categories == mason_package.Cat.Formatter then
    table.insert(null_sources, null_ls.builtins.formatting[package.name])
  end
  if package_categories == mason_package.Cat.Linter then
    table.insert(null_sources, null_ls.builtins.diagnostics[package.name])
  end
end

null_ls.setup({
    sources = null_sources,
})

lsp_zero.set_sign_icons({
  error = '✘',
  warn = '▲',
  hint = '⚑',
  info = ''
})

vim.diagnostic.config({
  virtual_text = true,
  severity_sort = true,
  float = {
    style = 'minimal',
    border = 'rounded',
    source = 'always',
    header = '',
    prefix = '',
  },
})

local cmp = require('cmp')
local cmp_action = lsp_zero.cmp_action()
-- default configuration for cmp
--local cmp_format = lsp_zero.cmp_format()
require('luasnip.loaders.from_vscode').lazy_load()
vim.opt.completeopt = {'menu', 'menuone', 'noselect'}

local lspkind = require('lspkind')

cmp.setup({
  --formatting = cmp_format,
  formatting = {
    format = lspkind.cmp_format({
      -- options: 'text', 'text_symbol', 'symbol_text', 'symbol'
      mode = 'symbol', -- show only symbol annotations
      maxwidth = 100, -- prevent the popup from showing more than provided characters (e.g 50 will not show more than 50 characters)
      ellipsis_char = '...', -- when popup menu exceed maxwidth, the truncated part would show ellipsis_char instead (must define maxwidth first)
      symbol_map = {
        Copilot = "",
        Text = "󰉿",
        Method = "󰆧",
        Function = "󰊕",
        Constructor = "",
        Field = "󰜢",
        Variable = "󰀫",
        Class = "󰠱",
        Interface = "",
        Module = "",
        Property = "󰜢",
        Unit = "󰑭",
        Value = "󰎠",
        Enum = "",
        Keyword = "󰌋",
        Snippet = "",
        Color = "󰏘",
        File = "󰈙",
        Reference = "󰈇",
        Folder = "󰉋",
        EnumMember = "",
        Constant = "󰏿",
        Struct = "󰙅",
        Event = "",
        Operator = "󰆕",
        TypeParameter = "",
      },
      ---- The function below will be called before any actual modifications from lspkind
      ---- so that you can provide more controls on popup customization. (See [#30](https://github.com/onsails/lspkind-nvim/pull/30))
      --before = function (entry, vim_item)
      --  return vim_item
      --end
    }),
  },
  preselect = 'item',
  completion = {
    completeopt = 'menu,menuone,noinsert'
  },
  window = {
    --documentation = cmp.config.window.bordered(),
    completion = {
        border = "rounded",
        winhighlight = "Normal:CmpNormal",
    },
    documentation = {
        border = "rounded",
        winhighlight = "Normal:CmpDocNormal",
    }
  },
  sources = {
    {name = "copilot"},
    {name = 'path'},
    {name = 'nvim_lsp'},
    {name = 'nvim_lua'},
    {name = 'buffer', keyword_length = 3},
    {name = 'luasnip', keyword_length = 2},
  },
  mapping = cmp.mapping.preset.insert({
    -- confirm completion item
    ['<Tab>'] = cmp.mapping.confirm({select = false}),
    -- toggle completion menu
    ['<C-e>'] = cmp_action.toggle_completion(),
    -- tab complete
    --['<Tab>'] = nil,
    ['<S-Tab>'] = nil,
    -- navigate between snippet placeholder
    ['<C-f>'] = cmp_action.luasnip_jump_forward(),
    ['<C-b>'] = cmp_action.luasnip_jump_backward(),
    -- scroll documentation window
    ['<C-d>'] = cmp.mapping.scroll_docs(5),
    ['<C-u>'] = cmp.mapping.scroll_docs(-5),
  }),
  --experimental = {
  --  ghost_text = true -- this feature conflict with copilot.vim's preview.
  --}
})

-- `:` cmdline setup.
cmp.setup.cmdline(':', {
  mapping = cmp.mapping.preset.cmdline(),
  sources = cmp.config.sources({
    { name = 'path' }
  }, {
    {
      name = 'cmdline',
      option = {
        ignore_cmds = { 'Man', '!' }
      }
    }
  })
})
