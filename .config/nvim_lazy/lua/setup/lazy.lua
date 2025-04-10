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
-- copilot chat prompts
local prompts = {
  -- Code related prompts
  Explain = "/COPILOT_EXPLAIN 次のコードの動作を説明してください。",
  Review = "/COPILOT_REVIEW 次のコードをレビューし、改善の提案をしてください。",
  Fix = "/COPILOT_GENERATE このコードの問題を説明し、解決策を提供してください。",
  Optimize = "/COPILOT_GENERATE 選択したコードを最適化して、パフォーマンスと可読性を向上させてください。",
  Docs = "/COPILOT_GENERATE 次のコードに対するドキュメンテーションを提供してください。",
  Tests = "/COPILOT_GENERATE このコードのテストを生成してください。",
}
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
          -- TelescopeNormal = { fg = "subtle", bg = "overlay" },
          -- TelescopeSelection = { fg = "text", bg = "highlight_med" },
          -- TelescopeSelectionCaret = { fg = "love", bg = "highlight_med" },
          -- TelescopeMultiSelection = { fg = "text", bg = "highlight_high" },
          --
          -- TelescopeTitle = { fg = "base", bg = "love" },
          -- TelescopePromptTitle = { fg = "base", bg = "pine" },
          -- TelescopePreviewTitle = { fg = "base", bg = "iris" },
          --
          -- TelescopePromptNormal = { fg = "text", bg = "surface" },
          -- TelescopePromptBorder = { fg = "surface", bg = "surface" },

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
          floats = "transparent",
          --sidebars = "dark", -- style for sidebars, see below
          -- floats = "dark", -- style for floating windows
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
    -- lazy = true,
    -- event = "VimEnter",
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
    version = "0.1.8",
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
    keys = {
      {
        "<leader>xX",
        "<cmd>Trouble diagnostics toggle<cr>",
        desc = "Workspace Diagnostics (Trouble)",
      },
      {
        "<leader>xx",
        "<cmd>Trouble diagnostics toggle filter.buf=0<cr>",
        desc = "Buffer Diagnostics (Trouble)",
      },
      {
        "<leader>xs",
        "<cmd>Trouble symbols toggle focus=false<cr>",
        desc = "Symbols (Trouble)",
      },
      {
        "<leader>xl",
        "<cmd>Trouble lsp toggle focus=false win.position=right<cr>",
        desc = "LSP Definitions / references / ... (Trouble)",
      },
      {
        "<leader>xL",
        "<cmd>Trouble loclist toggle<cr>",
        desc = "Location List (Trouble)",
      },
      {
        "<leader>xQ",
        "<cmd>Trouble qflist toggle<cr>",
        desc = "Quickfix List (Trouble)",
      },
    },
    config = function()
      require("trouble").setup {
        -- icons = false,
        -- your configuration comes here
        -- or leave it empty to use the default settings
        -- refer to the configuration section below
        icons = {
          ---@type trouble.Indent.symbols
          indent        = {
            top         = "│ ",
            middle      = "├╴",
            last        = "└╴",
            -- last          = "-╴",
            -- last       = "╰╴", -- rounded
            fold_open   = " ",
            fold_closed = " ",
            ws          = "  ",
          },
          folder_closed = " ",
          folder_open   = " ",
          kinds         = {
            Array         = " ",
            Boolean       = "󰨙 ",
            Class         = " ",
            Constant      = "󰏿 ",
            Constructor   = " ",
            Enum          = " ",
            EnumMember    = " ",
            Event         = " ",
            Field         = " ",
            File          = " ",
            Function      = "󰊕 ",
            Interface     = " ",
            Key           = " ",
            Method        = "󰊕 ",
            Module        = " ",
            Namespace     = "󰦮 ",
            Null          = " ",
            Number        = "󰎠 ",
            Object        = " ",
            Operator      = " ",
            Package       = " ",
            Property      = " ",
            String        = " ",
            Struct        = "󰆼 ",
            TypeParameter = " ",
            Variable      = "󰀫 ",
          },
        },
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
      { "<leader>vu",  "<cmd>lua require('vuffers').toggle()<cr>" },
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
      -- { 'hrsh7th/cmp-path' },
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
    -- "jose-elias-alvarez/null-ls.nvim",
    "nvimtools/none-ls.nvim",
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
      "nvim-neotest/nvim-nio",
      'mfussenegger/nvim-dap-python',
    },
  },
  -- show messages Top-Right, and Rich UI
  {
    "folke/noice.nvim",
    opts = {
      -- add any options here
    },
    dependencies = {
      -- if you lazy-load any plugin below, make sure to add proper `module="..."` entries
      "MunifTanjim/nui.nvim",
      -- OPTIONAL:
      --   `nvim-notify` is only needed, if you want to use the notification view.
      --   If not available, we use `mini` as the fallback
      "rcarriga/nvim-notify",
    },
    config = function()
      require("notify").setup({
        background_colour = "#000000",
      })
      require("noice").setup({
        lsp = {
          -- override markdown rendering so that **cmp** and other plugins use **Treesitter**
          override = {
            ["vim.lsp.util.convert_input_to_markdown_lines"] = true,
            ["vim.lsp.util.stylize_markdown"] = true,
            ["cmp.entry.get_documentation"] = true, -- requires hrsh7th/nvim-cmp
          },
        },
        -- you can enable a preset for easier configuration
        presets = {
          bottom_search = false,        -- use a classic bottom cmdline for search
          command_palette = true,       -- position the cmdline and popupmenu together
          long_message_to_split = true, -- long messages will be sent to a split
          inc_rename = false,           -- enables an input dialog for inc-rename.nvim
          lsp_doc_border = false,       -- add a border to hover docs and signature help
        },
      })
    end,
  },
  {
    "folke/snacks.nvim",
    priority = 1000,
    lazy = false,
    ---@type snacks.Config
    opts = {
      -- your configuration comes here
      -- or leave it empty to use the default settings
      -- refer to the configuration section below
      bigfile = { enabled = true },
      -- dashboard = { enabled = true },
      explorer = { enabled = true },
      -- indent = { enabled = true },
      input = { enabled = true },
      picker = { enabled = true },
      notifier = { enabled = true },
      quickfile = { enabled = true },
      scope = { enabled = true },
      scroll = { enabled = true },
      statuscolumn = { enabled = true },
      words = { enabled = true },
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
    cmd = "BlameToggle",
    config = function()
      require("blame").setup()
    end
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
  {
    'phaazon/hop.nvim',
    version = 'v2',
    config = function()
      local hop = require('hop')
      local directions = require('hop.hint').HintDirection
      vim.keymap.set("n", '<leader><leader>hw', ':HopWord<CR>',
        { remap = true, silent = true, desc = "hop - move to any word" })
      vim.keymap.set("n", '<leader>jj', ':HopWord<CR>', { remap = true, silent = true, desc = "hop - move to any word" })
      require("hop").setup({
      })
    end
  },
  -- SSH
  {
    -- https://github.com/nosduco/remote-sshfs.nvim
    "nosduco/remote-sshfs.nvim",
    dependencies = { "nvim-telescope/telescope.nvim" },
    opts = {
      -- Refer to the configuration section below
      -- or leave empty for defaults
    },
  },
  {
    -- remote-nvim
    "amitds1997/remote-nvim.nvim",
    version = "*",                     -- Pin to GitHub releases
    dependencies = {
      "nvim-lua/plenary.nvim",         -- For standard functions
      "MunifTanjim/nui.nvim",          -- To build the plugin UI
      "nvim-telescope/telescope.nvim", -- For picking b/w different remote methods
    },
    config = true,
  },
  -- Live Share
  {
    "azratul/live-share.nvim",
    dependencies = {
      "jbyuki/instant.nvim",
    },
    config = function()
      vim.g.instant_username = "sardonyx"
      require("live-share").setup({
        port_internal = 8765,
        max_attempts = 40, -- 10 seconds
        service = "serveo.net"
      })
    end
  },
  -- **********************************
  -- AI solutions
  -- **********************************
  {
    "zbirenbaum/copilot.lua",
    event = "InsertEnter",
    cmd = "Copilot",
    -- copilot_model = "claude-3.5-sonnet",
    copilot_language = "Japanese",
  },
  {
    "CopilotC-Nvim/CopilotChat.nvim",
    event = "VeryLazy",
    branch = "main",
    dependencies = {
      -- Or { "github/copilot.vim" }
      "zbirenbaum/copilot.lua",
      "nvim-lua/plenary.nvim",
      "nvim-telescope/telescope.nvim"
    },
    opts = {
      mode = "newbuffer", -- newbuffer or split, default: newbuffer
      -- model = 'claude-3.5-sonnet',
      model = 'gpt-4o',
      show_help = "no",   -- Show help text for CopilotChatInPlace, default: yes
      debug = false,      -- Enable or disable debug mode, the log file will be in ~/.local/state/nvim/CopilotChat.nvim.log
      language = "Japanese",
      prompts = prompts,
      auto_follow_cursor = false, -- Don't follow the cursor after getting response
    },
    config = function(_, opts)
      local chat = require("CopilotChat")
      local select = require("CopilotChat.select")
      -- Use unnamed register for the selection
      opts.selection = select.unnamed
      chat.setup(opts)
      vim.api.nvim_create_user_command("CopilotChatInline", function(args)
        chat.ask(args.args, {
          selection = select.visual,
          window = {
            title = "CopilotChatInline",
            layout = "float",
            relative = "cursor",
            width = 0.5,
            height = 0.4,
            row = 1,
          },
        })
      end, { nargs = "*", range = true })
    end,
  },
  -- AI Agent
  {
    "olimorris/codecompanion.nvim",
    dependencies = {
      "nvim-lua/plenary.nvim",
      "nvim-treesitter/nvim-treesitter",
    },
    opts = {
      language = "Japanese",
      strategies = {
        -- Change the default chat adapter
        chat = {
          adapter = "copilot",
          tools = {
            ["mcp"] = {
              -- Prevent mcphub from loading before needed
              callback = function()
                return require("mcphub.extensions.codecompanion")
              end,
              description = "Call tools and resources from the MCP Servers"
            }
          }
        },
      },
      schema = {
        model = {
          order = 1,
          mapping = "parameters",
          type = "enum",
          desc =
          "ID of the model to use. See the model endpoint compatibility table for details on which models work with the Chat API.",
          ---@type string|fun(): string
          default = "claude-3.7-sonnet",
          choices = {
            ["o3-mini-2025-01-31"] = { opts = { can_reason = true } },
            ["o1-2024-12-17"] = { opts = { can_reason = true } },
            ["o1-mini-2024-09-12"] = { opts = { can_reason = true } },
            "claude-3.5-sonnet",
            "claude-3.7-sonnet",
            "claude-3.7-sonnet-thought",
            "gpt-4o-2024-08-06",
            "gemini-2.0-flash-001",
          },
        },
      }

    }
  },
  {
    "yetone/avante.nvim",
    event = "VeryLazy",
    version = false, -- Never set this value to "*"! Never!
    opts = {
      provider = "copilot",
      copilot = {
        model = 'claude-3.7-sonnet',
        max_tokens = 8192,
      },
      claude = {
        endpoint = "https://api.anthropic.com",
        model = "claude-3-7-sonnet-20250219",
        api_key_name = "ANTHROPIC_API_KEY",
        temperature = 0,
        max_tokens = 8192,
      },
      gemini = {
        -- endpoint = "https://gemini.googleapis.com",
        -- model = "gemini-2.5-pro-preview-03-25",
        model = "gemini-2.0-flash",
        api_key_name = "GEMINI_API_KEY",
        temperature = 0,
        max_tokens = 8192,
      },
      ollama = {
        endpoint = "http://127.0.0.1:11434", -- Note that there is no /v1 at the end.
        model = "gemma3:4b",
      },
      vendors = {
        openrouter = {
          __inherited_from = 'openai',
          endpoint = 'https://openrouter.ai/api/v1',
          api_key_name = "OPENROUTER_API_KEY",
          model = 'anthropic/claude-3.7-sonnet',
        },
      },
      behaviour = {
        enable_claude_text_editor_tool_mode = true,
        auto_apply_diff_after_generation = true,
        enable_cursor_planning_mode = true,
        support_paste_from_clipboard = true,
      },
      system_prompt = function() -- LLMが常に最新のMCPサーバーの状態を持つように関数として定義 [6]
        local hub = require("mcphub").get_hub_instance()
        return hub:get_active_servers_prompt()
      end,
      custom_tools = function() -- mcphubがロードされる前にrequireされるのを防ぐために関数を使用 [6]
        return { require("mcphub.extensions.avante").mcp_tool() }
      end,
      disabled_tools = {
        "list_files",
        "search_files",
        "read_file",
        "create_file",
        "rename_file",
        "delete_file",
        "create_dir",
        "rename_dir",
        "delete_dir",
        "bash",
      },
    },
    -- if you want to build from source then do `make BUILD_FROM_SOURCE=true`
    build = "make",
    -- build = "powershell -ExecutionPolicy Bypass -File Build.ps1 -BuildFromSource false" -- for windows
    dependencies = {
      "nvim-treesitter/nvim-treesitter",
      "stevearc/dressing.nvim",
      "nvim-lua/plenary.nvim",
      "MunifTanjim/nui.nvim",
      --- The below dependencies are optional,
      "echasnovski/mini.pick",         -- for file_selector provider mini.pick
      "nvim-telescope/telescope.nvim", -- for file_selector provider telescope
      "hrsh7th/nvim-cmp",              -- autocompletion for avante commands and mentions
      "ibhagwan/fzf-lua",              -- for file_selector provider fzf
      "nvim-tree/nvim-web-devicons",   -- or echasnovski/mini.icons
      "zbirenbaum/copilot.lua",        -- for providers='copilot'
      {
        -- support for image pasting
        "HakonHarnes/img-clip.nvim",
        event = "VeryLazy",
        opts = {
          -- recommended settings
          default = {
            embed_image_as_base64 = false,
            prompt_for_file_name = false,
            drag_and_drop = {
              insert_mode = true,
            },
            -- required for Windows users
            use_absolute_path = true,
          },
        },
      },
      {
        -- Make sure to set this up properly if you have lazy=true
        'MeanderingProgrammer/render-markdown.nvim',
        opts = {
          file_types = { "markdown", "Avante" },
        },
        ft = { "markdown", "Avante" },
      },
    },
  },
  {
    "ravitemer/mcphub.nvim",
    dependencies = {
      "nvim-lua/plenary.nvim",
    },
    -- cmd = "MCPHub",                            -- lazy load by default
    build = "npm install -g mcp-hub@latest", -- Installs globally
    config = function()
      require("mcphub").setup({
        auto_approve = true,
        extensions = {
          codecompanion = {
            -- Show the mcp tool result in the chat buffer
            show_result_in_chat = true,
            -- Make chat #variables from MCP server resources
            make_vars = true,
            -- Create slash commands for prompts
            make_slash_commands = true,
          }
        }
      })
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
