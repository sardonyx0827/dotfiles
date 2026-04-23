-- fuzzy search using ripgrep
return {
  "nvim-telescope/telescope.nvim",
  branch = "master",
  dependencies = {
    { "nvim-lua/plenary.nvim" },
    {
      "nvim-telescope/telescope-fzf-native.nvim",
      build = "make",
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
