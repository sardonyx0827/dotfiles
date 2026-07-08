-- Terminal
--
-- CUI-tool terminals (lazydocker / claude / gemini / codex) are created lazily on
-- first use and cached here, so nothing is required until toggleterm actually loads.
local terminals = {}

local function toggle_tool(name, cmd)
  return function()
    if not terminals[name] then
      local Terminal = require("toggleterm.terminal").Terminal
      terminals[name] = Terminal:new({ cmd = cmd, direction = "float", hidden = true })
    end
    terminals[name]:toggle()
  end
end

return {
  "akinsho/toggleterm.nvim",
  version = "*",
  lazy = true,
  cmd = { "ToggleTerm" },
  -- Keymaps formerly in after/plugin/toggleterm.lua. Each entry lazy-loads toggleterm.
  keys = {
    { "<leader>ss",  ":ToggleTerm 1direction=vertical size=80<cr>" },
    { "<leader>sh1", ":ToggleTerm<cr>",                          desc = "ToggleTerm - toggle session 1" },
    { "<leader>sh2", ":ToggleTerm 2direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 2" },
    { "<leader>sh3", ":ToggleTerm 3direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 3" },
    { "<leader>sh4", ":ToggleTerm 4direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 4" },
    { "<leader>sh6", ":ToggleTerm 6direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 6" },
    { "<leader>sh7", ":ToggleTerm 7direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 7" },
    { "<leader>sh8", ":ToggleTerm 8direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 8" },
    { "<leader>sh9", ":ToggleTerm 9direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 9" },
    { "<leader>shf", ":ToggleTerm direction=float<cr>",          desc = "ToggleTerm - float" },
    { "<leader>shb", ":ToggleTerm direction=horizontal<cr>",     desc = "ToggleTerm - horizontal" },
    { "<leader>td",  toggle_tool("docker", "lazydocker"),        silent = true, desc = "docker - CUI tool" },
    { "<leader>cc",  toggle_tool("claude", "claude"),            silent = true, desc = "claude - CUI tool" },
    { "<leader>tc",  toggle_tool("claude", "claude"),            silent = true, desc = "claude - CUI tool" },
    { "<leader>tg",  toggle_tool("gemini", "gemini"),            silent = true, desc = "gemini cli - CUI tool" },
    { "<leader>tx",  toggle_tool("codex", "codex"),              silent = true, desc = "codex (OpenAI) - CUI tool" },
  },
  config = function()
    require("toggleterm").setup {
      -- "vertical" | "horizontal" | "tab" | "float"
      direction = "tab"
    }

    -- <esc> leaves terminal-insert mode in any terminal buffer.
    vim.api.nvim_create_autocmd("TermOpen", {
      pattern = "term://*",
      callback = function()
        vim.keymap.set("t", "<esc>", [[<C-\><C-n>]], { buf = 0 })
      end,
    })
  end,
}
