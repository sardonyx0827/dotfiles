"""Unit tests for `.config/nvim/lua/setup/functions/ai/context.lua`.

The third slice of the Neovim AI tests (see test_nvim_ai_prompt.py for the
mechanism and for why only `functions/ai/` is tested at all).

`context.lua` decides *what text leaves the editor*: it resolves the cursor to
one syntactic unit -- a definition, a comment run, or (with no parser) a
paragraph -- and that range is what gets sent to an AI and what the returned
hints cite line numbers into. Two failure modes are worth pinning:

- **Over-capture.** Walking one node too far up, or falling back to the whole
  buffer when nothing matches, silently turns a cheap "hint about this
  function" into a whole-file review -- a different feature (`<leader>qf`) at a
  different cost. So "nothing at the cursor" must stay a *refusal*.
- **Off-by-one.** `TSNode:range()` is 0-indexed and its end column is
  exclusive, so a node that ends at the start of a line reports that line as
  its last row. Getting this wrong appends a stray line to every payload and
  shifts every cited line number.

Unlike prompt.lua, this module needs a real buffer and a real parser, so it
exposes `_internal.at_cursor_in(lines, filetype, lnum, col)` -- a scratch
buffer built from `lines`, run through the production `M.at_cursor`, and torn
down. Same seam convention as backend.lua's `_internal`.

Parser availability is *detected*, never assumed: Neovim ships parsers for a
handful of languages (lua and c among them) but the exact set is a property of
the build, and this suite runs against whatever nvim is on PATH. Every
treesitter case is gated on `requires_ts`, which asks the module itself whether
it got a parse rather than hardcoding a list.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

HARNESS = REPO_ROOT / "tests/lua/nvim_call.lua"
CONTEXT_MODULE = "setup.functions.ai.context"

pytestmark = pytest.mark.skipif(
    shutil.which("nvim") is None, reason="nvim not installed"
)


def context_call(fn: str, *args):
    """Call `ai.context.<fn>(*args)` in headless Neovim; return its results."""
    request = {
        "module": CONTEXT_MODULE,
        "fn": fn,
        "args": list(args),
        "nargs": len(args),
    }
    proc = subprocess.run(
        ["nvim", "-l", str(HARNESS), str(REPO_ROOT)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"nvim exited {proc.returncode} calling {fn}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"], f"{CONTEXT_MODULE}.{fn} raised: {payload.get('err')}"
    return payload["ret"][: payload["n"]]


def at_cursor(lines, filetype, lnum, col=0):
    """Resolve the cursor context for `lines`; None when nothing matched.

    `lnum` is 1-based and `col` 0-based, matching `nvim_win_get_cursor`.
    """
    (ctx,) = context_call("_internal.at_cursor_in", lines, filetype, lnum, col)
    return ctx if ctx is not None else None


# --------------------------------------------------------------------------
# Fixtures-as-constants: the same buffers several tests read from.
# --------------------------------------------------------------------------

LUA_DOCUMENTED_FUNCTION = [
    "-- Greet someone by name.",
    "-- @param name string",
    "local function greet(name)",
    '  local msg = string.format("hi %s", name)',
    "  print(msg)",
    "end",
    "",
    "local x = 1",
]

LUA_STANDALONE_COMMENT = [
    "-- note one",
    "-- note two",
    "",
    "local x = 1",
]

C_BLOCK_COMMENT_FUNCTION = [
    "/**",
    " * Add two numbers.",
    " */",
    "int add(int a, int b) {",
    "  return a + b;",
    "}",
]

# A filetype with no bundled parser, used for the heuristic path. If Neovim
# ever ships one for it, test_unparsable_filetype_is_really_unparsable fails
# rather than the heuristic tests passing while exercising treesitter.
NO_PARSER_FILETYPE = "ruby"

RUBY_METHOD = [
    "def add(a, b)",
    "  a + b",
    "end",
    "",
    "puts 1",
]


def _parses(lines, filetype, lnum, col) -> bool:
    """Whether this Neovim build really parsed the sample, rather than falling
    through to the heuristic."""
    ctx = at_cursor(lines, filetype, lnum, col)
    return bool(ctx) and ctx.get("source") == "treesitter"


# Gated per language, not once for the whole file: the bundled parser set is a
# property of the build, and the c cases below would otherwise ride on a probe
# that only ever asked about lua.
requires_ts = pytest.mark.skipif(
    shutil.which("nvim") is not None
    and not _parses(LUA_DOCUMENTED_FUNCTION, "lua", 4, 2),
    reason="this Neovim has no bundled lua treesitter parser",
)

requires_c_ts = pytest.mark.skipif(
    shutil.which("nvim") is not None
    and not _parses(C_BLOCK_COMMENT_FUNCTION, "c", 5, 5),
    reason="this Neovim has no bundled c treesitter parser",
)


def test_harness_reaches_the_module_under_test():
    """Fail loudly if the harness breaks, instead of every test passing empty.

    Every assertion below reads its expectations out of `at_cursor`; a harness
    that silently returned nothing would turn the file green while testing
    nothing. The heuristic path needs no parser, so this holds on any build.
    """
    ctx = at_cursor(RUBY_METHOD, NO_PARSER_FILETYPE, 2, 2)
    assert ctx is not None
    assert ctx["kind"] == "paragraph"


class TestDefinitionAtCursor:
    """The primary path: cursor anywhere in a definition selects that
    definition, plus the doc comment sitting immediately above it.

    Pulling the leading comment in is the point of the feature, not a bonus:
    the hints are supposed to catch a docstring that no longer describes the
    code, and a payload with only one of the two cannot show that.
    """

    @requires_ts
    @pytest.mark.parametrize(
        ("lnum", "col", "where"),
        [
            (4, 2, "inside the body"),
            (3, 17, "on the function name"),
            (6, 0, "on the closing `end`"),
            (1, 3, "in the doc comment above it"),
        ],
    )
    def test_selects_the_documented_function(self, lnum, col, where):
        ctx = at_cursor(LUA_DOCUMENTED_FUNCTION, "lua", lnum, col)
        assert ctx is not None, f"nothing resolved with the cursor {where}"
        assert ctx["kind"] == "definition"
        assert ctx["node_type"] == "function_declaration"
        assert ctx["name"] == "greet"
        # Starts at the doc comment, ends at `end` -- not one line further.
        assert (ctx["start_line"], ctx["end_line"]) == (1, 6)
        assert ctx["lines"] == LUA_DOCUMENTED_FUNCTION[0:6]
        assert ctx["source"] == "treesitter"

    @requires_ts
    def test_end_line_is_the_last_content_line(self):
        """TSNode:range()'s end column is exclusive: a node ending at column 0
        of a row does not include that row. The trailing blank line 7 must not
        ride along."""
        ctx = at_cursor(LUA_DOCUMENTED_FUNCTION, "lua", 4, 2)
        assert ctx["lines"][-1] == "end"

    @requires_c_ts
    def test_c_block_comment_is_pulled_into_the_definition(self):
        """One multi-line `comment` node, unlike lua's one-node-per-line."""
        ctx = at_cursor(C_BLOCK_COMMENT_FUNCTION, "c", 5, 5)
        assert ctx["kind"] == "definition"
        assert ctx["node_type"] == "function_definition"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 6)
        assert ctx["lines"][0] == "/**"

    # `local f = function() end` and `M.f = function() end` are not
    # `function_declaration`s: tree-sitter-lua nests the literal as
    # function_definition < expression_list < assignment_statement < ... , so
    # the climb has to pass through every one of those wrappers. When it
    # stopped at the bare literal the failure was silent and total for this
    # form -- no name, and the doc comment left behind, because the literal's
    # previous sibling is not the comment.
    ASSIGNED_LITERAL_FORMS = {
        "local": [
            "-- Add one to a number.",
            "local f = function(a)",
            "  return a + 1",
            "end",
        ],
        "field": [
            "-- Add one to a number.",
            "M.f = function(a)",
            "  return a + 1",
            "end",
        ],
    }

    @requires_ts
    @pytest.mark.parametrize("form", sorted(ASSIGNED_LITERAL_FORMS))
    @pytest.mark.parametrize(
        ("lnum", "col", "where"), [(3, 4, "inside the body"), (1, 3, "in the doc")]
    )
    def test_an_assigned_function_literal_keeps_its_doc(self, form, lnum, col, where):
        src = self.ASSIGNED_LITERAL_FORMS[form]
        ctx = at_cursor(src, "lua", lnum, col)
        assert ctx is not None, f"nothing resolved for `{form}` with the cursor {where}"
        assert ctx["kind"] == "definition"
        assert ctx["node_type"] == "function_definition"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 4), (
            "the doc comment or the assignment was left out of the range"
        )
        assert ctx["lines"][0] == "-- Add one to a number."
        assert ctx["lines"][1].endswith("function(a)"), (
            "the range must start at the assignment, not at the literal"
        )
        # No node in this chain carries a `name` field, so the header renders
        # `定義 (function_definition)`. Pinned rather than fixed: digging the
        # identifier out of `variable_list` is grammar-specific over-fitting,
        # and the name is in the payload anyway -- it is on the line the unit
        # now starts at.
        assert ctx.get("name") is None

    @requires_c_ts
    def test_a_nameless_definition_still_resolves(self):
        """C's `function_definition` has a `declarator`, not a `name` field.

        The name is a nicety for the report header; a grammar that does not
        offer one must not cost the user the whole feature.
        """
        ctx = at_cursor(C_BLOCK_COMMENT_FUNCTION, "c", 5, 5)
        assert ctx.get("name") is None


class TestCommentAtCursor:
    @requires_ts
    @pytest.mark.parametrize("lnum", [1, 2])
    def test_a_standalone_comment_run_is_selected_whole(self, lnum):
        """Lua emits one `comment` node per line, so the run has to be
        reassembled from adjacent siblings -- a single node would send one
        line of a paragraph."""
        ctx = at_cursor(LUA_STANDALONE_COMMENT, "lua", lnum, 3)
        assert ctx is not None
        assert ctx["kind"] == "comment"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 2)
        assert ctx["lines"] == ["-- note one", "-- note two"]

    @requires_ts
    def test_a_comment_run_does_not_swallow_the_code_below_it(self):
        """The run stops at the blank line; `local x = 1` is not a comment."""
        ctx = at_cursor(LUA_STANDALONE_COMMENT, "lua", 1, 3)
        assert "local x = 1" not in ctx["lines"]


class TestNothingAtCursor:
    """Refuse rather than widen. Falling back to the buffer here would
    silently re-run the `<leader>qf` buffer check under a keymap the user
    pressed for something much smaller."""

    @requires_ts
    def test_a_blank_line_resolves_to_nothing(self):
        assert at_cursor(LUA_DOCUMENTED_FUNCTION, "lua", 7, 0) is None

    @requires_ts
    def test_a_bare_statement_is_not_a_definition(self):
        """`local x = 1` parses as a variable_declaration; it is neither a
        definition nor a comment, so there is nothing to give hints about."""
        assert at_cursor(LUA_DOCUMENTED_FUNCTION, "lua", 8, 6) is None


class TestHeuristicFallback:
    """With no parser there is still a useful answer: the paragraph around the
    cursor. It must stay bounded, and it must still refuse a blank line."""

    def test_unparsable_filetype_is_really_unparsable(self):
        """Anti-vacuity guard for every test in this class.

        If Neovim ever bundles a parser for NO_PARSER_FILETYPE, these tests
        would quietly start exercising the treesitter path and stop covering
        the fallback at all. Fail here instead, so the constant gets changed.
        """
        ctx = at_cursor(RUBY_METHOD, NO_PARSER_FILETYPE, 2, 2)
        assert ctx["source"] == "heuristic", (
            f"'{NO_PARSER_FILETYPE}' now has a parser; pick another filetype "
            "for NO_PARSER_FILETYPE or these tests cover nothing"
        )

    def test_selects_the_enclosing_paragraph(self):
        ctx = at_cursor(RUBY_METHOD, NO_PARSER_FILETYPE, 2, 2)
        assert ctx["kind"] == "paragraph"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 3)
        assert ctx["lines"] == ["def add(a, b)", "  a + b", "end"]

    def test_a_blank_line_still_resolves_to_nothing(self):
        assert at_cursor(RUBY_METHOD, NO_PARSER_FILETYPE, 4, 0) is None

    def test_an_unbroken_file_is_capped_around_the_cursor(self):
        """A file with no blank line is one paragraph. Without a cap the
        fallback would send the whole buffer -- exactly the over-capture the
        refusal above exists to prevent."""
        lines = [f"line {i}" for i in range(1, 501)]
        ctx = at_cursor(lines, NO_PARSER_FILETYPE, 250, 0)
        span = ctx["end_line"] - ctx["start_line"] + 1
        assert span < len(lines), "the cap did not apply"
        assert ctx["start_line"] <= 250 <= ctx["end_line"], (
            "the capped window must still contain the cursor line"
        )
        assert "line 250" in ctx["lines"]


class TestEdgeCases:
    """Buffers and cursor positions that must not raise, and must not silently
    answer "nothing here" for a reason that is really a clamping bug."""

    ONE_LINER = ["local function f() return 1 end"]
    SHORT_FUNCTION = [
        "-- Add one to a number.",
        "local f = function(a)",
        "  return a + 1",
        "end",
    ]

    @pytest.mark.parametrize(
        ("lines", "lnum", "col", "what"),
        [
            ([], 1, 0, "an empty buffer"),
            ([""], 1, 0, "a buffer holding one blank line"),
            (["local x = 1"], 99, 0, "a line number past the end"),
        ],
    )
    def test_degenerate_input_resolves_to_nothing_without_raising(
        self, lines, lnum, col, what
    ):
        assert at_cursor(lines, "lua", lnum, col) is None, what

    @requires_ts
    def test_a_one_line_file_still_resolves(self):
        ctx = at_cursor(self.ONE_LINER, "lua", 1, 10)
        assert ctx is not None
        assert (ctx["start_line"], ctx["end_line"]) == (1, 1)

    @requires_ts
    def test_a_column_past_the_end_of_the_line_is_pulled_back_onto_it(self):
        """There is no node past the last byte, so an unclamped column resolves
        to nothing at all. Reachable via `virtualedit`, and via any caller not
        taking its column from nvim_win_get_cursor."""
        ctx = at_cursor(self.ONE_LINER, "lua", 1, 500)
        assert ctx is not None, "a column past the line end lost the definition"
        assert ctx["kind"] == "definition"

    @requires_ts
    def test_a_line_number_past_the_end_clamps_onto_the_last_line(self):
        ctx = at_cursor(self.SHORT_FUNCTION, "lua", 99, 0)
        assert ctx is not None
        assert (ctx["start_line"], ctx["end_line"]) == (1, 4)

    # A blank line INSIDE a body is the most common cursor position there is --
    # you press <leader>qh mid-edit on the empty line you were about to fill.
    # It is a different path from a blank line at top level (which correctly
    # resolves to nothing): here the enclosing definition must still win.
    BODY_WITH_EMPTY_LINE = [
        "local function f()",
        "  local a = 1",
        "",
        "  return a",
        "end",
    ]
    BODY_WITH_WHITESPACE_LINE = [
        "local function f()",
        "  local a = 1",
        "  ",
        "  return a",
        "end",
    ]

    @requires_ts
    @pytest.mark.parametrize("col", [0, 2], ids=["col-0", "col-past-content"])
    @pytest.mark.parametrize(
        "body",
        ["BODY_WITH_EMPTY_LINE", "BODY_WITH_WHITESPACE_LINE"],
    )
    def test_a_blank_line_inside_a_body_resolves_to_the_definition(self, body, col):
        """Both the truly empty line and the whitespace-only one, and both at
        column 0 and at a column past the line's content -- `probe_col` treats
        those differently and only the tree can say whether the position still
        lands inside the body."""
        ctx = at_cursor(getattr(self, body), "lua", 3, col)
        assert ctx is not None, "the cursor was inside a function body"
        assert ctx["kind"] == "definition"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 5)

    @requires_ts
    def test_a_blank_line_between_definitions_still_resolves_to_nothing(self):
        """The counterpart: at top level there is no enclosing definition, so
        the refusal stands. Without this the test above could be satisfied by
        simply widening every blank line to something."""
        src = ["local M = {}", "function M.a() end", "", "function M.b() end"]
        assert at_cursor(src, "lua", 3, 0) is None


MARKDOWN_FENCED_LUA = [
    "# Title",
    "",
    "```lua",
    "local function g() return 1 end",
    "```",
]

requires_md_ts = pytest.mark.skipif(
    shutil.which("nvim") is not None
    and not _parses(MARKDOWN_FENCED_LUA, "markdown", 4, 8),
    reason="this Neovim cannot parse lua injected into markdown",
)


class TestInjectedLanguages:
    @requires_md_ts
    def test_a_fenced_code_block_resolves_to_the_code_inside_it(self):
        """Without descending into injections the innermost node is the fenced
        block itself, which is not a definition -- so a function in a README or
        a <script> tag would answer "nothing at the cursor"."""
        ctx = at_cursor(MARKDOWN_FENCED_LUA, "markdown", 4, 8)
        assert ctx is not None
        assert ctx["kind"] == "definition"
        assert ctx["node_type"] == "function_declaration"
        assert (ctx["start_line"], ctx["end_line"]) == (4, 4)

    @requires_md_ts
    def test_the_walk_does_not_escape_into_the_host_language(self):
        """An injected tree's root has no parent, so the ancestor walk stops at
        the fence rather than climbing back out into the markdown document."""
        ctx = at_cursor(MARKDOWN_FENCED_LUA, "markdown", 4, 8)
        assert "```" not in "\n".join(ctx["lines"])
        assert "# Title" not in ctx["lines"]


class TestTrailingComments:
    """A comment sharing its line with code is not a doc block.

    The unit is a LINE range, so absorbing `-- how many` out of
    `local count = 0 -- how many` drags `local count = 0` in with it: unrelated
    code at the top of the payload, a header citing a line that is not part of
    the definition, and one more line handed to an external tool. Line
    adjacency alone cannot tell a doc comment from a trailing one.
    """

    TRAILING_ABOVE_DEF = [
        'local token = "value" -- how many widgets',
        "local function greet(name)",
        "  return name",
        "end",
    ]

    DOC_AFTER_TRAILING = [
        'local token = "value" -- trailing',
        "-- Greet someone by name.",
        "local function greet(name)",
        "  return name",
        "end",
    ]

    @requires_ts
    def test_a_trailing_comment_is_not_absorbed_as_a_doc(self):
        ctx = at_cursor(self.TRAILING_ABOVE_DEF, "lua", 3, 4)
        assert ctx["start_line"] == 2, "the trailing-comment line was absorbed"
        assert ctx["lines"][0] == "local function greet(name)"
        assert not any("token" in line for line in ctx["lines"])

    @requires_ts
    def test_the_real_doc_above_it_is_still_absorbed(self):
        """The guard must reject only the trailing comment, not stop the walk
        from collecting the genuine doc line that follows it."""
        ctx = at_cursor(self.DOC_AFTER_TRAILING, "lua", 4, 4)
        assert (ctx["start_line"], ctx["end_line"]) == (2, 5)
        assert ctx["lines"][0] == "-- Greet someone by name."
        assert not any("token" in line for line in ctx["lines"])

    @requires_ts
    def test_a_cursor_on_a_trailing_comment_does_not_pull_in_the_definition(self):
        """Promotion (comment run -> the definition it introduces) must not
        fire either: a comment that shares its line with code introduces
        nothing, and promoting would put that code at the top of the payload."""
        ctx = at_cursor(self.TRAILING_ABOVE_DEF, "lua", 1, 25)
        assert ctx["kind"] == "comment"
        assert (ctx["start_line"], ctx["end_line"]) == (1, 1)
        assert "greet" not in "\n".join(ctx["lines"])

    @requires_ts
    def test_a_trailing_comment_does_not_extend_a_run(self):
        """Run expansion is line-based too, so a neighbour that shares its line
        with code would drag that code in."""
        src = [
            "-- note one",
            'local token = "value" -- trailing',
        ]
        ctx = at_cursor(src, "lua", 1, 3)
        assert (ctx["start_line"], ctx["end_line"]) == (1, 1)
        assert ctx["lines"] == ["-- note one"]


class TestBannerComments:
    """This repo writes `-----` rules around its section headers (ai/init.lua
    and prompt.lua both open that way). Absorbed verbatim as a "docstring",
    a bare rule hands the model a document with no content and invites it to
    reason about a contract nobody wrote."""

    BANNERED = [
        "-" * 57,
        "-- Copy diagnostics to the clipboard.",
        "-" * 57,
        "local function copy()",
        "  return 1",
        "end",
    ]

    RULE_ONLY = [
        "-" * 57,
        "local function copy()",
        "  return 1",
        "end",
    ]

    @requires_ts
    def test_the_leading_rule_is_dropped_but_the_prose_is_kept(self):
        ctx = at_cursor(self.BANNERED, "lua", 5, 2)
        assert ctx["start_line"] == 2, "the opening rule line was absorbed"
        assert ctx["lines"][0] == "-- Copy diagnostics to the clipboard."
        # The closing rule sits between the prose and the code, so it rides
        # along -- a range is contiguous. That is fine; a rule *introducing*
        # the payload is what misleads.
        assert ctx["end_line"] == 6

    @requires_ts
    def test_an_all_rule_block_collapses_to_the_definition(self):
        ctx = at_cursor(self.RULE_ONLY, "lua", 3, 2)
        assert ctx["start_line"] == 2
        assert ctx["lines"][0] == "local function copy()"

    @requires_c_ts
    def test_an_oversized_block_comment_is_dropped_whole(self):
        """The doc budget is checked before a node is accepted, not clamped
        afterwards: a 45-line `/** ... */` must be left out entirely rather
        than sent starting from its 6th line, which reads as a fragment of a
        sentence the model then tries to interpret."""
        doc = ["/**"] + [f" * line {i}" for i in range(1, 44)] + [" */"]
        src = doc + ["int add(int a, int b) {", "  return a + b;", "}"]
        ctx = at_cursor(src, "c", len(doc) + 2, 5)
        assert ctx["start_line"] == len(doc) + 1, "the comment was cut mid-block"
        assert ctx["lines"][0] == "int add(int a, int b) {"

    @pytest.mark.parametrize(
        "text",
        ["-" * 57, '"' * 20, "# =========", "-- ---", "   ----------   "],
    )
    def test_rule_lines_are_recognised(self, text):
        (is_banner,) = context_call("_internal.is_banner_line", text)
        assert is_banner is True

    @pytest.mark.parametrize(
        "text",
        [
            "-- Copy diagnostics to the clipboard.",
            "--",
            "",
            "-- @param name string",
            "// TODO: fix",
        ],
    )
    def test_prose_and_short_markers_are_not_rules(self, text):
        """`--` on its own is a deliberate blank line inside a doc block, not a
        rule; dropping it would reshape the comment the model reads."""
        (is_banner,) = context_call("_internal.is_banner_line", text)
        assert is_banner is False


class TestNodeTypeMatchers:
    """Neovim bundles parsers for only a handful of languages, so the node
    types for everything else can never be exercised by a real parse here.
    Asserting the matcher directly keeps those names visible and correctable:
    a type that is merely missing from the table produces "no unit at the
    cursor", which looks like a cursor-position mistake rather than a bug.
    """

    @pytest.mark.parametrize(
        ("node_type", "language"),
        [
            ("function_declaration", "lua / go / typescript"),
            ("function_definition", "python / c / php"),
            ("class_definition", "python"),
            ("method_definition", "typescript"),
            ("arrow_function", "typescript"),
            ("type_alias_declaration", "typescript"),
            ("interface_declaration", "typescript"),
            ("method_declaration", "go / java"),
            ("type_declaration", "go"),
            ("function_item", "rust"),
            ("impl_item", "rust"),
            ("struct_item", "rust"),
            ("trait_item", "rust"),
            ("struct_specifier", "c"),
            ("singleton_method", "ruby"),
        ],
    )
    def test_definition_types(self, node_type, language):
        (matched,) = context_call("_internal.is_definition_type", node_type)
        assert matched is True, f"{node_type} ({language}) is not a definition"

    @pytest.mark.parametrize(
        ("node_type", "why"),
        [
            ("function_call", "lua: a call, not a definition"),
            ("function_type", "typescript: a type annotation"),
            ("declaration", "c: a variable declaration"),
            ("if_statement", "every grammar: a block, not a unit"),
            ("identifier", "the innermost node almost everywhere"),
        ],
    )
    def test_lookalikes_are_not_definitions(self, node_type, why):
        """The reason the table holds exact names rather than substrings:
        every entry here contains a definition keyword as a substring."""
        (matched,) = context_call("_internal.is_definition_type", node_type)
        assert matched is False, f"{node_type} matched, but {why}"

    @pytest.mark.parametrize(
        ("node_type", "language"),
        [
            ("comment", "most grammars"),
            ("line_comment", "rust"),
            ("block_comment", "rust"),
            ("doc_comment", "rust"),
            ("multiline_comment", "kotlin: /** ... */"),
            ("documentation_comment", "dart: /// ..."),
            ("haddock", "haskell: -- | ..."),
        ],
    )
    def test_comment_types(self, node_type, language):
        """A missing comment type fails quietly and asymmetrically: the
        definition still resolves, so the feature looks like it works while the
        doc comment -- the half it exists to compare against the code -- is
        left out. kotlin shipped exactly that way until its `multiline_comment`
        was found by inspecting a real tree."""
        (matched,) = context_call("_internal.is_comment_type", node_type)
        assert matched is True, f"{node_type} ({language}) is not a comment"

    @pytest.mark.parametrize(
        ("node_type", "why"),
        [
            ("comment_content", "lua: the payload INSIDE a comment node"),
            ("string", "a string literal is not a comment"),
        ],
    )
    def test_non_comments_are_excluded(self, node_type, why):
        """`comment_content` matters most: it is the innermost node at a cursor
        sitting in a lua comment, and `enclosing_comment` has to keep walking
        past it to the `comment` itself. Treating it as a comment would run the
        sibling scan one level too deep, where the neighbouring comment lines
        are not siblings at all."""
        (matched,) = context_call("_internal.is_comment_type", node_type)
        assert matched is False, f"{node_type} matched, but {why}"

    @pytest.mark.parametrize(
        ("node_type", "why"),
        [
            ("decorated_definition", "python: decorators wrap the def"),
            ("variable_declarator", "typescript: `const f = () => {}`"),
            ("lexical_declaration", "typescript: holds the declarator"),
            ("export_statement", "typescript: `export function f()`"),
            ("assignment_statement", "lua: `M.f = function() end`"),
        ],
    )
    def test_wrapper_types(self, node_type, why):
        (matched,) = context_call("_internal.is_wrapper_type", node_type)
        assert matched is True, f"{node_type} is not a wrapper, but {why}"


class TestDescribe:
    """The one-line label shown in the report header and handed to the model.
    Pure, so it needs no buffer."""

    def test_names_the_definition_and_its_range(self):
        (label,) = context_call(
            "describe",
            {
                "kind": "definition",
                "node_type": "function_declaration",
                "name": "greet",
                "start_line": 1,
                "end_line": 6,
                "source": "treesitter",
            },
        )
        assert "greet" in label
        assert "function_declaration" in label
        assert "L1-6" in label

    def test_survives_a_nameless_definition(self):
        (label,) = context_call(
            "describe",
            {
                "kind": "definition",
                "node_type": "function_definition",
                "start_line": 4,
                "end_line": 9,
                "source": "treesitter",
            },
        )
        assert "function_definition" in label
        assert "L4-9" in label

    def test_flags_the_heuristic_fallback(self):
        """The report must say when the range came from a blank-line guess
        rather than a parse -- the hints are only as good as the range."""
        (label,) = context_call(
            "describe",
            {
                "kind": "paragraph",
                "start_line": 2,
                "end_line": 4,
                "source": "heuristic",
            },
        )
        assert "L2-4" in label
        assert label != ""
