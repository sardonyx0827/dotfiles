"""Unit tests for `.config/nvim/lua/setup/functions/ai/prompt.lua`.

The Neovim Lua tree is mostly *configuration*: plugin specs, colorschemes,
option and keymap wiring. That part is left to luacheck, because
it breaks loudly the moment the editor starts. `functions/ai/` is the exception
-- it is application code that happens to live in a dotfiles repo. It parses
untrusted LLM output and then rewrites the user's buffer with the result, and
it is where this tree actually regresses: commit de046d2 migrated the whole
tree off the pre-0.12 `buffer` keymap option and ad938a4 / 51cb2a8 promptly
reintroduced it in exactly these files.

`prompt.lua` is the first slice because it is pure: its only Neovim couplings
are `vim.trim`, `vim.split`, `vim.json.decode`, and `vim.diagnostic.severity`,
all of which exist in a bare headless Neovim with no plugins loaded. So the
tests need no plugin manager, no fixture buffers, and no `vim` shim -- a shim
would mean asserting against a reimplementation of `vim.split`'s `plain=true`
semantics rather than against the one the editor actually runs.

Mechanism: `tests/lua/nvim_call.lua` under `nvim -l`, one process per call
(~10ms), mirroring how the suite already shells out to `zsh` and skipping the
same way when the interpreter is absent. Lua is deliberately left out of the
coverage gates in .coveragerc / ci.yml: luacov would be a second toolchain for
marginal signal over a module this size.

A second, heavier mechanism lives at the bottom of this file. `apply_edits`
handing back a line the editor cannot write is not a defect any return value
shows -- it becomes one only once `ai.ui` has tried to render those lines and
the user has pressed `y`. Those cases therefore drive `ui.run_multi` /
`ui.open_report` for real (windows, buffers and keymaps, still under `nvim -l`)
from a scenario script the test writes to a tmp dir -- the shape
test_nvim_ai_backend.py's `_lua_probe` uses, copied rather than imported
because that helper is private there and drags its PATH-sealing fixtures along.
They stay in this file because the input under test is the model's edit JSON,
which is prompt.lua's contract; ui.lua is only what turns a violation of it
into a destroyed buffer.
"""

import json
import re
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

HARNESS = REPO_ROOT / "tests/lua/nvim_call.lua"
PROMPT_MODULE = "setup.functions.ai.prompt"
VIM_AI_RC = REPO_ROOT / ".vim/rc/70-ai.vim"

requires_nvim = pytest.mark.skipif(
    shutil.which("nvim") is None, reason="nvim not installed"
)

pytestmark = requires_nvim


def prompt_call(fn: str, *args):
    """Call `ai.prompt.<fn>(*args)` in headless Neovim; return its results.

    Returns the function's return values as a list, so a `return x, nil` tail
    is visible rather than swallowed. Python None is passed through as a real
    Lua nil (see the harness).
    """
    request = {
        "module": PROMPT_MODULE,
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
    assert payload["ok"], f"{PROMPT_MODULE}.{fn} raised: {payload.get('err')}"
    return payload["ret"][: payload["n"]]


def edit(start, stop, original, fixed):
    """An edit in the shape M.parse_edits produces ('end' is a Lua keyword)."""
    return {"start": start, "stop": stop, "original": original, "fixed": fixed}


def test_harness_reaches_the_module_under_test():
    """Fail loudly if the harness breaks, instead of every test passing empty.

    Everything below reads its expectations out of `prompt_call`; a harness
    that silently returned nothing would turn the whole file green.
    """
    (instruction,) = prompt_call("commit_instruction")
    assert "Conventional Commits" in instruction


class TestApplyEdits:
    """M.apply_edits: the highest-risk function in the tree.

    It rewrites a real buffer from model-proposed line ranges. Its contract is
    that a bad edit is *skipped*, never misapplied, so each documented skip
    reason gets a test -- a regression that turned any of them into a silent
    apply would corrupt a file the user is editing.
    """

    LINES = ["one", "two", "three"]

    def test_applies_a_single_edit(self):
        patched, applied, skipped = prompt_call(
            "apply_edits", self.LINES, [edit(2, 2, ["two"], ["TWO"])]
        )
        assert patched == ["one", "TWO", "three"]
        assert applied == 1
        assert skipped == []

    def test_no_edits_returns_the_lines_unchanged(self):
        patched, applied, skipped = prompt_call("apply_edits", self.LINES, [])
        assert patched == self.LINES
        assert applied == 0
        assert skipped == []

    def test_edits_are_applied_in_start_order_regardless_of_input_order(self):
        patched, applied, _ = prompt_call(
            "apply_edits",
            self.LINES,
            [edit(3, 3, ["three"], ["THREE"]), edit(1, 1, ["one"], ["ONE"])],
        )
        assert patched == ["ONE", "two", "THREE"]
        assert applied == 2

    def test_growing_edit_does_not_shift_later_edits(self):
        """The reason the result is built into a fresh array left-to-right.

        An in-place patcher would have to re-index every later edit after one
        that changes the line count; this one must not need to.
        """
        patched, applied, skipped = prompt_call(
            "apply_edits",
            self.LINES,
            [
                edit(1, 1, ["one"], ["1a", "1b", "1c"]),
                edit(3, 3, ["three"], ["THREE"]),
            ],
        )
        assert patched == ["1a", "1b", "1c", "two", "THREE"]
        assert applied == 2
        assert skipped == []

    def test_shrinking_edit_does_not_shift_later_edits(self):
        patched, applied, _ = prompt_call(
            "apply_edits",
            ["a", "b", "c", "d"],
            [edit(1, 2, ["a", "b"], ["ab"]), edit(4, 4, ["d"], ["D"])],
        )
        assert patched == ["ab", "c", "D"]
        assert applied == 2

    def test_empty_fixed_deletes_the_range(self):
        patched, applied, _ = prompt_call(
            "apply_edits", self.LINES, [edit(2, 2, ["two"], [])]
        )
        assert patched == ["one", "three"]
        assert applied == 1

    def test_edit_spanning_the_whole_buffer(self):
        patched, applied, _ = prompt_call(
            "apply_edits", self.LINES, [edit(1, 3, self.LINES, ["only"])]
        )
        assert patched == ["only"]
        assert applied == 1

    @pytest.mark.parametrize(
        ("bad", "reason"),
        [
            pytest.param(
                edit(1.5, 2, ["one", "two"], ["X"]),
                "non-integer range",
                id="fractional-start",
            ),
            pytest.param(
                edit("1", 1, ["one"], ["X"]),
                "non-integer range",
                id="string-start",
            ),
            pytest.param(
                edit(1, None, ["one"], ["X"]),
                "non-integer range",
                id="missing-stop",
            ),
            pytest.param(
                edit(0, 1, ["one"], ["X"]),
                "range out of bounds",
                id="start-below-one",
            ),
            pytest.param(
                edit(2, 1, ["two"], ["X"]),
                "range out of bounds",
                id="stop-before-start",
            ),
            pytest.param(
                edit(3, 9, ["three"], ["X"]),
                "range out of bounds",
                id="stop-past-end",
            ),
            pytest.param(
                edit(1, 1, "one", ["X"]),
                "missing original/fixed",
                id="original-not-a-list",
            ),
            pytest.param(
                edit(1, 1, ["one"], None),
                "missing original/fixed",
                id="fixed-missing",
            ),
            pytest.param(
                edit(1, 2, ["one"], ["X"]),
                "original length mismatch",
                id="original-too-short",
            ),
            pytest.param(
                edit(1, 1, ["one", "two"], ["X"]),
                "original length mismatch",
                id="original-too-long",
            ),
            pytest.param(
                edit(1, 1, ["ONE"], ["X"]),
                "original does not match buffer",
                id="stale-original",
            ),
            # `fixed` is the only value that reaches nvim_buf_set_lines
            # verbatim, and it rejects both of these shapes -- inside a job
            # callback, where the throw is swallowed. See
            # TestFixFlowNeverAcceptsTheLoadingPlaceholder for what that cost.
            pytest.param(
                edit(1, 1, ["one"], ["X\nY"]),
                "fixed line contains newlines",
                id="newline-inside-a-fixed-line",
            ),
            pytest.param(
                edit(1, 1, ["one"], ["fine", "still fine\nnot"]),
                "fixed line contains newlines",
                id="newline-in-a-later-fixed-line",
            ),
            pytest.param(
                edit(1, 1, ["one"], [42]),
                "non-string fixed line",
                id="number-as-a-fixed-line",
            ),
            pytest.param(
                edit(1, 1, ["one"], [None]),
                "non-string fixed line",
                id="null-as-a-fixed-line",
            ),
            pytest.param(
                edit(1, 1, ["one"], [["X"]]),
                "non-string fixed line",
                id="nested-array-as-a-fixed-line",
            ),
        ],
    )
    def test_bad_edit_is_skipped_with_its_reason(self, bad, reason):
        patched, applied, skipped = prompt_call("apply_edits", self.LINES, [bad])
        assert patched == self.LINES, "a rejected edit must not touch the buffer"
        assert applied == 0
        assert [s["reason"] for s in skipped] == [reason]

    def test_an_unwritable_fixed_line_does_not_poison_the_batch(self):
        """Same rule as every other skip reason: reject that edit, not the run."""
        patched, applied, skipped = prompt_call(
            "apply_edits",
            self.LINES,
            [
                edit(1, 1, ["one"], ["ONE"]),
                edit(2, 2, ["two"], ["TWO\nEXTRA"]),
                edit(3, 3, ["three"], ["THREE"]),
            ],
        )
        assert patched == ["ONE", "two", "THREE"]
        assert applied == 2
        assert [s["reason"] for s in skipped] == ["fixed line contains newlines"]

    def test_ordinary_text_is_not_caught_by_the_unwritable_line_guard(self):
        """The guard is about what nvim_buf_set_lines refuses, nothing wider.

        A tab, a lone carriage return and non-ASCII text are all writable, so
        a fix carrying them must still apply -- a guard that rejected them
        would quietly disable the feature for most real source lines.
        """
        patched, applied, skipped = prompt_call(
            "apply_edits",
            self.LINES,
            [edit(2, 2, ["two"], ["\tニ 🎌", "carriage\rreturn", ""])],
        )
        assert patched == ["one", "\tニ 🎌", "carriage\rreturn", "", "three"]
        assert applied == 1
        assert skipped == []

    def test_overlapping_edit_is_skipped_after_the_first_applies(self):
        patched, applied, skipped = prompt_call(
            "apply_edits",
            self.LINES,
            [
                edit(1, 2, ["one", "two"], ["merged"]),
                edit(2, 2, ["two"], ["late"]),
            ],
        )
        assert patched == ["merged", "three"]
        assert applied == 1
        assert [s["reason"] for s in skipped] == ["overlapping range"]

    def test_valid_edits_still_apply_alongside_a_skipped_one(self):
        """One bad edit must not poison the batch."""
        patched, applied, skipped = prompt_call(
            "apply_edits",
            self.LINES,
            [
                edit(1, 1, ["one"], ["ONE"]),
                edit(2, 2, ["STALE"], ["nope"]),
                edit(3, 3, ["three"], ["THREE"]),
            ],
        )
        assert patched == ["ONE", "two", "THREE"]
        assert applied == 2
        assert [s["reason"] for s in skipped] == ["original does not match buffer"]


class TestParseEdits:
    """M.parse_edits: the model's raw stdout is untrusted input."""

    def test_parses_a_json_array_and_renames_end_to_stop(self):
        raw = json.dumps(
            [{"start": 2, "end": 3, "original": ["a", "b"], "fixed": ["c"]}]
        )
        edits, err = prompt_call("parse_edits", raw)
        assert err is None
        assert edits == [
            {"start": 2, "stop": 3, "original": ["a", "b"], "fixed": ["c"]}
        ]

    def test_tolerates_a_markdown_fence_the_prompt_forbade(self):
        body = json.dumps([{"start": 1, "end": 1, "original": ["a"], "fixed": ["b"]}])
        edits, err = prompt_call("parse_edits", f"```json\n{body}\n```")
        assert err is None
        assert edits[0]["start"] == 1

    def test_empty_array_is_success_not_failure(self):
        """The prompt tells the model to reply `[]` when there is nothing to fix."""
        edits, err = prompt_call("parse_edits", "[]")
        assert err is None
        assert edits == []

    def test_non_table_elements_are_dropped(self):
        edits, err = prompt_call("parse_edits", '["junk", {"start": 1, "end": 1}]')
        assert err is None
        assert [e["start"] for e in edits] == [1]

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("", "empty response", id="empty"),
            pytest.param("   \n\t ", "empty response", id="whitespace"),
            pytest.param("```json\n\n```", "empty response", id="empty-fence"),
            pytest.param(None, "empty response", id="nil"),
            pytest.param("not json at all", "invalid JSON", id="prose"),
            pytest.param("[{", "invalid JSON", id="truncated"),
        ],
    )
    def test_unusable_output_reports_an_error(self, raw, expected):
        edits, err = prompt_call("parse_edits", raw)
        assert edits is None
        assert err == expected

    def test_json_scalar_is_rejected(self):
        """`5` decodes fine but is not a table, so it must not reach apply_edits."""
        edits, err = prompt_call("parse_edits", "5")
        assert edits is None
        assert err == "invalid JSON"

    def test_json_object_is_reported_as_nothing_to_fix_not_as_an_error(self):
        """Pins current behaviour, which conflates two different outcomes.

        `type(decoded) ~= "table"` is the only structural guard and a JSON
        object clears it; `ipairs` then finds no array part, so a model that
        replied with an object reaches the caller looking exactly like the
        `[]` that fix_buffer_system asks for when there is nothing to fix.
        Benign today -- the flow reports that it applied no edits, and
        apply_edits never sees a malformed edit -- but it means "the model
        answered wrongly" is indistinguishable from "the model found nothing".
        If that distinction is ever needed, this is the line to change.
        """
        edits, err = prompt_call("parse_edits", '{"start": 1}')
        assert edits == []
        assert err is None


class TestParseOllama:
    def test_splits_the_response_into_lines(self):
        lines, err = prompt_call("parse_ollama", json.dumps({"response": "a\nb"}))
        assert err is None
        assert lines == ["a", "b"]

    def test_trimming_is_whole_string_not_per_line(self):
        """Only the outer blank lines go; interior indentation must survive.

        Ollama pads its `response` with newlines, but the payload is code
        headed for a buffer -- a per-line strip would silently reindent it.
        """
        lines, err = prompt_call(
            "parse_ollama", json.dumps({"response": "\n\n  a  \n    b  \n\n"})
        )
        assert err is None
        assert lines == ["a  ", "    b"]

    def test_error_field_is_surfaced(self):
        lines, err = prompt_call(
            "parse_ollama", json.dumps({"error": "model not found"})
        )
        assert lines is None
        assert err == "model not found"

    def test_blank_response_falls_through_to_the_error_field(self):
        lines, err = prompt_call(
            "parse_ollama", json.dumps({"response": "   ", "error": "overloaded"})
        )
        assert lines is None
        assert err == "overloaded"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            pytest.param("<html>502</html>", "invalid JSON response", id="html"),
            pytest.param("", "invalid JSON response", id="empty"),
            pytest.param("7", "invalid JSON response", id="scalar"),
            pytest.param('{"response": ""}', "empty response", id="blank-response"),
            pytest.param("{}", "empty response", id="no-fields"),
        ],
    )
    def test_unusable_output_reports_an_error(self, raw, expected):
        lines, err = prompt_call("parse_ollama", raw)
        assert lines is None
        assert err == expected


class TestStripCodeFences:
    """M.strip_code_fences: must strip model fences without corrupting source.

    The asymmetry is the point -- a false positive deletes real lines from the
    user's buffer, so anything that is not unambiguously a whole-output fence
    is returned untouched.
    """

    @pytest.mark.parametrize(
        ("lines", "expected"),
        [
            pytest.param(["```lua", "x = 1", "```"], ["x = 1"], id="lang"),
            pytest.param(["```", "x = 1", "```"], ["x = 1"], id="no-lang"),
            pytest.param(
                ["", "```python", "x = 1", "```", ""], ["x = 1"], id="blank-padded"
            ),
            pytest.param(["```lua", "x = 1", "```  "], ["x = 1"], id="trailing-space"),
            pytest.param(
                ["```c++", "int x;", "```"], ["int x;"], id="lang-with-punctuation"
            ),
            pytest.param(["````md", "a", "````"], ["a"], id="four-backticks"),
            pytest.param(["```lua", "```"], [], id="empty-body"),
        ],
    )
    def test_strips_a_whole_output_fence(self, lines, expected):
        (stripped,) = prompt_call("strip_code_fences", lines)
        assert stripped == expected

    @pytest.mark.parametrize(
        "lines",
        [
            pytest.param(["x = 1", "y = 2"], id="no-fence"),
            pytest.param(["```lua", "x = 1"], id="unclosed"),
            pytest.param(["x = 1", "```"], id="close-only"),
            pytest.param(["```"], id="single-fence-line"),
            pytest.param(["local s = [[", "```", "]]"], id="fence-inside-real-source"),
            pytest.param(["```lua", "x = 1", "```lua"], id="closing-fence-has-lang"),
            pytest.param([], id="empty"),
            pytest.param([""], id="blank-only"),
        ],
    )
    def test_leaves_anything_else_untouched(self, lines):
        (stripped,) = prompt_call("strip_code_fences", lines)
        assert stripped == lines

    def test_non_list_input_yields_an_empty_list(self):
        (stripped,) = prompt_call("strip_code_fences", "not a list")
        assert stripped == []


class TestCleanCliLines:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            pytest.param(None, [], id="nil"),
            pytest.param([], [], id="empty"),
            pytest.param([""], [], id="only-the-trailing-blank"),
            pytest.param(["a", "b", ""], ["a", "b"], id="trailing-blank"),
            pytest.param(["a", "b"], ["a", "b"], id="no-trailing-blank"),
            pytest.param(["a", "", ""], ["a", ""], id="drops-exactly-one"),
            pytest.param(["", "a"], ["", "a"], id="leading-blank-kept"),
        ],
    )
    def test_drops_only_the_trailing_empty_line(self, data, expected):
        (cleaned,) = prompt_call("clean_cli_lines", data)
        assert cleaned == expected


class TestBoundCommitDiff:
    """M.bound_commit_diff keeps the payload under the model context and ARG_MAX.

    copilot has no stdin path, so backend.build_cli_cmd inlines the diff into
    argv; an unbounded diff there is an E2BIG, not a slow request.
    """

    def test_small_diff_is_returned_verbatim(self):
        (bounded,) = prompt_call("bound_commit_diff", "diff body", "stat", 1024)
        assert bounded == "diff body"

    def test_diff_exactly_at_the_limit_is_not_truncated(self):
        full = "x" * 64
        (bounded,) = prompt_call("bound_commit_diff", full, "stat", 64)
        assert bounded == full

    def test_oversized_diff_is_truncated_with_the_stat_summary(self):
        full = "L" * 4096
        (bounded,) = prompt_call("bound_commit_diff", full, "1 file changed", 1024)
        assert bounded != full
        assert "truncated" in bounded
        assert "1 file changed" in bounded
        # The kept slice is the head of the patch, and only that much of it.
        assert "L" * 1024 in bounded
        assert "L" * 1025 not in bounded

    def test_truncation_notice_reports_sizes_in_kb(self):
        (bounded,) = prompt_call("bound_commit_diff", "L" * 4096, "stat", 1024)
        assert "full patch is 4 KB" in bounded
        assert "first 1 KB" in bounded


class TestNumberLines:
    def test_prefixes_each_line_with_its_number(self):
        (numbered,) = prompt_call("number_lines", ["alpha", "beta"])
        assert numbered == "1 │ alpha\n2 │ beta"

    def test_width_is_padded_to_the_widest_line_number(self):
        """Right-aligned numbers keep the │ separator in one column."""
        (numbered,) = prompt_call("number_lines", [str(i) for i in range(1, 11)])
        lines = numbered.split("\n")
        assert lines[0] == " 1 │ 1"
        assert lines[9] == "10 │ 10"

    def test_empty_buffer_yields_an_empty_string(self):
        (numbered,) = prompt_call("number_lines", [])
        assert numbered == ""

    def test_numbering_can_start_at_a_buffer_offset(self):
        """A range review (ai.context) must cite the buffer's own line numbers.

        Numbering an excerpt from 1 would make every `L<n>` in the reply point
        at the wrong place -- silently, since the numbers still look plausible.
        """
        (numbered,) = prompt_call("number_lines", ["alpha", "beta"], 10)
        assert numbered == "10 │ alpha\n11 │ beta"

    def test_offset_width_follows_the_last_number_not_the_count(self):
        """Two lines starting at 9 render 9 and 10, so the column is two wide.

        Padding to the *count* (one digit here) would ragged the │ separator
        exactly when the range crosses a power of ten.
        """
        (numbered,) = prompt_call("number_lines", ["alpha", "beta"], 9)
        assert numbered == " 9 │ alpha\n10 │ beta"

    def test_an_explicit_start_of_one_matches_the_default(self):
        """The default path must stay byte-identical; every existing caller
        (the buffer check and its fix step) relies on it."""
        (implicit,) = prompt_call("number_lines", ["alpha", "beta"])
        (explicit,) = prompt_call("number_lines", ["alpha", "beta"], 1)
        assert implicit == explicit


class TestHintSystem:
    """The cursor-unit hint prompt (<leader>qh).

    Its two load-bearing instructions are negative ones, and a prompt edit that
    drops either turns the feature into something else: without the
    quote-the-code rule the reply is advice that never read the input, and
    without the no-rewrite rule the user gets a patch that nothing verified
    against the buffer (unlike the fix flow, where apply_edits checks it).
    """

    LANG = "lua"
    PATH = "lua/x.lua"
    LABEL = "定義 `greet` (function_declaration) L12-30"

    def system(self):
        (text,) = prompt_call("hint_system", self.LANG, self.PATH, 12, 30)
        return text

    def test_carries_the_language_path_and_range(self):
        text = self.system()
        assert self.LANG in text
        assert self.PATH in text
        assert "12-30" in text

    def test_holds_nothing_read_out_of_the_buffer(self):
        """The instruction is passed in argv (`claude -p <instruction>`), where
        `ps aux` shows it to every process on the machine. The unit's
        description carries an identifier lifted from the user's buffer, so it
        must travel in the stdin payload instead -- see hint_input. Only the
        filetype, the path and the line numbers belong here, matching what
        check_buffer_system already puts in argv.
        """
        assert "greet" not in self.system()

    def test_requires_every_hint_to_quote_the_code(self):
        text = self.system()
        assert "MUST quote an identifier or a line" in text
        assert "add tests" in text, "the generic-advice example was dropped"

    def test_forbids_returning_rewritten_code(self):
        text = self.system()
        assert "Do NOT output a rewritten version, a diff, or a patch." in text

    def test_declares_the_three_report_headings(self):
        for heading in ("## 要約", "## 改善ヒント", "## 確認が必要な点"):
            assert heading in self.system()

    def test_asks_for_the_line_number_citation_format(self):
        assert "`- L<n>: <問題点> -> <改善案>`" in self.system()

    def test_says_the_number_prefix_is_not_part_of_the_file(self):
        """number_lines' `N │` prefix is not valid source; without this the
        model reports the separator itself as a syntax error."""
        text = self.system()
        assert "'│' separator" in text
        assert "NOT part of the file" in text

    def test_warns_that_the_unit_was_cut_out_of_its_file(self):
        """Callers and callees are genuinely absent from the payload, so a
        guess about them must land under 確認が必要な点, not in the hints."""
        text = self.system()
        assert "cut out of its file" in text

    def test_defines_an_exact_reply_for_nothing_to_report(self):
        assert "指摘はありません。" in self.system()

    def test_does_not_name_a_transport(self):
        """Same trap as replace_system (3fae7ab): naming stdin points a tool
        with no stdin path at somewhere the code is not. This prompt goes to
        claude and gemini today; the wording must not be what breaks if it
        ever goes anywhere else."""
        assert "stdin" not in self.system().lower()

    def test_announces_the_shape_hint_input_produces(self):
        """The instruction promises a description followed by numbered code;
        hint_input is what has to deliver it. Split across two functions, the
        two can drift silently."""
        text = self.system()
        assert "opens with a one-line description" in text


class TestHintInput:
    """The stdin half of the hint request. Everything derived from buffer
    contents lives here rather than in the instruction, because only this half
    avoids argv."""

    LABEL = "定義 `greet` (function_declaration) L12-13"
    LINES = ["local function greet(name)", "end"]

    def payload(self):
        (text,) = prompt_call("hint_input", self.LABEL, self.LINES, 12)
        return text

    def test_leads_with_the_unit_description(self):
        assert self.payload().startswith("## 対象\n" + self.LABEL)

    def test_numbers_the_code_from_its_buffer_line(self):
        text = self.payload()
        assert "12 │ local function greet(name)" in text
        assert "13 │ end" in text

    def test_labels_the_number_prefix_the_way_the_instruction_describes_it(self):
        assert "## コード (各行: <行番号> │ <本文>)" in self.payload()


class TestFormatDiagnostics:
    # vim.diagnostic.severity: ERROR=1, WARN=2, INFO=3, HINT=4.
    ERROR, WARN, INFO, HINT = 1, 2, 3, 4

    def diag(self, lnum, col, severity, message, end_col=None):
        d = {"lnum": lnum, "col": col, "severity": severity, "message": message}
        if end_col is not None:
            d["end_col"] = end_col
        return d

    def test_converts_zero_based_positions_to_one_based(self):
        (lines,) = prompt_call(
            "format_diagnostics",
            [self.diag(0, 4, self.ERROR, "boom", end_col=9)],
            "lua/x.lua",
        )
        assert lines == ["[ERROR] boom @lua/x.lua :L1:C5-C10"]

    def test_missing_end_col_collapses_to_the_start_column(self):
        (lines,) = prompt_call(
            "format_diagnostics", [self.diag(2, 0, self.WARN, "hmm")], "x.lua"
        )
        assert lines == ["[WARN] hmm @x.lua :L3:C1-C1"]

    def test_diagnostics_are_sorted_by_line(self):
        (lines,) = prompt_call(
            "format_diagnostics",
            [
                self.diag(9, 0, self.INFO, "third"),
                self.diag(0, 0, self.HINT, "first"),
                self.diag(4, 0, self.ERROR, "second"),
            ],
            "x.lua",
        )
        assert [line.split()[1] for line in lines] == ["first", "second", "third"]

    def test_every_severity_has_a_label(self):
        (lines,) = prompt_call(
            "format_diagnostics",
            [
                self.diag(0, 0, self.ERROR, "e"),
                self.diag(1, 0, self.WARN, "w"),
                self.diag(2, 0, self.INFO, "i"),
                self.diag(3, 0, self.HINT, "h"),
            ],
            "x.lua",
        )
        assert [line.split("]")[0] for line in lines] == [
            "[ERROR",
            "[WARN",
            "[INFO",
            "[HINT",
        ]

    def test_unknown_severity_is_labelled_rather_than_dropped(self):
        (lines,) = prompt_call(
            "format_diagnostics", [self.diag(0, 0, 99, "mystery")], "x.lua"
        )
        assert lines == ["[UNKNOWN] mystery @x.lua :L1:C1-C1"]

    def test_no_diagnostics_yields_no_lines(self):
        (lines,) = prompt_call("format_diagnostics", [], "x.lua")
        assert lines == []


# The Vim string literals inside `let l:sys = printf(...)`; both editors build
# this prompt by concatenating double-quoted pieces across continuation lines.
_VIM_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unescape_vim(literal: str) -> str:
    """Resolve the escapes a Vim double-quoted string actually uses here."""
    return (
        literal.replace("\\\\", "\x00")
        .replace("\\n", "\n")
        .replace('\\"', '"')
        .replace("\x00", "\\")
    )


def vim_replace_system(lang: str, user_request: str) -> str:
    """Render .vim/rc/70-ai.vim's s:AI_Submit system prompt from source.

    Reading the literals is the only practical way to get at it: the prompt is
    built inside a script-local function that needs a `b:ai_ctx` set up by the
    surrounding UI, so it cannot simply be called. The region is anchored at
    both ends and the assertions below check the extraction is non-empty, so
    a rewrite of that block fails this test rather than passing vacuously.
    """
    src = VIM_AI_RC.read_text(encoding="utf-8")
    start = src.index("let l:sys = printf(")
    end = src.index("l:ctx.lang, l:prompt)", start)
    joined = "".join(
        _unescape_vim(m.group(1)) for m in _VIM_STRING.finditer(src[start:end])
    )
    assert "AI assistant" in joined, (
        "could not extract the Vim system prompt -- did s:AI_Submit's printf "
        "block get rewritten? Re-anchor this extractor before trusting it."
    )
    # printf's two %s, in order: the filetype, then the user's request.
    return joined.replace("%s", lang, 1).replace("%s", user_request, 1)


class TestReplaceSystemStaysInStepWithVim:
    """The one cross-editor invariant in this module, and it keeps breaking.

    `replace_system`'s docstring requires the sentence to stay byte-identical
    to `.vim/rc/70-ai.vim`'s `s:AI_Submit` apart from the editor name, and
    both files carry a comment saying so -- yet 4832ef8 ("align the AI tool
    set with Neovim") and 3fae7ab ("stop naming stdin in the replace-selection
    prompt") are that invariant drifting and being repaired by hand. Nothing
    held the line afterwards. The suite already guards duplicated content this
    way for the hooks (test_hook_sync); this does the same for the one prompt
    that exists twice.
    """

    LANG = "lua"
    REQUEST = "extract this into a helper"

    def test_the_two_editors_build_the_same_prompt(self):
        (from_nvim,) = prompt_call("replace_system", self.LANG, self.REQUEST)
        from_vim = vim_replace_system(self.LANG, self.REQUEST)
        assert from_vim.replace("a Vim editor", "a Neovim editor") == from_nvim

    def test_only_the_editor_name_differs(self):
        """Guard the normalisation above from hiding a real divergence.

        If the two prompts ever agreed only *because* the substitution papered
        over more than the editor name, the test above would still pass.
        """
        (from_nvim,) = prompt_call("replace_system", self.LANG, self.REQUEST)
        from_vim = vim_replace_system(self.LANG, self.REQUEST)
        assert "a Vim editor" in from_vim
        assert "a Neovim editor" in from_nvim
        assert from_vim.count("a Vim editor") == 1
        assert from_nvim.replace("a Neovim editor", "a Vim editor") == from_vim

    def test_neither_prompt_names_stdin(self):
        """The specific regression 3fae7ab fixed.

        copilot has no stdin path -- backend.build_cli_cmd appends the
        selection to this instruction under an `## Input` heading -- so a
        prompt that says "provided via stdin" points copilot at somewhere the
        text is not. The wording must stay transport-agnostic on both sides.
        """
        (from_nvim,) = prompt_call("replace_system", self.LANG, self.REQUEST)
        assert "stdin" not in from_nvim.lower()
        assert "stdin" not in vim_replace_system(self.LANG, self.REQUEST).lower()


# --------------------------------------------------------------------------
# The fix flow, end to end: model reply -> parse_edits -> apply_edits -> the
# result window -> `y`. See the module docstring for why this mechanism exists
# alongside nvim_call.lua.
# --------------------------------------------------------------------------

# The text ai.ui pre-fills every response buffer with. If this string ever
# reaches on_accept, init.lua's apply_fix overwrites the user's ENTIRE buffer
# with it -- the whole file replaced by one line of UI chrome.
LOADING_PLACEHOLDER = "[claude: waiting for response...]"

# Raw string: the Lua below writes real "\n" escapes that Python must not eat.
UI_SCENARIO_LUA = r"""
-- Drive ai.ui's result window for real and report what an accept keypress
-- hands back to the caller. Run as `nvim -l <this> <repo-root>` with a request
-- object on stdin; see test_nvim_ai_prompt.py, the only caller.
--
-- Request: { "mode": "fix",    "original": [...], "raw": "<model reply>" }
--       or { "mode": "direct", "original": [...], "lines": [...] }
--       or { "mode": "report", "lines": [...] }
-- Reply:   { "accepted": [...]|null,  -- what on_accept received, if it fired
--            "response": [...],       -- the response pane, before the keypress
--            "thrown": "<err>"|null,  -- what done() threw, if anything
--            "status": "<st>"|null,   -- report mode: the status `f` read
--            "notified": "<joined>" } -- every vim.notify message
local root = arg[1]
package.path = table.concat({
  root .. "/.config/nvim/lua/?.lua",
  root .. "/.config/nvim/lua/?/init.lua",
  package.path,
}, ";")

local prompt = require("setup.functions.ai.prompt")
local ui = require("setup.functions.ai.ui")

local req = vim.json.decode(io.read("*a"))

-- active_lines() refuses a tab that is not "done" through vim.notify, so these
-- messages are the only user-visible proof of the status it read.
local notified = {}
vim.notify = function(msg)
  notified[#notified + 1] = tostring(msg)
end

local out = {
  accepted = vim.NIL,
  response = vim.NIL,
  thrown = vim.NIL,
  status = vim.NIL,
}

-- The real done() runs inside a job callback, so a throw there is swallowed by
-- the event loop and the window is left standing -- which is the entire defect.
-- pcall reproduces that instead of tearing the scenario down at the throw.
local function deliver(done, ok, lines, err)
  local called, thrown = pcall(done, ok, lines, err)
  if not called then
    out.thrown = tostring(thrown)
  end
end

-- Mirrors init.lua's start_fix: parse the model's reply, splice it against the
-- snapshot, and hand the patched buffer to the UI.
local function start_fix(_, done)
  local edits, perr = prompt.parse_edits(req.raw)
  if not edits then
    deliver(done, false, {}, "修正の解析に失敗しました: " .. tostring(perr))
  elseif #edits == 0 then
    deliver(done, false, {}, "適用できる修正がありませんでした。")
  else
    local patched, applied = prompt.apply_edits(req.original, edits)
    if applied == 0 then
      deliver(done, false, {}, "修正を安全に適用できませんでした（行が一致しません）。")
    else
      deliver(done, true, patched, nil)
    end
  end
  return 0
end

-- Invoke a buffer-local mapping the way the user's keypress would.
local function press(buf, key)
  for _, m in ipairs(vim.api.nvim_buf_get_keymap(buf, "n")) do
    if m.lhs == key and m.callback then
      m.callback()
      return
    end
  end
  error("no '" .. key .. "' mapping on the result buffer")
end

if req.mode == "report" then
  local handle = ui.open_report({
    name = "[AI Buffer Check]",
    keymaps = {
      {
        key = "f",
        desc = "Fix issues with AI",
        fn = function(ctx) out.status = ctx.status end,
      },
    },
    start = function(done)
      deliver(done, true, req.lines, nil)
      return 0
    end,
  })
  out.response = vim.api.nvim_buf_get_lines(handle.buf, 0, -1, false)
  press(handle.buf, "f")
else
  ui.run_multi({
    mode = "diff",
    tools = { "claude" },
    original = req.original,
    footer = " y:apply fix  Y:merged  q:cancel ",
    start = req.mode == "fix" and start_fix or function(_, done)
      deliver(done, true, req.lines, nil)
      return 0
    end,
    on_accept = function(_, lines) out.accepted = lines end,
  })
  -- run_multi focuses the response pane, and accepting wipes its buffer, so
  -- snapshot what the user is looking at before pressing anything.
  local buf = vim.api.nvim_win_get_buf(vim.api.nvim_get_current_win())
  out.response = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  press(buf, "y")
end

-- Joined rather than an array: vim.json.encode renders an empty Lua table as
-- `{}`, and "no notifications" is a normal outcome here.
out.notified = table.concat(notified, "\n")
io.stdout:write(vim.json.encode(out), "\n")
"""


def ui_scenario(tmp_path, **request):
    """Run one UI_SCENARIO_LUA scenario and return its decoded reply."""
    script = tmp_path / "ai_ui_scenario.lua"
    script.write_text(UI_SCENARIO_LUA, encoding="utf-8")
    proc = subprocess.run(
        ["nvim", "-l", str(script), str(REPO_ROOT)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"scenario {request.get('mode')!r} exited {proc.returncode}\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    # nvim writes "Shell cwd was reset" and friends to stderr, but pick the
    # reply defensively rather than trusting stdout to hold nothing else.
    replies = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert replies, f"scenario emitted no reply\nstderr: {proc.stderr}"
    return json.loads(replies[-1])


def fix_reply(fixed, start=2, stop=2, original=("two",)):
    """A model reply in the shape fix_buffer_system asks for."""
    return json.dumps(
        [{"start": start, "end": stop, "original": list(original), "fixed": fixed}]
    )


class TestFixFlowNeverAcceptsTheLoadingPlaceholder:
    """The worst outcome this tree can produce, and it needed two bugs.

    `fixed` is the only model-supplied value that reaches nvim_buf_set_lines
    verbatim, and that API refuses an item that is not a string or that carries
    a newline. apply_edits checked `fixed` was a table and never looked
    inside it, so `{"fixed": ["TWO\\nEXTRA"]}` sailed through into ui.run_multi's
    done callback -- which flipped the tab's status to "done" BEFORE the render,
    then threw. The throw was swallowed by the job callback it runs in, leaving
    the window open, the response pane still showing its loading placeholder,
    and the tab claiming to be done. Pressing `y` there passed active_lines'
    status check and handed that one line of UI chrome to on_accept, whose
    apply_fix replaces the whole buffer with what it is given.

    Both halves are pinned here: element validation (so the throw stops
    happening) and the status ordering (so the next throw, for whatever reason,
    is a display bug rather than a destroyed file).
    """

    LINES = ["one", "two", "three"]

    def test_a_newline_inside_a_fixed_line_never_reaches_on_accept(self, tmp_path):
        got = ui_scenario(
            tmp_path, mode="fix", original=self.LINES, raw=fix_reply(["TWO\nEXTRA"])
        )
        assert got["accepted"] is None, (
            f"`y` handed {got['accepted']!r} to the buffer-overwrite path"
        )
        assert LOADING_PLACEHOLDER not in (got["accepted"] or [])

    def test_a_non_string_fixed_line_never_reaches_on_accept(self, tmp_path):
        got = ui_scenario(
            tmp_path, mode="fix", original=self.LINES, raw=fix_reply([42])
        )
        assert got["accepted"] is None, (
            f"`y` handed {got['accepted']!r} to the buffer-overwrite path"
        )
        assert LOADING_PLACEHOLDER not in (got["accepted"] or [])

    def test_a_rejected_fix_leaves_the_tab_unacceptable(self, tmp_path):
        """The status `y` consults must not say "done" for a fix that is not.

        Asserting on_accept stayed silent is not enough on its own: the tab has
        to be visibly not-done, or the same hole reopens the moment anything
        else in this path throws.
        """
        got = ui_scenario(
            tmp_path, mode="fix", original=self.LINES, raw=fix_reply(["TWO\nEXTRA"])
        )
        assert "not available" in got["notified"], got["notified"]
        assert got["response"] != [LOADING_PLACEHOLDER], (
            "the pane still shows the placeholder while the tab reports success"
        )

    def test_a_clean_fix_still_applies_end_to_end(self, tmp_path):
        """The regression guard for both fixes at once.

        An over-broad element check, or a status that is never written, would
        pass every assertion above by breaking the feature outright.
        """
        got = ui_scenario(
            tmp_path, mode="fix", original=self.LINES, raw=fix_reply(["TWO"])
        )
        assert got["thrown"] is None, got["thrown"]
        assert got["accepted"] == ["one", "TWO", "three"]
        assert got["response"] == ["one", "TWO", "three"]

    def test_the_writable_half_of_a_mixed_batch_still_reaches_the_buffer(
        self, tmp_path
    ):
        """The case only apply_edits' half can carry.

        Every other scenario here is satisfied by the status ordering alone --
        it turns the throw into a visible failure, and a failed tab is not
        acceptable. This one asks for more: one bad edit among good ones must
        cost the user only that edit. Without the element check the batch still
        carries the unwritable line into the render, the render throws, and the
        good edit dies with it.
        """
        raw = json.dumps(
            [
                {"start": 1, "end": 1, "original": ["one"], "fixed": ["ONE"]},
                {"start": 2, "end": 2, "original": ["two"], "fixed": ["TWO\nEXTRA"]},
            ]
        )
        got = ui_scenario(tmp_path, mode="fix", original=self.LINES, raw=raw)
        assert got["thrown"] is None, got["thrown"]
        assert got["accepted"] == ["ONE", "two", "three"]

    def test_an_unwritable_response_line_is_reported_as_a_failure(self, tmp_path):
        """ui.lua's own half, with apply_edits out of the picture.

        Nothing in the fix flow can produce these lines any more, which is
        exactly why this drives run_multi directly: the ordering fix exists to
        keep the NEXT source of a throw from costing the user their buffer.
        """
        got = ui_scenario(tmp_path, mode="direct", original=self.LINES, lines=["a\nb"])
        assert got["accepted"] is None, (
            f"`y` handed {got['accepted']!r} to the buffer-overwrite path"
        )
        assert "not available" in got["notified"], got["notified"]
        assert got["response"] != [LOADING_PLACEHOLDER]
        assert "failed" in got["response"][0], got["response"]

    def test_a_writable_response_is_still_accepted(self, tmp_path):
        got = ui_scenario(
            tmp_path, mode="direct", original=self.LINES, lines=["x", "y"]
        )
        assert got["accepted"] == ["x", "y"]
        assert got["notified"] == ""

    def test_open_report_does_not_call_a_failed_render_done(self, tmp_path):
        """The same ordering, in the driver behind the buffer-check report.

        `f` there reads ctx.status and then sends the report buffer off to be
        fixed; a status of "done" over an unrendered pane would feed the AI its
        own placeholder.
        """
        got = ui_scenario(tmp_path, mode="report", lines=["a\nb"])
        assert got["status"] == "failed", got
        assert "failed" in got["response"][0], got["response"]

    def test_open_report_still_calls_a_good_render_done(self, tmp_path):
        got = ui_scenario(tmp_path, mode="report", lines=["# report", "body"])
        assert got["status"] == "done"
        assert got["response"] == ["# report", "body"]
