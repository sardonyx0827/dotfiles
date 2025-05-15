-- Highlitght colors, Indents, etc
return {
  "nvim-treesitter/nvim-treesitter",
  event = "BufRead",
  dependencies = {
    -- show context
    { "nvim-treesitter/nvim-treesitter-context", },
  },
}
