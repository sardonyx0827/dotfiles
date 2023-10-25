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
    "folke/tokyonight.nvim",
    name = "tokyonight",
    event = "VeryLazy",
  },
  {
    "Mofiqul/vscode.nvim", -- default
    name = "vscode",
    config = function()
      vim.cmd("colorscheme vscode")
    end
  },
  {
    "startup-nvim/startup.nvim",
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
  },
  -- customize highlight
  {
    "nvim-treesitter/playground",
  },
  -- show context
  {
    "nvim-treesitter/nvim-treesitter-context",
    event = "VeryLazy",
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
  },
  -- highlight cursor text https://github.com/RRethy/vim-illuminate
  {
    "RRethy/vim-illuminate",
  },
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  "lukas-reineke/indent-blankline.nvim",
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
    event = "BufWinEnter",
  },
  -- lsp settings
  {
    "VonHeikemen/lsp-zero.nvim",
    branch = "v1.x",
    dependencies = {
      -- LSP Support
      { "neovim/nvim-lspconfig"},
      { "williamboman/mason.nvim" },
      { "williamboman/mason-lspconfig.nvim" },

      -- Autocompletion
      { "hrsh7th/nvim-cmp" },
      { "hrsh7th/cmp-buffer" },
      { "hrsh7th/cmp-path" },
      { "saadparwaiz1/cmp_luasnip" },
      { "hrsh7th/cmp-nvim-lsp" },
      { "hrsh7th/cmp-nvim-lua" },

      -- Snippets
      { "L3MON4D3/LuaSnip" },
      { "rafamadriz/friendly-snippets" },
    }
  },
  -- for lint and formatter(no lsp)
  {
    "jose-elias-alvarez/null-ls.nvim",
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
  {
    "akinsho/toggleterm.nvim",
    version = "*",
    event = "VeryLazy",
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
    event = "VeryLazy",
  },
  -- show icons https://github.com/nvim-tree/nvim-web-devicons
  {
    "nvim-tree/nvim-web-devicons",
    event = "VeryLazy",
  },
  -- git commands in nvim
  {
    "tpope/vim-fugitive",
    event = "BufWinEnter",
  },
  -- git client
  {
    "NeogitOrg/neogit",
    dependencies = {
      { "nvim-lua/plenary.nvim" }, -- required
      { "sindrets/diffview.nvim" }, -- optional but recommended
    },
    config = function()
      require("neogit").setup()
    end,
  },
  -- show git diff
  {
    "APZelos/blamer.nvim",
    event = "BufWinEnter",
  },
  -- key navigation
  {
    "folke/which-key.nvim",
    event = "VeryLazy",
  },
}

local lazy = require("lazy")
--lazy.config.default.lazy = true
local opts = {}
lazy.setup(plugins, opts)
