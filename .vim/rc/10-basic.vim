"*****************************************************************************
"" Basic Setup
"*****************************************************************************"
"" Encoding
set encoding=utf-8
set fileencoding=utf-8
set fileencodings=utf-8
set ttyfast

"" Fix backspace indent
set backspace=indent,eol,start

"" Tabs. May be overridden by autocmd rules
set tabstop=2
set softtabstop=2
set shiftwidth=2
set expandtab
set smartindent

"" Map leader to ,
let mapleader=','

"" Enable hidden buffers
set hidden

"" Disable wrap
set nowrap

"" Disable swap and backup
set noswapfile
set nobackup

"" Searching
set hlsearch
set incsearch
set ignorecase
set smartcase

set fileformats=unix,dos,mac

"" Show whitespace characters
set list
set listchars=tab:┊\ ,trail:·,extends:…,precedes:…

if exists('$SHELL')
  set shell=$SHELL
else
  set shell=/bin/sh
endif

" session management
if has('nvim')
  let g:session_directory = "~/.config/nvim/session"
else
  let g:session_directory = "~/.vim/session"
endif
let g:session_autoload = "no"
let g:session_autosave = "no"
let g:session_command_aliases = 1

if has('nvim')
  set undodir=~/.vim/undodir
else
  set undodir=~/.vim/undodir_vim
endif
set undofile


