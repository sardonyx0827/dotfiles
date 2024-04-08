--vim.keymap.set("i", "<C-j>", "<Plug>(copilot-next)")
--vim.keymap.set("i", "<C-k>", "<Plug>(copilot-previous)")
require("copilot").setup({

  suggestion = {
    -- enabled = true,
    -- auto_trigger = true,
    enabled = false,
    auto_trigger = false,
    debounce = 75,
    keymap = {
      accept = "<TAB>",
      -- accept = "<CR>",
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
vim.keymap.set("n", "<c-l>", ":Copilot panel<CR>", { silent = true })

-- Copilot Chat - Keymaps
vim.keymap.set("n", "<leader>cc", "ggVGy:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<leader>cc", "y:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>co", ":CopilotChat ", { desc = "Copilot Chat - ongoing" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<C-M-i>", "y:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>ce", "ggVGy:CopilotChatExplain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("v", "<leader>ce", "y:CopilotChatExplain<CR>", { desc = "Copilot Chat - /explain" })
vim.keymap.set("n", "<leader>ct", "ggVGy:CopilotChatTests<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("v", "<leader>ct", "y:CopilotChatTests<CR>", { desc = "Copilot Chat - /test" })
vim.keymap.set("n", "<leader>cj", "ggVGy:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("v", "<leader>cj", "y:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat - Translate to Japanese" })
vim.keymap.set("n", "<leader>cs", "{V}y:CopilotChat ", { desc = "Copilot Chat - yank surround" })
vim.keymap.set("n", "<leader>cf", ":CopilotChatFixDiagnostic<CR>", { desc = "Copilot Chat - /fix on cursor" })
vim.keymap.set("n", "<leader>cr", ":CopilotChatReset<CR>", { desc = "Copilot Chat - reset chat" })
vim.keymap.set("n", "<leader>cb", ":CopilotChatBuffer ", { desc = "Copilot Chat - use buffers" })
vim.keymap.set("n", "<leader>cm", ":CopilotChatCommitStaged<CR>", { desc = "Copilot Chat - Write commit message for the change with commitizen convention" })
vim.keymap.set("i", "<C-c>", "<ESC>:CopilotChatCommitStaged<CR>", { desc = "Copilot Chat - Write commit message for the change with commitizen convention" })

vim.keymap.set({"n", "v"}, "<C-h>",
    function()
      local actions = require("CopilotChat.actions") require("CopilotChat.integrations.telescope").pick(actions.prompt_actions())
    end,
    {desc = "CopilotChat - Prompt actions" })

vim.keymap.set({"n", "v"}, "<C-c>", ":CopilotChatInline<CR>", { desc = "Copilot Chat  Inline chat" })

-- jump to next error/warn and fix with Copilot Chat
local function quick_fix_next_error_with_ai()

  local diagnostics = vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})
  if #diagnostics == 0 then
    print("No errors found.")
    return
  end

  -- jump to next error/warn
  vim.diagnostic.goto_next({severity = vim.diagnostic.severity.ERROR})
  vim.cmd("CopilotChatFixDiagnostic")

end

vim.keymap.set("n", "<leader>qf", quick_fix_next_error_with_ai, {desc="Jump to Next Error and fix with CChat"})

