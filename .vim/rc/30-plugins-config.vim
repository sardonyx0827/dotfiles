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

"*****************************************************************************
"" NERDTree floating file preview
"*****************************************************************************
" While the cursor sits on a file node in NERDTree, show the first lines of
" that file in a floating window (Neovim: nvim_open_win / Vim: popup_create).
" Auto-updates on cursor move, closes when leaving the tree. Toggle with P.
let g:nerdtree_preview_enabled = 1
let s:nt_preview_win = 0

" extension -> filetype, used to syntax-highlight the preview buffer
let s:nt_preview_ft = {
      \ 'js': 'javascript', 'jsx': 'javascriptreact',
      \ 'ts': 'typescript', 'tsx': 'typescriptreact',
      \ 'py': 'python', 'go': 'go', 'rb': 'ruby', 'rs': 'rust',
      \ 'c': 'c', 'h': 'c', 'cpp': 'cpp', 'cc': 'cpp', 'hpp': 'cpp',
      \ 'java': 'java', 'php': 'php', 'vim': 'vim', 'lua': 'lua',
      \ 'sh': 'sh', 'bash': 'sh', 'zsh': 'sh', 'fish': 'fish',
      \ 'html': 'html', 'css': 'css', 'scss': 'scss', 'sass': 'sass',
      \ 'json': 'json', 'yaml': 'yaml', 'yml': 'yaml', 'toml': 'toml',
      \ 'md': 'markdown', 'sql': 'sql', 'xml': 'xml', 'vue': 'vue',
      \ }

" extensions we never want to dump into a text preview
let s:nt_preview_skip = {
      \ 'png': 1, 'jpg': 1, 'jpeg': 1, 'gif': 1, 'bmp': 1, 'ico': 1,
      \ 'webp': 1, 'svg': 1, 'pdf': 1, 'zip': 1, 'gz': 1, 'tar': 1,
      \ 'o': 1, 'so': 1, 'a': 1, 'dylib': 1, 'exe': 1, 'bin': 1,
      \ 'class': 1, 'pyc': 1, 'woff': 1, 'woff2': 1, 'ttf': 1, 'otf': 1,
      \ 'mp3': 1, 'mp4': 1, 'mov': 1, 'avi': 1, 'wav': 1, 'db': 1, 'sqlite': 1,
      \ }

" Keep the floating preview's background transparent (guibg/ctermbg = NONE) so
" it inherits the editor/terminal background instead of NERDTree's own bg.
" Re-applied on ColorScheme since custom groups are cleared when it changes.
function! s:NTPreviewHighlight() abort
  highlight NTPreviewNormal guibg=NONE ctermbg=NONE
  highlight NTPreviewBorder guibg=NONE ctermbg=NONE
endfunction
call s:NTPreviewHighlight()

function! s:NTPreviewClose() abort
  if s:nt_preview_win <= 0
    return
  endif
  if has('nvim')
    if nvim_win_is_valid(s:nt_preview_win)
      call nvim_win_close(s:nt_preview_win, v:true)
    endif
  else
    call popup_close(s:nt_preview_win)
  endif
  let s:nt_preview_win = 0
endfunction

function! s:NTPreviewShow() abort
  if !g:nerdtree_preview_enabled || &filetype !=# 'nerdtree'
        \ || !exists('g:NERDTreeFileNode')
    call s:NTPreviewClose()
    return
  endif
  let l:node = g:NERDTreeFileNode.GetSelected()
  if empty(l:node) || l:node.path.isDirectory
    call s:NTPreviewClose()
    return
  endif
  let l:path = l:node.path.str()
  let l:ext = tolower(fnamemodify(l:path, ':e'))
  if !filereadable(l:path) || get(s:nt_preview_skip, l:ext, 0)
        \ || getfsize(l:path) > 1024 * 1024
    call s:NTPreviewClose()
    return
  endif

  let l:lines = readfile(l:path, '', 300)
  if empty(l:lines)
    let l:lines = ['[empty file]']
  endif
  let l:ft = get(s:nt_preview_ft, l:ext, '')
  let l:name = fnamemodify(l:path, ':t')
  let l:width = max([40, float2nr(&columns * 0.5)])
  let l:height = max([10, float2nr(&lines * 0.6)])
  let l:col = g:NERDTreeWinSize + 4

  call s:NTPreviewClose()
  if has('nvim')
    let l:buf = nvim_create_buf(v:false, v:true)
    call nvim_buf_set_lines(l:buf, 0, -1, v:false, l:lines)
    if l:ft !=# ''
      call setbufvar(l:buf, '&filetype', l:ft)
    endif
    let l:opts = {
          \ 'relative': 'editor', 'anchor': 'NW',
          \ 'width': l:width, 'height': l:height,
          \ 'row': 2, 'col': l:col,
          \ 'style': 'minimal', 'border': 'rounded',
          \ 'focusable': v:false, 'noautocmd': v:true,
          \ }
    if has('nvim-0.9')
      let l:opts.title = ' ' . l:name . ' '
      let l:opts.title_pos = 'center'
    endif
    let s:nt_preview_win = nvim_open_win(l:buf, v:false, l:opts)
    " Make the float (body + border) background transparent.
    call setwinvar(s:nt_preview_win, '&winhighlight',
          \ 'NormalFloat:NTPreviewNormal,FloatBorder:NTPreviewBorder')
  else
    let s:nt_preview_win = popup_create(l:lines, {
          \ 'line': 3, 'col': l:col + 1,
          \ 'minwidth': l:width, 'maxwidth': l:width,
          \ 'minheight': l:height, 'maxheight': l:height,
          \ 'border': [], 'padding': [0, 1, 0, 1],
          \ 'highlight': 'NTPreviewNormal',
          \ 'borderhighlight': ['NTPreviewBorder'],
          \ 'title': ' ' . l:name . ' ',
          \ 'scrollbar': 0, 'zindex': 200,
          \ })
    if l:ft !=# ''
      call setbufvar(winbufnr(s:nt_preview_win), '&filetype', l:ft)
    endif
  endif
endfunction

function! s:NTPreviewToggle() abort
  let g:nerdtree_preview_enabled = !g:nerdtree_preview_enabled
  if g:nerdtree_preview_enabled
    echo 'NERDTree preview: ON'
    call s:NTPreviewShow()
  else
    echo 'NERDTree preview: OFF'
    call s:NTPreviewClose()
  endif
endfunction

augroup NERDTreePreview
  autocmd!
  " Re-assert the transparent preview highlights after a colorscheme switch.
  autocmd ColorScheme * call s:NTPreviewHighlight()
  autocmd FileType nerdtree nnoremap <buffer><silent> P :call <SID>NTPreviewToggle()<CR>
  " Refresh the preview as the cursor moves over nodes in the tree.
  autocmd CursorMoved * if &filetype ==# 'nerdtree' | call s:NTPreviewShow() | endif
  " Close the preview the moment focus lands on anything that is not the tree.
  " NERDTree opens files with `noautocmd`, so the tree's own WinLeave/BufLeave
  " never fire on <CR>/o; keying off *entering* the new window is what makes the
  " float disappear when a file is opened (not just when the tree is closed).
  autocmd WinEnter,BufEnter * if &filetype !=# 'nerdtree' | call s:NTPreviewClose() | endif
augroup END

" set cursor position in new tab(or file) when launch Vim
autocmd VimEnter * wincmd p

" show buffer list
nnoremap <silent> <leader>ll <cmd>Buffers<CR>
" jump to next buffer
nnoremap <silent> <C-l> :bnext<CR>
nnoremap <silent> <C-h> :bprevious<CR>


"*****************************************************************************
"" EasyMotion
"*****************************************************************************
" Use a single <leader> prefix instead of the default <leader><leader>.
let g:EasyMotion_do_mapping = 0
" Match upper & lower case so you can type the label without worrying about case.
let g:EasyMotion_smartcase = 1
" Keep the cursor on the matched line for n/N style repeats.
let g:EasyMotion_use_smartsign_us = 1

" 1-char search across all visible windows.
nmap <leader>jc <Plug>(easymotion-overwin-f)
" line-wise motions
map <leader>jj <Plug>(easymotion-bd-w)


