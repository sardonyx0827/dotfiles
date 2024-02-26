local lazypath = vim.fn.stdpath("data") .. "/lazy/lazy.nvim"
if not vim.loop.fs_stat(lazypath) then
  vim.fn.system({
    "git",
    "clone",
    "--filter=blob:none",
    "https://github.com/folke/lazy.nvim.git",
    "--branch=stable", -- latest stable release
    lazypath,
  })
end
vim.opt.rtp:prepend(lazypath)

vim.g.mapleader = ","

local plugins = {
  -- **********************************
  -- visual settings
  -- **********************************
  -- colorscheme
  {
    "rose-pine/neovim",
    name = "rose-pine",
    --event = "VeryLazy",
    config = function()
      require("rose-pine").setup({
        highlight_groups = {
          -- default
          --TelescopeNormal = { fg = "subtle", bg = "overlay" },
          --TelescopeSelection = { fg = "text", bg = "highlight_med" },
          --TelescopeSelectionCaret = { fg = "love", bg = "highlight_med" },
          --TelescopeMultiSelection = { fg = "text", bg = "highlight_high" },

          --TelescopeTitle = { fg = "base", bg = "love" },
          --TelescopePromptTitle = { fg = "base", bg = "pine" },
          --TelescopePreviewTitle = { fg = "base", bg = "iris" },

          --TelescopePromptNormal = { fg = "text", bg = "surface" },
          --TelescopePromptBorder = { fg = "surface", bg = "surface" },

          -- bg none settings
          TelescopeBorder = { fg = "overlay", bg = "none" },
          TelescopeNormal = { fg = "subtle", bg = "none" },
          TelescopeSelection = { fg = "text", bg = "highlight_med" },
          TelescopeSelectionCaret = { fg = "love", bg = "highlight_med" },
          TelescopeMultiSelection = { fg = "text", bg = "highlight_high" },

          TelescopeTitle = { fg = "base", bg = "love" },
          TelescopePromptTitle = { fg = "base", bg = "pine" },
          TelescopePreviewTitle = { fg = "base", bg = "iris" },

          TelescopePromptNormal = { fg = "text", bg = "none" },
          TelescopePromptBorder = { fg = "surface", bg = "none" },

          Normal = { bg = "none" },
          NormalNC = { bg = "none" },
          NormalFloat = { bg = "none" },
          FloatBorder = { bg = "none" },
        },
      })
    end
  },
  {
    "folke/tokyonight.nvim",
    name = "tokyonight",
    event = "VeryLazy",
    config = function()
      require("tokyonight").setup({
        transparent = true,
        styles = {
          -- Background styles. Can be "dark", "transparent" or "normal"
          sidebars = "transparent",
          --floats = "transparent",
          --sidebars = "dark", -- style for sidebars, see below
          floats = "dark", -- style for floating windows
        },
        on_colors = function(colors)
          colors.border = "#565f89"
        end
      })
    end
  },
  {
    "olimorris/onedarkpro.nvim",
    name = "onedark",
    event = "VeryLazy",
  },
  {
    "Mofiqul/vscode.nvim", -- default
    name = "vscode",
    event = "VeryLazy",
    config = function()
      local c = require('vscode.colors').get_colors()
      require("vscode").setup({
        -- Alternatively set style in setup
        style = 'dark',
        -- Enable transparent background
        transparent = true,
        -- Enable italic comment
        italic_comments = false,
        -- Disable nvim-tree background color
        disable_nvimtree_bg = true,

        -- Override colors (see ./lua/vscode/colors.lua)
        --color_overrides = {
        --    vscLineNumber = '#FFFFFF',
        --},

        -- Override highlight groups (see ./lua/vscode/theme.lua)
        group_overrides = {
          -- this supports the same val table as vim.api.nvim_set_hl
          -- use colors from this colorscheme by requiring vscode.colors!
          Cursor = { fg = c.vscDarkBlue, bg = c.vscLightGreen, bold = true },
        }
      })
    end
  },
  {
    "startup-nvim/startup.nvim",
    lazy = true,
    event = "VimEnter",
    dependencies = {
      { "nvim-telescope/telescope.nvim" },
      { "nvim-lua/plenary.nvim" }
    },
    config = function()
      require "startup".setup({ theme = "dashboard" }) -- dashboard(default), evil, startify
    end
  },
  -- Highlitght colors, Indents, etc
  {
    "nvim-treesitter/nvim-treesitter",
    event = "BufRead",
    dependencies = {
      -- show context
      { "nvim-treesitter/nvim-treesitter-context", },
    },
  },
  -- customize highlight
  {
    "nvim-treesitter/playground",
    event = "VeryLazy",
  },
  -- This Neovim plugin provides alternating syntax highlighting (“rainbow parentheses”) for Neovim
  {
    "HiPhish/rainbow-delimiters.nvim",
    event = "VeryLazy",
  },
  -- A high-performance color highlighter. show color in code, like #ffffff
  {
    "norcalli/nvim-colorizer.lua",
    event = "VeryLazy",
    config = function()
      require("colorizer").setup(config, {
        RRGGBBAA = true,
        rgb_fn = true,
        hsl_fn = true,
      })
    end,
  },
  -- Show Statusline
  {
    "nvim-lualine/lualine.nvim",
    event = "VeryLazy",
    dependencies = {
      'AndreM222/copilot-lualine'
    },
  },
  -- highlight cursor text
  {
    "RRethy/vim-illuminate",
    event = "VeryLazy",
  },
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  {
    "lukas-reineke/indent-blankline.nvim",
    event = "VeryLazy",
  },
  -- change args color
  {
    "m-demare/hlargs.nvim",
    event = "BufWinEnter",
    config = function()
      require("hlargs").setup({
        color = "#ef9123",
        performance = {
          max_iterations = 400,
        },
      })
    end,
  },
  -- gitsigns
  {
    "lewis6991/gitsigns.nvim",
    event = "BufWinEnter",
  },

  -- **********************************
  -- utilities
  -- **********************************
  -- fuzzy search using ripgrep
  {
    "nvim-telescope/telescope.nvim",
    version = "0.1.3",
    dependencies = { { "nvim-lua/plenary.nvim" } },
    config = function()
      require("telescope").setup({
        defaults = {
          file_ignore_patterns = { "node_modules", "vendor", "dist", "build" },
        },
        pickers = {
          show_all_buffers = true,
          live_grep = {
            --theme = "dropdown",
            additional_args = function()
              return { "--hidden" }
            end
          },
          buffers = {
            mappings = {
              n = {
                ['<M-x>'] = "delete_buffer"
              },
              i = {
                ['<M-x>'] = "delete_buffer"
              }
            },
          },
        },
      })
    end,
  },
  -- find Trouble in my code
  {
    "folke/trouble.nvim",
    lazy = true,
    cmd = { "TroubleToggle", "Trouble", "TroubleRefresh" },
    config = function()
      require("trouble").setup {
        icons = false,
        -- your configuration comes here
        -- or leave it empty to use the default settings
        -- refer to the configuration section below
      }
    end
  },
  -- +-tree on redo/undo
  {
    "jiaoshijie/undotree",
    dependencies = "nvim-lua/plenary.nvim",
    config = true,
    keys = { -- load the plugin only when using it's keybinding:
      { "<leader>u", "<cmd>lua require('undotree').toggle()<cr>" },
    },
  },
  {
    "Hajime-Suzuki/vuffers.nvim",
    dependencies = { "nvim-tree/nvim-web-devicons" },
    config = function()
      require("vuffers").setup({
      })
    end,
    keys = {
      { "<leader>vu", "<cmd>lua require('vuffers').toggle()<cr>" },
      { "<leader>vsa", "<cmd>lua require('vuffers').sort({ type = 'filename', direction = 'asc' })<cr>" },
      { "<leader>vsd", "<cmd>lua require('vuffers').sort({ type = 'filename', direction = 'desc' })<cr>" },
    },
  },
  -- lsp settings
  {
    'VonHeikemen/lsp-zero.nvim',
    branch = 'v3.x'
  },
  -- LSP Support
  { 'williamboman/mason.nvim' },
  { 'williamboman/mason-lspconfig.nvim' },
  { 'neovim/nvim-lspconfig' },
  -- Autocompletion
  {
    'hrsh7th/nvim-cmp',
    dependencies = {
      { 'hrsh7th/cmp-nvim-lsp' },
      { 'hrsh7th/cmp-buffer' },
      { 'hrsh7th/cmp-path' },
      { 'hrsh7th/cmp-cmdline' },
      { 'saadparwaiz1/cmp_luasnip' },
      { 'hrsh7th/cmp-nvim-lua' },
      {
        "L3MON4D3/LuaSnip",
        -- follow latest release.
        version = "v2.*", -- Replace <CurrentMajor> by the latest released major (first number of latest release)
      },
      { 'rafamadriz/friendly-snippets' },
      { 'onsails/lspkind.nvim' },
      {
        "zbirenbaum/copilot-cmp",
        event = { "InsertEnter", "LspAttach" },
        fix_pairs = true,
      },
    },

  },
  -- for lint and formatter(no lsp)
  {
    "jose-elias-alvarez/null-ls.nvim",
  },
  -- DAP for Debugging
  {
    'mfussenegger/nvim-dap',
    lazy = true,
    keys = {
      { "<F5>", mode = "n", },
    },
    dependencies = {
      'rcarriga/nvim-dap-ui',
      'jay-babu/mason-nvim-dap.nvim',
      'mfussenegger/nvim-dap-python',
    },
  },
  {
    "folke/edgy.nvim",
    event = "VeryLazy",
    init = function()
      vim.opt.laststatus = 3
      vim.opt.splitkeep = "screen"
    end,
    opts = {
      wo = {
        spell = false,
      },
      animate = {
        enabled = false,
      },
      right = {
        {
          title = "CopilotChat.nvim", -- Title of the window
          ft = "copilot-chat",        -- This is custom file type from CopilotChat.nvim
          size = { width = 0.3 },     -- Width of the window
        },
      },
      left = {
        {
          title = "vuffers",
          ft = "vuffers",
          size = { height = 0.2 },
        },
        "NvimTree",
        {
          title = "UndoTree",
          ft = "undotree",
        },
      },
      bottom = {
        "Trouble",
      },
    },
  },
  -- Terminal
  {
    "akinsho/toggleterm.nvim",
    version = "*",
    lazy = true,
    cmd = { "ToggleTerm" },
    config = function()
      require("toggleterm").setup {
        -- "vertical" | "horizontal" | "tab" | "float"
        direction = "tab"
      }
    end,
  },
  -- Toggle comments numToStr/Comment.nvim
  {
    "numToStr/Comment.nvim",
    event = "BufWinEnter",
    config = function()
      require("Comment").setup()
    end,
  },
  -- focus
  {
    "folke/zen-mode.nvim",
  },
  -- File Explorer
  {
    "nvim-tree/nvim-tree.lua",
    lazy = true,
    cmd = "NvimTreeToggle",
    dependencies = {
      -- show icons with Nerd Font
      "nvim-tree/nvim-web-devicons",
      {
        "JMarkin/nvim-tree.lua-float-preview",
        lazy = true,
        -- default
        opts = {
          -- wrap nvimtree commands
          wrap_nvimtree_commands = true,
          -- lines for scroll
          scroll_lines = 20,
          -- window config
          window = {
            style = "minimal",
            relative = "win",
            border = "rounded",
            wrap = false,
          },
          mapping = {
            -- scroll down float buffer
            down = { "<C-d>" },
            -- scroll up float buffer
            up = { "<C-e>", "<C-u>" },
            -- enable/disable float windows
            toggle = { "<C-p>" },
          },
          -- hooks if return false preview doesn't shown
          hooks = {
            pre_open = function(path)
              -- if file > 5 MB or not text -> not preview
              local size = require("float-preview.utils").get_size(path)
              if type(size) ~= "number" then
                return false
              end
              local is_text = require("float-preview.utils").is_text(path)
              return size < 5 and is_text
            end,
            post_open = function(bufnr)
              return true
            end,
          },
        },
      },
    }
  },
  -- git commands in nvim
  {
    "tpope/vim-fugitive",
    lazy = true,
    cmd = "Gvdiffsplit",
  },
  -- git client
  {
    "NeogitOrg/neogit",
    lazy = true,
    cmd = "Neogit",
    dependencies = {
      { "nvim-lua/plenary.nvim" }, -- required
      {
        "sindrets/diffview.nvim",
        lazy = true,
        cmd = { "DiffviewOpen", "DiffviewClose", "DiffviewToggleFiles", "DiffviewFocusFiles", "DiffviewRefresh", "Neogit" },
      }, -- optional but recommended
    },
    config = function()
      require("neogit").setup()
    end,
  },
  -- show git diff
  {
    "FabijanZulj/blame.nvim",
    lazy = true,
    cmd = "ToggleBlame",
  },
  -- key navigation
  {
    "folke/which-key.nvim",
    event = "VeryLazy",
    config = function()
      require("which-key").setup {}
    end,
  },
  -- Surround selections
  {
    "kylechui/nvim-surround",
    version = "*", -- Use for stability; omit to use `main` branch for the latest features
    event = "VeryLazy",
    config = function()
      require("nvim-surround").setup({
        -- Configuration here, or leave empty to use defaults
      })
    end
  },
  {
    "ThePrimeagen/harpoon",
    branch = "harpoon2",
    lazy = true,
    keys = {
      { "<C-e>", mode = "n", },
    },
  },

  -- **********************************
  -- AI solutions
  -- **********************************
  -- GitHub Copilot
  --{
  --  -- Official
  --  "github/copilot.vim",
  --},
  {
    "zbirenbaum/copilot.lua",
    event = "InsertEnter",
    cmd = "Copilot",
  },
  {
    "gptlang/CopilotChat.nvim",
    event = "VeryLazy",
    dependencies = { "zbirenbaum/copilot.lua" }, -- Or { "github/copilot.vim" }
    opts = {
      mode = "newbuffer",                        -- newbuffer or split, default: newbuffer
      show_help = "no",                          -- Show help text for CopilotChatInPlace, default: yes
      debug = false,                             -- Enable or disable debug mode, the log file will be in ~/.local/state/nvim/CopilotChat.nvim.log
      language = "Japanese",
    },
    build = function()
      vim.notify("Please update the remote plugins by running ':UpdateRemotePlugins', then restart Neovim.")
    end,
  },
  {
    'kiddos/gemini.nvim',
    event = "VeryLazy",
    build = { 'pip install -r requirements.txt', ':UpdateRemotePlugins' },
    dependencies = {
      { "nvim-lua/plenary.nvim" }, -- required
    },
    config = function()
      require('gemini').setup({
        menu_key = '<C-p>',
      })
      vim.keymap.set("n", "<C-g>", ":GeminiChat<CR>", { desc = "Gemini Chat - Prompt" })
    end,
  },

  -- **********************************
  -- others
  -- **********************************
  -- BlackJack
  {
    "alanfortlink/blackjack.nvim",
    lazy = true,
    cmd = "BlackJackNewGame",
    dependencies = { "nvim-lua/plenary.nvim" },
  },
}

local lazy = require("lazy")
local opts = {}
lazy.setup(plugins, opts)
