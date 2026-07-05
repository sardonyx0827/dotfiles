"*****************************************************************************
"" Visual Settings
"*****************************************************************************
scriptencoding utf-8

syntax on
set ruler
set number
set relativenumber
set cursorline
set termguicolors
set signcolumn=yes
set updatetime=50
set scrolloff=2

" silent!: survive the very first launch before :PlugInstall has run
silent! colorscheme rosepine

"" Cursor shape per mode (DECSCUSR): blinking block in normal, blinking bar in
"" insert, blinking underline in replace. On exit/suspend restore the block.
let &t_EI = "\e[1 q"
let &t_SI = "\e[5 q"
let &t_SR = "\e[3 q"
augroup vimrc-cursor-shape
  autocmd!
  autocmd VimEnter * silent! call echoraw(&t_EI)
  autocmd VimLeave,VimSuspend * silent! call echoraw("\e[1 q")
augroup END

set mouse=
set mousemodel=popup

" IndentLine
let g:indentLine_enabled = 1
let g:indentLine_char = '┆'
let g:indentLine_faster = 1

"" Status bar (airline renders the actual statusline)
set laststatus=2

"" Use modeline overrides
set modeline
set modelines=10

set title
set titleold="Terminal"
set titlestring=%F

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
  let g:airline_symbols.paste     = '∥'
  let g:airline_symbols.whitespace = 'Ξ'
else
  let g:airline#extensions#tabline#left_sep = ''
  let g:airline#extensions#tabline#left_alt_sep = ''

  " powerline symbols
  let g:airline_left_sep = ''
  let g:airline_left_alt_sep = ''
  let g:airline_right_sep = ''
  let g:airline_right_alt_sep = ''
  let g:airline_symbols.branch = ''
  let g:airline_symbols.readonly = ''
  let g:airline_symbols.linenr = ''
endif

"" Background transparency + whitespace colors. A colorscheme switch wipes these
"" overrides, so they are re-applied from a single function on every ColorScheme
"" event.
function! s:ApplyHighlightOverrides() abort
  highlight Normal ctermbg=NONE guibg=NONE
  highlight NormalNC ctermbg=NONE guibg=NONE
  highlight NormalFloat ctermbg=NONE guibg=NONE
  highlight FloatBorder ctermbg=NONE guibg=NONE
  highlight Terminal ctermbg=NONE guibg=NONE
  highlight Folded ctermbg=NONE guibg=NONE
  highlight LineNr ctermbg=NONE guibg=NONE
  highlight EndOfBuffer ctermbg=NONE guibg=NONE
  highlight SignColumn ctermbg=NONE guibg=NONE
  highlight StatusLine cterm=NONE gui=NONE
  highlight TabLineFill cterm=NONE gui=NONE
  highlight Whitespace ctermfg=red guifg=#Fb7280 ctermbg=NONE guibg=NONE
  highlight NonText ctermfg=red guifg=#Faa0a6 ctermbg=NONE guibg=NONE
  highlight SpecialKey ctermfg=red guifg=#Faa0a6 ctermbg=NONE guibg=NONE
endfunction
call s:ApplyHighlightOverrides()

augroup vimrc-highlight-overrides
  autocmd!
  autocmd ColorScheme * call s:ApplyHighlightOverrides()
augroup END
