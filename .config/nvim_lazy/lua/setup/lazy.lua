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
    "olimorris/onedarkpro.nvim",
    name = "onedark",
    event = "VeryLazy",
  },
  {
    "Mofiqul/vscode.nvim", -- default
    name = "vscode",
    event = "VeryLazy",
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
  {
    'hrsh7th/nvim-cmp',
    dependencies = {
      { 'hrsh7th/cmp-nvim-lsp' },
      { 'hrsh7th/cmp-buffer' },
      { 'hrsh7th/cmp-path' },
      { 'hrsh7th/cmp-cmdline' },
      { 'saadparwaiz1/cmp_luasnip' },
      { 'hrsh7th/cmp-nvim-lua' },
      { 'L3MON4D3/LuaSnip' },
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
  },
  -- File Explorer
  {
    "nvim-tree/nvim-tree.lua",
    lazy = true,
    cmd = "NvimTreeToggle",
  },
  -- show icons
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
    --event = "VeryLazy",
    keys = {
      { "<leader>", mode = "n", },
    },
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
  },
  -- Generate text using LLMs with customizable prompts
  -- install ollama: `curl https://ollama.ai/install.sh | sh`
  -- when use, run `ollama run mistral` in terminal
  -- when stop, run `systemctl stop ollama.service` in terminal
  {
    "David-Kunz/gen.nvim",
    opts = {
        model = "mistral", -- The default model to use.
        display_mode = "float", -- The display mode. Can be "float" or "split".
        show_prompt = false, -- Shows the Prompt submitted to Ollama.
        show_model = false, -- Displays which model you are using at the beginning of your chat session.
        no_auto_close = false, -- Never closes the window automatically.
        --init = function(options) pcall(io.popen, "ollama serve > /dev/null 2>&1 &") end,
        init = function(_) pcall(io.popen, "ollama serve > /dev/null 2>&1 &") end,
        -- Function to initialize Ollama
        command = "curl --silent --no-buffer -X POST http://localhost:11434/api/generate -d $body",
        -- The command for the Ollama service. You can use placeholders $prompt, $model and $body (shellescaped).
        -- This can also be a lua function returning a command string, with options as the input parameter.
        -- The executed command must return a JSON object with { response, context }
        -- (context property is optional).
        list_models = '<omitted lua function>', -- Retrieves a list of model names
        debug = false -- Prints errors and the command which is run.
    }
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
