vim.keymap.set("n", "<M-0>", ":colorscheme ", { noremap = true })

local function set_random_color_scheme(color_schemes)

  local color_scheme = color_schemes[math.random(#color_schemes)]
  vim.cmd("colorscheme " .. color_scheme)
  print("color scheme: " .. color_scheme)

end

local function set_color_scheme_from_tmux_pane(color_scheme)

  vim.cmd("colorscheme " .. color_scheme)
  print("color scheme: " .. color_scheme)

end
local function set_color_scheme()

  local tmux_pane_id = vim.fn.system("tmux run \"echo '#{pane_id}'\"")
  tmux_pane_id = string.gsub(tmux_pane_id, "%%", "")
  -- my recommended color schemes
  local color_schemes = {
    "rose-pine-main",
    "vscode",
    "tokyonight-night",
  }

  -- tmux_pane_id is number?
  if not tonumber(tmux_pane_id) then
    set_random_color_scheme(color_schemes)
  else
    -- tmux pane id(+1) is bigger than color_schemes length?
    if tmux_pane_id+1 > #color_schemes then
      set_random_color_scheme(color_schemes)
    else
      set_color_scheme_from_tmux_pane(color_schemes[tmux_pane_id+1])
    end
  end

end

-- set color scheme when vim start up, random or tmux pane id
--set_color_scheme()

-- default color scheme
vim.cmd("colorscheme rose-pine-main")
--vim.cmd("colorscheme vscode")
--vim.cmd("colorscheme tokyonight-night")
--vim.cmd("colorscheme slate")
--vim.cmd("colorscheme onedark")
