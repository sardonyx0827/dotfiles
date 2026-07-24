-- .luacheckrc — luacheck config for the Neovim Lua tree.
--
-- luacheck runs NON-GATING in CI (advisory only; see the `luacheck` job in
-- .github/workflows/ci.yml). Its job is to surface the one bug class that has
-- silently broken the editor before -- an undefined global (a typo'd name, or
-- a `require` result used where a global was meant), which lazy.lua's own
-- comment records as having "degraded the editor silently". It is kept
-- deliberately lenient so it never fights the hand-tuning this tree gets:
-- formatting is not enforced (stylua is intentionally absent) and the noisier
-- style diagnostics are off. Widen the checks only if a real regression slips
-- through, not preemptively.
--
-- The runtime/global facts mirror .luarc.json (LuaJIT + `vim`) so the lint and
-- the language server agree on what is defined.

std = "luajit" -- Neovim embeds LuaJIT (Lua 5.1 dialect); this defines its stdlib

-- Known runtime globals. `vim`/`R` go in `globals` (read+write) because config
-- code legitimately assigns their fields (`vim.opt.x = ...`) or defines them
-- (`R` = a plenary reload helper); listing them in read_globals instead is what
-- produced 77 spurious W122 "setting read-only field" warnings. `Snacks` is
-- injected by snacks.nvim and only read, so read_globals is right for it.
-- Declaring these known globals is what keeps the useful signal alive: a truly
-- undefined global (a typo'd name) still surfaces as W113 against this baseline.
globals = { "vim", "R" }
read_globals = { "Snacks" }

max_line_length = false -- formatting is hand-managed, not a lint concern
unused_args = false -- plugin/event callbacks routinely take unused (_, opts)
