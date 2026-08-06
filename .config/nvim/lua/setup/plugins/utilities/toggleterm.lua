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
    -- The terminal number is a Vim command COUNT and belongs on the command name.
    -- Written as `:ToggleTerm 2direction=...` it becomes an argument, and toggleterm's
    -- parser splits each token on "=" and reads the left side as the option name --
    -- yielding the unrecognized key "2direction" and a nil `direction`, so the terminal
    -- silently fell back to the setup default (tab) on a single shared instance instead
    -- of opening the numbered split each `desc` promises.
    -- ss は count を付けない。toggleterm の `toggle()` は count>=1 で
    -- toggle_nth_term(n)、0 で smart_toggle に分かれる。`1direction=vertical` は
    -- 無効キーだったので修正前の ss は実質 smart_toggle(size=80) であり、ここに
    -- `1` を補うと toggle_nth_term(1) に変わって sh1 (bare = smart_toggle、既定の
    -- 端末 1) と同じスロットを方向指定付きで奪い合う。方向は端末の初回生成時に
    -- 固定されるので、先に押した方が勝って他方の desc が黙って無効になる。
    -- 従来どおり smart_toggle のまま direction だけ効かせる (shf/shb と同じ形)。
    { "<leader>ss",  ":ToggleTerm direction=vertical size=80<cr>" },
    { "<leader>sh1", ":ToggleTerm<cr>",                          desc = "ToggleTerm - toggle session 1" },
    { "<leader>sh2", ":2ToggleTerm direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 2" },
    { "<leader>sh3", ":3ToggleTerm direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 3" },
    { "<leader>sh4", ":4ToggleTerm direction=horizontal<cr>",    desc = "ToggleTerm - toggle session 4" },
    { "<leader>sh6", ":6ToggleTerm direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 6" },
    { "<leader>sh7", ":7ToggleTerm direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 7" },
    { "<leader>sh8", ":8ToggleTerm direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 8" },
    { "<leader>sh9", ":9ToggleTerm direction=vertical size=80<cr>", desc = "ToggleTerm - toggle session 9" },
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
