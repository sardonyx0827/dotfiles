--- @diagnostic disable: undefined-global
---------------------------------------------------------
-- ai.prompt
-- Pure-ish helpers: prompt builders, diagnostic formatting, and output
-- post-processing. No floating windows or jobs live here so the logic stays
-- easy to reason about (and testable) in isolation.
---------------------------------------------------------
local M = {}

local SEVERITY = {
  [vim.diagnostic.severity.ERROR] = "ERROR",
  [vim.diagnostic.severity.WARN] = "WARN",
  [vim.diagnostic.severity.INFO] = "INFO",
  [vim.diagnostic.severity.HINT] = "HINT",
}

--- Format a diagnostic list into "[SEV] msg @file :Ln:Cs-Ce" lines.
--- Diagnostics are sorted by line number; the caller's list is not mutated.
--- @param diags table[] result of vim.diagnostic.get()
--- @param filepath string relative path shown in each line
--- @return string[] one formatted line per diagnostic
function M.format_diagnostics(diags, filepath)
  local sorted = {}
  for i = 1, #diags do
    sorted[i] = diags[i]
  end
  table.sort(sorted, function(a, b)
    return a.lnum < b.lnum
  end)

  local lines = {}
  for _, d in ipairs(sorted) do
    local severity = SEVERITY[d.severity] or "UNKNOWN"
    local line = d.lnum + 1
    local col_start = d.col + 1
    local col_end = d.end_col and (d.end_col + 1) or col_start
    table.insert(lines, string.format("[%s] %s @%s :L%d:C%d-C%d",
      severity, d.message, filepath, line, col_start, col_end))
  end
  return lines
end

--- Instruction for generating a git commit message from a diff.
--- @return string
function M.commit_instruction()
  return "Generate a git commit message for the following diff. "
      .. "Follow Conventional Commits format (e.g. feat:, fix:, refactor:, docs:, test:, chore:). "
      .. "Reply ONLY with the commit message, no markdown formatting, no explanation, no surrounding quotes. "
      .. "Keep the summary line under 50 characters. Add a body separated by a blank line if the change is complex. "
      .. "Write in English."
end

--- Bound a diff to a safe size for commit-message generation. When `full`
--- exceeds `max_bytes`, return the `stat` summary (file list + churn) followed
--- by the leading slice of the patch, clearly marked as truncated; otherwise
--- return `full` unchanged. This keeps the payload under the model's context
--- window and under ARG_MAX for tools that inline the diff into argv (copilot).
--- @param full string output of `git diff [--cached]`
--- @param stat string output of `git diff [--cached] --stat`
--- @param max_bytes integer size threshold in bytes
--- @return string
function M.bound_commit_diff(full, stat, max_bytes)
  if #full <= max_bytes then
    return full
  end
  local patch = string.sub(full, 1, max_bytes)
  return string.format(
    "[Diff truncated because it is large (full patch is %d KB). Base the commit "
    .. "message on the file summary plus the partial patch below.]\n\n"
    .. "## Changed files (git diff --stat)\n%s\n"
    .. "## Partial diff (first %d KB)\n%s\n... (patch truncated) ...\n",
    math.floor(#full / 1024),
    stat,
    math.floor(max_bytes / 1024),
    patch)
end

--- System prompt for "ask AI and replace the selection".
--- @param lang string filetype (or "plain text")
--- @param user_request string the user's instruction
--- @return string
function M.replace_system(lang, user_request)
  return string.format(
    "You are an AI assistant integrated into a Neovim editor. "
    .. "The selected %s code/text is provided via stdin. "
    .. "Apply the user's request and reply ONLY with the resulting text that should replace the selection. "
    .. "Do NOT wrap the output in markdown code fences. "
    .. "Do NOT include explanations, preambles, or trailing commentary. "
    .. "Preserve the original indentation style of the input.\n\n"
    .. "## User Request\n%s",
    lang,
    user_request
  )
end

--- Normalise the line array delivered by a buffered stdout job: drop the
--- trailing empty string Neovim appends when output ends with a newline.
--- The caller's array is not mutated.
--- @param data string[]|nil
--- @return string[]
function M.clean_cli_lines(data)
  if not data then
    return {}
  end
  local out = {}
  for i = 1, #data do
    out[i] = data[i]
  end
  if #out > 0 and out[#out] == "" then
    table.remove(out)
  end
  return out
end

--- Parse the JSON object returned by the Ollama /api/generate endpoint.
--- @param raw string raw stdout
--- @return string[]|nil lines, string|nil err
function M.parse_ollama(raw)
  local ok, decoded = pcall(vim.json.decode, raw)
  if not ok or type(decoded) ~= "table" then
    return nil, "invalid JSON response"
  end
  if type(decoded.response) == "string" then
    local trimmed = vim.trim(decoded.response)
    if trimmed ~= "" then
      return vim.split(trimmed, "\n", { plain = true }), nil
    end
  end
  if decoded.error then
    return nil, tostring(decoded.error)
  end
  return nil, "empty response"
end

return M
