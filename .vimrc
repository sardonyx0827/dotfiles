"*****************************************************************************
"" .vimrc (loader)
"*****************************************************************************
" This file is a thin loader. The actual configuration lives in .vim/rc/*.vim
" and is sourced here in numeric order (00-, 10-, 20- ...). Keeping the load
" order deterministic matters: plugins must load before colorscheme, the
" colorscheme before the highlight overrides, mapleader before <leader> maps,
" and so on.
"
" resolve() follows the ~/.vimrc symlink back to the dotfiles repo, so the
" split files are found next to the *real* .vimrc no matter where the repo is
" cloned. No symlink of .vim/rc into $HOME is required.
"
" Neovim is configured separately under ~/.config/nvim (lazy.nvim); this whole
" tree is for classic Vim only.

let s:rc_dir = fnamemodify(resolve(expand('<sfile>:p')), ':h') . '/.vim/rc'
if isdirectory(s:rc_dir)
  for s:f in sort(glob(s:rc_dir . '/*.vim', 0, 1))
    execute 'source' fnameescape(s:f)
  endfor
endif
