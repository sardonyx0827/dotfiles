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
  use("p00f/nvim-ts-rainbow")
  -- customize highlight
  use("nvim-treesitter/playground")
  use("nvim-treesitter/nvim-treesitter-context")
  -- A high-performance color highlighter
  use("norcalli/nvim-colorizer.lua")
  -- Show Statusline
  use("nvim-lualine/lualine.nvim")
  -- Toggle comments numToStr/Comment.nvim
  use("numToStr/Comment.nvim")
  -- highlight cursor text https://github.com/RRethy/vim-illuminate
  use("RRethy/vim-illuminate")
  -- indent lines https://github.com/lukas-reineke/indent-blankline.nvim
  use("lukas-reineke/indent-blankline.nvim")
  -- change args color
  use("m-demare/hlargs.nvim")
  -- show scroll bar
  use("petertriho/nvim-scrollbar")

  -- **********************************
  -- utilities
  -- **********************************
  -- fuzzy search using ripgrep
  use {
    'nvim-telescope/telescope.nvim', tag = '0.1.3',
    requires = { { 'nvim-lua/plenary.nvim' } }
  }
  -- Find Troubles in my code
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
  -- useful for +-trees on redo/undo
  use("mbbill/undotree")
  -- use git commands
  use("tpope/vim-fugitive")
  -- builtin lsp
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
  -- use coc-nvim without lsp
  -- [CocConfig]
  -- {
  --  "languageserver": {},
  --  "diagnostic.enable": false,
  --  "suggest.autoTrigger": "none"
  --}
  use({ "neoclide/coc.nvim", branch = 'release' })
  use({ "akinsho/toggleterm.nvim", tag = '*' })
  -- ZEN-MODE is good for focus
  use("folke/zen-mode.nvim")
  -- GitHub Copilot
  use("github/copilot.vim")
  -- animations :CellularAutomaton args
  use("eandrju/cellular-automaton.nvim")
  -- Cloak allows you to overlay *'s (or any other character) over defined patterns in defined files.
  use("laytan/cloak.nvim")
  -- File Explorer
  use("nvim-tree/nvim-tree.lua")
  -- Show icons https://github.com/nvim-tree/nvim-web-devicons
  use("nvim-tree/nvim-web-devicons")
end)
