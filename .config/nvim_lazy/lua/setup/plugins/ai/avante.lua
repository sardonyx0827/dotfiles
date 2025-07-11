--- @diagnostic disable: different-requires
return {
  "yetone/avante.nvim",
  event = "VeryLazy",
  version = false,   -- Never set this value to "*"! Never!
  opts = {
    provider = "copilot",
    providers = {
      copilot = {
        -- model = 'claude-sonnet-4',
        -- disable_auto_insert = true,
        -- disable_tools = true, -- disable tools!
        model = 'gpt-4.1',
      },
      claude = {
        endpoint = "https://api.anthropic.com",
        model = "claude-sonnet-4-20250514",
        api_key_name = "ANTHROPIC_API_KEY",
      },
      gemini = {
        -- endpoint = "https://gemini.googleapis.com",
        -- model = "gemini-2.5-pro-preview-05-06",
        model = "gemini-2.5-flash",
        api_key_name = "GEMINI_API_KEY",
      },
      ollama = {
        endpoint = "http://127.0.0.1:11434",   -- Note that there is no /v1 at the end.
        model = "gemma3:4b",
      },
      openai = {
        endpoint = "https://api.openai.com/v1",
        api_key_name = "OPENAI_API_KEY",
        model = "o4-mini",              -- your desired model (or use gpt-4o, etc.)
        -- model = "gpt-4o", -- your desired model (or use gpt-4o, etc.)
        timeout = 30000,                -- Timeout in milliseconds, increase this for reasoning models
        --reasoning_effort = "medium", -- low|medium|high, only used for reasoning models
      },
        openrouter = {
          __inherited_from = 'openai',
          endpoint = 'https://openrouter.ai/api/v1',
          api_key_name = "OPENROUTER_API_KEY",
          model = 'anthropic/claude-sonnet-4-20250514',
      },
    },
    behaviour = {
      enable_claude_text_editor_tool_mode = true,
      auto_apply_diff_after_generation = true,
      enable_cursor_planning_mode = true,
      support_paste_from_clipboard = true,
    },
    system_prompt = function()   -- LLMが常に最新のMCPサーバーの状態を持つように関数として定義 [6]
      local hub = require("mcphub").get_hub_instance()
      return hub:get_active_servers_prompt()
    end,
    custom_tools = function()   -- mcphubがロードされる前にrequireされるのを防ぐために関数を使用 [6]
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
      "web_search",
      "fetch"
    },
    selector = {
      exclude_auto_select = { "NvimTree" },
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
    "echasnovski/mini.pick",           -- for file_selector provider mini.pick
    "nvim-telescope/telescope.nvim",   -- for file_selector provider telescope
    "hrsh7th/nvim-cmp",                -- autocompletion for avante commands and mentions
    "ibhagwan/fzf-lua",                -- for file_selector provider fzf
    "nvim-tree/nvim-web-devicons",     -- or echasnovski/mini.icons
    "zbirenbaum/copilot.lua",          -- for providers='copilot'
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
  keys = {
    {
      "<leader>a+",
      function()
        local tree_ext = require("avante.extensions.nvim_tree")
        tree_ext.add_file()
      end,
      desc = "Select file in NvimTree",
      ft = "NvimTree",
    },
    {
      "<leader>a-",
      function()
        local tree_ext = require("avante.extensions.nvim_tree")
        tree_ext.remove_file()
      end,
      desc = "Deselect file in NvimTree",
      ft = "NvimTree",
    },
  },
}
