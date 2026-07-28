-- Call one function from the Neovim Lua tree and report the result as JSON.
--
-- Run as `nvim -l tests/lua/nvim_call.lua <repo-root>` with a request object on
-- stdin; see tests/test_nvim_ai_prompt.py, which is the only caller. `nvim -l`
-- is what makes this cheap and honest: it does NOT load the user's init.lua
-- (verified: package.loaded.lazy stays nil and "lazy" never enters
-- runtimepath), so no plugin manager bootstraps and the module under test is
-- the only thing that runs. `nvim --headless -c` would load the real config --
-- ~/.config/nvim is a symlink to this repo -- and test the plugin set instead.
--
-- Request:  { "module": "setup.functions.ai.prompt",
--             "fn": "apply_edits", "args": [...], "nargs": 2 }
-- Response: { "ok": true,  "n": <return count>, "ret": [...], "calls": [...] }
--        or { "ok": false, "err": "<lua error>" }
--
-- `fn` may be dotted ("_internal.build_cli_cmd") to reach a nested field.
--
-- `nargs` is sent explicitly rather than derived from #args because a JSON
-- `null` argument becomes nil once unwrapped, and `#` on a table with a hole is
-- undefined. Return values get the mirror treatment: nil is re-wrapped as
-- vim.NIL so the array stays dense and encodes as a JSON array.
--
-- An argument given as {"__callback": true} becomes a Lua function that records
-- every invocation into the response's "calls" -- backend.M.run reports through
-- a `done(ok, lines, err)` callback rather than a return value, so there is
-- nothing to assert on without one. Only synchronous calls are captured: `-l`
-- exits without running the event loop, so anything deferred through
-- vim.schedule or a job callback never fires. Tests must stay on code paths
-- that answer within the call.

-- Neovim embeds LuaJIT, i.e. Lua 5.1: `unpack` is a global and table.pack does
-- not exist. The fallbacks keep this working if it is ever run on 5.2+.
local unpack = table.unpack or unpack
local function pack(...)
  return { n = select("#", ...), ... }
end

local repo_root = arg[1]
if not repo_root then
  io.stderr:write("usage: nvim -l nvim_call.lua <repo-root>\n")
  os.exit(2)
end

package.path = table.concat({
  repo_root .. "/.config/nvim/lua/?.lua",
  repo_root .. "/.config/nvim/lua/?/init.lua",
  package.path,
}, ";")

-- Neovim's `print` goes through its message system, which under `-l` lands on
-- stderr -- indistinguishable from a real error, and invisible to a caller
-- reading stdout. Both streams show up interleaved in a terminal, so this is
-- only visible once something captures them separately. Write the reply to
-- stdout directly.
local function emit(value)
  io.stdout:write(vim.json.encode(value), "\n")
end

local req = vim.json.decode(io.read("*a"))

-- nil is not directly representable in the request, and neither is a callback.
-- vim.json.decode turns JSON null into vim.NIL, which is a real value: passing
-- it through would hand the function under test a userdata where it expects
-- nil, so an explicit-nil argument could never be tested.
local calls = {}
local args = req.args or {}
for i = 1, (req.nargs or 0) do
  if args[i] == vim.NIL then
    args[i] = nil
  elseif type(args[i]) == "table" and args[i].__callback then
    args[i] = function(...)
      local got = pack(...)
      local recorded = {}
      for j = 1, got.n do
        recorded[j] = got[j] == nil and vim.NIL or got[j]
      end
      calls[#calls + 1] = recorded
    end
  end
end

local mod = require(req.module)
local fn = mod
for part in req.fn:gmatch("[^.]+") do
  fn = type(fn) == "table" and fn[part] or nil
end
if type(fn) ~= "function" then
  emit({ ok = false, err = req.fn .. " is not a function on " .. req.module })
  return
end

local res = pack(pcall(fn, unpack(args, 1, req.nargs or 0)))
if not res[1] then
  emit({ ok = false, err = tostring(res[2]) })
  return
end

local ret = {}
for i = 2, res.n do
  ret[i - 1] = res[i] == nil and vim.NIL or res[i]
end
emit({ ok = true, n = res.n - 1, ret = ret, calls = calls })
