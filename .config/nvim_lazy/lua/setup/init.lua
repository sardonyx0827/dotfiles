--- @diagnostic disable: undefined-global
require("setup.lazy")
require("setup.set")
require("setup.remap")
require("setup.functions")

local augroup = vim.api.nvim_create_augroup
local setupGroup = augroup("setup", {})
local autocmd = vim.api.nvim_create_autocmd
local yank_group = augroup("HighlightYank", {})

function R(name)
  require("plenary.reload").reload_module(name)
end

-- Highlight yanked text(Blink on yank)
autocmd("TextYankPost", {
  group = yank_group,
  pattern = "*",
  callback = function()
    vim.highlight.on_yank({
      higroup = "IncSearch",
      timeout = 40,
    })
  end,
})

-- Trim trailing whitespace on save
autocmd({ "BufWritePre" }, {
  group = setupGroup,
  pattern = "*",
  command = [[%s/\s\+$//e]],
})

-- Check if file changed when its buffer is entered or focus is gained
autocmd({ "WinEnter", "FocusGained", "BufEnter" }, {
  pattern = "*",
  command = "checktime",
})
