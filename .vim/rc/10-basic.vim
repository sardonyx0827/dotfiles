"*****************************************************************************
"" Basic Setup
"*****************************************************************************"
"" Encoding
set encoding=utf-8
scriptencoding utf-8
set fileencodings=ucs-bom,utf-8,default,latin1
set ttyfast

"" Fix backspace indent
set backspace=indent,eol,start

"" Tabs. May be overridden by autocmd rules
set tabstop=2
set softtabstop=2
set shiftwidth=2
set expandtab
set autoindent
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

"" Persistent undo. Kept separate from Neovim's ~/.vim/undodir because the
"" two undofile formats are not compatible with each other.
set undodir=~/.vim/undodir_vim
set undofile

"" Searching
set hlsearch
set incsearch
set ignorecase
set smartcase

set fileformats=unix,dos,mac

"" Treat @-@ as part of file names
set isfname+=@-@

"" Command-line completion
set wildmenu
set wildmode=list:longest,list:full

"" No bells
set belloff=all

"" Fast key-code timeout so mode changes (and the mode-dependent cursor
"" shape) apply immediately.
set ttimeout
set ttimeoutlen=50

"" Show whitespace characters
set list
set listchars=tab:┊\ ,trail:·,extends:…,precedes:…

if exists('$SHELL')
  set shell=$SHELL
else
  set shell=/bin/sh
endif
