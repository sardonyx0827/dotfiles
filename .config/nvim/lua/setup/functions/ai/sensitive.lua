---------------------------------------------------------
-- ai.sensitive
-- The one definition of "a path whose contents must not leave the editor
-- unasked", and the helpers that apply it.
--
-- Two consumers, one list:
--   * plugins/ai/copilot.lua refuses to ATTACH Copilot completion to such a
--     buffer (completion has no content-level hook to scan, so the path is all
--     there is to go on);
--   * ai.backend asks for confirmation before sending a payload that was READ
--     from such a path to an external model. The credential scan there looks
--     at VALUES, and a `.env` full of `DB_HOST=...` lines holds nothing that is
--     credential-shaped.
--
-- .vim/rc/70-ai.vim carries the same list as Vim regexes
-- (s:ai_sensitive_paths). The two must stay identical in content; the
-- translation is mechanical (`%.` -> `\.`, `%-` -> `-`, `?` -> `\?`) and
-- tests/test_nvim_ai_backend.py compares them entry by entry.
--
-- Deliberately coarse, like the list it came from: a substring match on the
-- lower-cased path, so `credentials-service/main.go` matches too. The cost of a
-- false positive is one confirm dialog that defaults to No, not a block.
--
-- A relative path is judged as if it began with `/`. The directory entries
-- (`/%.kube/`, `/%.ssh/`, ...) carry a leading slash so that `docs/kube-notes.md`
-- does not match, but a repo-relative name such as a diff gives -- `.kube/config`
-- at the repository root -- has no slash before its first component and would
-- slip past every one of them. Done here, in the one function every caller goes
-- through, so a relative buffer name is covered the same way (s:AI_IsSensitivePath
-- does the same on the Vim side).
---------------------------------------------------------
local M = {}

M.PATTERNS = {
  "%.env$", "%.env%.", "%.envrc$",
  "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
  "%.pem$", "%.key$", "%.p12$", "%.pfx$", "%.jks$", "%.keystore$", "%.ppk$",
  "/%.ssh/", "/%.aws/", "/%.gnupg/", "/%.azure/", "/%.kube/", "/gcloud/",
  "%.netrc$", "%.npmrc$", "%.pypirc$", "%.pgpass$", "%.my%.cnf$",
  "kubeconfig", "%.tfstate", "%.tfvars", "%.dockercfg",
  "docker/config%.json$",
  -- cloud service-account / admin JSON keys (arbitrary basenames)
  "service.?account", "adminsdk", "%-key%.json$",
  "credentials", "secrets?", "password",
}

--- Whether `path` looks like a file that holds secrets.
--- @param path string|nil absolute (or at least as-given) file path
--- @return boolean
function M.is_sensitive_path(path)
  if type(path) ~= "string" or path == "" then
    return false
  end
  local lower = path:lower()
  if lower:sub(1, 1) ~= "/" then
    lower = "/" .. lower
  end
  for _, pat in ipairs(M.PATTERNS) do
    if lower:find(pat) then
      return true
    end
  end
  return false
end

--- The sensitive entries of `paths`, in order, each once.
--- @param paths string[]|nil
--- @return string[]
function M.sensitive_paths(paths)
  local out, seen = {}, {}
  for _, p in ipairs(paths or {}) do
    if not seen[p] and M.is_sensitive_path(p) then
      seen[p] = true
      out[#out + 1] = p
    end
  end
  return out
end

-- One path as git prints it: drop the tab git appends after a name that
-- contains a space, and unquote a C-style quoted name. nil for an empty name.
local function unquote(raw)
  local name = raw:gsub("\t.*$", "")
  local quoted = name:match('^"(.*)"$')
  if quoted then
    name = quoted
  end
  return name ~= "" and name or nil
end

-- One `---` / `+++` / `rename` header path: unquoted, minus the `a/` / `b/`
-- prefix. /dev/null (the absent side of a new or deleted file) names nothing.
local function header_path(raw)
  local name = unquote(raw)
  if not name or name == "/dev/null" then
    return nil
  end
  name = name:gsub("^[ab]/", "")
  return name ~= "" and name or nil
end

-- The path in a `diff --git a/X b/X` line, when both names are equal (every
-- change but a rename or copy): the remainder is `a/` + X + ` b/` + X, so X is
-- recovered exactly, spaces and all, by length. nil when the line cannot be
-- split that way (a rename, quoted names, a non-default prefix).
local function git_line_path(rest)
  local len = (#rest - 5) / 2
  if len >= 1 and len == math.floor(len)
    and rest:sub(1, 2) == "a/" and rest:sub(len + 3, len + 5) == " b/"
    and rest:sub(3, len + 2) == rest:sub(len + 6) then
    return rest:sub(3, len + 2)
  end
  return nil
end

--- The file names a `git diff` covers, read from the diff text itself.
---
--- Derived from the text that is about to be sent rather than from a second
--- `git` call, so the check cannot disagree with the payload about which files
--- are in it. Only the HEADER block of each file (from its `diff --git` line to
--- its first `@@` hunk) is read: a removed Lua comment `-- credentials` shows
--- up in a hunk as `--- credentials`, which is not a file name. Binary and
--- mode-only changes have no `---` / `+++` lines, so the `diff --git` line
--- itself counts too.
---
--- A `diff --git` line that cannot be split into its two names (a rename,
--- quoted names) is kept whole as a fallback, and only when the rest of that
--- header block names nothing: PATTERNS are unanchored substring tests, so the
--- unsplit line still matches, but it reads badly in a dialog next to the exact
--- names the `---` / `+++` / `rename` lines give.
--- @param diff string|nil
--- @return string[] names, in order of appearance, each once
function M.diff_paths(diff)
  local out, seen = {}, {}
  local function add(name)
    if name and not seen[name] then
      seen[name] = true
      out[#out + 1] = name
    end
  end
  local in_header = false
  local fallback, named = nil, false
  local function close_header()
    if fallback and not named then
      add(fallback)
    end
    fallback, named, in_header = nil, false, false
  end
  -- ANSI escapes are stripped first: with `color.ui=always` git wraps every
  -- header in SGR codes, `^diff` no longer matches, and the commit would be
  -- sent with no names at all. The caller also asks git for `--no-color`; this
  -- is the backstop for text that arrives some other way.
  local text = (diff or ""):gsub("\27%[[%d;:]*%a", "")
  for line in (text .. "\n"):gmatch("([^\n]*)\n") do
    local rest = line:match("^diff %-%-git (.+)$")
    -- A merge-conflict diff has ONE path after the flag, not an `a/ b/` pair,
    -- and no prefix to strip.
    local merged = line:match("^diff %-%-cc (.+)$") or line:match("^diff %-%-combined (.+)$")
    if merged then
      close_header()
      in_header = true
      local name = unquote(merged)
      if name then
        add(name)
        named = true
      end
    elseif rest then
      close_header()
      in_header = true
      local exact = git_line_path(rest)
      if exact then
        add(exact)
        named = true
      else
        fallback = rest
      end
    elseif line:find("^@@") then
      close_header()
    elseif in_header then
      local raw = line:match("^%+%+%+ (.+)$")
        or line:match("^%-%-%- (.+)$")
        or line:match("^rename from (.+)$")
        or line:match("^rename to (.+)$")
        or line:match("^copy from (.+)$")
        or line:match("^copy to (.+)$")
      if raw then
        local name = header_path(raw)
        if name then
          add(name)
          named = true
        end
      end
    end
  end
  close_header()
  return out
end

return M
