"*****************************************************************************
"" Vim-Plug
"*****************************************************************************
if has('nvim')
  let vimplug_exists=expand('~/.config/nvim/autoload/plug.vim')
else
  let vimplug_exists=expand('~/.vim/autoload/plug.vim')
endif
if has('win32')&&!has('win64')
  let curl_exists=expand('C:\Windows\Sysnative\curl.exe')
else
  let curl_exists=expand('curl')
endif

let g:vim_bootstrap_langs = "c,go,html,javascript,lua,python,typescript"
let g:vim_bootstrap_editor = "vim"
let g:vim_bootstrap_frams = ""

if !filereadable(vimplug_exists)
  if !executable(curl_exists)
    echoerr "You have to install curl or first install vim-plug yourself!"
    execute "q!"
  endif
  echo "Installing Vim-Plug..."
  echo ""
  silent exec "!"curl_exists" -fLo " . shellescape(vimplug_exists) . " --create-dirs https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim"
  let g:not_finish_vimplug = "yes"

  autocmd VimEnter * PlugInstall
endif

" required plugins
if has('nvim')
  call plug#begin(expand('~/.config/nvim/plugged'))
else
  call plug#begin(expand('~/.vim/plugged'))
endif

"*****************************************************************************
"" Plug install packages
"*****************************************************************************
" The NERDTree is a file system explorer for the Vim editor.
Plug 'preservim/nerdtree'
Plug 'jistr/vim-nerdtree-tabs'
" Comment stuff out.
Plug 'tpope/vim-commentary'
" Fugitive is the premier Vim plugin for Git.
Plug 'tpope/vim-fugitive'
" show git diff marks in the sign column.
Plug 'airblade/vim-gitgutter'
" statusline at the bottom of each vim window.
Plug 'vim-airline/vim-airline'
Plug 'vim-airline/vim-airline-themes'
" Grep search tools integration with Vim.
Plug 'vim-scripts/grep.vim'
" It's hard to find colorschemes for terminal Vim.
Plug 'vim-scripts/CSApprox'
" automatic closing of quotes, parenthesis, brackets, etc.
Plug 'Raimondi/delimitMate'
" displays tags in a window, ordered by scope.
Plug 'majutsushi/tagbar'
" ctags
Plug 'ludovicchabant/vim-gutentags'
" Asynchronous Lint Engine
Plug 'dense-analysis/ale'
" display the indention levels with thin vertical lines.
Plug 'Yggdroot/indentLine'
" using vim-bootstrap
Plug 'editor-bootstrap/vim-bootstrap-updater'
" GitHub extension for fugitive.vim
Plug 'tpope/vim-rhubarb'
" my cool color theme.
Plug 'joshdick/onedark.vim'
Plug 'rose-pine/vim'
" preview markdown
Plug 'skanehira/preview-markdown.vim'
" jump anywhere on screen with a few keystrokes
Plug 'easymotion/vim-easymotion'
"" GitHub Copilot
Plug 'github/copilot.vim'

if isdirectory('/usr/local/opt/fzf')
  Plug '/usr/local/opt/fzf' | Plug 'junegunn/fzf.vim'
else
  Plug 'junegunn/fzf', { 'dir': '~/.fzf', 'do': './install --bin' }
  Plug 'junegunn/fzf.vim'
endif
let g:make = 'gmake'
if exists('make')
  let g:make = 'make'
endif
Plug 'Shougo/vimproc.vim', {'do': g:make}

"" Vim-Session
Plug 'xolox/vim-misc'
Plug 'xolox/vim-session'

"" Snippets
" Plug 'SirVer/ultisnips'
Plug 'honza/vim-snippets'

"*****************************************************************************
"" Custom bundles
"*****************************************************************************
" html
"" HTML Bundle
Plug 'hail2u/vim-css3-syntax'
Plug 'gko/vim-coloresque'
Plug 'tpope/vim-haml'
Plug 'mattn/emmet-vim'

" javascript
"" Javascript Bundle
Plug 'jelera/vim-javascript-syntax'

" Async.vim
Plug 'prabirshrestha/async.vim'

" Asyncomplete.vim
Plug 'prabirshrestha/asyncomplete.vim'

" Asyncomplete lsp.vim
Plug 'prabirshrestha/asyncomplete-lsp.vim'

" typescript
Plug 'leafgarland/typescript-vim'
Plug 'HerringtonDarkholme/yats.vim'

"*****************************************************************************
"*****************************************************************************
" Include user's extra bundle
if has('nvim')
  if filereadable(expand("~/.config/nvim/local_bundles.vim"))
    source ~/.config/nvim/local_bundles.vim
  endif
else
  if filereadable(expand("~/.vimrc.local.bundles"))
    source ~/.vimrc.local.bundles
  endif
endif

call plug#end()

" Required:
filetype plugin indent on

