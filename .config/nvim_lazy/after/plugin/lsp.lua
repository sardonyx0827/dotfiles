--- @diagnostic disable: undefined-global
--- @diagnostic disable: different-requires
local lsp_zero = require('lsp-zero')

lsp_zero.on_attach(function(_, bufnr)
  -- see :help lsp-zero-keybindings
  -- to learn the available actions
  lsp_zero.default_keymaps({ buffer = bufnr })
  -- jump to definition
  vim.keymap.set("n", "<C-t>", function() vim.lsp.buf.definition() end,
    { buffer = bufnr, remap = false, desc = "jump to definition" })
  vim.keymap.set("n", "gd", function() vim.lsp.buf.definition() end,
    { buffer = bufnr, remap = false, desc = "jump to definition" })
  -- show definition in a split
  vim.keymap.set("n", "K", function() vim.lsp.buf.hover() end,
    { buffer = bufnr, remap = false, desc = "show definition" })
  -- vim.keymap.set("n", "<leader>ca", function() vim.lsp.buf.code_action() end, { buffer = bufnr, remap = false, desc = "code action" })
  vim.keymap.set("n", "<leader>ff", function() vim.lsp.buf.format { async = true } end,
    { buffer = bufnr, remap = false, desc = "format this file" })
  vim.keymap.set("n", "<leader>ra", function() vim.lsp.buf.rename() end, { desc = "rename all file in workspace" })
  vim.keymap.set('n', '<leader>ca', vim.lsp.buf.code_action, { desc = 'code action' })
end)

require('mason').setup()
require('mason-lspconfig').setup({
  ensure_installed = {},
  handlers = {
    lsp_zero.default_setup,
    lua_ls = function()
      local lua_opts = lsp_zero.nvim_lua_ls()
      require('lspconfig').lua_ls.setup(lua_opts)
    end,
  }
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
vim.opt.completeopt = { 'menu', 'menuone', 'noselect' }

local lspkind = require('lspkind')

cmp.setup({
  --formatting = cmp_format,
  formatting = {
    format = lspkind.cmp_format({
      -- options: 'text', 'text_symbol', 'symbol_text', 'symbol'
      mode = 'symbol',       -- show only symbol annotations
      maxwidth = 100,        -- prevent the popup from showing more than provided characters (e.g 50 will not show more than 50 characters)
      ellipsis_char = '...', -- when popup menu exceed maxwidth, the truncated part would show ellipsis_char instead (must define maxwidth first)
      symbol_map = {
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
    { name = 'path' },
    { name = 'nvim_lsp' },
    { name = 'nvim_lua' },
    { name = 'buffer',  keyword_length = 3 },
    { name = 'luasnip', keyword_length = 2 },
  },
  mapping = cmp.mapping.preset.insert({
    -- confirm completion item
    ['<Tab>'] = cmp.mapping.confirm({ select = false }),
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
