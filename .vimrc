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

"" GitHub Copilot
Plug 'github/copilot.vim'

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
set softtabstop=0
set shiftwidth=2
set expandtab

"" Map leader to ,
let mapleader=','

"" Enable hidden buffers
set hidden

"" Searching
set hlsearch
set incsearch
set ignorecase
set smartcase

set fileformats=unix,dos,mac

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

" change current dir when open any tabs
set autochdir
" no indent on/off when paste text from clipboard
set pastetoggle=<F9>

set undodir=~/.vim/undodir_vim
set undofile


"*****************************************************************************
"" Visual Settings
"*****************************************************************************
syntax on
set ruler
set number
set relativenumber

let no_buffers_menu=1
colorscheme rosepine

if has('nvim')
  " Better command line completion
  set wildmenu

  " mouse support
endif
set mouse=a
set mousemodel=popup
set t_Co=256
set guioptions=egmrti
set gfn=Monospace\ 10

if has("gui_running")
  if has("gui_mac") || has("gui_macvim")
    set guifont=Menlo:h12
    set transparency=7
  endif
else
  let g:CSApprox_loaded = 1

  " IndentLine
  let g:indentLine_enabled = 1
  "let g:indentLine_concealcursor = 0
  let g:indentLine_char = '┆'
  let g:indentLine_faster = 1
endif

"" Disable the blinking cursor.
set gcr=a:blinkon0

if has('nvim')
  au TermEnter * setlocal scrolloff=0
  au TermLeave * setlocal scrolloff=3
else
  set scrolloff=3
endif

"" Status bar
set laststatus=2

"" Use modeline overrides
set modeline
set modelines=10

set title
set titleold="Terminal"
set titlestring=%F

set statusline=%F%m%r%h%w%=(%{&ff}/%Y)\ (line\ %l\/%L,\ col\ %c)\

" Search mappings: These will make it so that going to the next one in a
" search will center on the line it's found in.
nnoremap n nzzzv
nnoremap N Nzzzv

if exists("*fugitive#statusline")
  set statusline+=%{fugitive#statusline()}
endif

" vim-airline
let g:airline_theme = 'powerlineish'
let g:airline#extensions#branch#enabled = 1
let g:airline#extensions#ale#enabled = 1
let g:airline#extensions#tabline#enabled = 1
let g:airline#extensions#tagbar#enabled = 1
let g:airline_skip_empty_sections = 1

if !exists('g:airline_symbols')
  let g:airline_symbols = {}
endif

if !exists('g:airline_powerline_fonts')
  let g:airline#extensions#tabline#left_sep = ' '
  let g:airline#extensions#tabline#left_alt_sep = '|'
  let g:airline_left_sep          = '▶'
  let g:airline_left_alt_sep      = '»'
  let g:airline_right_sep         = '◀'
  let g:airline_right_alt_sep     = '«'
  let g:airline#extensions#branch#prefix     = '⤴' "➔, ➥, ⎇
  let g:airline#extensions#readonly#symbol   = '⊘'
  let g:airline#extensions#linecolumn#prefix = '¶'
  let g:airline#extensions#paste#symbol      = 'ρ'
  let g:airline_symbols.linenr    = '␊'
  let g:airline_symbols.branch    = '⎇'
  let g:airline_symbols.paste     = 'ρ'
  let g:airline_symbols.paste     = 'Þ'
  let g:airline_symbols.paste     = '∥'
  let g:airline_symbols.whitespace = 'Ξ'
else
  let g:airline#extensions#tabline#left_sep = ''
  let g:airline#extensions#tabline#left_alt_sep = ''

  " powerline symbols
  let g:airline_left_sep = ''
  let g:airline_left_alt_sep = ''
  let g:airline_right_sep = ''
  let g:airline_right_alt_sep = ''
  let g:airline_symbols.branch = ''
  let g:airline_symbols.readonly = ''
  let g:airline_symbols.linenr = ''
endif

" vim
" set Colorscheme (clear)
highlight Normal ctermbg=none
highlight NonText ctermbg=none
highlight Terminal ctermbg=none
highlight Folded ctermbg=none
highlight LineNr ctermbg=none
highlight EndOfBuffer ctermbg=none

" " set color on tail space
highlight ExtraWhitespace ctermbg=red guibg=red
au ColorScheme * highlight ExtraWhitespace guibg=red
au BufEnter * match ExtraWhitespace /\s\+$/
au InsertEnter * match ExtraWhitespace /\s\+\%#\@<!$/
au InsertLeave * match ExtraWhiteSpace /\s\+$/


"*****************************************************************************
"" Abbreviations
"*****************************************************************************
"" no one is really happy until you have this shortcuts
cnoreabbrev W! w!
cnoreabbrev Q! q!
cnoreabbrev Qall! qall!
cnoreabbrev Wq wq
cnoreabbrev Wa wa
cnoreabbrev wQ wq
cnoreabbrev WQ wq
cnoreabbrev W w
cnoreabbrev Q q
cnoreabbrev Qall qall
cnoreabbrev f Files
cnoreabbrev gf GFiles

" grep.vim
nnoremap <silent> <leader>gf :Rgrep<CR>
let Grep_Default_Options = '-IR'
let Grep_Skip_Files = '*.log *.db'
let Grep_Skip_Dirs = '.git node_modules'

" netrw
let g:netrw_liststyle=3
let g:netrw_keepdir = 0
nnoremap <silent> <leader>p :Explore<CR>

" NERDTree configuration
" do chdir when change root
let g:NERDTreeChDirMode=2
" show ignore
let g:NERDTreeIgnore=['node_modules','\.rbc$', '\~$', '\.pyc$', '\.db$', '\.sqlite$', '__pycache__','\.swp']
" dir tree sorting
let g:NERDTreeSortOrder=['^__\.py$', '\/$', '*', '\.swp$', '\.bak$', '\~$']
" enable show bookmarks
let g:NERDTreeShowBookmarks=1
let g:nerdtree_tabs_focus_on_files=1
let g:NERDTreeWinSize = 30
set wildignore+=*/tmp/*,*.so,*.swp,*.zip,*.pyc,*.db,*.sqlite,*node_modules/
nnoremap <silent> <F2> :NERDTreeFind<CR>
nnoremap <silent> <F3> :NERDTreeToggle<CR>
" show .hidden files
let NERDTreeShowHidden = 1

nnoremap <silent><C-e> :NERDTreeFocusToggle<CR>
nnoremap <silent> <leader>e :NERDTreeFocusToggle<CR>

" NERDTreeでlキー: ファイルなら開いてNERDTreeを閉じ、ディレクトリなら展開
augroup NERDTreeCustomMappings
  autocmd!
  autocmd FileType nerdtree call s:NERDTree_l_mapping()
augroup END

function! s:NERDTree_l_mapping()
  nnoremap <buffer> l :call <SID>NERDTreeOpenOrExpand()<CR>
endfunction

function! s:NERDTreeOpenOrExpand()
  let node = g:NERDTreeFileNode.GetSelected()
  if node.path.isDirectory
    execute "normal o"
  else
    execute "normal \r"
    NERDTreeClose
  endif
endfunction

" show nerdtree default
let g:nerdtree_tabs_open_on_console_startup=0

" set cursor position in new tab(or file) when launch Vim
autocmd VimEnter * wincmd p

" show buffer list
nnoremap <silent> <leader>ll <cmd>Buffers<CR>
" jump to next buffer
nnoremap <silent> <C-l> :bnext<CR>
nnoremap <silent> <C-h> :blast<CR>


"*****************************************************************************
"" Terminal
"*****************************************************************************
" terminal emulation
if has('nvim')
  " Terminal Setting likes Vim
  " start with Insert-Mode
  autocmd TermOpen * :startinsert
  " no line number
  autocmd TermOpen * setlocal norelativenumber
  autocmd TermOpen * setlocal nonumber
  nnoremap <silent> <leader>sh <cmd>sp new<CR><cmd>terminal<CR>
  " close terminal
  "autocmd TermClose * if !v:event.status | exe 'bdelete! '..expand('<abuf>') | endif
  autocmd TermClose * exe 'close!'


  " exec terminal keymaps
  function! s:TermEnter(_)
    if getbufvar(bufnr(), 'term_insert', 0)
      startinsert
      call setbufvar(bufnr(), 'term_insert', 0)
    endif
  endfunction

  function! <SID>TermExec(cmd)
    let b:term_insert = 1
    execute a:cmd
  endfunction

  augroup Term
    autocmd CmdlineLeave,WinEnter,BufWinEnter * call timer_start(0, function('s:TermEnter'), {})
  augroup end

  " Terminal-Normal Mode
  tnoremap <silent> <C-W>N      <C-\><C-N>
  " Commands
  tnoremap <silent> <C-W>:      <C-\><C-N>:call <SID>TermExec('call feedkeys(":")')<CR>
  " Move Window
  tnoremap <silent> <C-W><C-W>  <cmd>call <SID>TermExec('wincmd w')<CR>
  tnoremap <silent> <C-W>h      <cmd>call <SID>TermExec('wincmd h')<CR>
  tnoremap <silent> <C-W>j      <cmd>call <SID>TermExec('wincmd j')<CR>
  tnoremap <silent> <C-W>k      <cmd>call <SID>TermExec('wincmd k')<CR>
  tnoremap <silent> <C-W>l      <cmd>call <SID>TermExec('wincmd l')<CR>
  " Replace Window
  tnoremap <silent> <C-W>H      <cmd>call <SID>TermExec('wincmd H')<CR>
  tnoremap <silent> <C-W>J      <cmd>call <SID>TermExec('wincmd J')<CR>
  tnoremap <silent> <C-W>K      <cmd>call <SID>TermExec('wincmd K')<CR>
  tnoremap <silent> <C-W>L      <cmd>call <SID>TermExec('wincmd L')<CR>
else
  nnoremap <silent> <leader>sh :terminal<CR>
endif


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

"" make/cmake
augroup vimrc-make-cmake
  autocmd!
  autocmd FileType make setlocal noexpandtab
  autocmd BufNewFile,BufRead CMakeLists.txt setlocal filetype=cmake
augroup END

set autoread


"*****************************************************************************
"" Mappings
"*****************************************************************************

"" Split
noremap <Leader>h :<C-u>split<CR>
noremap <Leader>v :<C-u>vsplit<CR>

" session management
nnoremap <leader>so :OpenSession<Space>
nnoremap <leader>ss :SaveSession<Space>
nnoremap <leader>sd :DeleteSession<CR>
nnoremap <leader>sc :CloseSession<CR>

"" Set working directory
nnoremap <leader>. :lcd %:p:h<CR>

"" fzf.vim
set wildmode=list:longest,list:full
set wildignore+=*.o,*.obj,.git,*.rbc,*.pyc,__pycache__
let $FZF_DEFAULT_COMMAND =  "find . -path '*/\.*' -prune -o -path 'node_modules/**' -prune -o -path 'target/**' -prune -o -path 'dist/**' -prune -o -type f"

" The Silver Searcher
if executable('ag')
  let $FZF_DEFAULT_COMMAND = 'ag --hidden --ignore .git -g ""'
  set grepprg=ag\ --nogroup\ --nocolor
endif

" ripgrep
if executable('rg')
  let $FZF_DEFAULT_COMMAND = 'rg --files --hidden --follow --glob "!.git/*"'
  set grepprg=rg\ --vimgrep
  command! -bang -nargs=* Find call fzf#vim#grep('rg --column --line-number --no-heading --fixed-strings --ignore-case --hidden --follow --glob "!.git/*" --color "always" '.shellescape(<q-args>).'| tr -d "\017"', 1, <bang>0)
endif

cnoremap <C-P> <C-R>=expand("%:p:h") . "/" <CR>
"Recovery commands from history through FZF
nmap <leader>y :History:<CR>
" execute my ":Files" command by fzf from current dir
"nmap <leader>sf :call fzf#run(fzf#wrap({'dir': '~'}), {'options':'--hidden'})<CR>
command! -bang -nargs=? -complete=dir Files
      \ call fzf#vim#files(<q-args>, fzf#vim#with_preview(), <bang>0)

function! s:fzf_with_dots(cmd)
  let $FZF_DEFAULT_COMMAND =  "find . -path '*/\.*' -prune -o -path 'node_modules/**' -prune -o -path 'target/**' -prune -o -path 'dist/**' -prune -o -type f"
  execute a:cmd
endfunction
function! s:fzf_without_dots(cmd)
  let $FZF_DEFAULT_COMMAND =  "find * -path '*/\.*' -prune -o -path 'node_modules/**' -prune -o -path 'target/**' -prune -o -path 'dist/**' -prune -o -type f -print -o -type l -print 2> /dev/null"
  execute a:cmd
endfunction
" nmap <leader>sf :call <SID>fzf_with_dots('Files ~')<CR>
nmap <leader>sf :FZF<CR>

" vimgrep search and copen(use vimgrep instead of grep)
function! s:vimgrep_search(pattern)
  execute 'lcd ' . expand('%:p:h')
  let files = systemlist("find . -type d \\( -name .git -o -name node_modules -o -name build \\) -prune -o -type f -print")
  if empty(files)
    echo "No target files found"
    return
  endif
  execute 'vimgrep /' . a:pattern . '/gj ' . join(files)
  if len(getqflist()) > 0
    copen
  else
    echo "No search results found"
  endif
endfunction
nmap <leader>gr :<C-u>call <SID>vimgrep_search(input('Grep Search: '))<CR>

" ale
let g:ale_linters = {}

" Tagbar
nmap <silent> <F4> :TagbarToggle<CR>
let g:tagbar_autofocus = 1

" Disable visualbell
set noerrorbells visualbell t_vb=
if has('autocmd')
  autocmd GUIEnter * set visualbell t_vb=
endif

"" Copy/Paste/Cut
if has('unnamedplus')
  set clipboard=unnamed,unnamedplus
endif

" noremap <leader>p "+gP<CR>

if has('macunix')
  " pbcopy for OSX copy/paste
  vmap <C-x> :!pbcopy<CR>
  vmap <C-c> :w !pbcopy<CR><CR>
endif

"" Buffer nav
noremap <leader>z :bp<CR>
noremap <leader>q :bp<CR>
noremap <leader>x :bn<CR>
noremap <leader>w :bn<CR>

"" Close buffer
noremap <leader>c :bd<CR>

"" Clean search (highlight)
nnoremap <silent> <leader><space> :noh<cr>

"" Vmap for maintain Visual Mode after shifting > and <
vmap < <gv
vmap > >gv

"" Move visual block
vnoremap J :m '>+1<CR>gv=gv
vnoremap K :m '<-2<CR>gv=gv

"" count up/down. prefix 'C-a' is already used in Tmux
vnoremap <C-k> <C-a>gv
vnoremap <C-j> <C-x>gv
nmap <C-k> <C-a>
nmap <C-j> <C-x>

"" Open current line on GitHub
nnoremap <Leader>o :.Gbrowse<CR>

" check documentation on cursor
" text must contains '()' to detect input and its must be 1 character
function! ChoseAction(actions) abort
  echo join(map(copy(a:actions), { _, v -> v.text }), ", ") .. ": "
  let result = getcharstr()
  let result = filter(a:actions, { _, v -> v.text =~# printf(".*\(%s\).*", result)})
  return len(result) ? result[0].value : ""
endfunction

"" select next suggestion with GitHub copilot
imap <C-j> <Plug>(copilot-next)
imap <C-k> <Plug>(copilot-previous)

" save buffer
noremap <silent> <C-s> :w<CR>


"*****************************************************************************
"" Custom configs
"*****************************************************************************
" html
" for html files, 2 spaces
autocmd Filetype html setlocal ts=2 sw=2 expandtab

" javascript
let g:javascript_enable_domhtmlcss = 1

" vim-javascript
augroup vimrc-javascript
  autocmd!
  autocmd FileType javascript setl tabstop=2|setl shiftwidth=2|setl expandtab softtabstop=2
augroup END

" vim-airline
let g:airline#extensions#virtualenv#enabled = 1

" Syntax highlight
let python_highlight_all = 1

" typescript
let g:yats_host_keyword = 1

" Trim trailing whitespace on save
augroup TrimWhitespace
  autocmd!
  autocmd BufWritePre * :%s/\s\+$//e
augroup END
"*****************************************************************************
"" local config
"*****************************************************************************
" Include user's local vim config
if has('nvim')
  if filereadable(expand("~/.config/nvim/local_init.vim"))
    source ~/.config/nvim/local_init.vim
  endif
else
  if filereadable(expand("~/.vimrc.local"))
    source ~/.vimrc.local
  endif
endif


"*****************************************************************************
"" tests
"*****************************************************************************
