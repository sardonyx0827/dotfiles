"*****************************************************************************
"" Commands
"*****************************************************************************
" remove trailing whitespaces
command! FixWhitespace :%s/\s\+$//e


"*****************************************************************************
"" Functions
"*****************************************************************************
function! s:setupWrapping() abort
  setlocal wrap
  setlocal wrapmargin=2
  setlocal textwidth=79
endfunction


"*****************************************************************************
"" Autocmd Rules
"*****************************************************************************
"" The PC is fast enough, do syntax highlight syncing from start unless 200 lines
augroup vimrc-sync-fromstart
  autocmd!
  autocmd BufEnter * :syntax sync maxlines=200
augroup END

"" Remember cursor position
augroup vimrc-remember-cursor-position
  autocmd!
  autocmd BufReadPost * if line("'\"") > 1 && line("'\"") <= line("$") | exe "normal! g`\"" | endif
augroup END

"" txt
augroup vimrc-wrapping
  autocmd!
  autocmd BufRead,BufNewFile *.txt call s:setupWrapping()
augroup END

"" cmake
augroup vimrc-cmake
  autocmd!
  autocmd BufNewFile,BufRead CMakeLists.txt setlocal filetype=cmake
augroup END

set autoread

"" Check if file changed when focus is gained
augroup vimrc-checktime
  autocmd!
  autocmd WinEnter,FocusGained,BufEnter * checktime
augroup END

"" go/make: use tabs instead of spaces
augroup vimrc-go-make
  autocmd!
  autocmd FileType make,go setlocal noexpandtab
augroup END

"" Don't auto-continue comments on <CR> / o / O (fo-=c fo-=r fo-=o)
augroup vimrc-formatoptions
  autocmd!
  autocmd FileType * setlocal formatoptions-=c formatoptions-=r formatoptions-=o
augroup END

"" Highlight yanked text. Briefly flashes the yanked region with the IncSearch
"" group.
if exists('##TextYankPost')
  function! s:ClearYankHighlight(winid, ids, timer) abort
    for l:id in a:ids
      silent! call matchdelete(l:id, a:winid)
    endfor
  endfunction

  function! s:HighlightYank() abort
    if v:event.operator !=# 'y' || v:event.regtype ==# ''
      return
    endif
    let [l:lnum1, l:col1] = getpos("'[")[1:2]
    let [l:lnum2, l:col2] = getpos("']")[1:2]
    let l:charwise = v:event.regtype ==# 'v'
    let l:blockwise = v:event.regtype[0] ==# "\<C-v>"
    let l:pos = []
    for l:lnum in range(l:lnum1, l:lnum2)
      let l:width = col([l:lnum, '$']) - 1
      if l:width == 0
        continue
      endif
      let l:s = 1
      let l:e = l:width
      if l:blockwise
        let l:s = min([l:col1, l:col2])
        let l:e = min([max([l:col1, l:col2]), l:width])
      elseif l:charwise
        if l:lnum == l:lnum1
          let l:s = l:col1
        endif
        if l:lnum == l:lnum2
          let l:e = min([l:col2, l:width])
        endif
      endif
      if l:s > l:width || l:e < l:s
        continue
      endif
      call add(l:pos, [l:lnum, l:s, l:e - l:s + 1])
    endfor
    if empty(l:pos)
      return
    endif
    let l:ids = []
    " matchaddpos() accepts at most 8 positions per call
    for l:i in range(0, len(l:pos) - 1, 8)
      call add(l:ids, matchaddpos('IncSearch', l:pos[l:i : l:i + 7]))
    endfor
    " Clear the highlight after a 40ms flash
    call timer_start(40, function('s:ClearYankHighlight', [win_getid(), l:ids]))
  endfunction

  augroup vimrc-highlight-yank
    autocmd!
    autocmd TextYankPost * call s:HighlightYank()
  augroup END
endif
