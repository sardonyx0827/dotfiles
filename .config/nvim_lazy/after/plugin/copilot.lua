--- @diagnostic disable: undefined-global
-- Copilot Chat - Keymaps
vim.keymap.set("n", "<leader>cc", ":CopilotChatOpen<CR>", { desc = "Copilot Chat - Open"})
vim.keymap.set("v", "<leader>cc", "y:CopilotChatOpen<CR>", { desc = "Copilot Chat - Open with selected" })
vim.keymap.set("n", "<leader>cC", ":CopilotChatClose<CR>", { desc = "Copilot Chat - Close" })
vim.keymap.set("v", "<leader>cC", ":CopilotChatClose<CR>", { desc = "Copilot Chat - Close" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<C-M-i>", "y:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>cs", "{V}y:CopilotChat ", { desc = "Copilot Chat - yank surround" })
vim.keymap.set("n", "<leader>cm", ":CopilotChatCommit<CR>", { desc = "Copilot Chat - Write commit message for the change with commitizen convention" })
vim.keymap.set({ "n", "v" }, "<leader>ci", ":CopilotChatInline<CR>", { desc = "Copilot Chat - Inline" })
vim.keymap.set({ "n", "v" }, "<C-c>", ":CopilotChatInline<CR>", { desc = "Copilot Chat - Inline" })

-- jump to next error and prompt AI for fix
local function quick_fix_next_error_with_ai()
  local diagnostics = vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})
  if #diagnostics == 0 then
    print("No errors found.")
    return
  end

  -- jump to next error
  vim.diagnostic.jump({ count = 1, float = true, severity = vim.diagnostic.severity.ERROR})

  -- get current cursor position
  local pos = vim.api.nvim_win_get_cursor(0)
  local line_nr = pos[1]
  local buf = vim.api.nvim_get_current_buf()
  local line_text = vim.api.nvim_buf_get_lines(buf, line_nr-1, line_nr, false)[1]

  -- get diagnostics at cursor
  local cursor_diags = vim.diagnostic.get(buf, {lnum = line_nr-1, severity = vim.diagnostic.severity.ERROR})
  local diag_msg = cursor_diags[1] and cursor_diags[1].message or "No diagnostic message."

  -- prompt AI plugin (例: CopilotChatPrompt)
  local prompt = string.format("#buffer\nこのコード行にエラーがあります: '%s'\nエラー内容: %s\n修正案を提案してください。", line_text, diag_msg)
  vim.cmd({cmd = "CopilotChat", args = {prompt}})
end

vim.keymap.set("n", "<leader>qf", quick_fix_next_error_with_ai, {desc="Jump to Next Error and prompt AI for fix"})

-- file selection in chat
local function copilot_file_selection()
  -- telescopeでworkspaceのファイルを選択して、選択したファイルのパスを変数に格納
  local actions = require("telescope.actions")
  local action_state = require("telescope.actions.state")

  require("telescope.builtin").find_files({
    prompt_title = "CopilotChat - File picker",
    hidden = true,
    cwd = vim.fn.expand("%:p:h"),
    attach_mappings = function(prompt_bufnr, _)
      actions.select_default:replace(function()
        actions.close(prompt_bufnr)
        local selection = action_state.get_selected_entry()
        local file_name = selection.path
        print("Selected file: " .. file_name)
        -- ここでfile_nameを使って必要な処理を行います
        print(file_name)
        vim.api.nvim_put({ "> #file:" .. file_name }, "c", true, true)
        -- 最後に改行を追加
        vim.api.nvim_put({ "" }, "l", true, true)
      end)
      return true
    end,
  })
end

vim.keymap.set("n", "<leader>cp", copilot_file_selection, {desc="CopilotChat - File picker"})
