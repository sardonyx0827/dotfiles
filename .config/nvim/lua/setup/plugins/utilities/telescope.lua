-- fuzzy search using ripgrep
local function builtin(name)
  return function()
    require("telescope.builtin")[name]()
  end
end

return {
  "nvim-telescope/telescope.nvim",
  branch = "master",
  lazy = true,
  dependencies = {
    { "nvim-lua/plenary.nvim" },
    {
      "nvim-telescope/telescope-fzf-native.nvim",
      build = "make",
    },
  },
  -- Keymaps formerly in after/plugin/telescope.lua (+ <M-0> from after/plugin/colorscheme.lua).
  -- Each key lazy-loads telescope on first use.
  keys = {
    { "<leader>sf", builtin("find_files"),             desc = "Find Files" },
    { "<leader>sg", builtin("git_files"),              desc = "Search Git Files" },
    { "<leader>h/", builtin("search_history"),         desc = "Search History" },
    { "<leader>h:", builtin("command_history"),        mode = { "n", "v" }, desc = "Command History" },
    { "<leader>gf", builtin("git_files"),              desc = "Git Files" },
    { "<leader>gs", builtin("git_status"),             desc = "Git Status" },
    { "<leader>gl", builtin("git_commits"),            desc = "Git Commits" },
    { "<leader>of", builtin("oldfiles"),               desc = "Old Files" },
    { "<leader>ls", builtin("buffers"),                desc = "Buffers" },
    { "<leader>sl", builtin("buffers"),                desc = "Buffers" },
    { "<leader>ll", builtin("buffers"),                desc = "Buffers" },
    { "<leader>jl", builtin("jumplist"),               desc = "Jump List" },
    { "<leader>he", builtin("help_tags"),              desc = "Help Tags" },
    { "<leader>rg", builtin("registers"),              desc = "Registers" },
    { "<leader>sO", builtin("lsp_workspace_symbols"),  desc = "LSP Workspace Symbols" },
    { "<leader>so", builtin("treesitter"),             desc = "Treesitter Symbols" },
    -- using ripgrep. "sudo apt install ripgrep" or "brew install ripgrep"
    { "<leader>gr", builtin("live_grep"),              desc = "Live Grep" },
    { "<leader>gw", builtin("grep_string"),            desc = "Grep String" },
    {
      "<M-0>",
      function()
        require("telescope.builtin").colorscheme({ enable_preview = true })
        vim.cmd("autocmd ColorScheme * lua vim.api.nvim_set_hl(0, 'StatusLine', { blend = 0 })")
      end,
      noremap = true,
      desc = "Pick colorscheme (transparent statusline)",
    },
  },
  config = function()
    local telescope = require("telescope")
    telescope.setup({
      defaults = {
        file_ignore_patterns = { "node_modules", "vendor", "dist", "build", "^.git/" },
        path_display = { "truncate" },
      },
      pickers = {
        show_all_buffers = true,
        find_files = {
          hidden = true,
        },
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
      extensions = {
        fzf = {
          fuzzy = true,
          override_generic_sorter = true,
          override_file_sorter = true,
          case_mode = "smart_case",
        },
      },
    })
    pcall(telescope.load_extension, "fzf")
  end,
}
