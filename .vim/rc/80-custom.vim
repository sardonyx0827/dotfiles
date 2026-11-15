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
" Manual trigger for the trim above, kept safe the same way. It trims in any
" filetype: asking for it is the user's call.
command! FixWhitespace call s:TrimTrailingWhitespace()
" The save-time trim skips filetypes where trailing whitespace is content:
" Markdown's two-space hard line break, the lone-space context lines of a diff /
" patch (a trimmed patch no longer applies), and commit messages (`git commit -v`
" embeds a diff). Keep in step with keep_trailing_ws in nvim's setup/init.lua.
let s:keep_trailing_ws = ['markdown', 'diff', 'gitcommit']
augroup TrimWhitespace
  autocmd!
  autocmd BufWritePre * if index(s:keep_trailing_ws, &filetype) < 0 | call s:TrimTrailingWhitespace() | endif
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
