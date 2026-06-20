"*****************************************************************************
"" Commands
"*****************************************************************************
" remove trailing whitespaces
command! FixWhitespace :%s/\s\+$//e


"*****************************************************************************
"" Functions
"*****************************************************************************
if !exists('*s:setupWrapping')
  function s:setupWrapping()
    set wrap
    set wm=2
    set textwidth=79
  endfunction
endif


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

"" Check if file changed when focus is gained (match nvim_lazy init.lua)
augroup vimrc-checktime
  autocmd!
  autocmd WinEnter,FocusGained,BufEnter * checktime
augroup END

"" go/make: use tabs instead of spaces (match nvim_lazy set.lua)
augroup vimrc-go-make
  autocmd!
  autocmd FileType make,go setlocal noexpandtab
augroup END


