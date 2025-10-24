--- @diagnostic disable: undefined-global
return {
  "ThePrimeagen/harpoon",
  branch = "harpoon2",
  lazy = true,
  keys = {
    { "<C-e>",      mode = "n" },
    { "<leader>ha", mode = "n" },
    { "<leader>1",      mode = "n" },
    { "<M-2>",      mode = "n" },
    { "<M-3>",      mode = "n" },
    { "<M-4>",      mode = "n" },
    { "<M-5>",      mode = "n" },
    { "<M-6>",      mode = "n" },
    { "<M-7>",      mode = "n" },
    { "<M-8>",      mode = "n" },
    { "<M-9>",      mode = "n" }
  },
  config = function()
    local harpoon = require("harpoon")
    -- REQUIRED
    harpoon:setup()
    -- REQUIRED
    harpoon:extend({
      UI_CREATE = function(cx)
        vim.keymap.set("n", "<C-v>", function()
          harpoon.ui:select_menu_item({ vsplit = true })
        end, { buffer = cx.bufnr })
        vim.keymap.set("n", "<C-h>", function()
          harpoon.ui:select_menu_item({ split = true })
        end, { buffer = cx.bufnr })
        vim.keymap.set("n", "<C-t>", function()
          harpoon.ui:select_menu_item({ tabedit = true })
        end, { buffer = cx.bufnr })
      end,
    })
    vim.keymap.set("n", "<leader>ha", function() harpoon:list():append() end, { desc = 'harpoon append' })
    vim.keymap.set("n", "<C-e>", function() harpoon.ui:toggle_quick_menu(harpoon:list()) end)
    vim.keymap.set("n", "<leader>1", function() harpoon:list():select(1) end)
    vim.keymap.set("n", "<leader>2", function() harpoon:list():select(2) end)
    vim.keymap.set("n", "<leader>3", function() harpoon:list():select(3) end)
    vim.keymap.set("n", "<leader>4", function() harpoon:list():select(4) end)
    vim.keymap.set("n", "<leader>5", function() harpoon:list():select(5) end)
    vim.keymap.set("n", "<leader>6", function() harpoon:list():select(6) end)
    vim.keymap.set("n", "<leader>7", function() harpoon:list():select(7) end)
    vim.keymap.set("n", "<leader>8", function() harpoon:list():select(8) end)
    vim.keymap.set("n", "<leader>9", function() harpoon:list():select(9) end)
  end,
}
