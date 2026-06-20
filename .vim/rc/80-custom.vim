"*****************************************************************************
"" Custom configs
"*****************************************************************************
" html
" for html files, 2 spaces
autocmd Filetype html setlocal ts=2 sw=2 expandtab

" javascript
let g:javascript_enable_domhtmlcss = 1

" vim-javascript
augroup vimrc-javascript
  autocmd!
  autocmd FileType javascript setl tabstop=2|setl shiftwidth=2|setl expandtab softtabstop=2
augroup END

" vim-airline
let g:airline#extensions#virtualenv#enabled = 1

" Syntax highlight
let python_highlight_all = 1

" typescript
let g:yats_host_keyword = 1

" Trim trailing whitespace on save
augroup TrimWhitespace
  autocmd!
  autocmd BufWritePre * :%s/\s\+$//e
augroup END

"*****************************************************************************
"" local config
"*****************************************************************************
" Include user's local vim config
if has('nvim')
  if filereadable(expand("~/.config/nvim/local_init.vim"))
    source ~/.config/nvim/local_init.vim
  endif
else
  if filereadable(expand("~/.vimrc.local"))
    source ~/.vimrc.local
  endif
endif

" close current buffer
noremap <C-q> :bd<CR>
