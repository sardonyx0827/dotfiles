--- @diagnostic disable: undefined-global
---------------------------------------------------------
-- ai (entry point)
-- Wires the AI features to keymaps. The heavy lifting lives in the sibling
-- modules:
--   ai.prompt   - prompt builders & diagnostic formatting (pure helpers)
--   ai.backend  - tool invocation (CLI / Ollama), job & temp-file management
--   ai.ui       - the shared floating-window driver (popup / diff)
-- A feature here is just "build a prompt + pick the input + choose the UI".
---------------------------------------------------------
local backend = require("setup.functions.ai.backend")
local prompt = require("setup.functions.ai.prompt")
local ui = require("setup.functions.ai.ui")

local map = vim.keymap.set

-- Commit messages favour cheap/fast models; everything else uses backend
-- defaults (claude=sonnet, gemini=flash-lite, codex=default, gemma=ollama).
local COMMIT_MODELS = { claude = "haiku" }

-- Upper bound on the diff sent for commit generation. Past this we fall back to
-- `git diff --stat` plus a truncated patch (see commit_diff), which keeps the
-- payload under the model's context window and under ARG_MAX for tools that
-- inline the diff into argv (copilot). Without this, staging many files errors.
local MAX_DIFF_BYTES = 50 * 1024

-- Copy to the system clipboard, the unnamed register, and the tmux buffer.
local function copy_to_clipboard(content)
  vim.fn.setreg("+", content)
  vim.fn.setreg('"', content)
  if vim.env.TMUX then
    vim.fn.system("tmux load-buffer -", content)
  end
end

---------------------------------------------------------
-- Copy LSP diagnostics to clipboard for AI assistance
---------------------------------------------------------
local function copy_lsp_diagnostics()
  local diagnostics = vim.diagnostic.get(0)
  if #diagnostics == 0 then
    print("No LSP diagnostics found.")
    return
  end
  local filepath = vim.fn.expand("%:.")
  local lines = { "Can you help me fix the diagnostics in @" .. filepath .. "?" }
  vim.list_extend(lines, prompt.format_diagnostics(diagnostics, filepath))
  copy_to_clipboard(table.concat(lines, "\n"))
  print("Copied LSP diagnostics to clipboard.")
end
map("n", "<leader><leader>d", copy_lsp_diagnostics,
  { desc = "Copy LSP diagnostics to clipboard", noremap = true })

---------------------------------------------------------
-- Copy all LSP diagnostics (every loaded buffer) to clipboard
---------------------------------------------------------
local function copy_all_lsp_diagnostics()
  local lines = { "Can you help me fix the following diagnostics in my project?" }
  for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(bufnr) and vim.api.nvim_buf_is_loaded(bufnr) then
      local filepath = vim.api.nvim_buf_get_name(bufnr)
      if filepath ~= "" then
        local diagnostics = vim.diagnostic.get(bufnr)
        if #diagnostics > 0 then
          local relative_path = vim.fn.fnamemodify(filepath, ":.")
          vim.list_extend(lines, prompt.format_diagnostics(diagnostics, relative_path))
        end
      end
    end
  end
  if #lines > 1 then
    copy_to_clipboard(table.concat(lines, "\n"))
    print("Copied all LSP diagnostics to clipboard.")
  else
    print("No LSP diagnostics found.")
  end
end
map("n", "<leader><leader>a", copy_all_lsp_diagnostics,
  { desc = "Copy all LSP diagnostics to clipboard", noremap = true })

---------------------------------------------------------
-- Check the current buffer for typos / syntax errors (claude -> gemini)
---------------------------------------------------------
local CHECK_MODELS = { claude = "sonnet", gemini = "gemini-flash-lite-latest" }
-- Same tiers as the check: claude first, gemini as the fallback.
local FIX_MODELS = { claude = "sonnet", gemini = "gemini-flash-lite-latest" }

local function check_current_buffer()
  local buf = vim.api.nvim_get_current_buf()
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  if #lines == 0 or (#lines == 1 and lines[1] == "") then
    vim.notify("Buffer is empty.", vim.log.levels.WARN)
    return
  end

  local filetype = vim.bo[buf].filetype
  local lang = filetype ~= "" and filetype or "plain text"
  local filepath = vim.fn.expand("%:.")
  if filepath == "" then filepath = "[No Name]" end
  local system = prompt.check_buffer_system(lang, filepath)
  local input = prompt.number_lines(lines)

  vim.notify("Checking buffer with Claude...", vim.log.levels.INFO)

  -- Overwrite the whole checked buffer with the AI's corrected version.
  local function apply_fix(fixed)
    if not vim.api.nvim_buf_is_valid(buf) then
      vim.notify("Target buffer no longer valid.", vim.log.levels.ERROR)
      return false
    end
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, fixed)
    return true
  end

  -- `f` in the report: feed the issue list + current buffer to the AI, show the
  -- fix as a diff against the original, and replace the buffer on accept.
  local function start_fix(report_lines)
    local report = vim.trim(table.concat(report_lines, "\n"))
    if report == "" or report:find(prompt.NO_ISSUES, 1, true) then
      vim.notify("No issues to fix.", vim.log.levels.INFO)
      return
    end

    local fix_system = prompt.fix_buffer_system(lang, filepath)
    -- Numbered source (same "N │" form as the check) so the model's edit ranges
    -- line up with the issue list's L<n> references.
    local fix_input = table.concat({
      "## Issues found",
      report,
      "",
      "## Current file content (each line: <number> │ <text>)",
      prompt.number_lines(lines),
    }, "\n")

    vim.notify("Fixing buffer with Claude...", vim.log.levels.INFO)

    ui.run_multi({
      mode = "diff",
      tools = { "claude" },
      filetype = filetype,
      original = lines,
      footer = " y:apply fix  Y:merged  q:cancel ",
      start = function(_, done)
        -- claude first; on error fall back to gemini (same order as the check).
        return backend.run_with_fallback({
          { tool = "claude", prompt = fix_system, input = fix_input, model = FIX_MODELS.claude },
          { tool = "gemini", prompt = fix_system, input = fix_input, model = FIX_MODELS.gemini },
        }, function(ok, result, err, tool)
          if not ok then
            done(false, {}, err)
            return
          end
          if tool ~= "claude" then
            vim.notify("Claude failed; fell back to " .. tool .. ".", vim.log.levels.WARN)
          end
          -- The model returns only the changed regions; verify each against the
          -- snapshot and splice locally into the full patched buffer for the diff.
          local edits, perr = prompt.parse_edits(table.concat(result, "\n"))
          if not edits then
            done(false, {}, "修正の解析に失敗しました: " .. perr)
            return
          end
          if #edits == 0 then
            done(false, {}, "適用できる修正がありませんでした。")
            return
          end
          local patched, applied, skipped = prompt.apply_edits(lines, edits)
          if applied == 0 then
            done(false, {}, "修正を安全に適用できませんでした（行が一致しません）。")
            return
          end
          if #skipped > 0 then
            vim.notify(
              string.format("%d件の修正をスキップしました（行が一致せず安全に適用できません）。", #skipped),
              vim.log.levels.WARN)
          end
          done(true, patched, nil)
        end)
      end,
      on_accept = function(_, fixed)
        if apply_fix(fixed) then
          vim.notify("Buffer fixed with AI's response.")
        end
      end,
      on_accept_merged = function(fixed)
        if apply_fix(fixed) then
          vim.notify("Buffer fixed with merged result.")
        end
      end,
    })
  end

  -- Report opens in a vertical split on the right with wrap on (`tw` toggles it).
  ui.open_report({
    name = "[AI Buffer Check]",
    filetype = "markdown",
    winbar = " AI Buffer Check    y:yank  f:fix  tw:wrap  q:close ",
    copy_notify = "Buffer check copied to clipboard.",
    keymaps = {
      {
        key = "f",
        desc = "Fix issues with AI (diff + replace)",
        fn = function(ctx)
          if ctx.status ~= "done" then
            vim.notify("Buffer check is not ready yet.", vim.log.levels.WARN)
            return
          end
          -- Snapshot the report, close the split, then drive the diff UI.
          local report_lines = vim.api.nvim_buf_get_lines(ctx.buf, 0, -1, false)
          ctx.close()
          start_fix(report_lines)
        end,
      },
    },
    start = function(done)
      -- claude first; on error fall back to gemini (see backend.run_with_fallback).
      return backend.run_with_fallback({
        { tool = "claude", prompt = system, input = input, model = CHECK_MODELS.claude },
        { tool = "gemini", prompt = system, input = input, model = CHECK_MODELS.gemini },
      }, function(ok, result, err, tool)
        if not ok then
          done(false, {}, err)
          return
        end
        if tool ~= "claude" then
          vim.notify("Claude failed; fell back to " .. tool .. ".", vim.log.levels.WARN)
        end
        -- Prepend a source line so it is clear which tool/model answered.
        local out = {
          string.format("> Checked with **%s** (%s)", tool, CHECK_MODELS[tool] or "default"),
          "",
        }
        vim.list_extend(out, result)
        done(true, out, nil)
      end)
    end,
  })
end
map("n", "<leader><leader>k", check_current_buffer,
  { desc = "Check current buffer for typos/syntax errors (AI)", noremap = true })

---------------------------------------------------------
-- Copy file + line reference from a visual selection
---------------------------------------------------------
local function get_file_line_info_visual(start_line, end_line)
  if not start_line or not end_line or start_line == 0 or end_line == 0 then
    print("No visual selection found.")
    return
  end
  if start_line > end_line then
    start_line, end_line = end_line, start_line
  end
  local filepath = vim.fn.expand("%:.")
  local content
  if start_line == end_line then
    content = string.format("@%s#L%d", filepath, start_line)
  else
    content = string.format("@%s#L%d-%d", filepath, start_line, end_line)
  end
  copy_to_clipboard(content)
  print("Copied file and line info to clipboard.")
end
map("x", "<leader><leader>c", function()
  -- Exit visual mode so '< and '> reflect the just-completed selection
  vim.cmd("normal! \27")
  get_file_line_info_visual(vim.fn.line("'<"), vim.fn.line("'>"))
end, { desc = "Get file and line info from visual selection", noremap = true, silent = true })

---------------------------------------------------------
-- Close current buffer
---------------------------------------------------------
local function close_current_buffer()
  local current_buf = vim.api.nvim_get_current_buf()
  if vim.api.nvim_buf_is_loaded(current_buf) then
    vim.api.nvim_buf_delete(current_buf, { force = true })
  end
end
map("n", "<C-q>", close_current_buffer,
  { noremap = true, silent = true, desc = "Close Current Buffer" })
map("n", "<leader>bc", close_current_buffer,
  { noremap = true, silent = true, desc = "Close Current Buffer" })

---------------------------------------------------------
-- Generate a commit message (claude / codex / gemini / all / gemma)
---------------------------------------------------------
-- Fetch the diff for commit generation, bounded to MAX_DIFF_BYTES. `cached` is
-- the extra `git diff` flag ("--cached " for staged, "" for unstaged). Only when
-- the full diff is large do we make the extra `--stat` call.
local function commit_diff(cached)
  local full = vim.fn.system("git diff " .. cached)
  if vim.v.shell_error ~= 0 or #full <= MAX_DIFF_BYTES then
    return full
  end
  local stat = vim.fn.system("git diff --stat " .. cached)
  return prompt.bound_commit_diff(full, stat, MAX_DIFF_BYTES)
end

local function generate_commit_message(tool)
  local diff = commit_diff("--cached ")
  if vim.v.shell_error ~= 0 then
    vim.notify("Not a git repository.", vim.log.levels.ERROR)
    return
  end

  local diff_type = "staged"
  if diff == "" then
    diff = commit_diff("")
    diff_type = "unstaged"
  end
  if diff == "" then
    vim.notify("No changes detected.", vim.log.levels.WARN)
    return
  end

  local tools = tool == "all" and { "claude", "codex", "copilot" } or { tool }
  vim.notify("Generating commit message with " .. tool .. "...", vim.log.levels.INFO)

  local instruction = prompt.commit_instruction()
  local footer = string.format(
    " Commit Message (%s) | y:yank  p:paste  q:close%s ",
    diff_type,
    #tools > 1 and "  <Tab>/<S-Tab>:switch  1/2:jump" or "")

  ui.run_multi({
    mode = "popup",
    tools = tools,
    filetype = "gitcommit",
    footer = footer,
    copy_notify = "Commit message copied to clipboard.",
    start = function(t, done)
      return backend.run({
        tool = t,
        prompt = instruction,
        input = diff,
        model = COMMIT_MODELS[t],
      }, done)
    end,
  })
end
map("n", "<leader>cm", function() generate_commit_message("claude") end,
  { desc = "Generate commit message with Claude Code", noremap = true })
map("n", "<leader>cx", function() generate_commit_message("codex") end,
  { desc = "Generate commit message with Codex", noremap = true })
map("n", "<leader>cg", function() generate_commit_message("gemini") end,
  { desc = "Generate commit message with Gemini", noremap = true })
map("n", "<leader>cl", function() generate_commit_message("all") end,
  { desc = "Generate commit message with All (Claude/Codex/Copilot)", noremap = true })
map("n", "<leader>co", function() generate_commit_message("gemma") end,
  { desc = "Generate commit message with Ollama (Gemma)", noremap = true })

---------------------------------------------------------
-- Ask the AI about a selection and replace it (claude / codex / gemini / all / gemma)
---------------------------------------------------------
local function ask_ai_and_replace(start_line, end_line, tool)
  if not start_line or not end_line or start_line == 0 or end_line == 0 then
    vim.notify("No visual selection found.", vim.log.levels.ERROR)
    return
  end
  if start_line > end_line then
    start_line, end_line = end_line, start_line
  end

  local tools = tool == "all" and { "claude", "codex", "gemini", "copilot" } or { tool }
  local target_buf = vim.api.nvim_get_current_buf()
  local selected_lines = vim.api.nvim_buf_get_lines(target_buf, start_line - 1, end_line, false)
  local filetype = vim.bo[target_buf].filetype
  local lang = filetype ~= "" and filetype or "plain text"

  -- Prompt window for the user's instruction.
  local prompt_buf = vim.api.nvim_create_buf(false, true)
  local prompt_width = math.min(80, vim.o.columns - 4)
  local prompt_height = math.min(10, vim.o.lines - 4)
  local prompt_win = vim.api.nvim_open_win(prompt_buf, true, {
    relative = "editor",
    width = prompt_width,
    height = prompt_height,
    row = math.floor((vim.o.lines - prompt_height) / 2),
    col = math.floor((vim.o.columns - prompt_width) / 2),
    style = "minimal",
    border = "rounded",
    title = string.format(" Ask %s (lines %d-%d, %s) ", tool, start_line, end_line, lang),
    title_pos = "center",
    footer = " <C-s>:submit  q:cancel(normal) ",
    footer_pos = "center",
  })
  vim.bo[prompt_buf].modifiable = true
  vim.bo[prompt_buf].filetype = "markdown"
  vim.cmd("startinsert")

  -- Trap focus inside the prompt window: snap back if the user moves away.
  local pgroup = vim.api.nvim_create_augroup("AskAiPrompt_" .. prompt_win, { clear = true })
  vim.api.nvim_create_autocmd("WinClosed", {
    group = pgroup,
    pattern = tostring(prompt_win),
    callback = function() pcall(vim.api.nvim_del_augroup_by_id, pgroup) end,
  })
  vim.api.nvim_create_autocmd("WinEnter", {
    group = pgroup,
    callback = function()
      if vim.api.nvim_get_current_win() ~= prompt_win
        and vim.api.nvim_win_is_valid(prompt_win) then
        vim.schedule(function()
          if vim.api.nvim_win_is_valid(prompt_win) then
            vim.api.nvim_set_current_win(prompt_win)
          end
        end)
      end
    end,
  })

  local function close_prompt()
    if vim.api.nvim_win_is_valid(prompt_win) then
      vim.api.nvim_win_close(prompt_win, true)
    end
  end
  map("n", "q", close_prompt, { buffer = prompt_buf, desc = "Cancel prompt" })

  local function replace_range(lines)
    if vim.api.nvim_buf_is_valid(target_buf) then
      vim.api.nvim_buf_set_lines(target_buf, start_line - 1, end_line, false, lines)
      return true
    end
    vim.notify("Target buffer no longer valid.", vim.log.levels.ERROR)
    return false
  end

  local function submit()
    local user_prompt = vim.trim(table.concat(
      vim.api.nvim_buf_get_lines(prompt_buf, 0, -1, false), "\n"))
    if user_prompt == "" then
      vim.notify("Prompt is empty.", vim.log.levels.WARN)
      return
    end
    close_prompt()
    vim.notify("Asking " .. tool .. "...", vim.log.levels.INFO)

    local system = prompt.replace_system(lang, user_prompt)
    local input = table.concat(selected_lines, "\n")
    local footer = string.format(
      " y:AI  Y:merged  q:cancel%s ",
      #tools > 1 and "  <Tab>/<S-Tab>:switch  1/2/3:jump" or "")

    ui.run_multi({
      mode = "diff",
      tools = tools,
      filetype = filetype,
      original = selected_lines,
      footer = footer,
      start = function(t, done)
        return backend.run({ tool = t, prompt = system, input = input, skip_git_check = true }, done)
      end,
      on_accept = function(t, lines)
        if replace_range(lines) then
          vim.notify(string.format("Selection replaced with %s's response.", t))
        end
      end,
      on_accept_merged = function(lines)
        if replace_range(lines) then
          vim.notify("Selection replaced with merged result.")
        end
      end,
    })
  end

  map({ "n", "i" }, "<C-s>", submit,
    { buffer = prompt_buf, desc = "Submit prompt to " .. tool })
end

local function replace_mapping(tool)
  return function()
    -- Exit visual mode so '< and '> reflect the just-completed selection
    vim.cmd("normal! \27")
    ask_ai_and_replace(vim.fn.line("'<"), vim.fn.line("'>"), tool)
  end
end
map("x", "<C-c>", replace_mapping("claude"),
  { desc = "Ask AI(Claude) and replace selection", noremap = true, silent = true })
map("x", "<C-x>", replace_mapping("codex"),
  { desc = "Ask AI(Codex) and replace selection", noremap = true, silent = true })
map("x", "<C-g>", replace_mapping("gemini"),
  { desc = "Ask AI(Gemini) and replace selection", noremap = true, silent = true })
map("x", "<C-p>", replace_mapping("copilot"),
  { desc = "Ask AI(Copilot) and replace selection", noremap = true, silent = true })
map("x", "<C-l>", replace_mapping("all"),
  { desc = "Ask AI(All: Claude/Codex/Gemini/Copilot) and replace selection", noremap = true, silent = true })
map("x", "<C-o>", replace_mapping("gemma"),
  { desc = "Ask AI(Gemma) and replace selection", noremap = true, silent = true })
