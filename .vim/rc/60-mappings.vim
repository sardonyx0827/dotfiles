"*****************************************************************************
"" Mappings
"*****************************************************************************

"" Set working directory
nnoremap <leader>cd :cd %:p:h<CR>
nnoremap <leader>cu :cd ..<CR>

"*****************************************************************************
"" fzf.vim — keymaps mirror telescope.lua on the Neovim side
"*****************************************************************************
set wildignore+=*.o,*.obj,.git,*.rbc,*.pyc,__pycache__

" plain-find fallback; overridden by ripgrep below when available
let $FZF_DEFAULT_COMMAND = "find . -path '*/\.*' -prune -o -path 'node_modules/**' -prune -o -path 'target/**' -prune -o -path 'dist/**' -prune -o -type f"
if executable('rg')
  let $FZF_DEFAULT_COMMAND = 'rg --files --hidden --follow --glob "!.git/*"'
  set grepprg=rg\ --vimgrep
  " Literal (fixed-string) grep, for when :Rg's regex gets in the way
  command! -bang -nargs=* Find call fzf#vim#grep('rg --column --line-number --no-heading --fixed-strings --ignore-case --hidden --follow --glob "!.git/*" --color "always" '.shellescape(<q-args>).'| tr -d "\017"', 1, <bang>0)
endif

" :Files with a floating preview pane (install `bat` for syntax highlighting)
command! -bang -nargs=? -complete=dir Files
      \ call fzf#vim#files(<q-args>, fzf#vim#with_preview(), <bang>0)

nnoremap <silent> <leader>sf :Files<CR>
nnoremap <silent> <leader>gf :GFiles<CR>
nnoremap <silent> <leader>sg :GFiles<CR>
nnoremap <silent> <leader>gs :GFiles?<CR>
nnoremap <silent> <leader>gl :Commits<CR>
nnoremap <silent> <leader>gr :Rg<CR>
nnoremap <silent> <leader>gw :Rg <C-r><C-w><CR>
nnoremap <silent> <leader>of :History<CR>
nnoremap <silent> <leader>h/ :History/<CR>
nnoremap <silent> <leader>h: :History:<CR>
" recover commands from history through FZF (legacy alias of <leader>h:)
nnoremap <silent> <leader>y :History:<CR>
nnoremap <silent> <leader>ll :Buffers<CR>
nnoremap <silent> <leader>he :Helptags<CR>

" insert the directory of the current file into the command line
cnoremap <C-P> <C-R>=expand("%:p:h") . "/" <CR>

"" Buffer nav (match nvim remap.lua)
nnoremap <silent> <C-l> :bnext<CR>
nnoremap <silent> <C-h> :bprevious<CR>
nnoremap <silent> gt :bnext<CR>
nnoremap <silent> gT :bprevious<CR>

"" close all buffers
nnoremap <silent> <leader>cb :%bdelete<CR>

"" Search: center the match on n/N, clear highlight
nnoremap n nzzzv
nnoremap N Nzzzv
nnoremap <silent> <leader><space> :noh<cr>

" Tagbar
nmap <silent> <F4> :TagbarToggle<CR>
let g:tagbar_autofocus = 1

"" Copy/Paste/Cut through the system clipboard
if has('unnamedplus')
  set clipboard=unnamed,unnamedplus
elseif has('clipboard')
  set clipboard=unnamed
endif

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
"" (LSP-enabled buffers override this with <plug>(lsp-definition), see 35-lsp)
nnoremap <C-t> <C-]>

"" window resize with arrow keys (match nvim_lazy remap.lua)
nnoremap <silent> <C-Up> 1<C-w>+
nnoremap <silent> <C-Down> 1<C-w>-
nnoremap <silent> <C-Right> 1<C-w>>
nnoremap <silent> <C-Left> 1<C-w><

"" move cursor in insert mode; let <Left>/<Right> wrap across line boundaries
set whichwrap+=[,]
inoremap <C-b> <Left>
inoremap <C-f> <Right>

"" move cursor in command mode
cnoremap <C-b> <Left>
cnoremap <C-f> <Right>

"" toggle mouse
nnoremap <leader>tm :if &mouse ==# 'a' \| set mouse= \| else \| set mouse=a \| endif<CR>

"" Open current line on GitHub
nnoremap <Leader>go :.GBrowse<CR>

" save buffer
nnoremap <silent> <C-s> :w<CR>
