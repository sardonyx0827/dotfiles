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
    event = "VeryLazy",
  },
  {
    "folke/tokyonight.nvim",
    name = "tokyonight",
    event = "VeryLazy",
  },
  {
    "navarasu/onedark.nvim",
    name = "onedark",
    config = function()
      -- clear bg color
      require("onedark").setup({
        transparent = true,
        styles = {
            sidebars = "transparent",
            floats = "transparent",
        },
      })
    end
  },
  {
    "Mofiqul/vscode.nvim", -- default
    name = "vscode",
    --event = "VeryLazy",
    config = function()
      -- clear bg color
      require("vscode").setup({
        transparent = true,
        styles = {
            sidebars = "transparent",
            floats = "transparent",
        },
      })
      vim.cmd("colorscheme vscode")
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
    --event = "VeryLazy",
  },
  -- customize highlight
  {
    "nvim-treesitter/playground",
    event = "VeryLazy",
  },
  -- show context
  {
    "nvim-treesitter/nvim-treesitter-context",
  },
  -- This Neovim plugin provides alternating syntax highlighting (“rainbow parentheses”) for Neovim
  {
    "hiphish/rainbow-delimiters.nvim",
    event = "BufWinEnter",
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
  },
  -- highlight cursor text https://github.com/RRethy/vim-illuminate
  {
    "RRethy/vim-illuminate",
    event = "VeryLazy",
  },
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  {
    "lukas-reineke/indent-blankline.nvim",
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
  -- show scroll bar
  {
    "petertriho/nvim-scrollbar",
    event = "BufWinEnter",
    config = function()
      require("scrollbar").setup()
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
          live_grep = {
            --theme = "dropdown",
            additional_args = function()
              return { "--hidden" }
            end
          },
        },
      })
    end,
  },
  -- find Trouble in my code
  {
    "folke/trouble.nvim",
    event = "VeryLazy",
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
    "mbbill/undotree",
    lazy = true,
    cmd = "UndotreeToggle",
  },
  -- lsp settings
  { 'VonHeikemen/lsp-zero.nvim',        branch = 'v3.x' },

  -- LSP Support
  { 'williamboman/mason.nvim' },
  { 'williamboman/mason-lspconfig.nvim' },
  { 'neovim/nvim-lspconfig' },

  -- Autocompletion
  { 'hrsh7th/nvim-cmp' },
  { 'hrsh7th/cmp-nvim-lsp' },
  { 'hrsh7th/cmp-buffer' },
  { 'hrsh7th/cmp-path' },
  { 'saadparwaiz1/cmp_luasnip' },
  { 'hrsh7th/cmp-nvim-lua' },
  { 'L3MON4D3/LuaSnip' },
  { 'rafamadriz/friendly-snippets' },
  -- for lint and formatter(no lsp)
  {
    "jose-elias-alvarez/null-ls.nvim",
    event = "VeryLazy",
    config = function()
      local null_ls = require("null-ls")
      null_ls.setup({
        sources = {
          null_ls.builtins.formatting.stylua,
          null_ls.builtins.diagnostics.eslint,
          null_ls.builtins.completion.spell,
        },
      })
    end,
  },
  -- DAP
  {'mfussenegger/nvim-dap'},
  {'rcarriga/nvim-dap-ui'},
  {'jay-babu/mason-nvim-dap.nvim'},
  {'https://github.com/mfussenegger/nvim-dap-python'},

  -- Terminal
  {
    "akinsho/toggleterm.nvim",
    version = "*",
    lazy = true,
    cmd = { "ToggleTerm" },
    config = function()
      require("toggleterm").setup {
        -- "vertical" | "horizontal" | "tab" | "float"
        direction = "float"
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
    event = "BufWinEnter",
  },
  -- GitHub Copilot
  {
    "github/copilot.vim",
  },
  -- File Explorer
  {
    "nvim-tree/nvim-tree.lua",
    lazy = true,
    cmd = "NvimTreeToggle",
  },
  -- show icons https://github.com/nvim-tree/nvim-web-devicons
  {
    "nvim-tree/nvim-web-devicons",
    event = "VeryLazy",
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
    "APZelos/blamer.nvim",
    lazy = true,
    cmd = "BlamerToggle",
  },
  -- key navigation
  {
    "folke/which-key.nvim",
    lazy = true,
    cmd = "WhichKey",
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
  -- **********************************
  -- others
  -- **********************************
  -- BlackJack
  {
    "alanfortlink/blackjack.nvim",
    event = "VeryLazy",
    dependencies = { "nvim-lua/plenary.nvim" },
  },
}

local lazy = require("lazy")
--lazy.config.default.lazy = true
local opts = {}
lazy.setup(plugins, opts)
