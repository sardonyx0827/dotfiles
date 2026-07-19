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

-- Trim trailing whitespace on save.
-- Wrapped in winsaveview/winrestview so the cursor and scroll position are
-- preserved, and `keeppatterns` so the last search pattern is not clobbered by
-- `\s\+$` (a bare `:%s/.../` would leave the cursor moved and pollute `n`/`N`).
autocmd({ "BufWritePre" }, {
  group = setupGroup,
  pattern = "*",
  callback = function()
    local view = vim.fn.winsaveview()
    vim.cmd([[keeppatterns %s/\s\+$//e]])
    vim.fn.winrestview(view)
  end,
})

-- Check if file changed when its buffer is entered or focus is gained
autocmd({ "WinEnter", "FocusGained", "BufEnter" }, {
  group = setupGroup,
  pattern = "*",
  command = "checktime",
})

-- Soft-wrap Markdown: visual folding only (no hard line breaks); keep the
-- indent of wrapped list/quote lines. Toggle off per-window with <leader>ww.
autocmd("FileType", {
  group = setupGroup,
  pattern = "markdown",
  callback = function()
    vim.opt_local.wrap = true
    vim.opt_local.linebreak = true
    vim.opt_local.breakindent = true
  end,
})

-- Stop insert mode when switching buffers (e.g. when used telescope buffer)
vim.api.nvim_create_autocmd("BufWinEnter", {
  group = setupGroup,
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
  group = setupGroup,
  callback = function()
    set_cursor_blinking_block()
  end,
})
