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
    filetypes = {
    yaml = true,
    markdown = true,
    help = true,
    gitcommit = true,
    gitrebase = true,
    hgcommit = true,
  },

})
require("copilot_cmp").setup()
vim.keymap.set("n", "<c-l>", ":Copilot panel<CR>", { silent = true })

-- Copilot Chat - Keymaps
vim.keymap.set("n", "<leader>cc", ":CopilotChatOpen<CR>", { desc = "Copilot Chat" })
vim.keymap.set("v", "<leader>cc", "y:CopilotChatOpen<CR>", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:CopilotChat ", { desc = "Copilot Chat - select all" })
vim.keymap.set("v", "<C-M-i>", "y:CopilotChat ", { desc = "Copilot Chat - selected" })
vim.keymap.set("n", "<leader>cs", "{V}y:CopilotChat ", { desc = "Copilot Chat - yank surround" })
vim.keymap.set("n", "<leader>cm", ":CopilotChatCommit<CR>", { desc = "Copilot Chat - Write commit message for the change with commitizen convention" })

vim.keymap.set({"n", "v"}, "<C-h>",
    function()
      local actions = require("CopilotChat.actions") require("CopilotChat.integrations.telescope").pick(actions.prompt_actions())
    end,
    {desc = "CopilotChat - Prompt actions" })

-- vim.keymap.set("n", "<C-c>", ":CopilotChatInline<CR>", { desc = "Copilot Chat  Inline chat" })
-- vim.keymap.set("v", "<C-c>", "y:CopilotChatInline<CR>", { desc = "Copilot Chat  Inline chat - selected" })

-- jump to next error/warn and fix with Copilot Chat
local function quick_fix_next_error_with_ai()

  local diagnostics = vim.diagnostic.get(0, {severity = vim.diagnostic.severity.ERROR})
  if #diagnostics == 0 then
    print("No errors found.")
    return
  end

  -- jump to next error/warn
  vim.diagnostic.goto_next({severity = vim.diagnostic.severity.ERROR})
  vim.cmd("CopilotChatFix")

end

vim.keymap.set("n", "<leader>qf", quick_fix_next_error_with_ai, {desc="Jump to Next Error and fix with CChat"})

-- file selection in chat
local function copilot_file_selection()
  -- telescopeでworkspaceのファイルを選択して、選択したファイルのパスを変数に格納
  local actions = require("telescope.actions")
  local action_state = require("telescope.actions.state")

  require("telescope.builtin").find_files({
    prompt_title = "CopilotChat - File picker",
    hidden = true,
    cwd = vim.fn.expand("%:p:h"),
    attach_mappings = function(prompt_bufnr, map)
      actions.select_default:replace(function()
        actions.close(prompt_bufnr)
        local selection = action_state.get_selected_entry()
        local file_name = selection.path
        print("Selected file: " .. file_name)
        -- ここでfile_nameを使って必要な処理を行います
        print(file_name)
        vim.api.nvim_put({ "> #file:" .. file_name }, "c", true, true)
      end)
      return true
    end,
  })
end

vim.keymap.set("n", "<leader>cp", copilot_file_selection, {desc="CopilotChat - File picker"})
