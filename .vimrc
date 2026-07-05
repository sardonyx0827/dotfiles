"*****************************************************************************
"" .vimrc (loader)
"*****************************************************************************
" Thin loader: sources .vim/rc/*.vim in numeric order (00-, 10-, 20- ...).
" resolve() follows the ~/.vimrc symlink back to the dotfiles repo, so the split
" files are found next to the real .vimrc no matter where it is cloned.

let s:rc_dir = fnamemodify(resolve(expand('<sfile>:p')), ':h') . '/.vim/rc'
if isdirectory(s:rc_dir)
  for s:f in sort(glob(s:rc_dir . '/*.vim', 0, 1))
    execute 'source' fnameescape(s:f)
  endfor
endif
