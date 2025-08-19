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

autocmd({ "BufWritePre" }, {
  group = setupGroup,
  pattern = "*",
  command = [[%s/\s\+$//e]],
})

autocmd({ "WinEnter", "FocusGained", "BufEnter" }, {
  pattern = "*",
  command = "checktime",
})
