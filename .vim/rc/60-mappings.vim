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
" Use :Files (defined above with fzf#vim#with_preview()) so the picker shows a
" floating preview pane. Neovim renders fzf itself in a floating window; install
" `bat` for syntax-highlighted previews (falls back to plain text without it).
nmap <leader>sf :Files<CR>

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

"" tag jump with Ctrl-t (same as Ctrl-]). use Ctrl-o to go back
nnoremap <C-t> <C-]>

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


