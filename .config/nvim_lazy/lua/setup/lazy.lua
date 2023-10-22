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
    'folke/tokyonight.nvim',
    name = 'tokyonight'
  },
  {
    'Mofiqul/vscode.nvim', -- default
    name = 'vscode',
    config = function()
      vim.cmd('colorscheme vscode')
    end
  },
  {
    -- Highlitght colors, Indents, etc
    'nvim-treesitter/nvim-treesitter',
    --build = function()
    --  local ts_update = require('nvim-treesitter.install').update({ with_sync = true })
    --  ts_update()
    --end,
  },
  -- customize highlight
  "nvim-treesitter/playground",
  -- show context
  "nvim-treesitter/nvim-treesitter-context",
  -- This Neovim plugin provides alternating syntax highlighting (“rainbow parentheses”) for Neovim
  "hiphish/rainbow-delimiters.nvim",
  -- A high-performance color highlighter. show color in code, like #ffffff
  "norcalli/nvim-colorizer.lua",
  -- Show Statusline
  "nvim-lualine/lualine.nvim",
  -- highlight cursor text https://github.com/RRethy/vim-illuminate
  "RRethy/vim-illuminate",
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  "lukas-reineke/indent-blankline.nvim",
  -- change args color
  "m-demare/hlargs.nvim",
  -- show scroll bar
  "petertriho/nvim-scrollbar",
  -- gitsigns
  "lewis6991/gitsigns.nvim",


  -- **********************************
  -- utilities
  -- **********************************
  -- fuzzy search using ripgrep
  {
    'nvim-telescope/telescope.nvim', version = "0.1.3",
    dependencies = { { 'nvim-lua/plenary.nvim' } }
  },
  -- find Trouble in my code
  {
    "folke/trouble.nvim",
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
  "mbbill/undotree",
  -- lsp settings
  {
    'VonHeikemen/lsp-zero.nvim',
    branch = 'v1.x',
    dependencies = {
      -- LSP Support
      { 'neovim/nvim-lspconfig' },
      { 'williamboman/mason.nvim' },
      { 'williamboman/mason-lspconfig.nvim' },

      -- Autocompletion
      { 'hrsh7th/nvim-cmp' },
      { 'hrsh7th/cmp-buffer' },
      { 'hrsh7th/cmp-path' },
      { 'saadparwaiz1/cmp_luasnip' },
      { 'hrsh7th/cmp-nvim-lsp' },
      { 'hrsh7th/cmp-nvim-lua' },

      -- Snippets
      { 'L3MON4D3/LuaSnip' },
      { 'rafamadriz/friendly-snippets' },
    }
  },
  -- for lint and formatter(no lsp)
  "jose-elias-alvarez/null-ls.nvim",
  { "akinsho/toggleterm.nvim", version = "*" },
  -- Toggle comments numToStr/Comment.nvim
  "numToStr/Comment.nvim",
  -- focus
  "folke/zen-mode.nvim",
  -- GitHub Copilot
  "github/copilot.vim",
  -- File Explorer
  "nvim-tree/nvim-tree.lua",
  -- show icons https://github.com/nvim-tree/nvim-web-devicons
  "nvim-tree/nvim-web-devicons",
  -- git commands in nvim
  "tpope/vim-fugitive",
  -- git client
  {
    "NeogitOrg/neogit",
    dependencies = {
      { "nvim-lua/plenary.nvim" }, -- required
      { "sindrets/diffview.nvim" }, -- optional but recommended
    },
  },
  -- show git diff
  "APZelos/blamer.nvim",
}
local opts = {
}
require("lazy").setup(plugins, opts)
