"*****************************************************************************
"" Terminal
"*****************************************************************************
" Vim's :terminal already opens in a split and starts in Terminal-Job mode,
" and <C-w>N / <C-w>: / <C-w>h,j,k,l work natively — none of the TermOpen /
" startinsert machinery Neovim needs is required here.
nnoremap <silent> <leader>sh :terminal<CR>

" No line numbers in terminal windows (match nvim's TermOpen settings)
if exists('##TerminalWinOpen')
  augroup vimrc-terminal
    autocmd!
    autocmd TerminalWinOpen * setlocal nonumber norelativenumber
  augroup END
endif
