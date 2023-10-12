require("nvim-treesitter.configs").setup {
  rainbow = {
    enable = true,
    -- disable = { "jsx", "cpp" }, list of languages you want to disable the plugin for
    extended_mode = true, -- Also highlight non-bracket delimiters like html tags, boolean or table: lang -> boolean
    max_file_lines = 500, -- Do not enable for files with more than n lines, int
    -- colors = {}, -- table of hex strings
    -- termcolors = {} -- table of colour name strings
  }
}

vim.cmd [[
hi rainbowcol1 guifg=#E06C75
hi rainbowcol2 guifg=#E5C07B
hi rainbowcol3 guifg=#61AFEF
hi rainbowcol4 guifg=#D19A66
hi rainbowcol5 guifg=#98C379
hi rainbowcol6 guifg=#C678DD
hi rainbowcol7 guifg=#56B6C2
]]
