--- @diagnostic disable: undefined-global
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
  claude = { kind = "cli", default_model = "sonnet" },
  codex  = { kind = "cli", default_model = nil },
  gemini = { kind = "cli", default_model = "gemini-flash-lite-latest" },
  gemma  = { kind = "ollama", default_model = "gemma4:e4b" },
}

M.TOOLS = TOOLS

--- Build the shell command for a CLI tool reading the payload from `tmpfile`.
--- `skip_git_check` adds codex's --skip-git-repo-check (used by the replace
--- feature so codex works outside a repo; commit generation leaves it off).
local function build_cli_cmd(tool, model, instruction, tmpfile, skip_git_check)
  local esc_file = vim.fn.shellescape(tmpfile)
  local esc_prompt = vim.fn.shellescape(instruction)
  if tool == "codex" then
    local skip = skip_git_check and "--skip-git-repo-check " or ""
    return string.format("cat %s | codex exec %s%s",
      esc_file, skip, esc_prompt)
  elseif tool == "gemini" then
    return string.format("cat %s | gemini -m %s -p %s",
      esc_file, vim.fn.shellescape(model), esc_prompt)
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
  local cmd = build_cli_cmd(tool, model, instruction, tmpfile, skip_git_check)

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
function M.run(spec, done)
  local def = TOOLS[spec.tool]
  if not def then
    done(false, {}, "unknown tool: " .. tostring(spec.tool))
    return nil
  end
  local model = spec.model or def.default_model
  if def.kind == "ollama" then
    return run_ollama(model, spec.prompt, spec.input, done)
  end
  return run_cli(spec.tool, model, spec.prompt, spec.input, spec.skip_git_check, done)
end

return M
