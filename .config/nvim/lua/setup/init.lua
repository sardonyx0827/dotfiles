--- @diagnostic disable: undefined-global
require("setup.lazy")
require("setup.set")
require("setup.remap")
require("setup.functions.file")
require("setup.functions.ai")

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

-- Stop insert mode when switching buffers (e.g. when used telescope buffer)
vim.api.nvim_create_autocmd("BufWinEnter", {
  callback = function()
    if vim.bo.buftype == "" then
      vim.cmd("stopinsert")
    end
  end,
})

vim.opt.guicursor = table.concat({
  "n-v-c:block-blinkon500-blinkoff500",
  "i-ci-ve:ver25-blinkon500-blinkoff500",
  "r-cr:hor20-blinkon500-blinkoff500",
  "o:hor50-blinkon500-blinkoff500",
  "a:blinkwait700",
}, ",")

local function set_cursor_blinking_block()
  -- DECSCUSR Ps=1: blinking block
  vim.api.nvim_chan_send(vim.v.stderr, "\x1b[1 q")
end

vim.api.nvim_create_autocmd({ "VimLeave", "VimSuspend" }, {
  callback = function()
    set_cursor_blinking_block()
  end,
})
