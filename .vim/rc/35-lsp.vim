"*****************************************************************************
"" LSP / Completion / Lint
"*****************************************************************************
" Classic Vim port of the Neovim LSP stack (after/plugin/lsp.lua):
"   mason            -> vim-lsp-settings (:LspInstallServer in a target buffer)
"   nvim-lspconfig   -> vim-lsp
"   blink.cmp        -> asyncomplete.vim + asyncomplete-lsp.vim
" Keymaps mirror nvim's LspAttach block so muscle memory works in both.
scriptencoding utf-8

" ALE keeps linting/fixing duty only; LSP diagnostics come from vim-lsp.
" (Canonical setting when ALE coexists with an LSP client.)
let g:ale_disable_lsp = 1
let g:ale_sign_error = '✘'
let g:ale_sign_warning = '▲'
" vim-lsp already renders virtual text; avoid double annotations from ALE.
let g:ale_virtualtext_cursor = 'disabled'

" Diagnostics display (match nvim vim.diagnostic.config)
let g:lsp_diagnostics_enabled = 1
let g:lsp_diagnostics_virtual_text_enabled = 1
let g:lsp_diagnostics_virtual_text_align = 'after'
let g:lsp_diagnostics_signs_error = {'text': '✘'}
let g:lsp_diagnostics_signs_warning = {'text': '▲'}
let g:lsp_diagnostics_signs_hint = {'text': '⚑'}
" nvim hides the INFO sign with ''; Vim rejects empty sign text (E239),
" so a blank space is the closest equivalent.
let g:lsp_diagnostics_signs_information = {'text': ' '}
" Highlight other references of the symbol under the cursor (documentHighlight)
let g:lsp_document_highlight_enabled = 1

function! s:on_lsp_buffer_enabled() abort
  setlocal omnifunc=lsp#complete
  setlocal signcolumn=yes
  " Keymaps mirror nvim's LspAttach (after/plugin/lsp.lua). <C-t> falls back
  " to the global tag-jump mapping (60-mappings) in non-LSP buffers.
  nmap <buffer> gd <plug>(lsp-definition)
  nmap <buffer> <C-t> <plug>(lsp-definition)
  nmap <buffer> K <plug>(lsp-hover)
  nmap <buffer> <leader>ra <plug>(lsp-rename)
  nmap <buffer> <leader>ca <plug>(lsp-code-action)
  nmap <buffer> gr <plug>(lsp-references)
  nmap <buffer> gl <plug>(lsp-document-diagnostics)
  nmap <buffer> [d <plug>(lsp-previous-diagnostic)
  nmap <buffer> ]d <plug>(lsp-next-diagnostic)
  " Format buffer (nvim: conform <leader>ff with lsp fallback)
  nmap <buffer> <leader>ff <plug>(lsp-document-format)
endfunction

augroup vimrc-lsp
  autocmd!
  " Called for every buffer a language server attaches to.
  autocmd User lsp_buffer_enabled call s:on_lsp_buffer_enabled()
augroup END
