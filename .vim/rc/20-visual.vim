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


