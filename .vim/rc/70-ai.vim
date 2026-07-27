"*****************************************************************************
"" [AI safety] Path-based guard for GitHub Copilot completion
"*****************************************************************************
" copilot.vim (loaded in 00-plugins.vim) streams buffer context to GitHub for
" inline suggestions, and -- unlike the custom AI feature below -- there is NO
" content-level pre-send scan for secrets. Mirror the nvim copilot.lua guard:
" refuse to enable Copilot in buffers whose *path* is obviously sensitive
" (dotenv, private keys, cloud-credential dirs, ...). This does not catch a
" secret pasted into an ordinary file -- an accepted limitation, same as nvim.
if !has('nvim')
  let s:copilot_sensitive_paths = [
        \ '\.env$', '\.env\.', '\.envrc$',
        \ 'id_rsa', 'id_ed25519', 'id_ecdsa', 'id_dsa',
        \ '\.pem$', '\.key$', '\.p12$', '\.pfx$', '\.jks$', '\.keystore$', '\.ppk$',
        \ '/\.ssh/', '/\.aws/', '/\.gnupg/', '/\.azure/', '/\.kube/', '/gcloud/',
        \ '\.netrc$', '\.npmrc$', '\.pypirc$', '\.pgpass$', '\.my\.cnf$',
        \ 'kubeconfig', '\.tfstate', '\.tfvars', '\.dockercfg',
        \ 'docker/config\.json$',
        \ 'service.\?account', 'adminsdk', '-key\.json$',
        \ 'credentials', 'secrets\?', 'password',
        \ ]

  " Disable Copilot for the current buffer when its path looks sensitive.
  " b:copilot_enabled = v:false is copilot.vim's per-buffer off switch, checked
  " lazily before each suggestion, so setting it any time before insert is enough.
  function! s:AI_CopilotGuard() abort
    let l:name = tolower(expand('%:p'))
    if l:name ==# ''
      return
    endif
    for l:pat in s:copilot_sensitive_paths
      if l:name =~# l:pat
        let b:copilot_enabled = v:false
        return
      endif
    endfor
  endfunction

  augroup AICopilotSensitiveGuard
    autocmd!
    autocmd BufReadPost,BufNewFile,BufWinEnter * call s:AI_CopilotGuard()
  augroup END
endif


"*****************************************************************************
"" [AI solution] Ask AI and replace selection
"*****************************************************************************
" Select a range, type an instruction in a prompt split, send the selection to
" an AI CLI (claude / codex / gemini) over stdin, preview the result in a diff
" tab, then replace the original selection. <C-o> hits a local Ollama model.
if !has('nvim') && has('job') && has('channel') && has('timers')

  " Map a short tool alias to the actual Ollama model tag.
  let s:ai_ollama_models = { 'gemma': 'gemma4:e4b' }

  " Tools launched together by the 'all' mode, in tab order. Kept in step with
  " the Neovim side (ai/init.lua's replace tool list); the two editors offer the
  " same set for "ask about a selection and replace it" so muscle memory carries
  " between them. Everything downstream (tab count, the 1..N jump keys, the
  " status hint) is derived from this list rather than hardcoded to two entries.
  let s:ai_all_tools = ['claude', 'codex', 'gemini', 'copilot']

  " copilot has no stdin path, so its payload rides in argv; see s:AI_BuildCmd.
  let s:ai_copilot_model = 'gpt-5-mini'

  " Upper bound on the `sh -c` string handed to job_start(). The whole command
  " is one argv entry and counts against ARG_MAX (argv + envp, ~1 MB here), so a
  " tool that inlines the payload can push it past the limit and exec fails with
  " a bare E2BIG that surfaces as an unexplained non-zero exit. Tools that pipe
  " the selection in from the tmpfile keep the command short however large the
  " selection is, so in practice only copilot can trip this. Checking the built
  " string rather than the raw selection keeps the estimate honest: shellescape's
  " quoting can inflate the text several-fold. Mirrors MAX_CMD_BYTES in
  " .config/nvim/lua/setup/functions/ai/backend.lua.
  let s:ai_max_cmd_bytes = 256 * 1024

  " Return an error message when `cmd` cannot safely be exec'd, '' when it can.
  " Refusing beats truncating: the payload IS the text about to be replaced, so
  " a reply written for a fragment would then be applied over the whole range.
  function! s:AI_CmdTooLarge(tool, cmd) abort
    if len(a:cmd) <= s:ai_max_cmd_bytes
      return ''
    endif
    return printf(
          \ 'Selection too large for %s: the command line would be %d KB '
          \ . '(limit %d KB). %s inlines the payload into argv; use a '
          \ . 'stdin-based tool (claude / codex / gemini) or select less.',
          \ a:tool, len(a:cmd) / 1024, s:ai_max_cmd_bytes / 1024, a:tool)
  endfunction

  " ---- pre-send credential scan -------------------------------------------
  " Before a selection leaves the editor for an AI CLI, run it through the shared
  " scanner (scripts/secret_scan.py -> the same scan_secrets the bash-review
  " hooks use; the regexes live in one place, never reimplemented in VimScript).
  " A hit prompts for confirmation defaulting to abort; a missing scanner/python
  " fails OPEN with a warning (blocking all AI when python is absent -- e.g. a
  " GUI vim without the shell PATH -- is worse than the risk it guards).
  " This file is sourced by its real repo path (see .vimrc's resolve()), so step
  " up from .vim/rc/70-ai.vim to the repo root to find scripts/.
  let s:ai_secret_scanner =
        \ fnamemodify(resolve(expand('<sfile>:p')), ':h:h:h') . '/scripts/secret_scan.py'

  " Returns [status, label]: 'clean' | 'secret',<label> | 'unavailable'.
  function! s:AI_ScanPayload(text) abort
    if !filereadable(s:ai_secret_scanner) || !executable('python3')
      return ['unavailable', '']
    endif
    " Payload on stdin, never argv (argv would leak the secret via `ps`).
    let l:out = system('python3 ' . shellescape(s:ai_secret_scanner), a:text)
    if v:shell_error == 0
      return ['clean', '']
    elseif v:shell_error == 1
      return ['secret', trim(l:out)]
    endif
    return ['unavailable', '']
  endfunction

  " Gate before sending. Returns 1 to proceed, 0 to abort. confirm()'s default is
  " No, so Enter/Esc aborts the send.
  function! s:AI_ConfirmSend(text) abort
    let [l:status, l:label] = s:AI_ScanPayload(a:text)
    if l:status ==# 'secret'
      let l:choice = confirm(
            \ printf("Possible credential (%s) detected in the AI payload.\n"
            \        . "Send it to the AI tool anyway?", l:label),
            \ "&No\n&Yes", 1, 'Warning')
      return l:choice == 2
    elseif l:status ==# 'unavailable'
      echohl WarningMsg
      echom 'secret-scan unavailable (python3 / secret_scan.py); '
            \ . 'sending AI payload without a credential check.'
      echohl None
      return 1
    endif
    return 1
  endfunction

  function! s:AI_TrimOutput(list) abort
    let l:out = copy(a:list)
    while len(l:out) > 0 && l:out[-1] ==# ''
      call remove(l:out, -1)
    endwhile
    return l:out
  endfunction

  " Strip a markdown code fence that wraps the WHOLE reply (mirrors the nvim
  " prompt.strip_code_fences). The submit prompt tells the model not to fence its
  " output, but models (Claude especially) often wrap the reply in ```lang ...
  " ```; those fence lines must never land in the replaced selection. Only strips
  " when an opening ```lang line and a matching closing ``` line clearly bracket
  " the whole output (ignoring surrounding blank lines). Any other shape -- no
  " fence, or a lone ``` inside otherwise-plain code -- is returned unchanged so
  " ordinary source is never corrupted.
  function! s:AI_StripCodeFences(list) abort
    let l:first = 0
    let l:last = len(a:list) - 1
    while l:first <= l:last && a:list[l:first] =~# '^\s*$'
      let l:first += 1
    endwhile
    while l:last >= l:first && a:list[l:last] =~# '^\s*$'
      let l:last -= 1
    endwhile
    " Need at least a distinct opening and closing fence line to strip.
    if l:first >= l:last
      return copy(a:list)
    endif
    let l:opens = a:list[l:first] =~# '^\s*```\+\s*[0-9A-Za-z_#+.-]*\s*$'
    let l:closes = a:list[l:last] =~# '^\s*```\+\s*$'
    if !(l:opens && l:closes)
      return copy(a:list)
    endif
    return a:list[l:first + 1 : l:last - 1]
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

  " Build the shell command for one tool. Every tool but copilot reads the
  " selection from `tmpfile` over stdin; copilot takes it in `selected`.
  function! s:AI_BuildCmd(tool, tmpfile, sys, selected) abort
    if a:tool ==# 'codex'
      return 'cat ' . shellescape(a:tmpfile) . ' | codex exec --skip-git-repo-check ' . shellescape(a:sys)
    elseif a:tool ==# 'gemini'
      return 'cat ' . shellescape(a:tmpfile) . ' | gemini -m gemini-flash-lite-latest -p ' . shellescape(a:sys)
    elseif a:tool ==# 'copilot'
      " copilot CLI does not read stdin as context, so the payload is inlined
      " into the prompt. `-s` keeps stdout to the agent response only.
      "
      " Deliberate exception to the "payload on stdin, never argv" rule stated
      " above (and in scripts/secret_scan.py / ai/backend.lua): there is no
      " stdin path for this tool, so the choice is argv or no copilot at all.
      " The cost is real -- while the job runs the whole selection is visible to
      " every process on the machine via `ps aux`, unlike the tmpfile the other
      " tools use. s:AI_ConfirmSend still gates copilot, so a credential the
      " scanner RECOGNISES never reaches argv; what escapes is what
      " value-scanning cannot see. Prefer a stdin tool for anything sensitive;
      " if copilot ever grows a stdin mode, move it there and delete this branch.
      let l:prompt = a:sys . "\n\n## Input\n```\n" . join(a:selected, "\n") . "\n```"
      return 'copilot --model ' . shellescape(s:ai_copilot_model)
            \ . ' -s -p ' . shellescape(l:prompt)
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
    if !bufexists(l:target)
      echohl ErrorMsg | echom 'Target buffer no longer valid.' | echohl None
      return
    endif
    " Guard against edits made to the target buffer while the AI response was
    " in flight (prompt window open, then the async job itself): the recorded
    " start/end line numbers would otherwise replace the wrong range.
    if getbufvar(l:target, 'changedtick') != a:state.changedtick
      echohl WarningMsg
      echom 'Target buffer changed since the selection was made; aborting to avoid replacing the wrong range.'
      echohl None
      return
    endif
    call s:AI_SetLines(l:target, l:start, l:end, a:lines)
    echo 'Selection replaced.'
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
    " Always remove the tmpfile, even if the diff tab was already closed via
    " `q` while the job was still pending (closed=1 short-circuits below).
    " delete() silently no-ops on a missing file, so this stays safe even if
    " invoked more than once.
    call delete(l:s.tmpfile)
    if l:s.closed
      return
    endif
    if l:s.status !=# 'cancelled'
      let l:out = s:AI_StripCodeFences(s:AI_TrimOutput(l:s.output))
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

  " Shared scaffolding for single-response tools (CLI or local Ollama): open the
  " diff tab + response window, seed the shared state, and start the job. Only
  " the shell command and the exit callback differ per backend, so both
  " s:AI_RunSingle and s:AI_RunOllama funnel through here instead of each
  " duplicating this window/state setup.
  function! s:AI_RunJob(ctx, tmpfile, cmd, exit_cb) abort
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
          \ 'changedtick': a:ctx.changedtick,
          \ 'orig_buf': l:orig_buf, 'orig_win': l:orig_win,
          \ 'resp_buf': l:resp_buf, 'resp_win': l:resp_win,
          \ 'status': 'pending', 'output': [], 'closed': 0, 'tmpfile': a:tmpfile,
          \ }
    call setbufvar(l:orig_buf, 'ai_state', l:state)
    call setbufvar(l:resp_buf, 'ai_state', l:state)
    call s:AI_SingleStatus(l:state)

    let l:state.job = job_start(['sh', '-c', a:cmd], {
          \ 'out_cb': function('s:AI_JobOut', [l:state.output]),
          \ 'out_mode': 'nl',
          \ 'exit_cb': function(a:exit_cb, [l:state]),
          \ })
  endfunction

  function! s:AI_RunSingle(ctx, sys, tmpfile) abort
    let l:cmd = s:AI_BuildCmd(a:ctx.tool, a:tmpfile, a:sys, a:ctx.selected)
    " Refuse before tabnew: with a single tool there is no half-usable result to
    " show, so opening a diff tab only to fill it with an error is worse than
    " saying so on the command line and leaving the buffer untouched.
    let l:err = s:AI_CmdTooLarge(a:ctx.tool, l:cmd)
    if l:err !=# ''
      call delete(a:tmpfile)
      echohl ErrorMsg | echom l:err | echohl None
      return
    endif
    call s:AI_RunJob(a:ctx, a:tmpfile, l:cmd, 's:AI_SingleExit')
  endfunction

  " ---- all mode (s:ai_all_tools in parallel) ------------------------------
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
    " Derive the jump hint from the tool count so it can never advertise a key
    " s:AI_SetMaps did not bind (it read "1/2:jump" while the list grew).
    let l:nums = map(range(1, len(a:state.tools)), 'string(v:val)')
    call setwinvar(a:state.orig_win, '&statusline', ' Original ')
    call setwinvar(a:state.resp_win, '&statusline',
          \ ' ' . join(l:parts, ' | ') . '   [y:AI Y:merged q:cancel Tab:switch '
          \ . join(l:nums, '/') . ':jump] ')
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
    " Decrement pending and clean up the shared tmpfile regardless of
    " closed=1 (tab already closed via `q`), so it is removed once the last
    " outstanding job actually exits. delete() silently no-ops on a missing
    " file, so this stays safe even if invoked more than once.
    let l:s.pending -= 1
    if l:s.pending <= 0
      call delete(l:s.tmpfile)
    endif
    if l:s.closed
      return
    endif
    if l:s.status[a:idx] ==# 'cancelled'
      return
    endif
    let l:buf = l:s.bufs[a:idx]
    let l:out = s:AI_StripCodeFences(s:AI_TrimOutput(l:s.output[a:idx]))
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
    let l:tools = copy(s:ai_all_tools)
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
          \ 'changedtick': a:ctx.changedtick,
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
      let l:cmd = s:AI_BuildCmd(l:t, a:tmpfile, a:sys, a:ctx.selected)
      let l:err = s:AI_CmdTooLarge(l:t, l:cmd)
      if l:err !=# ''
        " Fail just this tab and keep the others running, so an oversized
        " selection costs you copilot rather than the whole comparison.
        "
        " `pending` counts outstanding jobs and is what frees the tmpfile every
        " tool shares (see s:AI_AllFinish). A tool that never starts still has
        " to be accounted for here, or the count never reaches zero and the file
        " holding the selection is left behind on disk.
        let l:state.status[l:i] = 'failed'
        call setbufvar(l:state.bufs[l:i], '&modifiable', 1)
        call s:AI_SetBufAll(l:state.bufs[l:i], ['[' . l:err . ']'])
        call setbufvar(l:state.bufs[l:i], '&modifiable', 0)
        let l:state.pending -= 1
        if l:state.pending <= 0
          call delete(a:tmpfile)
        endif
      else
        let l:state.jobs[l:i] = job_start(['sh', '-c', l:cmd], {
              \ 'out_cb': function('s:AI_JobOut', [l:state.output[l:i]]),
              \ 'out_mode': 'nl',
              \ 'exit_cb': function('s:AI_AllExit', [l:state, l:i]),
              \ })
      endif
      let l:i += 1
    endfor
    call s:AI_AllStatus(l:state)
  endfunction

  " ---- ollama mode (local HTTP API, single tool) --------------------------
  " `ollama run` writes ANSI control codes onto STDOUT, corrupting the captured
  " text. Instead POST to the local Ollama HTTP API with stream=false and parse
  " the JSON, which yields clean output. think=false keeps any reasoning out of
  " the `.response` field and avoids erroring on models that do not support the
  " think parameter (kept in sync with the nvim backend, which also sends
  " think=false). Reuses the single-mode UI/accept/close machinery; only the
  " command and the JSON output parsing differ.
  function! s:AI_BuildOllamaCmd(tmpfile) abort
    return 'curl -s http://localhost:11434/api/generate --data-binary @'
          \ . shellescape(a:tmpfile)
  endfunction

  function! s:AI_OllamaFinish(state, status, timer) abort
    let l:s = a:state
    " Always remove the tmpfile, even if the diff tab was already closed via
    " `q` while the job was still pending; delete() no-ops safely on a
    " missing file. See s:AI_SingleFinish for the same pattern.
    call delete(l:s.tmpfile)
    if l:s.closed
      return
    endif
    if l:s.status !=# 'cancelled'
      " The API returns a single JSON object: { "response": "...", ... }.
      let l:raw = join(l:s.output, "\n")
      let l:result = []
      let l:err = ''
      try
        let l:decoded = json_decode(l:raw)
        if type(l:decoded) == v:t_dict
          if has_key(l:decoded, 'response') && type(l:decoded.response) == v:t_string
            let l:result = s:AI_StripCodeFences(split(trim(l:decoded.response), "\n", 1))
          endif
          if has_key(l:decoded, 'error')
            let l:err = string(l:decoded.error)
          endif
        endif
      catch
      endtry
      call setbufvar(l:s.resp_buf, '&modifiable', 1)
      if a:status == 0 && len(l:result) > 0
            \ && !(len(l:result) == 1 && l:result[0] ==# '')
        let l:s.status = 'done'
        call s:AI_SetBufAll(l:s.resp_buf, l:result)
      else
        let l:s.status = 'failed'
        let l:msg = printf('[%s failed (exit code %d)]', l:s.tool, a:status)
        if l:err !=# ''
          let l:msg = printf('[%s error: %s]', l:s.tool, l:err)
        endif
        call s:AI_SetBufAll(l:s.resp_buf, [l:msg])
        call setbufvar(l:s.resp_buf, '&modifiable', 0)
      endif
    endif
    call s:AI_SingleStatus(l:s)
    if l:s.status ==# 'done'
      call win_execute(l:s.orig_win, 'diffthis')
      call win_execute(l:s.resp_win, 'diffthis')
    endif
  endfunction

  function! s:AI_OllamaExit(state, job, status) abort
    call timer_start(0, function('s:AI_OllamaFinish', [a:state, a:status]))
  endfunction

  function! s:AI_RunOllama(ctx, tmpfile) abort
    call s:AI_RunJob(a:ctx, a:tmpfile,
          \ s:AI_BuildOllamaCmd(a:tmpfile), 's:AI_OllamaExit')
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
      " One jump key per tool, derived from the list rather than spelled out:
      " these were hardcoded to 1 and 2, so a third tool would have been
      " reachable only by <Tab> and the status line would still have said 1/2.
      for l:n in range(1, len(s:ai_all_tools))
        execute printf('nnoremap <buffer><silent> %d :call <SID>AI_AllJump(%d)<CR>',
              \ l:n, l:n)
      endfor
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
    " Pre-send credential gate: scan the selection + the user's instruction.
    " Skipped for Ollama, which hits the local HTTP API (localhost) and never
    " leaves the machine -- the external-send gate does not apply there.
    if !has_key(s:ai_ollama_models, l:ctx.tool)
          \ && !s:AI_ConfirmSend(join(l:ctx.selected, "\n") . "\n" . l:prompt)
      echohl WarningMsg
      echom 'AI request cancelled (credential detected in payload).'
      echohl None
      return
    endif
    " Ollama tools hit the local HTTP API; the body is JSON, not the raw text.
    if has_key(s:ai_ollama_models, l:ctx.tool)
      let l:body = json_encode({
            \ 'model': s:ai_ollama_models[l:ctx.tool],
            \ 'system': l:sys,
            \ 'prompt': join(l:ctx.selected, "\n"),
            \ 'stream': v:false,
            \ 'think': v:false,
            \ })
      let l:tmpfile = tempname()
      call writefile([l:body], l:tmpfile)
      echo 'Asking ' . l:ctx.tool . ' (ollama)...'
      call s:AI_RunOllama(l:ctx, l:tmpfile)
      return
    endif
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
    " Validate against the same list the 'all' mode uses, so adding a tool there
    " cannot leave a new mapping silently falling back to claude while the
    " prompt and status line still claim the tool the user asked for.
    let l:tool = a:tool
    if index(s:ai_all_tools + ['all'], l:tool) < 0
          \ && !has_key(s:ai_ollama_models, l:tool)
      let l:tool = 'claude'
    endif
    let l:ft = &filetype
    let l:ctx = {
          \ 'tool': l:tool, 'target_buf': bufnr('%'),
          \ 'start': l:start, 'end': l:end,
          \ 'selected': getline(l:start, l:end),
          \ 'ft': l:ft, 'lang': l:ft !=# '' ? l:ft : 'plain text',
          \ 'changedtick': getbufvar(bufnr('%'), 'changedtick'),
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

  " Intentional override: <C-c> is conventionally an Esc/abort alias in
  " visual mode, but here it triggers the Claude AI action instead; use
  " <Esc> to leave visual mode as usual.
  xnoremap <silent> <C-c> :<C-u>call <SID>AI_Start('claude')<CR>
  xnoremap <silent> <C-x> :<C-u>call <SID>AI_Start('codex')<CR>
  xnoremap <silent> <C-g> :<C-u>call <SID>AI_Start('gemini')<CR>
  xnoremap <silent> <C-p> :<C-u>call <SID>AI_Start('copilot')<CR>
  xnoremap <silent> <C-l> :<C-u>call <SID>AI_Start('all')<CR>
  xnoremap <silent> <C-o> :<C-u>call <SID>AI_Start('gemma')<CR>
endif


