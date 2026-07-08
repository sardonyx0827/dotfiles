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
      -- 'avante' は avante.lua が依存に持つ blink-cmp-avante の登録名。
      -- ここに provider として登録しないとプラグインが読み込まれるだけで
      -- @ / # 補完が一切有効にならない (AvanteInput 以外では自動で無効)。
      default = { 'avante', 'copilot', 'lsp', 'snippets', 'buffer' },
      providers = {
        copilot = {
          name = 'copilot',
          module = 'blink-copilot',
          score_offset = 100,
          async = true,
        },
        avante = {
          name = 'Avante',
          module = 'blink-cmp-avante',
          -- Avante の入力バッファ以外では無効化する。通常バッファの @ / #
          -- に反応させず、avante.nvim (と依存の blink-cmp-avante) が
          -- 未ロードの段階で module を require させないためのガード。
          enabled = function()
            return vim.bo.filetype == 'AvanteInput'
          end,
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
