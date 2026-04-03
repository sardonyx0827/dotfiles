-- Autocompletion (blink.cmp)
return {
  'saghen/blink.cmp',
  version = '1.*',
  dependencies = {
    { 'rafamadriz/friendly-snippets' },
    {
      'fang2hou/blink-copilot',
      opts = {
        max_completions = 3,
        max_attempts = 4,
      },
    },
  },

  ---@module 'blink.cmp'
  opts = {
    keymap = {
      preset = 'default',
      ['<Tab>'] = {
        function(cmp)
          if cmp.snippet_active() then
            return cmp.accept()
          else
            return cmp.select_and_accept()
          end
        end,
        'snippet_forward',
        'fallback',
      },
      ['<C-e>'] = { 'show', 'hide' },
      ['<C-f>'] = { 'snippet_forward', 'fallback' },
      ['<C-b>'] = { 'snippet_backward', 'fallback' },
      ['<C-d>'] = { 'scroll_documentation_down', 'fallback' },
      ['<C-u>'] = { 'scroll_documentation_up', 'fallback' },
    },

    appearance = {
      nerd_font_variant = 'mono',
      kind_icons = {
        Copilot = "",
        Text = "󰉿",
        Method = "󰆧",
        Function = "󰊕",
        Constructor = "",
        Field = "󰜢",
        Variable = "󰀫",
        Class = "󰠱",
        Interface = "",
        Module = "",
        Property = "󰜢",
        Unit = "󰑭",
        Value = "󰎠",
        Enum = "",
        Keyword = "󰌋",
        Snippet = "",
        Color = "󰏘",
        File = "󰈙",
        Reference = "󰈇",
        Folder = "󰉋",
        EnumMember = "",
        Constant = "󰏿",
        Struct = "󰙅",
        Event = "",
        Operator = "󰆕",
        TypeParameter = "",
      },
    },

    completion = {
      list = {
        selection = {
          preselect = true,
          auto_insert = true,
        },
      },
      documentation = { auto_show = true, treesitter_highlighting = false },
      menu = {
        border = 'rounded',
        winhighlight = 'Normal:CmpNormal',
      },
    },

    sources = {
      default = { 'copilot', 'lsp', 'path', 'snippets', 'buffer' },
      providers = {
        copilot = {
          name = 'copilot',
          module = 'blink-copilot',
          score_offset = 100,
          async = true,
        },
      },
    },

    cmdline = {
      enabled = true,
      keymap = { preset = 'cmdline' },
      sources = { 'cmdline', 'buffer' },
    },

    fuzzy = { implementation = 'prefer_rust_with_warning' },
  },
  opts_extend = { 'sources.default' },
}
