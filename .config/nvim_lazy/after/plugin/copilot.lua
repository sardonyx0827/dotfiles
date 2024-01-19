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
vim.keymap.set("n", "<leader>cc", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(select all)" })
vim.keymap.set("v", "<leader>cc", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(selected)" })
vim.keymap.set("n", "<leader>co", ":CopilotChat ", { desc = "Copilot Chat(ongoing)" })
vim.keymap.set("n", "<C-M-i>", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(select all)" })
vim.keymap.set("v", "<C-M-i>", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(selected)" })
vim.keymap.set("n", "<leader>ce", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat(explain)" })
vim.keymap.set("v", "<leader>ce", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /explain<CR>", { desc = "Copilot Chat(explain)" })
vim.keymap.set("n", "<leader>cf", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat(fix)" })
vim.keymap.set("v", "<leader>cf", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /fix<CR>", { desc = "Copilot Chat(fix)" })
vim.keymap.set("n", "<leader>ct", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat(test)" })
vim.keymap.set("v", "<leader>ct", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat /test<CR>", { desc = "Copilot Chat(test)" })
vim.keymap.set("n", "<leader>cj", "ggVGy:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat(Translate to Japanese)" })
vim.keymap.set("v", "<leader>cj", "y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat 日本語訳して<CR>", { desc = "Copilot Chat(Translate to Japanese)" })
vim.keymap.set("n", "<leader>cs", "{V}y:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(copy surround)" })
vim.keymap.set("n", "<leader>cl", "50kV100j50ky:vertical rightbelow new<CR>:setlocal filetype=markdown<CR>:CopilotChat ", { desc = "Copilot Chat(copy 100lines)" })
