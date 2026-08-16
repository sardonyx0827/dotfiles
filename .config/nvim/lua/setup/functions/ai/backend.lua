---------------------------------------------------------
-- ai.backend
-- Unified AI invocation. Knows how to run each tool (CLI tools over stdin,
-- Gemini over its REST API through scripts/gemini_api.py, Ollama over its local
-- HTTP API), manages the temp file and job lifecycle, and reports the result
-- through a single `done(ok, lines, err)` callback.
-- The UI layer never talks to a tool directly; it only drives `run`.
---------------------------------------------------------
local prompt = require("setup.functions.ai.prompt")

local M = {}

--- Path to a helper in the dotfiles repo's scripts/ directory.
--- ~/.config/nvim is a symlink into the repo, so resolve it and step up to the
--- repo root.
---
--- Deliberately NOT gated on filereadable(): callers here build a command
--- string, and a missing helper has to surface as python3's own "No such file
--- or directory" on the job's stderr -- which cli_failure_reason quotes -- and
--- not as a silently different command. The one caller that DOES need the
--- distinction (scan_payload, which must fail open) checks readability itself.
--- @param name string basename under scripts/
--- @return string absolute path
local function repo_script(name)
  local cfg = vim.fn.resolve(vim.fn.stdpath("config"))
  return vim.fn.fnamemodify(cfg, ":h:h") .. "/scripts/" .. name
end

--- Which Gemini model a request will use.
---
--- Same variable and same default as the bash-review hooks
--- (.claude/hooks/bash-review.py) and scripts/gemini_api.py, so the repository
--- has ONE answer to "which Gemini model".
---
--- This is a REPORTING helper, not the decision: nothing here passes the result
--- to the request. scripts/gemini_api.py resolves $GEMINI_MODEL itself when the
--- child runs, which is what lets a value set mid-session take effect and what
--- keeps this side answering identically to the VimScript port (which pins no
--- model at all). The literal is repeated because ai/init.lua DISPLAYS the model
--- in a report header, and "(default)" is not something a reader can act on;
--- tests/test_gemini_api_cli.py pins the two copies together.
---
--- Read on every call rather than captured at load: a value frozen at `require`
--- time would make the header name a model the request never used.
--- @return string
local function gemini_model()
  local env = vim.env.GEMINI_MODEL
  if env and vim.trim(env) ~= "" then
    return vim.trim(env)
  end
  return "gemini-flash-lite-latest"
end

-- Tool registry. `kind` selects the transport; `default_model` is used when the
-- caller does not pass an explicit model in the spec.
--
-- "api" and "cli" share a transport (spawn a process, pipe the payload in on
-- stdin, read the reply off stdout) and differ only in what is on the other end
-- of the pipe: gemini reaches Google over HTTPS through a local helper rather
-- than through the `gemini` CLI. They are kept apart because the credential
-- gate in M.run keys off `kind`, and folding gemini in with the LOCAL "ollama"
-- transport is exactly the mistake that would send buffers to Google unscanned.
local TOOLS = {
  claude  = { kind = "cli", default_model = "sonnet" },
  codex   = { kind = "cli", default_model = nil },
  -- No default_model: the helper resolves $GEMINI_MODEL when the child runs.
  -- Pinning one here would freeze it at `require` time and, worse, put the two
  -- editors on different answers -- the VimScript port has no such table.
  gemini  = { kind = "api", default_model = nil },
  copilot = { kind = "cli", default_model = "gpt-5-mini" },
  gemma   = { kind = "ollama", default_model = "gemma4:e4b" },
}

M.TOOLS = TOOLS

--- Which Gemini model a request will use, for display in a report header.
--- See the local of the same name; public because ai/init.lua does the display.
M.gemini_model = gemini_model

---------------------------------------------------------
-- Pre-send credential scan.
-- Before any payload leaves the editor for an AI tool, it is run through the
-- shared scanner CLI (scripts/secret_scan.py -> the same scan_secrets the
-- bash-review hooks use; the regexes live in one place so Lua never reimplements
-- them). A hit prompts for confirmation defaulting to abort; a missing scanner
-- or python fails OPEN with a visible warning, because blocking every AI action
-- when python is absent (e.g. a GUI-launched nvim without the shell's PATH)
-- would be worse than the risk this guards. Every request funnels through M.run,
-- so this is the single choke point.
---------------------------------------------------------

-- Scan `text`. Returns "clean" | "secret",<label> | "unavailable".
local function scan_payload(text)
  -- Unlike the other repo_script callers this one checks readability: a missing
  -- scanner has to degrade to "unavailable" (fail open with a warning), never
  -- to a python3 error that the caller would read as a failed scan.
  local scanner = repo_script("secret_scan.py")
  -- Fail OPEN when python3 is absent (e.g. a GUI-launched nvim that did not
  -- inherit the shell's PATH). executable() must be checked FIRST: vim.fn.system
  -- with a LIST arg raises E475 (not a v:shell_error) when the binary is
  -- missing, so a bare call would throw past this guard instead of degrading to
  -- "unavailable". pcall wraps the call as a further backstop.
  if vim.fn.filereadable(scanner) ~= 1 or vim.fn.executable("python3") ~= 1 then
    return "unavailable"
  end
  -- Payload on stdin, never argv (argv would leak the secret via `ps`).
  local ok, out = pcall(vim.fn.system, { "python3", scanner }, text or "")
  if not ok then
    return "unavailable"
  end
  local code = vim.v.shell_error
  if code == 0 then
    return "clean"
  elseif code == 1 then
    return "secret", vim.trim(out)
  end
  return "unavailable" -- scanner import error (2) or any other non-{0,1} exit
end

-- Dedupe scans within one synchronous burst: run_multi's "all" mode calls
-- M.run once per tool with an identical payload, all in the same tick. Cache the
-- decision keyed by payload text and clear it on the next tick, so "all" prompts
-- once, not once per tool, without persisting an approval across user actions.
local scan_cache = { text = nil, ok = nil }

-- Gate a payload before sending. Returns true to proceed, false to abort.
-- confirm() defaults to "No", so Enter/Esc aborts the send.
local function confirm_send(text)
  if scan_cache.text == text then
    return scan_cache.ok
  end
  local ok
  local status, label = scan_payload(text)
  if status == "secret" then
    local choice = vim.fn.confirm(
      string.format(
        "Possible credential (%s) detected in the AI payload.\n"
        .. "Send it to the AI tool anyway?", label),
      "&No\n&Yes", 1, "Warning")
    ok = choice == 2
  elseif status == "unavailable" then
    vim.notify(
      "secret-scan unavailable (python3 / secret_scan.py); "
      .. "sending AI payload without a credential check.",
      vim.log.levels.WARN)
    ok = true
  else
    ok = true
  end
  scan_cache.text, scan_cache.ok = text, ok
  vim.schedule(function()
    scan_cache.text, scan_cache.ok = nil, nil
  end)
  return ok
end

-- Upper bound on the `sh -c` command string. Every request runs as
-- jobstart({ "sh", "-c", cmd }), so `cmd` is a single argv entry and counts
-- against ARG_MAX (argv + envp; 1 MB on this machine, measured). Tools that
-- pipe the payload in from a temp file keep `cmd` tiny no matter how large the
-- input is, so this only ever trips for copilot, which inlines the payload into
-- argv. The check is on the BUILT string, not on the raw input: shellescape's
-- quoting can inflate a payload several-fold, so an input-side estimate is
-- wrong in both directions.
--
-- Distinct from ai.init's MAX_DIFF_BYTES, which bounds what is worth sending
-- for commit generation and can truncate safely. Here the payload IS the text
-- about to be replaced, so truncating it would produce a reply written for a
-- fragment and then apply it over the whole range -- worse than the failure it
-- would be papering over. Refuse instead, and say which tools do not have the
-- limit. In "all" mode only the offending tool's tab fails; the rest run.
local MAX_CMD_BYTES = 256 * 1024

-- How much of a failing tool's stderr to quote in the report. The useful part
-- is the first line or two ("command not found", "invalid API key"); a Python
-- traceback would otherwise push it out of view.
local MAX_STDERR_CHARS = 500

-- Marks the child as an editor-driven, generation-only invocation. Read by the
-- Stop hooks (.claude/hooks/stop-audit.sh and its .codex sibling), which skip
-- their debug-statement audit when it is set.
--
-- Without it the audit fires on a `<leader>cm` run and blocks with "remove
-- console.log / debugger": the agent takes that as an instruction, EDITS THE
-- USER'S WORKING TREE, and returns "removed the debug statement" as the commit
-- message. Both halves are wrong -- the generated text is destroyed and changes
-- nobody asked for land in the repo. The audit belongs to interactive sessions,
-- where the agent wrote the code it is being asked to clean up; here it is
-- auditing the user's own uncommitted work, which is exactly what a commit
-- message is FOR.
--
-- Prefixed onto the command string rather than passed through jobstart's `env`
-- so the whole contract lives in one testable string, and so .vim/rc/70-ai.vim
-- can carry the identical shape without depending on Vim's job env semantics.
-- Only claude and codex get it; gemini and copilot run no such hook.
local ONESHOT_ENV = "EDITOR_AI_ONESHOT=1"

--- Build the shell command for a CLI tool. Most tools read the payload from
--- `tmpfile` over stdin; copilot inlines `input` into the prompt instead.
--- `skip_git_check` adds codex's --skip-git-repo-check (used by the replace
--- feature so codex works outside a repo; commit generation leaves it off).
local function build_cli_cmd(tool, model, instruction, tmpfile, input, skip_git_check)
  local esc_file = vim.fn.shellescape(tmpfile)
  local esc_prompt = vim.fn.shellescape(instruction)
  if tool == "codex" then
    local skip = skip_git_check and "--skip-git-repo-check " or ""
    -- --sandbox read-only for the same reason the claude branch takes its tools
    -- away: nothing here needs to write. It is the flag .claude/hooks/
    -- _bash_review_common.py's _call_codex already uses for its own untrusted
    -- one-shot, so the repo has one answer to this question rather than two.
    --
    -- Weaker than the claude branch, and knowingly so: the sandbox governs
    -- model-run shell commands, not tools a configured MCP server provides --
    -- and an MCP tool is exactly what got through on the claude side. codex has
    -- no --strict-mcp-config equivalent that has been verified here, so the
    -- Stop-hook marker above is the load-bearing guard for codex and this is
    -- the backstop, not the reverse.
    return string.format("cat %s | %s codex exec --sandbox read-only %s%s",
      esc_file, ONESHOT_ENV, skip, esc_prompt)
  elseif tool == "gemini" then
    -- The REST API, not the `gemini` CLI. scripts/gemini_api.py POSTs to
    -- generateContent and is shared with .vim/rc/70-ai.vim, so the retry
    -- policy, the response parsing and the MAX_TOKENS truncation guard exist
    -- once instead of being ported into Lua and VimScript separately.
    --
    -- Still a `cat ... |` pipeline, and that is the point: the helper reads the
    -- payload on stdin exactly as the CLIs do, so it inherits run_cli's job
    -- handling, stderr capture and failure phrasing unchanged. GEMINI_API_KEY
    -- is read by the child out of its own environment -- never passed here,
    -- which is what keeps it off argv (and out of `ps aux`) and off disk.
    --
    -- `--model` appears only when a caller pinned one. Left off, the helper
    -- resolves $GEMINI_MODEL at request time, so the command for the one
    -- feature both editors share -- replace a selection, which pins nothing --
    -- comes out byte-identical to the VimScript port's. Passing a frozen
    -- default here instead would put the two editors on different answers the
    -- moment that variable changed.
    local with_model = model and (" --model " .. vim.fn.shellescape(model)) or ""
    return string.format("cat %s | python3 %s%s --system %s",
      esc_file, vim.fn.shellescape(repo_script("gemini_api.py")),
      with_model, esc_prompt)
  elseif tool == "copilot" then
    -- copilot CLI does not read stdin as context, so inline the payload into the
    -- prompt. `instruction` already carries the task/language context; `-s` keeps
    -- stdout to the agent response only so clean_cli_lines gets usable text.
    --
    -- Deliberate exception to the "payload on stdin, never argv" rule stated at
    -- the top of this file (and in scripts/secret_scan.py / .vim/rc/70-ai.vim):
    -- there is no stdin path for this tool, so the choice is argv or no copilot
    -- at all. The cost is real -- while the job runs, the whole payload is
    -- visible to any process on this machine via `ps aux`, and unlike the
    -- tmpfile the CLI tools use it is not confined to nvim's 0700 temp dir.
    -- The pre-send scan in M.run still runs for copilot (kind = "cli"), so a
    -- credential the scanner RECOGNISES is refused before it reaches argv; what
    -- leaks is what value-scanning cannot see (see the note in M.run). Prefer a
    -- stdin tool for anything sensitive; if copilot ever grows a stdin mode,
    -- move it there and delete this branch.
    local copilot_prompt = string.format("%s\n\n## Input\n```\n%s\n```",
      instruction, input)
    return string.format("copilot --model %s -s -p %s",
      vim.fn.shellescape(model), vim.fn.shellescape(copilot_prompt))
  else -- claude
    -- ツールを一切与えない。ここの呼び出しは全部「テキストを受け取ってテキスト
    -- を返す」だけで、リポジトリを触る必要がない。
    --
    -- 二つのフラグで一組。片方だけでは書き込み経路が残る:
    --   --tools ''            落とせるのは *組み込み* ツールだけ。
    --   --strict-mcp-config   --mcp-config を伴わないので MCP サーバが 0 個になる。
    -- 実測 (claude 2.1.228): `--tools ''` だけを付けた状態で Stop フックの
    -- ブロックを受けた claude が、生き残っていた mcp__serena__replace_content
    -- 経由で作業ツリーのファイルを書き換えた。組み込みだけ塞いでも意味がない。
    --
    -- MCP を落とすのは安全側の副作用も持つ: 一発の生成にサーバ群を起動しない。
    return string.format(
      "cat %s | %s claude --model %s --tools '' --strict-mcp-config -p %s",
      esc_file, ONESHOT_ENV, vim.fn.shellescape(model), esc_prompt)
  end
end

--- Why a CLI run produced no answer, phrased for whoever reads the report.
---
--- The exit code alone is a poor message. It does not say WHICH tool is
--- missing or misconfigured -- a tool that is not installed exits 127 and puts
--- "command not found" on stderr, which is the whole diagnosis -- and for a
--- run that exited 0 with no output it produced "exit code 0", stating a
--- success as the reason for a failure.
---
--- @param exit_code integer
--- @param stderr string[]|nil captured stderr lines
--- @return string
local function cli_failure_reason(exit_code, stderr)
  local detail = vim.trim(table.concat(stderr or {}, "\n"))
  -- Long tracebacks push the useful first line out of view in the report.
  -- Measured and cut in CHARACTERS, not bytes: CLIs localise their errors, and
  -- `:sub` at a byte offset lands mid-character for anything outside ASCII,
  -- emitting invalid UTF-8 into the buffer.
  if vim.fn.strchars(detail) > MAX_STDERR_CHARS then
    detail = vim.fn.strcharpart(detail, 0, MAX_STDERR_CHARS) .. "..."
  end
  if exit_code == 0 then
    -- Reached only when the tool succeeded but printed nothing usable.
    return detail ~= "" and ("empty response: " .. detail) or "empty response"
  end
  if detail ~= "" then
    return string.format("exit code %d: %s", exit_code, detail)
  end
  return string.format("exit code %d", exit_code)
end

--- Why a failed Ollama request failed.
---
--- `err or ("exit code %d"):format(code)` looked right and was unreachable in the
--- branch that mattered: parse_ollama returns a non-nil error for ANY unusable body,
--- and an unreachable server yields an empty body, so the parse error always won and
--- "could not connect" surfaced as "invalid JSON response". Transport first, parse
--- error only once the transport actually succeeded.
---
--- Delegates to cli_failure_reason so both backends phrase a failure identically
--- (stderr folded in, exit 0 never stated as the cause, truncation by characters).
---
--- @param exit_code integer
--- @param stderr string[]|nil captured stderr lines
--- @param parse_err string|nil parse_ollama's complaint about the body
--- @return string
local function ollama_failure_reason(exit_code, stderr, parse_err)
  if exit_code ~= 0 then
    return cli_failure_reason(exit_code, stderr)
  end
  -- Transport fine, body unusable: the parse error is the informative half.
  return parse_err or cli_failure_reason(exit_code, stderr)
end

--- Run a CLI tool: write `input` to a temp file, pipe it into the tool, and
--- return the cleaned stdout lines. Returns the job id (or <=0 on failure).
local function run_cli(tool, model, instruction, input, skip_git_check, done)
  local tmpfile = vim.fn.tempname()
  vim.fn.writefile(vim.split(input, "\n", { plain = true }), tmpfile)
  local cmd = build_cli_cmd(tool, model, instruction, tmpfile, input, skip_git_check)
  -- Refuse rather than let exec fail with a bare E2BIG, which surfaces to the
  -- user as an opaque "exit code 1" with no hint that the SIZE was the problem.
  if #cmd > MAX_CMD_BYTES then
    vim.fn.delete(tmpfile)
    done(false, {}, string.format(
      "payload too large for %s: the command line would be %d KB (limit %d KB). "
      .. "%s inlines the payload into argv; use a stdin-based tool "
      .. "(claude / codex / gemini) or select a smaller range.",
      tool, math.floor(#cmd / 1024), math.floor(MAX_CMD_BYTES / 1024), tool))
    return nil
  end

  local result = {}
  local stderr = {}
  local job_id = vim.fn.jobstart({ "sh", "-c", cmd }, {
    stdout_buffered = true,
    stderr_buffered = true,
    on_stdout = function(_, data)
      if data then
        result = prompt.clean_cli_lines(data)
      end
    end,
    -- Without this the tool's own explanation is discarded and the report can
    -- only quote a number. run_ollama has parse_ollama to fall back on; a CLI
    -- has nothing else to say why it failed.
    on_stderr = function(_, data)
      if data then
        stderr = data
      end
    end,
    on_exit = function(_, exit_code)
      vim.fn.delete(tmpfile)
      vim.schedule(function()
        if exit_code == 0 and #result > 0 then
          done(true, result, nil)
        else
          done(false, {}, cli_failure_reason(exit_code, stderr))
        end
      end)
    end,
  })

  if not job_id or job_id <= 0 then
    vim.fn.delete(tmpfile)
    done(false, {}, "failed to start job")
  end
  return job_id
end

--- Run Ollama via its local HTTP API. `ollama run` writes ANSI control codes
--- onto stdout which corrupts captured text, so we POST to /api/generate with
--- stream=false and parse the JSON. think=false keeps reasoning out of the
--- `.response` field.
local function run_ollama(model, system, input, done)
  local body = vim.json.encode({
    model = model,
    system = system,
    prompt = input,
    stream = false,
    think = false,
  })
  local tmpfile = vim.fn.tempname()
  vim.fn.writefile({ body }, tmpfile)
  local cmd = string.format(
    "curl -s http://localhost:11434/api/generate --data-binary @%s",
    vim.fn.shellescape(tmpfile))

  local stdout = {}
  local stderr = {}
  local job_id = vim.fn.jobstart({ "sh", "-c", cmd }, {
    stdout_buffered = true,
    stderr_buffered = true,
    on_stdout = function(_, data)
      if data then
        stdout = data
      end
    end,
    on_stderr = function(_, data)
      if data then
        stderr = data
      end
    end,
    on_exit = function(_, exit_code)
      vim.fn.delete(tmpfile)
      vim.schedule(function()
        local lines, err = prompt.parse_ollama(table.concat(stdout, "\n"))
        if exit_code == 0 and lines and #lines > 0 then
          done(true, lines, nil)
        else
          done(false, {}, ollama_failure_reason(exit_code, stderr, err))
        end
      end)
    end,
  })

  if not job_id or job_id <= 0 then
    vim.fn.delete(tmpfile)
    done(false, {}, "failed to start job")
  end
  return job_id
end

--- Run an AI request.
--- @param spec table { tool, prompt (instruction), input (stdin string), model?, skip_git_check? }
--- @param done fun(ok: boolean, lines: string[], err: string|nil)
--- @return integer|nil job_id usable with vim.fn.jobstop (nil/<=0 on failure)
--- `_skip_scan` is set by run_with_fallback, which scans the shared payload once
--- up front so a fallback attempt does not re-prompt.
function M.run(spec, done, _skip_scan)
  local def = TOOLS[spec.tool]
  if not def then
    done(false, {}, "unknown tool: " .. tostring(spec.tool))
    return nil
  end
  -- Pre-send credential gate (single choke point for every AI request).
  -- Value-only, like the bash-review hooks: it scans the payload text for raw
  -- credential VALUES, not the source file's path. A secret that value-scanning
  -- cannot see (e.g. base64 `client-key-data` in a kubeconfig, or an opaque
  -- token) can still slip through here; a path-based backstop keyed on the
  -- buffer name would be the next layer if that gap matters.
  --
  -- Local Ollama (hardcoded to http://localhost:11434) never leaves the machine,
  -- so the *external*-send gate does not apply -- prompting there is friction
  -- with no matching benefit. Every other kind is scanned, "api" included:
  -- gemini reaching Google directly rather than through its CLI does not make
  -- the payload any less external, and an exemption written as
  -- `kind ~= "cli"` would have quietly granted it one.
  if not _skip_scan and def.kind ~= "ollama"
    and not confirm_send((spec.input or "") .. "\n" .. (spec.prompt or "")) then
    done(false, {}, "credential detected in payload; not sent to AI")
    return nil
  end
  local model = spec.model or def.default_model
  if def.kind == "ollama" then
    return run_ollama(model, spec.prompt, spec.input, done)
  end
  return run_cli(spec.tool, model, spec.prompt, spec.input, spec.skip_git_check, done)
end

--- Run a request with ordered fallbacks: try each spec in turn, stopping at the
--- first success. On success `done` fires with that spec's tool; if every spec
--- fails it fires with the last error and the last tool tried.
---
--- Returns a mutable handle `{ job = <id> }` instead of a plain job id: `.job`
--- is updated to whichever attempt is currently in flight, so a canceller that
--- holds onto the handle (see ui.lua's resolve_job) can still stop a fallback
--- attempt after the first one has already failed -- a plain job id would go
--- stale the moment the fallback starts and jobstop on it would be a no-op.
--- @param specs table[] list of run specs (see M.run), tried in order
--- @param done fun(ok: boolean, lines: string[], err: string|nil, tool: string|nil)
--- @return table|nil handle { job: integer|nil } tracking the in-flight attempt
function M.run_with_fallback(specs, done)
  if not specs or #specs == 0 then
    done(false, {}, "no tools specified", nil)
    return nil
  end
  -- Scan once over every spec's payload (fallbacks reuse the same input/prompt,
  -- differing only by tool), then skip the per-attempt scan so a fallback that
  -- fires on a later tick does not re-prompt.
  --
  -- Only the payloads actually bound for a cloud CLI are collected. M.run
  -- exempts Ollama because it hits localhost and never leaves the machine;
  -- gathering every spec unconditionally here would re-impose the prompt on an
  -- all-Ollama list and make the two entry points disagree about the same
  -- policy. When nothing external is left, skip the gate entirely.
  local parts = {}
  for _, s in ipairs(specs) do
    local def = TOOLS[s.tool]
    if not def or def.kind ~= "ollama" then
      parts[#parts + 1] = (s.input or "") .. "\n" .. (s.prompt or "")
    end
  end
  if #parts > 0 and not confirm_send(table.concat(parts, "\n")) then
    done(false, {}, "credential detected in payload; not sent to AI", nil)
    return nil
  end
  local handle = { job = nil }
  -- Every attempt's reason is kept, not just the last one. Reporting only the
  -- final failure was survivable while gemini was a CLI that usually worked;
  -- it stopped being so once gemini can fail for a reason of its own that has
  -- nothing to do with the request. With GEMINI_API_KEY unset -- the normal
  -- state of a GUI-launched editor -- EVERY claude outage came out as
  -- "exit code 2: GEMINI_API_KEY is not set", discarding what the tool the user
  -- actually asked for had said and pointing the reader at the wrong problem.
  local failures = {}
  local function attempt(i)
    local spec = specs[i]
    handle.job = M.run(spec, function(ok, lines, err)
      if ok then
        done(true, lines, nil, spec.tool)
        return
      end
      failures[#failures + 1] = { tool = spec.tool, err = err or "failed" }
      if specs[i + 1] then
        attempt(i + 1)
        return
      end
      local detail
      if #failures == 1 then
        -- A one-step chain is a request with nothing to fall back to, and the
        -- UI already names the tool: prefixing it here would render as
        -- "[gemini failed: gemini: exit code 2: ...]".
        detail = failures[1].err
      else
        local parts = {}
        for j, f in ipairs(failures) do
          parts[j] = string.format("%s: %s", f.tool, f.err)
        end
        detail = table.concat(parts, " | ")
      end
      done(false, {}, detail, spec.tool)
    end, true)
  end
  attempt(1)
  return handle
end

-- Test seam. These two are private -- nothing outside tests/ may read this --
-- but they carry the invariants worth pinning: the pre-send credential gate and
-- the shape of the argv handed to each tool. Reaching them through
-- debug.getupvalue instead would couple the tests to this file's call graph,
-- and a source-level splice would load the module differently from production;
-- a plain table costs four lines and gets checked for free, because a rename of
-- either local turns the reference below into an undefined global that the
-- gating luacheck job reports (W113).
--
-- MAX_CMD_BYTES is deliberately NOT exposed: the limit is observable in the
-- refusal message run_cli produces, and a test that hardcodes 256 KB is
-- supposed to fail when the bound moves.
M._internal = {
  scan_payload = scan_payload,
  build_cli_cmd = build_cli_cmd,
  cli_failure_reason = cli_failure_reason,
  ollama_failure_reason = ollama_failure_reason,
  -- Where the shared python helpers are looked up. Load-bearing for the API
  -- path and not observable from outside: it decides whether gemini_api.py is
  -- found at all. (gemini_model is not here -- it is public on M, because
  -- ai/init.lua needs it for report headers.)
  repo_script = repo_script,
}

return M
