"*****************************************************************************
"" Terminal
"*****************************************************************************
" Vim's :terminal already opens in a split and starts in Terminal-Job mode, with
" <C-w>N / <C-w>: / <C-w>h,j,k,l working natively.
nnoremap <silent> <leader>sh :terminal<CR>

" No line numbers in terminal windows
if exists('##TerminalWinOpen')
  augroup vimrc-terminal
    autocmd!
    autocmd TerminalWinOpen * setlocal nonumber norelativenumber
  augroup END
endif
