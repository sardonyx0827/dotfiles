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
    for i = 1, 9 do
      vim.keymap.set("n", "<leader>" .. i, function() harpoon:list():select(i) end)
    end
  end,
}
