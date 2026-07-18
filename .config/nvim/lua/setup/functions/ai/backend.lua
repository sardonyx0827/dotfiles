---------------------------------------------------------
-- ai.backend
-- Unified AI invocation. Knows how to run each tool (CLI tools over stdin,
-- Ollama over its local HTTP API), manages the temp file and job lifecycle,
-- and reports the result through a single `done(ok, lines, err)` callback.
-- The UI layer never talks to a tool directly; it only drives `run`.
---------------------------------------------------------
local prompt = require("setup.functions.ai.prompt")

local M = {}

-- Tool registry. `kind` selects the transport; `default_model` is used when the
-- caller does not pass an explicit model in the spec.
local TOOLS = {
  claude  = { kind = "cli", default_model = "sonnet" },
  codex   = { kind = "cli", default_model = nil },
  gemini  = { kind = "cli", default_model = "gemini-flash-lite-latest" },
  copilot = { kind = "cli", default_model = "gpt-5-mini" },
  gemma   = { kind = "ollama", default_model = "gemma4:e4b" },
}

M.TOOLS = TOOLS

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

-- Locate scripts/secret_scan.py relative to this (symlinked) config dir.
-- ~/.config/nvim is a symlink into the dotfiles repo; resolve it and step up to
-- the repo root. Returns nil when not found (treated as unavailable -> fail-open).
local function secret_scanner_path()
  local cfg = vim.fn.resolve(vim.fn.stdpath("config"))
  local scanner = vim.fn.fnamemodify(cfg, ":h:h") .. "/scripts/secret_scan.py"
  if vim.fn.filereadable(scanner) == 1 then
    return scanner
  end
  return nil
end

-- Scan `text`. Returns "clean" | "secret",<label> | "unavailable".
local function scan_payload(text)
  local scanner = secret_scanner_path()
  -- Fail OPEN when python3 is absent (e.g. a GUI-launched nvim that did not
  -- inherit the shell's PATH). executable() must be checked FIRST: vim.fn.system
  -- with a LIST arg raises E475 (not a v:shell_error) when the binary is
  -- missing, so a bare call would throw past this guard instead of degrading to
  -- "unavailable". pcall wraps the call as a further backstop.
  if not scanner or vim.fn.executable("python3") ~= 1 then
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

--- Build the shell command for a CLI tool. Most tools read the payload from
--- `tmpfile` over stdin; copilot inlines `input` into the prompt instead.
--- `skip_git_check` adds codex's --skip-git-repo-check (used by the replace
--- feature so codex works outside a repo; commit generation leaves it off).
local function build_cli_cmd(tool, model, instruction, tmpfile, input, skip_git_check)
  local esc_file = vim.fn.shellescape(tmpfile)
  local esc_prompt = vim.fn.shellescape(instruction)
  if tool == "codex" then
    local skip = skip_git_check and "--skip-git-repo-check " or ""
    return string.format("cat %s | codex exec %s%s",
      esc_file, skip, esc_prompt)
  elseif tool == "gemini" then
    return string.format("cat %s | gemini -m %s -p %s",
      esc_file, vim.fn.shellescape(model), esc_prompt)
  elseif tool == "copilot" then
    -- copilot CLI does not read stdin as context, so inline the payload into the
    -- prompt. `instruction` already carries the task/language context; `-s` keeps
    -- stdout to the agent response only so clean_cli_lines gets usable text.
    local copilot_prompt = string.format("%s\n\n## Input\n```\n%s\n```",
      instruction, input)
    return string.format("copilot --model %s -s -p %s",
      vim.fn.shellescape(model), vim.fn.shellescape(copilot_prompt))
  else -- claude
    return string.format("cat %s | claude --model %s -p %s",
      esc_file, vim.fn.shellescape(model), esc_prompt)
  end
end

--- Run a CLI tool: write `input` to a temp file, pipe it into the tool, and
--- return the cleaned stdout lines. Returns the job id (or <=0 on failure).
local function run_cli(tool, model, instruction, input, skip_git_check, done)
  local tmpfile = vim.fn.tempname()
  vim.fn.writefile(vim.split(input, "\n", { plain = true }), tmpfile)
  local cmd = build_cli_cmd(tool, model, instruction, tmpfile, input, skip_git_check)

  local result = {}
  local job_id = vim.fn.jobstart({ "sh", "-c", cmd }, {
    stdout_buffered = true,
    on_stdout = function(_, data)
      if data then
        result = prompt.clean_cli_lines(data)
      end
    end,
    on_exit = function(_, exit_code)
      vim.fn.delete(tmpfile)
      vim.schedule(function()
        if exit_code == 0 and #result > 0 then
          done(true, result, nil)
        else
          done(false, {}, string.format("exit code %d", exit_code))
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
  local job_id = vim.fn.jobstart({ "sh", "-c", cmd }, {
    stdout_buffered = true,
    on_stdout = function(_, data)
      if data then
        stdout = data
      end
    end,
    on_exit = function(_, exit_code)
      vim.fn.delete(tmpfile)
      vim.schedule(function()
        local lines, err = prompt.parse_ollama(table.concat(stdout, "\n"))
        if exit_code == 0 and lines and #lines > 0 then
          done(true, lines, nil)
        else
          done(false, {}, err or string.format("exit code %d", exit_code))
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
  -- with no matching benefit. Only the cloud CLIs are scanned. (Re-enable this
  -- if run_ollama is ever pointed at a remote host.)
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
--- The returned job id is the FIRST attempt's job (what the UI uses to cancel).
--- A fallback started after the first failure is not exposed for cancellation,
--- which only matters in the brief window between a failure and the next result.
--- @param specs table[] list of run specs (see M.run), tried in order
--- @param done fun(ok: boolean, lines: string[], err: string|nil, tool: string|nil)
--- @return integer|nil job_id of the first attempt
function M.run_with_fallback(specs, done)
  if not specs or #specs == 0 then
    done(false, {}, "no tools specified", nil)
    return nil
  end
  -- Scan once over every spec's payload (fallbacks reuse the same input/prompt,
  -- differing only by tool), then skip the per-attempt scan so a fallback that
  -- fires on a later tick does not re-prompt.
  local parts = {}
  for _, s in ipairs(specs) do
    parts[#parts + 1] = (s.input or "") .. "\n" .. (s.prompt or "")
  end
  if not confirm_send(table.concat(parts, "\n")) then
    done(false, {}, "credential detected in payload; not sent to AI", nil)
    return nil
  end
  local function attempt(i)
    local spec = specs[i]
    return M.run(spec, function(ok, lines, err)
      if ok then
        done(true, lines, nil, spec.tool)
      elseif specs[i + 1] then
        attempt(i + 1)
      else
        done(false, {}, err, spec.tool)
      end
    end, true)
  end
  return attempt(1)
end

return M
