--- @diagnostic disable: undefined-global
return {
  "zbirenbaum/copilot.lua",
  event = "InsertEnter",
  cmd = "Copilot",
  dependencies = {
    {
      "zbirenbaum/copilot-cmp",
      config = function()
        require("copilot_cmp").setup({
          fix_pairs = true,
        })
      end,
    },
  },
  config = function()
    require("copilot").setup({
      -- copilot_model = "gpt-5-mini",

      suggestion = {
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

      panel = {
        enabled = true,
        auto_refresh = true,
        keymap = {
          jump_prev = "[[",
          jump_next = "]]",
          accept = "<CR>",
          refresh = "gr",
          open = "<M-CR>"
        },
        layout = {
          position = "right", -- | top | left | right
          ratio = 0.5
        },
      },
      filetypes = {
        yaml = true,
        markdown = true,
        help = true,
        gitcommit = true,
        gitrebase = true,
        hgcommit = true,
      },
    })
  end
}
