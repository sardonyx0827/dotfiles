--vim.keymap.set("i", "<C-j>", "<Plug>(copilot-next)")
--vim.keymap.set("i", "<C-k>", "<Plug>(copilot-previous)")
require("copilot").setup({
  suggestion = {
    --enabled = true,
    enabled = false,
    auto_trigger = false,
    debounce = 75,
    keymap = {
      accept = "<TAB>",
      accept_word = false,
      accept_line = false,
      next = "<c-j>",
      prev = "<c-k>",
      dismiss = "<C-]>",
    },
  },
  panel = {
    enabled = true,
    auto_refresh = true,
    keymap = {
      jump_prev = "[[",
      jump_next = "]]",
      accept = "<CR>",
      refresh = "gr",
      open = "<M-CR>"
    },
    layout = {
      position = "right", -- | top | left | right
      ratio = 0.5
    },
  },
  --panel = { enabled = false },

})
require("copilot_cmp").setup()
vim.keymap.set("n", "<c-p>", ":Copilot panel<CR>", { silent = true })
vim.keymap.set("i", "<c-l>", "<ESC>:Copilot panel<CR>", { silent = true })

--import sys
--sys.path.append(os.path.dirname(os.path.abspath(__file__)))
vim.keymap.set("n", "<leader>cc", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<leader>cc", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>co", ":CopilotChat ", { desc = "Copilot Chat - ongoing" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<C-M-i>", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>ce", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("v", "<leader>ce", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("n", "<leader>cf", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat - /fix" })
vim.keymap.set("v", "<leader>cf", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat - /fix" })
vim.keymap.set("n", "<leader>ct", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("v", "<leader>ct", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("n", "<leader>cj", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("v", "<leader>cj", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("n", "<leader>cs", "{V}y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - yank surround" })
vim.keymap.set("n", "<leader>cl", "50kV100j50ky:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat - yank 100lines" })

-- jump to next error/warn and fix with Copilot
local function quick_fix_next_error_with_ai()
  if vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})[1] == nil then
    print("no error")
    return
  end
  -- jump to next error/warn
  vim.diagnostic.goto_next({severity = vim.diagnostic.severity.ERROR})
  -- fix with Copilot
  -- copy diagnostic message and current line
  local diagnostic_message = vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})[1].message
  local current_line_text = vim.api.nvim_get_current_line()
  -- 5 lines above and 5 lines below
  local current_line = vim.api.nvim_win_get_cursor(0)[1]
  local start = -5
  local finish = 5
  if current_line < 5 then
    start = 0
  else
    start = current_line - 5
  end
  -- max line number
  local max_line = vim.api.nvim_buf_line_count(0)
  if current_line + 5 > max_line then
    finish = max_line
  else
    finish = current_line + 5
  end

  local lines_above = vim.api.nvim_buf_get_lines(0, start, finish, false)
  local lines_text = ""
  for _, line in ipairs(lines_above) do
    if line ~= "" then
      lines_text = lines_text .. line .. "\\n"
    end
  end
  -- open Copilot chat window
  vim.cmd("vertical rightbelow new")
  vim.cmd("setlocal filetype=markdown")
  vim.cmd("CopilotChat ".. "error message : " .. diagnostic_message .. " | current line text : " .. lines_text .. " | your job : how to fix it?")
end
vim.keymap.set("n", "<leader>xn", vim.diagnostic.goto_next, {desc="Jump to Next Error/Warn"})
vim.keymap.set("n", "<leader>qf", quick_fix_next_error_with_ai, {desc="Jump to Next Error and fix with Copilot"})
