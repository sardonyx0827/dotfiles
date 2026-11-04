"""session-dossier: the Markdown subset rendered server-side.

Claude's replies, prompts and subagent reports are Markdown. The page renders
a fixed subset of it in Python. Every character of content is escaped first,
and only fixed tags (no attributes taken from content, no links, no images)
are produced, so the page keeps its one constant script and nothing from
the transcript can become markup.
"""

import sys
import time

from conftest import REPO_ROOT
from dossier_fixtures import SessionBuilder

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_markdown  # noqa: E402
import dossier_render  # noqa: E402
import dossier_scope  # noqa: E402

md = dossier_markdown.render_markdown


class TestBlocks:
    def test_table_with_inline_formatting(self):
        out = md("| a | b |\n|---|:-:|\n| 1 | **2** |\n| `x` | 3 \\| 4 |")
        assert "<table>" in out
        assert "<th>a</th><th>b</th>" in out
        assert "<td>1</td><td><strong>2</strong></td>" in out
        assert "<td><code>x</code></td><td>3 | 4</td>" in out

    def test_extra_cells_are_cut_to_the_header(self):
        out = md("| a | b |\n|---|---|\n| 1 | 2 | 3 |")
        assert "<td>3</td>" not in out

    def test_headings(self):
        assert md("## 見出し") == '<div class="md-h md-h2">見出し</div>'
        assert md("####### seven") == "<p>####### seven</p>"

    def test_fenced_code_is_escaped_verbatim(self):
        out = md("```html\n<script>alert(1)</script>\n**not bold**\n```")
        assert out == (
            '<pre class="md-code"><code>&lt;script&gt;alert(1)&lt;/script&gt;\n'
            "**not bold**</code></pre>"
        )

    def test_unclosed_fence_runs_to_the_end(self):
        assert md("```\na\nb").startswith('<pre class="md-code"><code>a\nb')

    def test_nested_and_ordered_lists(self):
        assert md("- a\n  - b\n- c") == (
            "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
        )
        assert md("1. x\n2. y") == "<ol><li>x</li><li>y</li></ol>"
        assert md("- a\n  続き") == "<ul><li>a\n続き</li></ul>"

    def test_quote_rule_and_paragraphs(self):
        assert md("> q") == "<blockquote><p>q</p></blockquote>"
        assert md("---") == "<hr>"
        assert md("a\nb\n\nc") == "<p>a\nb</p>\n<p>c</p>"


class TestSafety:
    def test_markup_in_content_stays_text(self):
        out = md(
            '**<img src=x onerror=alert(1)>** `<b>` | <a href="javascript:x">l</a>'
        )
        assert "<img" not in out
        assert "<a " not in out
        assert "<strong>&lt;img src=x onerror=alert(1)&gt;</strong>" in out
        assert "<code>&lt;b&gt;</code>" in out

    def test_links_and_images_are_not_turned_into_markup(self):
        out = md("[t](javascript:alert(1)) ![i](http://x/y.png)")
        assert "href" not in out and "src" not in out

    def test_only_fixed_tags_are_produced(self):
        import re

        out = md("# h\n| a |\n|---|\n| b |\n- x\n> q\n---\n```\nc\n```\n`i` **s**")
        tags = set(re.findall(r"</?([a-z0-9]+)", out))
        assert tags <= {
            "div",
            "p",
            "table",
            "thead",
            "tbody",
            "tr",
            "th",
            "td",
            "ul",
            "ol",
            "li",
            "pre",
            "code",
            "strong",
            "blockquote",
            "hr",
        }

    def test_pathological_input_stays_fast(self):
        start = time.perf_counter()
        md(
            "**" * 50_000
            + "\n"
            + "`" * 50_000
            + "\n"
            + "|" * 50_000
            + "\n"
            + "> " * 2_000
        )
        assert time.perf_counter() - start < 2.0


def load(main):
    return dossier_link.load_session(dossier_locate.paths_for(main))


class TestInThePage:
    def render(self, tmp_path, b, **opts):
        session = load(b.write(tmp_path))
        scope = dossier_scope.select_scope(session)
        return dossier_render.render(
            session, scope, dossier_render.RenderOptions(**opts)
        ).html

    def test_replies_prompts_and_agent_reports_are_rendered(self, tmp_path):
        b = SessionBuilder()
        b.prompt("## 依頼\n- 表を見せて")
        b.say("| 項目 | 値 |\n|---|---|\n| 件数 | **3** |")
        child = b.child("agentR")
        child.legacy_prompt("x")
        b.agent_call("a1", "agentR", child)
        page = self.render(tmp_path, b)
        assert '<div class="md-h md-h2">依頼</div>' in page
        assert "<td><strong>3</strong></td>" in page
        assert page.count('<div class="md">') >= 2

    def test_tool_output_stays_preformatted(self, tmp_path):
        b = SessionBuilder()
        b.bash("t1", "cat t.md", "| a |\n|---|\n| b |")
        page = self.render(tmp_path, b)
        assert "<table>" not in page

    def test_artifact_wrap_never_splits_a_tag(self, tmp_path):
        b = SessionBuilder()
        b.say("| a |\n|---|\n| " + "**x** " * 2000 + " |")
        page = self.render(tmp_path, b, target="artifact", wrap_width=1_000)
        body = page[page.index('<main class="dossier">') : page.index("</main>")]
        for line in page.splitlines():
            assert len(line) < 2_000
        # Inside the body every "<" and ">" is a tag the renderer wrote; a tag
        # split across lines would leave a line with one but not the other.
        for line in body.splitlines():
            assert line.count("<") == line.count(">"), line[:120]


class TestReviewFollowUps:
    def test_loose_ordered_lists_stay_one_list_and_keep_their_numbers(self):
        out = md("1. **First**\n   - d\n\n2. **Second**\n\n3. **Third**")
        assert out.count("<ol") == 1
        assert out.count("<li>") == 4
        assert md("3. c\n4. d") == '<ol start="3"><li>c</li><li>d</li></ol>'

    def test_fenced_code_inside_a_list_item(self):
        out = md("1. step\n\n   ```bash\n   ls -la\n   ```\n\n2. step2")
        assert out.count("<ol") == 1
        assert '<li>step<pre class="md-code"><code>ls -la</code></pre></li>' in out
        assert "<li>step2</li>" in out
        assert md("- a\n    ```\n    code\n    ```") == (
            '<ul><li>a<pre class="md-code"><code>code</code></pre></li></ul>'
        )

    def test_backtick_runs_are_never_mistaken_for_a_code_span(self):
        assert "<code>`</code>" not in md("a ``` b")

    def test_heading_with_a_long_run_of_spaces_is_linear(self):
        start = time.perf_counter()
        out = md("# a" + " " * 40_000 + "b")
        assert time.perf_counter() - start < 1.0
        assert out.startswith('<div class="md-h md-h1">a')
        assert md("## title ##") == '<div class="md-h md-h2">title</div>'
        assert md("#") == '<div class="md-h md-h1"></div>'

    def test_crlf_is_normalised(self):
        assert md("a\r\n\r\n---\r\n\r\nb") == "<p>a</p>\n<hr>\n<p>b</p>"

    def test_bold_around_a_code_span_and_triple_stars(self):
        assert md("**`foo`**") == "<p><strong><code>foo</code></strong></p>"
        assert "<td><strong><code>a</code></strong></td>" in md(
            "| x |\n|---|\n| **`a`** |"
        )
        assert md("***x***") == "<p>***x***</p>"
        assert md("a 0 b") == "<p>a 0 b</p>"


class TestTableBounds:
    def test_a_huge_header_is_not_a_table(self):
        source = "a|" * 6000 + "\n-|\n" + "|\n" * 6000
        start = time.perf_counter()
        out = md(source)
        assert time.perf_counter() - start < 2.0
        assert "<table>" not in out
        assert len(out) < 10 * len(source)

    def test_separator_must_match_the_header_width(self):
        assert "<table>" not in md("| a | b |\n|---|\n| 1 | 2 |")

    def test_short_rows_are_not_padded(self):
        out = md("| a | b |\n|---|---|\n| only |")
        assert "<tr><td>only</td></tr>" in out
