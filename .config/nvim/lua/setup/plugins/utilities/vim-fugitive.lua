-- git commands in nvim
return {
  "tpope/vim-fugitive",
  lazy = true,
  cmd = "Gvdiffsplit",
  keys = {
    {
      "<leader>gd",
      ":Gvdiffsplit<CR>",
      noremap = true,
      silent = true,
      desc = "Git Diff Split - Vertical",
    },
  },
}
