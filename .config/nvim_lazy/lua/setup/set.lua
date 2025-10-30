--- @diagnostic disable: undefined-global
vim.opt.nu = true
vim.opt.relativenumber = true

vim.opt.tabstop = 2
vim.opt.softtabstop = 2
vim.opt.shiftwidth = 2
vim.opt.expandtab = true

vim.opt.smartindent = true

vim.opt.wrap = false
vim.opt.whichwrap:append("<,>,[,],h,l")

vim.opt.swapfile = false
vim.opt.backup = false
vim.opt.undodir = os.getenv("HOME") .. "/.vim/undodir"
vim.opt.undofile = true

vim.opt.hlsearch = true
vim.opt.incsearch = true
vim.opt.list = true

vim.opt.termguicolors = true

vim.opt.scrolloff = 2
vim.opt.signcolumn = "yes"
vim.opt.isfname:append("@-@")

vim.opt.updatetime = 50

vim.opt.clipboard:append { "unnamedplus" }

vim.opt.mouse = ""

vim.opt.ignorecase = true
vim.opt.smartcase = true

vim.opt.cursorline = true

vim.cmd([[au FileType * set fo-=c fo-=r fo-=o]])
vim.cmd([[ let g:netrw_bufsettings = 'noma nomod nu nowrap ro nobl' ]])

vim.g.netrw_liststyle = 3
vim.g.netrw_winsize = 80


vim.opt.listchars = { tab = ">·", trail = "·", extends = "…", precedes = "…" }

-- listchars の色（前景色のみ）
vim.api.nvim_set_hl(0, "Whitespace", { fg = "#Fb7280", bg = "NONE" })
-- 必要に応じて併用（記号によっては効くことがあります）
vim.api.nvim_set_hl(0, "NonText", { fg = "#Faa0a6", bg = "NONE" })
vim.api.nvim_set_hl(0, "SpecialKey", { fg = "#Faa0a6", bg = "NONE" })

-- カラースキーム変更時に再適用
vim.api.nvim_create_autocmd("ColorScheme", {
  callback = function()
    vim.api.nvim_set_hl(0, "Whitespace", { fg = "#Fb7280", bg = "NONE" })
    vim.api.nvim_set_hl(0, "NonText", { fg = "#Faa0a6", bg = "NONE" })
    vim.api.nvim_set_hl(0, "SpecialKey", { fg = "#Faa0a6", bg = "NONE" })
  end,
})

-- go lang has no tabs
vim.api.nvim_create_autocmd("FileType", {
  pattern = { "make", "go" },
  callback = function()
    vim.opt_local.expandtab = false
  end,
})

