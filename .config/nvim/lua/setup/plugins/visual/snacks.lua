--- @diagnostic disable: undefined-global
--- @diagnostic disable: undefined-doc-name

return {
  "folke/snacks.nvim",
  priority = 1000,
  lazy = false,
  ---@type snacks.Config
  opts = {
    -- your configuration comes here
    -- or leave it empty to use the default settings
    -- refer to the configuration section below
    -- bigfile = { enabled = true },
    -- dashboard = { enabled = true },
    -- explorer = { enabled = true },
    picker = {
      sources = {
        files = { hidden = true },
        grep = { hidden = true },
        explorer = {
          hidden = true,
          auto_close = true,
          layout = {
            auto_hide = { "input" },
          },
          actions = {
            explorer_diff = function(picker)
              local selected = picker:selected()
              if #selected ~= 2 then
                vim.notify("Select exactly 2 files with <Tab> to diff", vim.log.levels.WARN)
                return
              end
              local paths = {}
              for i, item in ipairs(selected) do
                if not item.file then
                  vim.notify("Please select a file", vim.log.levels.WARN)
                  return
                end
                local p = vim.fn.fnamemodify(item.file, ":p")
                if vim.fn.isdirectory(p) == 1 then
                  vim.notify("Cannot diff a directory", vim.log.levels.WARN)
                  return
                end
                paths[i] = p
              end
              picker:close()
              vim.cmd("tabnew " .. vim.fn.fnameescape(paths[1]))
              vim.cmd("diffthis")
              vim.cmd("vsplit " .. vim.fn.fnameescape(paths[2]))
              vim.cmd("diffthis")
            end,
          },
          win = {
            list = {
              wo = {
                relativenumber = true,
              },
              keys = {
                ["/"] = { "/", mode = "n", expr = true, desc = "Vim search" },
                ["<C-d>"] = { "explorer_diff", mode = "n", desc = "Diff two files" },
              },
            },
          },
        },
      },
    },
    -- indent = { enabled = true },
    -- input = { enabled = true },
    -- picker = { enabled = true },
    -- notifier = { enabled = true },
    -- quickfile = { enabled = true },
    -- scope = { enabled = true },
    scroll = { enabled = true },
    -- statuscolumn = { enabled = true },
    words = { enabled = true },
  },
  keys = {
    { "<leader>.",  function() Snacks.scratch() end, desc = "Toggle Scratch Buffer" },
    { "]]",         function() Snacks.words.jump(vim.v.count1) end, desc = "Next Reference", mode = { "n", "t" } },
    { "[[",         function() Snacks.words.jump(-vim.v.count1) end, desc = "Prev Reference", mode = { "n", "t" } },
    -- { "<leader>e",  function() Snacks.explorer() end, desc = "Toggle Explorer" },
  }
}
