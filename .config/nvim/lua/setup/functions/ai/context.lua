---------------------------------------------------------
-- ai.context
-- Resolve the cursor to ONE syntactic unit worth asking an AI about: a
-- definition (function / class / type / ...), a run of comment lines, or --
-- when the buffer has no treesitter parser -- the paragraph around the cursor.
--
-- This module decides what text leaves the editor, so it is deliberately
-- conservative in both directions:
--   * it never widens to the whole buffer. A cursor sitting on nothing
--     interesting returns nil, and the caller says so. Falling back to the
--     buffer would silently re-run the <leader>qf buffer check under a keymap
--     pressed for something much smaller, at that feature's cost.
--   * it pulls the doc comment directly above a definition INTO the unit. The
--     hints are meant to catch a comment that no longer describes the code, and
--     a payload holding only one of the two cannot show that.
--
-- No jobs, no windows, no prompts: the result is a plain table, which is what
-- makes the walk testable (see _internal at the bottom).
---------------------------------------------------------
local M = {}

-- Node types that count as "a definition worth reviewing". Exact names, not
-- substrings: `function_call` (lua), `function_type` (typescript) and
-- `declaration` (c, a variable decl) all contain a word from this list while
-- being the wrong answer -- a cursor inside `foo(bar)` would resolve to the
-- call rather than the enclosing function.
--
-- Unlisted grammars are not a failure: the walk finds nothing, the cursor is
-- reported as "no unit here", and a comment or the paragraph fallback still
-- covers the common cases. Add a name here when a language proves to need it.
local DEFINITION_TYPES = {
  -- lua
  function_declaration = true,
  function_definition = true, -- also python, c/cpp, php, bash, vimscript
  -- python
  class_definition = true,
  -- javascript / typescript
  function_expression = true,
  generator_function_declaration = true,
  arrow_function = true,
  method_definition = true,
  class_declaration = true,
  abstract_class_declaration = true,
  interface_declaration = true,
  type_alias_declaration = true,
  enum_declaration = true,
  -- go
  method_declaration = true,
  type_declaration = true,
  -- rust
  function_item = true,
  impl_item = true,
  struct_item = true,
  enum_item = true,
  trait_item = true,
  mod_item = true,
  type_item = true,
  union_item = true,
  -- c / c++
  struct_specifier = true,
  union_specifier = true,
  enum_specifier = true,
  class_specifier = true,
  type_definition = true,
  namespace_definition = true,
  -- java / kotlin / c#
  constructor_declaration = true,
  record_declaration = true,
  object_declaration = true,
  struct_declaration = true,
  -- ruby
  method = true,
  singleton_method = true,
  class = true,
  module = true,
  -- php / swift
  trait_declaration = true,
  protocol_declaration = true,
}

-- Types the walk keeps climbing THROUGH once a definition has been found, so
-- the leading doc travels with the unit -- and so does the name, when some node
-- in the chain has a `name` field. Without this, `const f = () => {}` resolves
-- to the bare `arrow_function`: the name lives on the `variable_declarator`,
-- the `export` on the `export_statement`, and the JSDoc sits above the
-- `lexical_declaration` -- none of them inside the unit.
--
-- The name is best-effort, not guaranteed. Lua's `local f = function() end`
-- climbs through nodes that have no `name` field at all, so the unit is
-- correct and the doc is attached but the label reads `(function_definition)`
-- with no name. Digging the identifier out of `variable_list` would be
-- grammar-specific over-fitting for a report header; the name is in the payload
-- either way, on the assignment line the unit now starts at.
--
-- Only ever entered from a node already matched above, so a wrapper can never
-- select a unit on its own.
local WRAPPER_TYPES = {
  -- lua: `local f = function() end`, `M.f = function() end`, `{ f = function() end }`.
  -- `expression_list` is the one that is easy to miss and breaks both
  -- directions when absent: tree-sitter-lua puts it between the function
  -- literal and the assignment, so without it the climb stops on the bare
  -- `function_definition` -- which has no `name` field and whose previous
  -- sibling is no longer the doc comment.
  expression_list = true,
  variable_declaration = true,
  local_declaration = true,
  assignment_statement = true,
  field = true,
  -- python: the decorators are siblings of the def inside this wrapper
  decorated_definition = true,
  -- javascript / typescript
  variable_declarator = true,
  lexical_declaration = true,
  export_statement = true,
  expression_statement = true,
  assignment_expression = true,
  public_field_definition = true,
  pair = true,
}

-- Comment node names across grammars. Most call it `comment`; the rest were
-- found by inspecting real trees rather than guessed, because a missing name
-- here fails quietly and asymmetrically: the definition still resolves, so the
-- feature looks like it works while the doc comment -- the half this feature
-- exists to compare against the code -- is silently left out.
--   line_comment / block_comment / doc_comment : rust
--   multiline_comment                          : kotlin (`/** ... */`)
--   documentation_comment                      : dart (`/// ...`)
--   haddock                                    : haskell (`-- | ...`)
-- Widening this table cannot over-capture: comments are only ever absorbed
-- directly above a definition, or selected on their own.
local COMMENT_TYPES = {
  comment = true,
  line_comment = true,
  block_comment = true,
  doc_comment = true,
  multiline_comment = true,
  documentation_comment = true,
  haddock = true,
}

-- Absorbed above a definition alongside comments: these decorate the thing
-- being defined and read as part of its contract.
local ATTRIBUTE_TYPES = {
  attribute_item = true, -- rust  #[derive(...)]
  attribute = true,
  decorator = true, -- javascript / typescript
  annotation = true, -- java / kotlin
  marker_annotation = true,
}

-- Upper bound on the doc comment absorbed above a definition. Guards against a
-- file-header licence block being pulled into every unit defined right under it.
local MAX_DOC_LINES = 40

-- Upper bound on the paragraph fallback. A file with no blank line is one
-- paragraph, and sending it whole would be the over-capture this module exists
-- to avoid.
local MAX_PARAGRAPH_LINES = 200

--- True for a comment line made only of rule characters (`-----`, `"*****`,
--- `# =====`). This repo writes those as section banners around real prose, so
--- absorbing one as if it were a docstring hands the model a "document" with no
--- content and invites it to reason about a contract that was never written.
--- A short `--` (an intentionally blank line inside a doc block) is not a
--- banner and stays.
--- @param text string
--- @return boolean
local function is_banner_line(text)
  local trimmed = vim.trim(text)
  return #trimmed >= 6 and trimmed:match('^[%-=%*#/"\';%%_~ \t]+$') ~= nil
end

--- Last buffer row (0-based) a node actually covers. TSNode:range()'s end
--- column is exclusive, so a node ending at column 0 of a row stops just before
--- that row and must not claim it.
--- @param node userdata TSNode
--- @return integer srow, integer erow both 0-based, inclusive
local function node_rows(node)
  local srow, _, erow, ecol = node:range()
  if ecol == 0 and erow > srow then
    erow = erow - 1
  end
  return srow, erow
end

--- Text of a node's `name` field, or nil. Read straight out of the buffer
--- rather than through vim.treesitter.get_node_text, which the treesitter
--- plugin spec monkey-patches to swallow errors and return "" -- a silent empty
--- name is indistinguishable from a grammar that has no name field.
--- @param buf integer
--- @param node userdata TSNode
--- @return string|nil
local function node_name(buf, node)
  local ok, field = pcall(node.field, node, "name")
  if not ok or not field or not field[1] then
    return nil
  end
  local srow, scol, erow, ecol = field[1]:range()
  if srow ~= erow then
    return nil
  end
  local got, text = pcall(vim.api.nvim_buf_get_text, buf, srow, scol, erow, ecol, {})
  if not got or not text[1] or text[1] == "" then
    return nil
  end
  return text[1]
end

--- Climb out of a matched definition through its wrappers, remembering the
--- outermost name seen on the way (the definition's own name wins when it has
--- one; an arrow function has none and inherits the declarator's).
--- @param buf integer
--- @param node userdata TSNode a node whose type is in DEFINITION_TYPES
--- @return userdata node, string|nil name
local function climb_wrappers(buf, node)
  local name = node_name(buf, node)
  local parent = node:parent()
  while parent and WRAPPER_TYPES[parent:type()] do
    node = parent
    name = name or node_name(buf, node)
    parent = node:parent()
  end
  return node, name
end

--- Whether `node` is the first thing on its line (only whitespace before it).
---
--- This is what separates a doc comment from a trailing one. The unit is a
--- LINE range, so absorbing `-- how many` out of `local count = 0 -- how many`
--- drags `local count = 0` in with it: unrelated code in the payload, a header
--- citing a line that is not part of the definition, and -- if that line
--- happened to hold a literal -- one more thing sent to an external tool.
--- Adjacency alone cannot tell the two apart.
--- @param buf integer
--- @param node userdata TSNode
--- @return boolean
local function starts_its_line(buf, node)
  local srow, scol = node:range()
  if scol == 0 then
    return true
  end
  local line = vim.api.nvim_buf_get_lines(buf, srow, srow + 1, false)[1]
  return line ~= nil and line:sub(1, scol):match("^%s*$") ~= nil
end

--- First row (0-based) of the comment / attribute block sitting directly above
--- `node`, or `start_row` when there is none. Stops at a blank line, at a
--- non-comment sibling, at a trailing comment, and at MAX_DOC_LINES; leading
--- banner rules are dropped from the top of whatever it collected.
--- @param buf integer
--- @param node userdata TSNode
--- @param start_row integer 0-based first row of the unit so far
--- @return integer
local function absorb_leading_comments(buf, node, start_row)
  local first = start_row
  local prev = node:prev_named_sibling()
  while prev do
    local ptype = prev:type()
    if not (COMMENT_TYPES[ptype] or ATTRIBUTE_TYPES[ptype]) then
      break
    end
    local psrow, perow = node_rows(prev)
    -- Must end on the line directly above; a blank line ends the doc block.
    if perow + 1 ~= first or not starts_its_line(buf, prev) then
      break
    end
    -- Budget checked BEFORE accepting, so the block is never cut mid-node: a
    -- 50-line `/** ... */` is dropped whole rather than sent starting from its
    -- 11th line, which would read as a fragment of a sentence.
    if start_row - psrow > MAX_DOC_LINES then
      break
    end
    first = psrow
    prev = prev:prev_named_sibling()
  end
  if first >= start_row then
    return start_row
  end
  -- Drop banner rules from the top. Every row in [first, start_row) belongs to
  -- a comment that starts its own line, so this cannot walk into code. A block
  -- that is ALL banner collapses back to the definition itself.
  local rows = vim.api.nvim_buf_get_lines(buf, first, start_row, false)
  local skip = 0
  while skip < #rows and is_banner_line(rows[skip + 1]) do
    skip = skip + 1
  end
  return first + skip
end

--- Walk from the innermost node at the cursor out to the first definition.
--- @param node userdata|nil TSNode
--- @return userdata|nil
local function enclosing_definition(node)
  while node do
    if DEFINITION_TYPES[node:type()] then
      return node
    end
    node = node:parent()
  end
  return nil
end

--- Walk out to the enclosing comment node. The innermost node inside a comment
--- is usually its payload rather than the comment itself (lua yields
--- `comment_content`), so testing only the node under the cursor would miss
--- every cursor position except the delimiters.
--- @param node userdata|nil TSNode
--- @return userdata|nil
local function enclosing_comment(node)
  while node do
    if COMMENT_TYPES[node:type()] then
      return node
    end
    node = node:parent()
  end
  return nil
end

--- The definition a comment block introduces, when the very next sibling is
--- one. `node` may be the definition itself or a wrapper holding it (an
--- `export_statement`, a `lexical_declaration` around an arrow function), in
--- which case the wrapper is the unit and the inner type is the label.
--- @param node userdata|nil TSNode the sibling following the comment run
--- @return userdata|nil unit, string|nil node_type
local function definition_introduced_by(node)
  if not node then
    return nil, nil
  end
  if DEFINITION_TYPES[node:type()] then
    return node, node:type()
  end
  if not WRAPPER_TYPES[node:type()] then
    return nil, nil
  end
  -- Bounded descent: only wrappers are entered, so this cannot wander into an
  -- unrelated definition nested deep inside a body.
  local cur, depth = node, 0
  while cur and depth < 4 do
    for child in cur:iter_children() do
      if child:named() and DEFINITION_TYPES[child:type()] then
        return node, child:type()
      end
    end
    local next_wrapper = nil
    for child in cur:iter_children() do
      if child:named() and WRAPPER_TYPES[child:type()] then
        next_wrapper = child
        break
      end
    end
    cur = next_wrapper
    depth = depth + 1
  end
  return nil, nil
end

--- Expand a comment node into the run of adjacent comment lines around it.
--- Lua emits one `comment` node per line, so a single node would send one line
--- of a paragraph; C emits one node for a whole `/** ... */`, which this leaves
--- alone. Returns the first/last rows and the last node of the run.
---
--- Only comments that START their line join the run, and a trailing comment
--- never grows one at all: the range is line-based, so pulling in a neighbour
--- that shares its line with code pulls the code in too.
--- @param buf integer
--- @param node userdata TSNode a comment node
--- @return integer first_row, integer last_row, userdata last_node all 0-based
local function comment_run(buf, node)
  local first_row, last_row = node_rows(node)
  local last_node = node
  if not starts_its_line(buf, node) then
    return first_row, last_row, last_node
  end

  local prev = node:prev_named_sibling()
  while prev and COMMENT_TYPES[prev:type()] do
    local psrow, perow = node_rows(prev)
    if perow + 1 ~= first_row or not starts_its_line(buf, prev) then
      break
    end
    first_row = psrow
    prev = prev:prev_named_sibling()
  end

  local next_node = node:next_named_sibling()
  while next_node and COMMENT_TYPES[next_node:type()] do
    local nsrow, nerow = node_rows(next_node)
    if nsrow ~= last_row + 1 or not starts_its_line(buf, next_node) then
      break
    end
    last_row = nerow
    last_node = next_node
    next_node = next_node:next_named_sibling()
  end

  return first_row, last_row, last_node
end

--- The paragraph (run of non-blank lines) around `lnum`, capped around the
--- cursor. Used only when the buffer has no parser at all.
---
--- Deliberately not commentstring-aware: a comment block IS a paragraph here,
--- so the extra branch would buy nothing while adding a path that the headless
--- test harness cannot reach (`&commentstring` is unset with no ftplugin).
--- @param lines string[]
--- @param lnum integer 1-based
--- @return integer|nil first, integer|nil last both 1-based inclusive
local function paragraph_range(lines, lnum)
  local cur = lines[lnum]
  if not cur or vim.trim(cur) == "" then
    return nil, nil
  end
  local first, last = lnum, lnum
  while first > 1 and vim.trim(lines[first - 1] or "") ~= "" do
    first = first - 1
  end
  while last < #lines and vim.trim(lines[last + 1] or "") ~= "" do
    last = last + 1
  end
  if last - first + 1 > MAX_PARAGRAPH_LINES then
    first = math.max(first, lnum - math.floor(MAX_PARAGRAPH_LINES / 2))
    last = math.min(last, first + MAX_PARAGRAPH_LINES - 1)
  end
  return first, last
end

--- Column to probe the tree at: the cursor's own column, pulled onto the line's
--- actual content. Both clamps matter, and both produce "nothing at the cursor"
--- when skipped:
---   * inside the leading whitespace, the innermost node is the enclosing block
---     rather than anything on the line;
---   * past the last byte, there is no node at all -- reachable through
---     `virtualedit`, and through any caller that does not get its column from
---     nvim_win_get_cursor.
--- @param line string|nil
--- @param col integer 0-based
--- @return integer
local function probe_col(line, col)
  if not line or line == "" then
    return 0
  end
  local indent = #(line:match("^%s*") or "")
  return math.max(indent, math.min(col, #line - 1))
end

--- Resolve the cursor to one unit.
--- @param buf integer
--- @param lnum integer 1-based cursor line
--- @param col integer 0-based cursor column
--- @return table|nil unit {
---   kind = "definition"|"comment"|"paragraph",
---   node_type = string|nil,  -- treesitter type behind the unit
---   name = string|nil,
---   start_line = integer, end_line = integer,  -- 1-based, inclusive
---   lines = string[],
---   source = "treesitter"|"heuristic",
--- }
function M.at_cursor(buf, lnum, col)
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  if #lines == 0 then
    return nil
  end
  lnum = math.max(1, math.min(lnum, #lines))
  col = probe_col(lines[lnum], col or 0)

  local first, last, kind, node_type, name

  -- get_parser returns nil (rather than raising) for a filetype with no parser;
  -- pcall covers the rest, because a broken parser must degrade to the
  -- heuristic rather than take the keymap down with it.
  local ok, parser = pcall(vim.treesitter.get_parser, buf, nil, { error = false })
  if ok and parser then
    pcall(parser.parse, parser, true)
    -- Descend into injected languages, so a lua block fenced inside markdown
    -- (or a <script> in html) resolves to the function under the cursor rather
    -- than to the code block that contains it. Injected trees carry
    -- buffer-absolute ranges, and their root has no parent, so the ancestor
    -- walk below stays inside the injected language.
    local at = vim.treesitter.get_node({
      bufnr = buf,
      pos = { lnum - 1, col },
      ignore_injections = false,
    })
    -- Definition first: a comment sitting INSIDE a body should resolve to the
    -- body's definition, not to itself.
    local def = enclosing_definition(at)
    local comment = not def and enclosing_comment(at) or nil
    if def then
      node_type = def:type()
      local unit, unit_name = climb_wrappers(buf, def)
      local srow, erow = node_rows(unit)
      first, last, name = absorb_leading_comments(buf, unit, srow), erow, unit_name
      kind = "definition"
    elseif comment then
      local crow, clast, last_node = comment_run(buf, comment)
      -- A doc comment introducing a definition belongs WITH that definition;
      -- comparing the two is the whole point of the feature. A comment that
      -- shares its line with code introduces nothing -- promoting it would put
      -- that code at the top of the payload.
      local intro, intro_type
      if starts_its_line(buf, comment) then
        intro, intro_type = definition_introduced_by(last_node:next_named_sibling())
      end
      local intro_row = intro and node_rows(intro) or nil
      if intro and intro_row == clast + 1 then
        local unit, unit_name = climb_wrappers(buf, intro)
        local _, erow = node_rows(unit)
        node_type, name = intro_type, unit_name
        first, last, kind = crow, erow, "definition"
      else
        first, last, kind = crow, clast, "comment"
        node_type = comment:type()
      end
    else
      return nil
    end
    first, last = first + 1, last + 1
    return {
      kind = kind,
      node_type = node_type,
      name = name,
      start_line = first,
      end_line = last,
      lines = vim.list_slice(lines, first, last),
      source = "treesitter",
    }
  end

  first, last = paragraph_range(lines, lnum)
  if not first then
    return nil
  end
  return {
    kind = "paragraph",
    start_line = first,
    end_line = last,
    lines = vim.list_slice(lines, first, last),
    source = "heuristic",
  }
end

--- One-line Japanese label for a unit: what it is, and where it came from.
--- Shown in the report header and handed to the model, so a range guessed from
--- blank lines is never presented as if it had been parsed.
--- @param unit table as returned by M.at_cursor
--- @return string
function M.describe(unit)
  local where = string.format("L%d-%d", unit.start_line, unit.end_line)
  if unit.kind == "definition" then
    local what = unit.name and string.format("`%s` (%s)", unit.name, unit.node_type)
        or string.format("(%s)", unit.node_type)
    return string.format("定義 %s %s", what, where)
  elseif unit.kind == "comment" then
    return string.format("コメントブロック %s", where)
  end
  return string.format("段落 %s (treesitter パーサーが無いため空行区切りで推定)", where)
end

-- Test seam. Same intent as backend.lua's: these are private, but they carry
-- the invariants worth pinning -- WHAT text leaves the editor for a given
-- cursor position, and which node types are treated as definitions.
--
-- `at_cursor_in` exists because the walk needs a real buffer and a real parse,
-- which the headless harness cannot set up through a JSON argument list. It
-- runs the production M.at_cursor against a scratch buffer, so the seam
-- exercises the same code path the keymap does rather than a copy of it.
--
-- The two matchers are exposed so the node types for grammars Neovim does not
-- bundle a parser for (python, typescript, go, rust) are still asserted
-- somewhere: a test naming `decorated_definition` is a correction anyone can
-- make, whereas an unlisted type is silently "no unit here".
local function at_cursor_in(lines, filetype, lnum, col)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].filetype = filetype
  local ok, unit = pcall(M.at_cursor, buf, lnum, col or 0)
  pcall(vim.api.nvim_buf_delete, buf, { force = true })
  if not ok then
    error(unit)
  end
  return unit
end

M._internal = {
  at_cursor_in = at_cursor_in,
  is_definition_type = function(t) return DEFINITION_TYPES[t] == true end,
  is_wrapper_type = function(t) return WRAPPER_TYPES[t] == true end,
  is_comment_type = function(t) return COMMENT_TYPES[t] == true end,
  is_banner_line = is_banner_line,
}

return M
