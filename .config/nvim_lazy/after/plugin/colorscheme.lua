--- @diagnostic disable: undefined-global
local set_hl_with_transparent_statusline = function()
  require('telescope.builtin').colorscheme({ enable_preview = true }) -- Use Telescope to select colorscheme
  vim.cmd("autocmd ColorScheme * lua vim.api.nvim_set_hl(0, 'StatusLine', { blend = 0 })")
end
vim.keymap.set("n", "<M-0>", set_hl_with_transparent_statusline, { noremap = true })

-- default color scheme
-- vim.cmd("colorscheme rose-pine-main")
-- vim.cmd("colorscheme kanagawa-dragon")
vim.cmd("colorscheme vscode")
-- vim.cmd("colorscheme tokyonight-night")

-- clear bg color
vim.api.nvim_set_hl(0, "Normal", { bg = "none" })
vim.api.nvim_set_hl(0, "NormalNC", { bg = "none" })
vim.api.nvim_set_hl(0, "NormalFloat", { bg = "none" })
vim.api.nvim_set_hl(0, "FloatBorder", { bg = "none" })
vim.api.nvim_set_hl(0, "StatusLine", { blend = 0 })
