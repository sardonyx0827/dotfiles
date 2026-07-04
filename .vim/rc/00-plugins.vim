"*****************************************************************************
"" Vim-Plug
"*****************************************************************************
" This tree is for classic Vim only (Neovim lives in ~/.config/nvim with its
" own lazy.nvim setup), so there is no has('nvim') branching in .vim/rc.

let s:vimplug_path = expand('~/.vim/autoload/plug.vim')
if !filereadable(s:vimplug_path)
  if !executable('curl')
    echoerr 'curl is required to bootstrap vim-plug (or install it manually).'
    execute 'q!'
  endif
  echo 'Installing Vim-Plug...'
  silent execute '!curl -fLo ' . shellescape(s:vimplug_path)
        \ . ' --create-dirs https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
  autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
endif

call plug#begin(expand('~/.vim/plugged'))

"*****************************************************************************
"" Plug install packages
"*****************************************************************************
"" File explorer / UI
Plug 'preservim/nerdtree'
Plug 'jistr/vim-nerdtree-tabs'
Plug 'vim-airline/vim-airline'
Plug 'vim-airline/vim-airline-themes'
Plug 'Yggdroot/indentLine'
Plug 'majutsushi/tagbar'

"" Editing
Plug 'tpope/vim-commentary'
Plug 'Raimondi/delimitMate'
Plug 'easymotion/vim-easymotion'

"" Git
Plug 'tpope/vim-fugitive'
Plug 'tpope/vim-rhubarb'
Plug 'airblade/vim-gitgutter'

"" Fuzzy finder
if isdirectory('/opt/homebrew/opt/fzf')
  Plug '/opt/homebrew/opt/fzf'
elseif isdirectory('/usr/local/opt/fzf')
  Plug '/usr/local/opt/fzf'
else
  Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --bin' }
endif
Plug 'junegunn/fzf.vim'

"" LSP / completion — the Vim counterpart of mason + nvim-lspconfig +
"" blink.cmp on the Neovim side. vim-lsp-settings provides :LspInstallServer.
Plug 'prabirshrestha/async.vim'
Plug 'prabirshrestha/vim-lsp'
Plug 'mattn/vim-lsp-settings'
Plug 'prabirshrestha/asyncomplete.vim'
Plug 'prabirshrestha/asyncomplete-lsp.vim'

"" Lint / tags
Plug 'dense-analysis/ale'
Plug 'ludovicchabant/vim-gutentags'

"" GitHub Copilot
Plug 'github/copilot.vim'

"" Sessions
Plug 'xolox/vim-misc'
Plug 'xolox/vim-session'

"" Markdown preview
Plug 'skanehira/preview-markdown.vim'

"" Colorschemes
Plug 'rose-pine/vim'
Plug 'joshdick/onedark.vim'

"" Language syntax
Plug 'hail2u/vim-css3-syntax'
Plug 'gko/vim-coloresque'
Plug 'mattn/emmet-vim'
Plug 'jelera/vim-javascript-syntax'
Plug 'HerringtonDarkholme/yats.vim'

"" Include user's extra bundle
if filereadable(expand('~/.vimrc.local.bundles'))
  source ~/.vimrc.local.bundles
endif

call plug#end()

" Required:
filetype plugin indent on
