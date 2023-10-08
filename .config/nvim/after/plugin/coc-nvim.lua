-- Some servers have issues with backup files, see #649
vim.opt.backup = false
vim.opt.writebackup = false

-- Having longer updatetime (default is 4000 ms = 4s) leads to noticeable
-- delays and poor user experience
vim.opt.updatetime = 300

-- Always show the signcolumn, otherwise it would shift the text each time
-- diagnostics appeared/became resolved
vim.opt.signcolumn = "yes"

local keyset = vim.keymap.set
-- Autocomplete
function _G.check_back_space()
  local col = vim.fn.col('.') - 1
  return col == 0 or vim.fn.getline('.'):sub(col, col):match('%s') ~= nil
end

local opts = {silent = true, noremap = true, expr = true, replace_keycodes = false}
keyset("i", "<TAB>", 'coc#pum#visible() ? coc#pum#next(1) : v:lua.check_back_space() ? "<TAB>" : coc#refresh()', opts)
keyset("i", "<S-TAB>", [[coc#pum#visible() ? coc#pum#prev(1) : "\<C-h>"]], opts)

function _G.show_docs()
  local cw = vim.fn.expand('<cword>')
  if vim.fn.index({'vim', 'help'}, vim.bo.filetype) >= 0 then
    vim.api.nvim_command('h ' .. cw)
  elseif vim.api.nvim_eval('coc#rpc#ready()') then
    vim.fn.CocActionAsync('doHover')
  else
    vim.api.nvim_command('!' .. vim.o.keywordprg .. ' ' .. cw)
  end
end
keyset("n", "<leader>cd", '<CMD>lua _G.show_docs()<CR>', {silent = true})

vim.cmd[[

" set file type for tsx when file is opend first time
autocmd BufNewFile,BufRead *.tsx let b:tsx_ext_found = 1
autocmd BufNewFile,BufRead *.tsx set filetype=typescript.tsx
let g:coc_global_extensions = [ 'coc-tsserver', 'coc-eslint8', 'coc-rust-analyzer', 'coc-react-refactor', 'coc-xml',
  \ 'coc-yaml', 'coc-translator', 'coc-sh', 'coc-lua', 'coc-json', 'coc-jedi', 'coc-diagnostic', 'coc-css', 'coc-prettier', 'coc-fzf-preview', 'coc-lists' ]

" no new line when hit the enter key
inoremap <silent><expr> <CR> coc#pum#visible() ? coc#pum#confirm() : "\<CR>"

" check documentation on cursor
" text must contains '()' to detect input and its must be 1 character
function! ChoseAction(actions) abort
echo join(map(copy(a:actions), { _, v -> v.text }), ", ") .. ": "
let result = getcharstr()
let result = filter(a:actions, { _, v -> v.text =~# printf(".*\(%s\).*", result)})
return len(result) ? result[0].value : ""
endfunction

function! CocJumpAction() abort
let actions = [
  \ {"text": "(s)plit", "value": "split"},
  \ {"text": "(v)slit", "value": "vsplit"},
  \ {"text": "(t)ab", "value": "tabedit"},
\ ]
return ChoseAction(actions)
endfunction
nnoremap <silent> <C-t> :<C-u>call CocActionAsync('jumpDefinition', CocJumpAction())<CR>

]]
