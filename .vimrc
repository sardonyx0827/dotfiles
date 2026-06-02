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


"*****************************************************************************
"" Visual Settings
"*****************************************************************************
syntax on
set ruler
set number
set relativenumber
set cursorline
set termguicolors
set signcolumn=yes
set updatetime=50

let no_buffers_menu=1
colorscheme rosepine

if has('nvim')
  " Better command line completion
  set wildmenu

  " mouse support
endif
set mouse=
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
  au TermLeave * setlocal scrolloff=2
else
  set scrolloff=2
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

" Background transparency (match nvim_lazy colorscheme.lua)
highlight Normal ctermbg=none guibg=NONE
highlight NormalNC ctermbg=none guibg=NONE
highlight NormalFloat ctermbg=none guibg=NONE
highlight FloatBorder ctermbg=none guibg=NONE
highlight NonText ctermbg=none guibg=NONE
highlight Terminal ctermbg=none guibg=NONE
highlight Folded ctermbg=none guibg=NONE
highlight LineNr ctermbg=none guibg=NONE
highlight EndOfBuffer ctermbg=none guibg=NONE
highlight SignColumn ctermbg=none guibg=NONE
highlight StatusLine cterm=none gui=none
highlight TabLineFill cterm=none gui=none

" Highlight whitespace characters (match nvim_lazy set.lua)
highlight Whitespace ctermfg=red guifg=#Fb7280 ctermbg=none guibg=NONE
highlight NonText ctermfg=red guifg=#Faa0a6 ctermbg=none guibg=NONE
highlight SpecialKey ctermfg=red guifg=#Faa0a6 ctermbg=none guibg=NONE
au ColorScheme * highlight Normal ctermbg=none guibg=NONE
au ColorScheme * highlight NormalNC ctermbg=none guibg=NONE
au ColorScheme * highlight NormalFloat ctermbg=none guibg=NONE
au ColorScheme * highlight FloatBorder ctermbg=none guibg=NONE
au ColorScheme * highlight SignColumn ctermbg=none guibg=NONE
au ColorScheme * highlight StatusLine cterm=none gui=none
au ColorScheme * highlight TabLineFill cterm=none gui=none
au ColorScheme * highlight Whitespace ctermfg=red guifg=#Fb7280 ctermbg=none guibg=NONE
au ColorScheme * highlight NonText ctermfg=red guifg=#Faa0a6 ctermbg=none guibg=NONE
au ColorScheme * highlight SpecialKey ctermfg=red guifg=#Faa0a6 ctermbg=none guibg=NONE


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
nnoremap <silent> <C-h> :bprevious<CR>


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


"*****************************************************************************
"" Mappings
"*****************************************************************************

"" Set working directory
nnoremap <leader>cd :cd %:p:h<CR>
nnoremap <leader>cu :cd ..<CR>

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

"" Clean search (highlight)
nnoremap <silent> <leader><space> :noh<cr>

"" Vmap for maintain Visual Mode after shifting > and <
vmap < <gv
vmap > >gv

"" Move visual block
vnoremap J :m '>+1<CR>gv=gv
vnoremap K :m '<-2<CR>gv=gv

"" rename text in this file (match nvim_lazy remap.lua)
nnoremap <leader>rn :%s/\<<C-r><C-w>\>/<C-r><C-w>/gI<Left><Left><Left>

"" vimgrep and open quickfix window (match nvim_lazy remap.lua)
nnoremap <leader>vg :vimgrep /<C-r>=input("Grep For > ")<CR>/ **/*<CR>:copen<CR>

"" edit block, add String to each line
vnoremap <leader>eb :s/\(\w.*\)/\1<Left><Left>

"" insert tab character in insert mode
inoremap <C-t> <C-v><Tab>

"" window resize with arrow keys (match nvim_lazy remap.lua)
nnoremap <silent> <C-Up> 1<C-w>+
nnoremap <silent> <C-Down> 1<C-w>-
nnoremap <silent> <C-Right> 1<C-w>>
nnoremap <silent> <C-Left> 1<C-w><

"" close all buffers
nnoremap <silent> <leader>cb :%bdelete<CR>

"" move cursor in insert mode
inoremap <C-b> <Left>
inoremap <C-f> <Right>

"" move cursor in command mode
cnoremap <C-b> <Left>
cnoremap <C-f> <Right>

"" toggle mouse
nnoremap <leader>tm :if &mouse ==# 'a' \| set mouse= \| else \| set mouse=a \| endif<CR>

"" Open current line on GitHub
nnoremap <Leader>go :.Gbrowse<CR>

" check documentation on cursor
" text must contains '()' to detect input and its must be 1 character
function! ChoseAction(actions) abort
  echo join(map(copy(a:actions), { _, v -> v.text }), ", ") .. ": "
  let result = getcharstr()
  let result = filter(a:actions, { _, v -> v.text =~# printf(".*\(%s\).*", result)})
  return len(result) ? result[0].value : ""
endfunction

" save buffer
noremap <silent> <C-s> :w<CR>


"*****************************************************************************
"" [AI solution] Ask AI and replace selection (classic Vim port of nvim ai.lua)
"*****************************************************************************
" Select a range, type an instruction in a prompt split, send the selection to
" an AI CLI (claude / codex / gemini) over stdin, preview the result in a diff
" tab, then replace the original selection.
"   <C-c> claude   <C-x> codex   <C-g> gemini   <C-l> all (claude|codex)
" Diff tab keys: y=accept AI  Y=accept merged  q=cancel  <Tab>/<S-Tab>/1/2=switch
" Neovim is handled by lua/setup/functions/ai.lua, so this is Vim-only.
if !has('nvim') && has('job') && has('channel') && has('timers')

  function! s:AI_TrimOutput(list) abort
    let l:out = copy(a:list)
    while len(l:out) > 0 && l:out[-1] ==# ''
      call remove(l:out, -1)
    endwhile
    return l:out
  endfunction

  " Replace lines [start, end] (1-indexed inclusive) of buf with a:lines.
  function! s:AI_SetLines(buf, start, end, lines) abort
    let l:old = a:end - a:start + 1
    let l:new = len(a:lines)
    let l:i = 0
    while l:i < l:new && l:i < l:old
      call setbufline(a:buf, a:start + l:i, a:lines[l:i])
      let l:i += 1
    endwhile
    if l:new < l:old
      call deletebufline(a:buf, a:start + l:new, a:end)
    elseif l:new > l:old
      call appendbufline(a:buf, a:start + l:old - 1, a:lines[l:old :])
    endif
  endfunction

  " Overwrite the whole buffer with a:lines.
  function! s:AI_SetBufAll(buf, lines) abort
    call deletebufline(a:buf, 1, '$')
    call setbufline(a:buf, 1, a:lines)
  endfunction

  function! s:AI_BuildCmd(tool, tmpfile, sys) abort
    if a:tool ==# 'codex'
      return 'cat ' . shellescape(a:tmpfile) . ' | codex exec --skip-git-repo-check ' . shellescape(a:sys)
    elseif a:tool ==# 'gemini'
      return 'cat ' . shellescape(a:tmpfile) . ' | gemini -m gemini-3.1-flash-lite-preview -p ' . shellescape(a:sys)
    else
      return 'cat ' . shellescape(a:tmpfile) . ' | claude --model sonnet -p ' . shellescape(a:sys)
    endif
  endfunction

  function! s:AI_JobOut(buffer, ch, msg) abort
    call add(a:buffer, a:msg)
  endfunction

  " ---- shared close / focus / apply ---------------------------------------
  function! s:AI_Close(...) abort
    let l:s = a:0 ? a:1 : get(b:, 'ai_state', {})
    if empty(l:s) || get(l:s, 'closed', 0)
      return
    endif
    let l:s.closed = 1
    if l:s.mode ==# 'single'
      if l:s.status ==# 'pending' && has_key(l:s, 'job') && job_status(l:s.job) ==# 'run'
        call job_stop(l:s.job)
      endif
    else
      let l:i = 1
      for l:t in l:s.tools
        if l:s.status[l:i] ==# 'pending' && has_key(l:s.jobs, l:i)
              \ && job_status(l:s.jobs[l:i]) ==# 'run'
          call job_stop(l:s.jobs[l:i])
        endif
        let l:i += 1
      endfor
    endif
    let l:winid = bufwinid(l:s.orig_buf)
    if l:winid > 0
      let l:tabnr = win_id2tabwin(l:winid)[0]
      if l:tabnr > 0
        execute l:tabnr . 'tabclose'
      endif
    endif
    if l:s.mode !=# 'single'
      for l:b in values(l:s.bufs)
        if bufexists(l:b)
          execute 'bwipeout! ' . l:b
        endif
      endfor
    endif
  endfunction

  function! s:AI_Focus(which) abort
    let l:s = b:ai_state
    call win_gotoid(a:which ==# 'orig' ? l:s.orig_win : l:s.resp_win)
  endfunction

  function! s:AI_Apply(state, lines) abort
    let l:target = a:state.target_buf
    let l:start = a:state.start
    let l:end = a:state.end
    call s:AI_Close(a:state)
    if bufexists(l:target)
      call s:AI_SetLines(l:target, l:start, l:end, a:lines)
      echo 'Selection replaced.'
    else
      echohl ErrorMsg | echom 'Target buffer no longer valid.' | echohl None
    endif
  endfunction

  " ---- single-tool mode ---------------------------------------------------
  function! s:AI_SingleStatus(state) abort
    let l:st = a:state.status
    let l:m = l:st ==# 'pending' ? ' (loading)'
          \ : l:st ==# 'failed' ? ' (failed)'
          \ : l:st ==# 'cancelled' ? ' (cancelled)' : ''
    call setwinvar(a:state.orig_win, '&statusline', ' Original ')
    call setwinvar(a:state.resp_win, '&statusline',
          \ printf(" %s's Response%s   [y:AI  Y:merged  q:cancel] ", a:state.tool, l:m))
  endfunction

  function! s:AI_SingleAccept() abort
    let l:s = b:ai_state
    if l:s.status ==# 'pending'
      echohl WarningMsg | echom l:s.tool . ' response is still loading.' | echohl None
      return
    endif
    if l:s.status !=# 'done'
      echohl WarningMsg | echom l:s.tool . ' response is not available.' | echohl None
      return
    endif
    call s:AI_Apply(l:s, getbufline(l:s.resp_buf, 1, '$'))
  endfunction

  function! s:AI_SingleAcceptMerged() abort
    let l:s = b:ai_state
    call s:AI_Apply(l:s, getbufline(l:s.orig_buf, 1, '$'))
  endfunction

  function! s:AI_SingleFinish(state, status, timer) abort
    let l:s = a:state
    if l:s.closed
      return
    endif
    call delete(l:s.tmpfile)
    if l:s.status !=# 'cancelled'
      let l:out = s:AI_TrimOutput(l:s.output)
      call setbufvar(l:s.resp_buf, '&modifiable', 1)
      if a:status == 0 && len(l:out) > 0
        let l:s.status = 'done'
        call s:AI_SetBufAll(l:s.resp_buf, l:out)
      else
        let l:s.status = 'failed'
        call s:AI_SetBufAll(l:s.resp_buf,
              \ [printf('[%s failed (exit code %d)]', l:s.tool, a:status)])
        call setbufvar(l:s.resp_buf, '&modifiable', 0)
      endif
    endif
    call s:AI_SingleStatus(l:s)
    if l:s.status ==# 'done'
      call win_execute(l:s.orig_win, 'diffthis')
      call win_execute(l:s.resp_win, 'diffthis')
    endif
  endfunction

  function! s:AI_SingleExit(state, job, status) abort
    call timer_start(0, function('s:AI_SingleFinish', [a:state, a:status]))
  endfunction

  function! s:AI_RunSingle(ctx, sys, tmpfile) abort
    tabnew
    setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
    call setline(1, a:ctx.selected)
    let &l:filetype = a:ctx.ft
    let l:orig_buf = bufnr('%')
    let l:orig_win = win_getid()
    call s:AI_SetMaps('single')

    rightbelow vnew
    setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
    call setline(1, printf('[%s: waiting for response...]', a:ctx.tool))
    let &l:filetype = a:ctx.ft
    setlocal nomodifiable
    let l:resp_buf = bufnr('%')
    let l:resp_win = win_getid()
    call s:AI_SetMaps('single')

    let l:state = {
          \ 'mode': 'single', 'tool': a:ctx.tool,
          \ 'target_buf': a:ctx.target_buf, 'start': a:ctx.start, 'end': a:ctx.end,
          \ 'orig_buf': l:orig_buf, 'orig_win': l:orig_win,
          \ 'resp_buf': l:resp_buf, 'resp_win': l:resp_win,
          \ 'status': 'pending', 'output': [], 'closed': 0, 'tmpfile': a:tmpfile,
          \ }
    call setbufvar(l:orig_buf, 'ai_state', l:state)
    call setbufvar(l:resp_buf, 'ai_state', l:state)
    call s:AI_SingleStatus(l:state)

    let l:cmd = s:AI_BuildCmd(a:ctx.tool, a:tmpfile, a:sys)
    let l:state.job = job_start(['sh', '-c', l:cmd], {
          \ 'out_cb': function('s:AI_JobOut', [l:state.output]),
          \ 'out_mode': 'nl',
          \ 'exit_cb': function('s:AI_SingleExit', [l:state]),
          \ })
  endfunction

  " ---- all mode (claude | codex in parallel) ------------------------------
  function! s:AI_AllStatus(state) abort
    let l:parts = []
    let l:i = 1
    for l:t in a:state.tools
      let l:st = a:state.status[l:i]
      let l:m = l:st ==# 'pending' ? ' (loading)'
            \ : l:st ==# 'failed' ? ' (failed)'
            \ : l:st ==# 'cancelled' ? ' (cancelled)' : ''
      let l:label = l:t . l:m
      if l:i == a:state.active
        let l:label = '[' . l:label . ']'
      endif
      call add(l:parts, l:label)
      let l:i += 1
    endfor
    call setwinvar(a:state.orig_win, '&statusline', ' Original ')
    call setwinvar(a:state.resp_win, '&statusline',
          \ ' ' . join(l:parts, ' | ') . '   [y:AI Y:merged q:cancel Tab:switch 1/2:jump] ')
  endfunction

  function! s:AI_AllSwitch(state, idx) abort
    if a:idx < 1 || a:idx > len(a:state.tools)
      return
    endif
    call win_execute(a:state.orig_win, 'diffoff')
    call win_execute(a:state.resp_win, 'diffoff')
    let a:state.active = a:idx
    call win_execute(a:state.resp_win, 'buffer ' . a:state.bufs[a:idx])
    call s:AI_AllStatus(a:state)
    if a:state.status[a:idx] ==# 'done'
      call win_execute(a:state.orig_win, 'diffthis')
      call win_execute(a:state.resp_win, 'diffthis')
    endif
  endfunction

  function! s:AI_AllJump(idx) abort
    call s:AI_AllSwitch(b:ai_state, a:idx)
  endfunction

  function! s:AI_AllSwitchOffset(off) abort
    let l:s = b:ai_state
    let l:n = len(l:s.tools)
    let l:new = ((l:s.active - 1 + a:off) % l:n + l:n) % l:n + 1
    call s:AI_AllSwitch(l:s, l:new)
  endfunction

  function! s:AI_AllAccept() abort
    let l:s = b:ai_state
    let l:i = l:s.active
    if l:s.status[l:i] ==# 'pending'
      echohl WarningMsg | echom l:s.tools[l:i - 1] . ' response is still loading.' | echohl None
      return
    endif
    if l:s.status[l:i] !=# 'done'
      echohl WarningMsg | echom l:s.tools[l:i - 1] . ' response is not available.' | echohl None
      return
    endif
    call s:AI_Apply(l:s, getbufline(l:s.bufs[l:i], 1, '$'))
  endfunction

  function! s:AI_AllAcceptMerged() abort
    let l:s = b:ai_state
    call s:AI_Apply(l:s, getbufline(l:s.orig_buf, 1, '$'))
  endfunction

  function! s:AI_AllFinish(state, idx, status, timer) abort
    let l:s = a:state
    if l:s.closed
      return
    endif
    let l:s.pending -= 1
    if l:s.pending <= 0
      call delete(l:s.tmpfile)
    endif
    if l:s.status[a:idx] ==# 'cancelled'
      return
    endif
    let l:buf = l:s.bufs[a:idx]
    let l:out = s:AI_TrimOutput(l:s.output[a:idx])
    call setbufvar(l:buf, '&modifiable', 1)
    if a:status == 0 && len(l:out) > 0
      let l:s.status[a:idx] = 'done'
      call s:AI_SetBufAll(l:buf, l:out)
    else
      let l:s.status[a:idx] = 'failed'
      call s:AI_SetBufAll(l:buf,
            \ [printf('[%s failed (exit code %d)]', l:s.tools[a:idx - 1], a:status)])
      call setbufvar(l:buf, '&modifiable', 0)
    endif
    call s:AI_AllStatus(l:s)
    if l:s.active == a:idx && l:s.status[a:idx] ==# 'done'
      call win_execute(l:s.orig_win, 'diffthis')
      call win_execute(l:s.resp_win, 'diffthis')
    endif
  endfunction

  function! s:AI_AllExit(state, idx, job, status) abort
    call timer_start(0, function('s:AI_AllFinish', [a:state, a:idx, a:status]))
  endfunction

  function! s:AI_RunAll(ctx, sys, tmpfile) abort
    let l:tools = ['claude', 'codex']
    tabnew
    setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
    call setline(1, a:ctx.selected)
    let &l:filetype = a:ctx.ft
    let l:orig_buf = bufnr('%')
    let l:orig_win = win_getid()
    call s:AI_SetMaps('all')

    rightbelow vnew
    let l:resp_win = win_getid()
    let l:bufs = {}
    let l:status = {}
    let l:output = {}
    let l:idx = 1
    let l:first = 1
    for l:t in l:tools
      if !l:first
        enew
      endif
      setlocal buftype=nofile bufhidden=hide noswapfile nobuflisted
      call setline(1, printf('[%s: waiting for response...]', l:t))
      let &l:filetype = a:ctx.ft
      setlocal nomodifiable
      call s:AI_SetMaps('all')
      let l:bufs[l:idx] = bufnr('%')
      let l:status[l:idx] = 'pending'
      let l:output[l:idx] = []
      let l:first = 0
      let l:idx += 1
    endfor
    execute 'buffer ' . l:bufs[1]

    let l:state = {
          \ 'mode': 'all', 'tools': l:tools,
          \ 'target_buf': a:ctx.target_buf, 'start': a:ctx.start, 'end': a:ctx.end,
          \ 'orig_buf': l:orig_buf, 'orig_win': l:orig_win, 'resp_win': l:resp_win,
          \ 'bufs': l:bufs, 'status': l:status, 'output': l:output, 'jobs': {},
          \ 'active': 1, 'pending': len(l:tools), 'closed': 0, 'tmpfile': a:tmpfile,
          \ }
    call setbufvar(l:orig_buf, 'ai_state', l:state)
    for l:b in values(l:bufs)
      call setbufvar(l:b, 'ai_state', l:state)
    endfor
    call s:AI_AllStatus(l:state)

    let l:i = 1
    for l:t in l:tools
      let l:cmd = s:AI_BuildCmd(l:t, a:tmpfile, a:sys)
      let l:state.jobs[l:i] = job_start(['sh', '-c', l:cmd], {
            \ 'out_cb': function('s:AI_JobOut', [l:state.output[l:i]]),
            \ 'out_mode': 'nl',
            \ 'exit_cb': function('s:AI_AllExit', [l:state, l:i]),
            \ })
      let l:i += 1
    endfor
  endfunction

  " ---- buffer-local keymaps for the diff tab ------------------------------
  function! s:AI_SetMaps(mode) abort
    nnoremap <buffer><silent> q :call <SID>AI_Close()<CR>
    nnoremap <buffer><silent> <C-w>h :call <SID>AI_Focus('orig')<CR>
    nnoremap <buffer><silent> <C-w>l :call <SID>AI_Focus('resp')<CR>
    if a:mode ==# 'single'
      nnoremap <buffer><silent> y :call <SID>AI_SingleAccept()<CR>
      nnoremap <buffer><silent> Y :call <SID>AI_SingleAcceptMerged()<CR>
    else
      nnoremap <buffer><silent> y :call <SID>AI_AllAccept()<CR>
      nnoremap <buffer><silent> Y :call <SID>AI_AllAcceptMerged()<CR>
      nnoremap <buffer><silent> <Tab> :call <SID>AI_AllSwitchOffset(1)<CR>
      nnoremap <buffer><silent> <S-Tab> :call <SID>AI_AllSwitchOffset(-1)<CR>
      nnoremap <buffer><silent> 1 :call <SID>AI_AllJump(1)<CR>
      nnoremap <buffer><silent> 2 :call <SID>AI_AllJump(2)<CR>
    endif
  endfunction

  " ---- prompt window + entry ----------------------------------------------
  function! s:AI_PromptCancel() abort
    bwipeout
  endfunction

  function! s:AI_Submit() abort
    if !exists('b:ai_ctx')
      return
    endif
    let l:ctx = b:ai_ctx
    let l:prompt = trim(join(getline(1, '$'), "\n"))
    if l:prompt ==# ''
      echohl WarningMsg | echom 'Prompt is empty.' | echohl None
      return
    endif
    bwipeout
    let l:sys = printf(
          \ "You are an AI assistant integrated into a Vim editor. "
          \ . "The selected %s code/text is provided via stdin. "
          \ . "Apply the user's request and reply ONLY with the resulting text that should replace the selection. "
          \ . "Do NOT wrap the output in markdown code fences. "
          \ . "Do NOT include explanations, preambles, or trailing commentary. "
          \ . "Preserve the original indentation style of the input.\n\n"
          \ . "## User Request\n%s", l:ctx.lang, l:prompt)
    let l:tmpfile = tempname()
    call writefile(l:ctx.selected, l:tmpfile)
    echo 'Asking ' . l:ctx.tool . '...'
    if l:ctx.tool ==# 'all'
      call s:AI_RunAll(l:ctx, l:sys, l:tmpfile)
    else
      call s:AI_RunSingle(l:ctx, l:sys, l:tmpfile)
    endif
  endfunction

  function! s:AI_Start(tool) abort
    let l:start = line("'<")
    let l:end = line("'>")
    if l:start == 0 || l:end == 0
      echohl WarningMsg | echom 'No visual selection found.' | echohl None
      return
    endif
    if l:start > l:end
      let [l:start, l:end] = [l:end, l:start]
    endif
    let l:tool = a:tool
    if index(['claude', 'codex', 'gemini', 'all'], l:tool) < 0
      let l:tool = 'claude'
    endif
    let l:ft = &filetype
    let l:ctx = {
          \ 'tool': l:tool, 'target_buf': bufnr('%'),
          \ 'start': l:start, 'end': l:end,
          \ 'selected': getline(l:start, l:end),
          \ 'ft': l:ft, 'lang': l:ft !=# '' ? l:ft : 'plain text',
          \ }

    botright 10new
    setlocal buftype=nofile bufhidden=wipe noswapfile nobuflisted
    setlocal filetype=markdown
    let b:ai_ctx = l:ctx
    let &l:statusline = printf(' Ask %s (lines %d-%d, %s)   [<C-s>:submit  q:cancel] ',
          \ l:tool, l:start, l:end, l:ctx.lang)
    inoremap <buffer><silent> <C-s> <Esc>:call <SID>AI_Submit()<CR>
    nnoremap <buffer><silent> <C-s> :call <SID>AI_Submit()<CR>
    nnoremap <buffer><silent> q :call <SID>AI_PromptCancel()<CR>
    startinsert
  endfunction

  xnoremap <silent> <C-c> :<C-u>call <SID>AI_Start('claude')<CR>
  xnoremap <silent> <C-x> :<C-u>call <SID>AI_Start('codex')<CR>
  xnoremap <silent> <C-g> :<C-u>call <SID>AI_Start('gemini')<CR>
  xnoremap <silent> <C-l> :<C-u>call <SID>AI_Start('all')<CR>
endif


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

" close current buffer
noremap <C-q> :bd<CR>
