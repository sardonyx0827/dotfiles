--- @diagnostic disable: undefined-global
--- @diagnostic disable: different-requires

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
  require("setup.plugins.colorscheme.rose-pine"),
  require("setup.plugins.colorscheme.tokyonight"),
  require("setup.plugins.colorscheme.onedark"),
  require("setup.plugins.colorscheme.vscode"),
  -- visual
  require("setup.plugins.visual.nvim-treesitter"),
  require("setup.plugins.visual.playground"),
  require("setup.plugins.visual.rainbow-delimiters"),
  require("setup.plugins.visual.nvim-colorizer"),
  require("setup.plugins.visual.lualine"),
  require("setup.plugins.visual.vim-illuminate"),
  require("setup.plugins.visual.indent-blankline"),
  require("setup.plugins.visual.hlargs"),
  require("setup.plugins.visual.gitsigns"),
  require("setup.plugins.visual.noice"),
  require("setup.plugins.visual.snacks"),
  require("setup.plugins.visual.zen-mode"),

  -- **********************************
  -- utilities
  -- **********************************
  -- explorer
  require("setup.plugins.utilities.nvim-tree"),
  require("setup.plugins.utilities.harpoon"),
  -- programming
  require("setup.plugins.utilities.telescope"),
  require("setup.plugins.utilities.trouble"),
  require("setup.plugins.utilities.undotree"),
  require("setup.plugins.utilities.comment"),
  require("setup.plugins.utilities.toggleterm"),
  require("setup.plugins.utilities.nvim-autopairs"),
  require("setup.plugins.utilities.nvim-surround"),
  -- git
  require("setup.plugins.utilities.neogit"),
  require("setup.plugins.utilities.blame"),
  -- lsp
  require("setup.plugins.utilities.lsp-zero"),
  require("setup.plugins.utilities.mason"),
  require("setup.plugins.utilities.mason-lspconfig"),
  require("setup.plugins.utilities.nvim-lspconfig"),
  require("setup.plugins.utilities.nvim-cmp"),
  require("setup.plugins.utilities.none-ls"),
  -- debugging
  require("setup.plugins.utilities.nvim-dap"),
  require("setup.plugins.utilities.vim-fugitive"),
  -- others
  require("setup.plugins.utilities.which-key"),
  require("setup.plugins.utilities.hop"),
  -- quickfix
  require("setup.plugins.utilities.vim-qfedit"),

  -- **********************************
  -- AI solutions
  -- **********************************
  require("setup.plugins.ai.copilot"),
  require("setup.plugins.ai.claudecode")
}

local lazy = require("lazy")
local opts = {}
lazy.setup(plugins, opts)
