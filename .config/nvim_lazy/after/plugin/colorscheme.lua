local function set_bg_color_to_clear()
  -- clear bg color
  local color_scheme = vim.g.colors_name
  require(color_scheme).setup({
    transparent = true,
    styles = {
      sidebars = "transparent",
      floats = "transparent",
    },
  })
  --vim.cmd("colorscheme " .. color_scheme)
  vim.cmd.colorscheme(color_scheme)
end

local function set_bg_color_to_default()
  -- set bg color to default
  local color_scheme = vim.g.colors_name
  require(color_scheme).setup({
    transparent = false,
    styles = {
      sidebars = "bg",
      floats = "bg",
    },
  })
  --vim.cmd("colorscheme " .. color_scheme)
  vim.cmd.colorscheme(color_scheme)
end

local transparent = false
local function toggle_transparent()
  if transparent then
    set_bg_color_to_default()
    transparent = false
  else
    set_bg_color_to_clear()
    transparent = true
  end
end
--vim.keymap.set("n", "<M-1>", ":colorscheme vscode<CR>", { noremap = true })
--vim.keymap.set("n", "<M-2>", ":colorscheme tokyonight-moon<CR>", { noremap = true })
--vim.keymap.set("n", "<M-3>", ":colorscheme onedark<CR>", { noremap = true })
--vim.keymap.set("n", "<M-4>", ":colorscheme tokyonight<CR>", { noremap = true })
--vim.keymap.set("n", "<M-5>", ":colorscheme rose-pine-moon<CR>", { noremap = true })
--vim.keymap.set("n", "<M-6>", ":colorscheme rose-pine-main<CR>", { noremap = true })
--vim.keymap.set("n", "<M-7>", ":colorscheme lunaperche<CR>", { noremap = true })
--vim.keymap.set("n", "<M-8>", ":colorscheme slate<CR>", { noremap = true })
--vim.keymap.set("n", "<M-9>", ":colorscheme default<CR>", { noremap = true })
--vim.keymap.set("n", "<M-0>", toggle_transparent, { noremap = true, silent = true })

-- default color scheme
--vim.cmd("colorscheme vscode")
vim.cmd("colorscheme rose-pine-main")
--vim.cmd("colorscheme onedark")
