---------------------------------------------------------
-- ai.prompt
-- Pure-ish helpers: prompt builders, diagnostic formatting, and output
-- post-processing. No floating windows or jobs live here so the logic stays
-- easy to reason about (and testable) in isolation.
---------------------------------------------------------
local M = {}

-- Sentinel the buffer-check model must emit when it finds nothing. Shared so the
-- check prompt and the "fix" guard agree on the exact string.
M.NO_ISSUES = "問題は見つかりませんでした。"

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

--- Prefix each line with its 1-based line number and a "│" separator so an AI
--- reviewer can cite accurate line numbers. The prefix is NOT valid source; the
--- accompanying prompt tells the model to ignore it. The caller's list is not
--- mutated.
--- @param lines string[]
--- @return string numbered lines joined by "\n"
function M.number_lines(lines)
  local width = #tostring(#lines)
  local fmt = "%" .. width .. "d │ %s"
  local out = {}
  for i, line in ipairs(lines) do
    out[i] = string.format(fmt, i, line)
  end
  return table.concat(out, "\n")
end

--- System prompt for "check the current buffer for typos / syntax errors".
--- The buffer content is sent via stdin with M.number_lines line prefixes.
--- @param lang string filetype (or "plain text")
--- @param filepath string relative path, shown for context
--- @return string
function M.check_buffer_system(lang, filepath)
  return string.format(
    "You are a meticulous reviewer integrated into a Neovim editor. "
    .. "The full contents of a %s buffer (%s) are provided via stdin. "
    .. "Every line is prefixed with its line number and a '│' separator; "
    .. "that prefix is NOT part of the file -- ignore it and never report it. "
    .. "Inspect the buffer for typos, misspellings (in identifiers, comments, and "
    .. "strings), syntax errors, and other obvious mistakes. "
    .. "Reply in Japanese, in Markdown only, with no preamble. "
    .. "List each issue as a bullet of the form `- L<n>: <問題の説明> -> <修正案>`, "
    .. "ordered by line number. "
    .. "If you find no problems, reply with exactly: `" .. M.NO_ISSUES .. "`",
    lang, filepath)
end

--- System prompt for "fix the issues found in the buffer" via STRUCTURED EDITS.
--- Sent alongside the check report (the issue list) and the current file with
--- every line prefixed by its number (see M.number_lines). The model must return
--- ONLY the changed regions as a JSON array, so generation stays small even for
--- large files (the slow part of a fix is regenerating unchanged lines). Each
--- edit carries its `original` lines so M.apply_edits can verify the range before
--- touching the buffer.
--- @param lang string filetype (or "plain text")
--- @param filepath string relative path, shown for context
--- @return string
function M.fix_buffer_system(lang, filepath)
  return string.format(
    "You are an AI assistant integrated into a Neovim editor. "
    .. "A reviewer checked a %s buffer (%s) and listed issues (typos, misspellings, "
    .. "syntax errors, and other obvious mistakes). Your stdin contains that issue list, "
    .. "then the complete file where every line is prefixed with its line number and a "
    .. "'│' separator. That prefix is NOT part of the file: use it only to locate lines "
    .. "and never include it in your output.\n\n"
    .. "Return ONLY the minimal set of edits that fix the listed issues, as a JSON array "
    .. "(no markdown code fences, no prose). Each element must be:\n"
    .. '  {"start": <first line, 1-based>, "end": <last line, inclusive>, '
    .. '"original": [<the exact current lines start..end, WITHOUT the number prefix>], '
    .. '"fixed": [<the replacement lines>]}\n'
    .. "Rules:\n"
    .. "- `original` MUST exactly match the current buffer lines in that range so the "
    .. "editor can verify the edit before applying it.\n"
    .. "- Ranges MUST NOT overlap; merge adjacent or related changes into a single edit, "
    .. "even when the fix spans multiple lines.\n"
    .. "- `fixed` may contain a different number of lines than `original`.\n"
    .. "- Do NOT wrap the output in code fences. If there is nothing to fix, reply with "
    .. "exactly: []",
    lang, filepath)
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

--- Parse the JSON edit array produced by M.fix_buffer_system. Tolerates a
--- surrounding markdown code fence. Each edit is normalised to
--- { start, stop, original, fixed } ('end' is a Lua keyword, hence 'stop').
--- Structural validation (ranges, line matching) is left to M.apply_edits; an
--- empty array decodes to an empty list (caller treats that as "nothing to fix").
--- @param raw string the model's raw stdout
--- @return table[]|nil edits, string|nil err
function M.parse_edits(raw)
  local text = vim.trim(raw or "")
  -- Strip a leading ```lang / trailing ``` fence if the model added one.
  text = text:gsub("^```%w*[ \t]*\r?\n", ""):gsub("\r?\n```%s*$", "")
  text = vim.trim(text)
  if text == "" then
    return nil, "empty response"
  end
  local ok, decoded = pcall(vim.json.decode, text)
  if not ok or type(decoded) ~= "table" then
    return nil, "invalid JSON"
  end
  local edits = {}
  for _, e in ipairs(decoded) do
    if type(e) == "table" then
      edits[#edits + 1] = {
        start = e.start,
        stop = e["end"],
        original = e.original,
        fixed = e.fixed,
      }
    end
  end
  return edits, nil
end

--- Apply structured edits (from M.parse_edits) to `lines`, returning a brand-new
--- line array. Edits are validated and sorted by start; an edit is SKIPPED
--- (never misapplied) when it is malformed, out of range, overlaps an
--- already-applied edit, or its `original` does not match the buffer. Because the
--- result is built left-to-right into a fresh array, multi-line edits and edits
--- that change the line count cannot shift the positions of later edits. The
--- caller's `lines` is not mutated.
--- @param lines string[] current buffer lines (snapshot)
--- @param edits table[] normalised edits { start, stop, original, fixed }
--- @return string[] patched, integer applied, table[] skipped (each { edit, reason })
function M.apply_edits(lines, edits)
  local sorted = {}
  for i = 1, #edits do
    sorted[i] = edits[i]
  end
  table.sort(sorted, function(a, b)
    return (a.start or 0) < (b.start or 0)
  end)

  local function is_int(n)
    return type(n) == "number" and n == math.floor(n)
  end

  local out, skipped, applied = {}, {}, 0
  local cursor = 1 -- next original line to copy through unchanged
  for _, e in ipairs(sorted) do
    local reason
    if not (is_int(e.start) and is_int(e.stop)) then
      reason = "non-integer range"
    elseif e.start < 1 or e.stop < e.start or e.stop > #lines then
      reason = "range out of bounds"
    elseif type(e.original) ~= "table" or type(e.fixed) ~= "table" then
      reason = "missing original/fixed"
    elseif #e.original ~= (e.stop - e.start + 1) then
      reason = "original length mismatch"
    elseif e.start < cursor then
      reason = "overlapping range"
    else
      for i = 1, #e.original do
        if lines[e.start + i - 1] ~= e.original[i] then
          reason = "original does not match buffer"
          break
        end
      end
    end

    if reason then
      skipped[#skipped + 1] = { edit = e, reason = reason }
    else
      for i = cursor, e.start - 1 do
        out[#out + 1] = lines[i]
      end
      for i = 1, #e.fixed do
        out[#out + 1] = e.fixed[i]
      end
      cursor = e.stop + 1
      applied = applied + 1
    end
  end
  for i = cursor, #lines do
    out[#out + 1] = lines[i]
  end
  return out, applied, skipped
end

return M
