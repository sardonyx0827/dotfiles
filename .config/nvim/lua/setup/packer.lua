-- This file can be loaded by calling `lua require('plugins')` from your init.vim

-- Only required if you have packer configured as `opt`
vim.cmd.packadd('packer.nvim')

return require('packer').startup(function(use)
  -- Packer can manage itself
  use 'wbthomason/packer.nvim'

  -- **********************************
  -- visual settings
  -- **********************************
  -- colorscheme
  use({
    'Mofiqul/vscode.nvim', -- VSCode theme
    as = 'vscode',
    config = function()
      vim.cmd('colorscheme vscode')
    end
  })
  use {
    -- Highlitght colors, Indents, etc
    'nvim-treesitter/nvim-treesitter',
    run = function()
      local ts_update = require('nvim-treesitter.install').update({ with_sync = true })
      ts_update()
    end, }
  -- customize highlight
  use("nvim-treesitter/playground")
  -- show context
  use("nvim-treesitter/nvim-treesitter-context")
  -- This Neovim plugin provides alternating syntax highlighting (“rainbow parentheses”) for Neovim
  use("hiphish/rainbow-delimiters.nvim")
  -- A high-performance color highlighter. show color in code, like #ffffff
  use("norcalli/nvim-colorizer.lua")
  -- Show Statusline
  use("nvim-lualine/lualine.nvim")
  -- highlight cursor text https://github.com/RRethy/vim-illuminate
  use("RRethy/vim-illuminate")
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  use("lukas-reineke/indent-blankline.nvim")
  -- change args color
  use("m-demare/hlargs.nvim")
  -- show scroll bar
  use("petertriho/nvim-scrollbar")
  -- gitsigns
  use("lewis6991/gitsigns.nvim")


  -- **********************************
  -- utilities
  -- **********************************
  -- fuzzy search using ripgrep
  use {
    'nvim-telescope/telescope.nvim', tag = '0.1.3',
    requires = { { 'nvim-lua/plenary.nvim' } }
  }
  -- find Trouble in my code
  use({
    "folke/trouble.nvim",
    config = function()
      require("trouble").setup {
        icons = false,
        -- your configuration comes here
        -- or leave it empty to use the default settings
        -- refer to the configuration section below
      }
    end
  })
  -- +-tree on redo/undo
  use("mbbill/undotree")
  -- lsp settings
  use {
    'VonHeikemen/lsp-zero.nvim',
    branch = 'v1.x',
    requires = {
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
  }
  -- for lint and formatter(no lsp)
  use("jose-elias-alvarez/null-ls.nvim")
  use({ "akinsho/toggleterm.nvim", tag = '*' })
  -- Toggle comments numToStr/Comment.nvim
  use("numToStr/Comment.nvim")
  -- focus
  use("folke/zen-mode.nvim")
  -- GitHub Copilot
  use("github/copilot.vim")
  -- File Explorer
  use("nvim-tree/nvim-tree.lua")
  -- show icons https://github.com/nvim-tree/nvim-web-devicons
  use("nvim-tree/nvim-web-devicons")
  -- git commands in nvim
  use("tpope/vim-fugitive")
  -- git client
  use {
    "NeogitOrg/neogit",
    requires = {
      { "nvim-lua/plenary.nvim" }, -- required
      { "sindrets/diffview.nvim" }, -- optional but recommended
    },
  }
  -- show git diff
  use ("APZelos/blamer.nvim")
end)
