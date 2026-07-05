"*****************************************************************************
"" LSP / Completion / Lint
"*****************************************************************************
" vim-lsp-settings provides :LspInstallServer (run it in a target buffer).
scriptencoding utf-8

" ALE keeps linting/fixing duty only; LSP diagnostics come from vim-lsp.
" (Canonical setting when ALE coexists with an LSP client.)
let g:ale_disable_lsp = 1
let g:ale_sign_error = '✘'
let g:ale_sign_warning = '▲'
" vim-lsp already renders virtual text; avoid double annotations from ALE.
let g:ale_virtualtext_cursor = 'disabled'

" Diagnostics display
let g:lsp_diagnostics_enabled = 1
let g:lsp_diagnostics_virtual_text_enabled = 1
let g:lsp_diagnostics_virtual_text_align = 'after'
let g:lsp_diagnostics_signs_error = {'text': '✘'}
let g:lsp_diagnostics_signs_warning = {'text': '▲'}
let g:lsp_diagnostics_signs_hint = {'text': '⚑'}
" Vim rejects empty sign text (E239), so use a blank space to hide the INFO sign.
let g:lsp_diagnostics_signs_information = {'text': ' '}
" Highlight other references of the symbol under the cursor (documentHighlight)
let g:lsp_document_highlight_enabled = 1

function! s:on_lsp_buffer_enabled() abort
  setlocal omnifunc=lsp#complete
  setlocal signcolumn=yes
  " <C-t> falls back to the global tag-jump mapping (60-mappings) in non-LSP
  " buffers.
  nmap <buffer> gd <plug>(lsp-definition)
  nmap <buffer> <C-t> <plug>(lsp-definition)
  nmap <buffer> K <plug>(lsp-hover)
  nmap <buffer> <leader>ra <plug>(lsp-rename)
  nmap <buffer> <leader>ca <plug>(lsp-code-action)
  nmap <buffer> gr <plug>(lsp-references)
  nmap <buffer> gl <plug>(lsp-document-diagnostics)
  nmap <buffer> [d <plug>(lsp-previous-diagnostic)
  nmap <buffer> ]d <plug>(lsp-next-diagnostic)
  " Format buffer
  nmap <buffer> <leader>ff <plug>(lsp-document-format)
endfunction

augroup vimrc-lsp
  autocmd!
  " Called for every buffer a language server attaches to.
  autocmd User lsp_buffer_enabled call s:on_lsp_buffer_enabled()
augroup END
