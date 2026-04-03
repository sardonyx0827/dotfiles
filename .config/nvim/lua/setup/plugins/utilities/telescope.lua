-- fuzzy search using ripgrep
return {
  "nvim-telescope/telescope.nvim",
  branch = "master",
  dependencies = { { "nvim-lua/plenary.nvim" } },
  config = function()
    require("telescope").setup({
      defaults = {
        file_ignore_patterns = { "node_modules", "vendor", "dist", "build", "^.git/" },
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
    })
  end,
}
