"*****************************************************************************
"" Custom configs
"*****************************************************************************
" Python: enable all of the built-in syntax highlighting
let python_highlight_all = 1

" Trim trailing whitespace on save.
" Wrapped so the cursor/scroll position (winsaveview) and the last search
" pattern (keeppatterns) survive: a bare :%s/\s\+$//e moves the cursor and
" clobbers the search register used by n/N.
function! s:TrimTrailingWhitespace() abort
  let l:view = winsaveview()
  keeppatterns %s/\s\+$//e
  call winrestview(l:view)
endfunction
augroup TrimWhitespace
  autocmd!
  autocmd BufWritePre * call s:TrimTrailingWhitespace()
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
