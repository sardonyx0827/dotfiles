require("setup.set")
require("setup.remap")

local augroup = vim.api.nvim_create_augroup
local setupGroup = augroup('setup', {})

local autocmd = vim.api.nvim_create_autocmd
local yank_group = augroup('HighlightYank', {})

function R(name)
  require("plenary.reload").reload_module(name)
end

autocmd('TextYankPost', {
  group = yank_group,
  pattern = '*',
  callback = function()
    vim.highlight.on_yank({
      higroup = 'IncSearch',
      timeout = 40,
    })
  end,
})

autocmd({"BufWritePre"}, {
  group = setupGroup,
  pattern = "*",
  command = [[%s/\s\+$//e]],
})

vim.g.netrw_liststyle = 3
vim.g.netrw_winsize = 80
vim.g.netrw_browse_split = 2

vim.cmd[[

" Terminal Setting (Vim)
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
    " Move Tab
    tnoremap <silent> <C-W>gt     <cmd>call <SID>TermExec('tabn')<CR>
    tnoremap <silent> <C-W>gT     <cmd>call <SID>TermExec('tabp')<CR>

    ]]
