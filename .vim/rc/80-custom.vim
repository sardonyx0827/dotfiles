"*****************************************************************************
"" Custom configs
"*****************************************************************************
" Python: enable all of the built-in syntax highlighting
let python_highlight_all = 1

" Trim trailing whitespace on save (match nvim_lazy init.lua BufWritePre)
augroup TrimWhitespace
  autocmd!
  autocmd BufWritePre * :%s/\s\+$//e
augroup END

"*****************************************************************************
"" local config
"*****************************************************************************
" Include user's local vim config
if filereadable(expand('~/.vimrc.local'))
  source ~/.vimrc.local
endif

" close current buffer
nnoremap <C-q> :bd<CR>
