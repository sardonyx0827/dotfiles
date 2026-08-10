"""Tests for the session-report renderer.

The renderer turns a markdown report (with ```mermaid fences) into a single
self-contained HTML page that is opened straight from `file://`. Two of the
assertions here are not style preferences but the reason the design works at
all, so they must not be relaxed:

- The library tags must stay *classic* scripts. Chrome refuses ES-module
  imports on a `file://` origin (opaque origin -> CORS failure), so a
  `type="module"` tag would render every diagram blank. mermaid's
  `dist/mermaid.min.js` ends with `globalThis["mermaid"] = ...` and marked's
  `marked.min.js` is a UMD bundle ending in `g["marked"]=f()`, which is exactly
  why the classic form works.
- The markdown must reach the page base64-encoded. Reports quote shell and JS,
  so a report that merely mentions `</script>` would otherwise terminate the
  embedding tag early and truncate the page.
"""

import base64
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

SKILL_DIR = REPO_ROOT / ".claude/skills/session-report"
sys.path.insert(0, str(SKILL_DIR))

render_report = pytest.importorskip("render_report")


def decode_embedded_markdown(html: str) -> str:
    """Pull the base64 payload back out of the generated page."""
    match = re.search(r'const MD_B64 = "([^"]*)"', html)
    assert match, "generated page carries no MD_B64 payload"
    return base64.b64decode(match.group(1)).decode("utf-8")


class TestBuildHtml:
    def test_markdown_round_trips_through_the_payload(self):
        md = "# 見出し\n\nマルチバイトと `code` を含む本文\n"
        html = render_report.build_html(md, title="t", assets=render_report.CDN_ASSETS)
        assert decode_embedded_markdown(html) == md

    def test_content_that_closes_a_script_tag_cannot_break_out(self):
        # A report about this very feature would quote a closing script tag.
        md = "# r\n\n```html\n</script><img src=x onerror=alert(1)>\n```\n"
        html = render_report.build_html(md, title="t", assets=render_report.CDN_ASSETS)
        assert decode_embedded_markdown(html) == md
        assert "onerror=alert(1)" not in html
        # Exactly the shell's own script tags -- none contributed by content.
        assert html.count("</script>") == html.count("<script")

    def test_library_tags_are_classic_not_modules(self):
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        assert 'type="module"' not in html
        assert ".esm." not in html
        for src in render_report.CDN_ASSETS.values():
            assert f'<script src="{src}"></script>' in html

    def test_local_assets_are_referenced_as_file_urls(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        for name in ("mermaid.min.js", "marked.min.js"):
            (assets_dir / name).write_text("//stub\n", encoding="utf-8")

        assets = render_report.resolve_assets(assets_dir)

        assert assets["mermaid"] == (assets_dir / "mermaid.min.js").as_uri()
        assert assets["marked"] == (assets_dir / "marked.min.js").as_uri()

    def test_cdn_is_used_when_a_local_asset_is_missing(self, tmp_path):
        assets_dir = tmp_path / "assets"
        assets_dir.mkdir()
        (assets_dir / "marked.min.js").write_text("//stub\n", encoding="utf-8")

        assets = render_report.resolve_assets(assets_dir)

        assert assets["mermaid"] == render_report.CDN_ASSETS["mermaid"]
        assert assets["marked"] == (assets_dir / "marked.min.js").as_uri()

    def test_mermaid_selector_matches_what_marked_actually_emits(self):
        """The one string that silently breaks every diagram if marked changes it.

        marked 15.0.12 renders a ```mermaid fence as
        `<pre><code class="language-mermaid">`. The page's selector is written
        against exactly that. A marked upgrade that altered the class would not
        raise anything -- diagrams would just stay code blocks -- so pin it.
        """
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        assert "pre > code.language-mermaid" in html

    def test_untrusted_markup_is_scrubbed_in_an_inert_document(self):
        """Reports quote tool output; innerHTML would fire <img onerror> on assign."""
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        assert "DOMParser" in html
        assert "content.innerHTML" not in html
        assert 'name.startsWith("on")' in html

    def test_svg_animation_elements_are_removed(self):
        """`<animate attributeName="href">` rewrites a link that passed the scrub.

        SVG-namespace names are case-sensitive to a CSS type selector, unlike
        HTML's, so the exact casing in the selector is load-bearing.
        """
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        for tag in ("animate", "animateTransform", "animateMotion", "set"):
            assert f", {tag}" in html, f"{tag} is missing from the removal selector"

    def test_mermaid_security_level_is_stated_explicitly(self):
        """mermaid builds its own SVG from the same markdown, downstream of the scrub."""
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        assert 'securityLevel: "strict"' in html

    def test_no_control_characters_leaked_into_the_page(self):
        """The scheme filter strips C0 bytes; it must not be written using them."""
        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        assert not any(ord(c) < 9 or 13 < ord(c) < 32 for c in html)
        assert r"\u0000-\u0020" in html

    def test_title_is_html_escaped(self):
        html = render_report.build_html(
            "# r\n", title='a<b>&"', assets=render_report.CDN_ASSETS
        )
        assert "<title>a&lt;b&gt;&amp;&quot;</title>" in html
        assert "<title>a<b>" not in html


class TestExtractTitle:
    def test_prefers_the_first_atx_heading(self):
        assert (
            render_report.extract_title("\n\n# 実装レポート\n\n## 詳細\n", "fallback")
            == "実装レポート"
        )

    def test_ignores_a_heading_inside_a_fenced_block(self):
        md = "```md\n# ダミー\n```\n\n# 本物\n"
        assert render_report.extract_title(md, "fallback") == "本物"

    def test_falls_back_when_there_is_no_heading(self):
        assert render_report.extract_title("just text\n", "fallback") == "fallback"

    def test_a_different_fence_marker_inside_a_block_does_not_close_it(self):
        # A shell example containing a heredoc marker is ordinary report content.
        md = "```bash\ncat <<~~~\n# ダミー見出し\n~~~\n```\n\n# 本物\n"
        assert render_report.extract_title(md, "fallback") == "本物"

    def test_a_longer_closing_fence_still_closes(self):
        md = "```\n# ダミー\n````\n\n# 本物\n"
        assert render_report.extract_title(md, "fallback") == "本物"

    def test_closed_atx_heading_drops_the_trailing_hashes(self):
        assert render_report.extract_title("# タイトル #\n", "fallback") == "タイトル"


class TestRender:
    def test_writes_html_next_to_the_markdown_without_opening(self, tmp_path):
        md_path = tmp_path / "20260810-report.md"
        md_path.write_text(
            "# タイトル\n\n```mermaid\nflowchart LR\n A-->B\n```\n", encoding="utf-8"
        )

        html_path = render_report.render(md_path, open_browser=False)

        assert html_path == tmp_path / "20260810-report.html"
        html = html_path.read_text(encoding="utf-8")
        assert "<title>タイトル</title>" in html
        assert decode_embedded_markdown(html).startswith("# タイトル")

    def test_open_browser_uses_the_platform_opener(self, tmp_path, monkeypatch):
        md_path = tmp_path / "r.md"
        md_path.write_text("# r\n", encoding="utf-8")
        calls = []
        monkeypatch.setattr(
            render_report.subprocess, "run", lambda cmd, **kw: calls.append(cmd)
        )

        html_path = render_report.render(md_path, open_browser=True)

        assert calls, "no opener was invoked"
        assert str(html_path) == calls[0][-1]

    def test_a_missing_opener_does_not_lose_the_rendered_file(
        self, tmp_path, monkeypatch, capsys
    ):
        """check=False suppresses a bad exit code, not a missing executable.

        A headless box has no xdg-open at all, and the raised FileNotFoundError
        would throw away a page that had already been written successfully.
        """
        md_path = tmp_path / "r.md"
        md_path.write_text("# r\n", encoding="utf-8")

        def no_such_opener(cmd, **kwargs):
            raise FileNotFoundError(2, "No such file or directory", cmd[0])

        monkeypatch.setattr(render_report.subprocess, "run", no_such_opener)

        html_path = render_report.render(md_path, open_browser=True)

        assert html_path.is_file()
        assert "could not launch" in capsys.readouterr().err

    def test_missing_input_is_a_clean_error_not_a_traceback(self, tmp_path, capsys):
        exit_code = render_report.main([str(tmp_path / "nope.md"), "--no-open"])

        assert exit_code != 0
        assert "nope.md" in capsys.readouterr().err


class TestMain:
    def test_success_prints_the_html_path_and_exits_zero(self, tmp_path, capsys):
        md_path = tmp_path / "r.md"
        md_path.write_text("# タイトル\n", encoding="utf-8")

        exit_code = render_report.main([str(md_path), "--no-open"])

        assert exit_code == 0
        printed = capsys.readouterr().out.strip()
        assert printed == str(tmp_path / "r.html")
        assert Path(printed).is_file()

    def test_out_directory_is_created_and_used(self, tmp_path, capsys):
        md_path = tmp_path / "r.md"
        md_path.write_text("# r\n", encoding="utf-8")
        out_dir = tmp_path / "nested" / "out"

        exit_code = render_report.main(
            [str(md_path), "--out", str(out_dir), "--no-open"]
        )

        assert exit_code == 0
        assert (out_dir / "r.html").is_file()
        assert capsys.readouterr().out.strip() == str(out_dir / "r.html")


class TestEmbeddedScript:
    def test_the_generated_page_script_is_syntactically_valid(self, tmp_path):
        """The page's JS is a Python string literal; nothing else parses it.

        Every other test asserts on substrings, so a stray brace or bad escape
        would ship as a page that silently renders nothing.
        """
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")

        html = render_report.build_html(
            "# r\n\n```mermaid\nflowchart LR\n A-->B\n```\n",
            title="t",
            assets=render_report.CDN_ASSETS,
        )
        # The inline script is the only <script> without a src attribute.
        inline = re.search(r"<script>\n(.*?)\n</script>", html, re.DOTALL)
        assert inline, "no inline script found in the generated page"

        script = tmp_path / "page.js"
        script.write_text(inline.group(1), encoding="utf-8")
        result = subprocess.run(
            [node, "--check", str(script)], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        ("url", "safe"),
        [
            # The bug this replaced a denylist to fix: the HTML parser turns the
            # entity into a real TAB, a `^(javascript|...)` regex stops matching,
            # and the URL parser strips the TAB again on navigation.
            ("java\tscript:alert(1)", False),
            ("java\nscript:alert(1)", False),
            ("java\rscript:alert(1)", False),
            ("  JaVaScRiPt:alert(1)", False),
            ("\x01javascript:alert(1)", False),
            ("data:text/html,<script>alert(1)</script>", False),
            ("vbscript:msgbox(1)", False),
            # An unknown scheme must fail closed, not sail through a denylist.
            ("chrome-extension://abc/x.html", False),
            ("https://example.com/a", True),
            ("http://example.com/a", True),
            ("mailto:someone@example.com", True),
            ("#section-1", True),
            ("./sibling.html", True),
            ("/absolute/path", True),
            ("", True),
        ],
    )
    def test_url_scheme_filter_rejects_known_evasions(self, tmp_path, url, safe):
        """Run the page's own isSafeUrl, not a reimplementation of it."""
        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")

        html = render_report.build_html(
            "# r\n", title="t", assets=render_report.CDN_ASSETS
        )
        fn = re.search(r"( *function isSafeUrl\(value\) \{.*?\n *\})", html, re.DOTALL)
        assert fn, "isSafeUrl is gone -- the scheme filter was renamed or removed"

        harness = tmp_path / "check.js"
        harness.write_text(
            f"{fn.group(1)}\nprocess.stdout.write(String(isSafeUrl(process.argv[2])));\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [node, str(harness), url], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == str(safe).lower(), f"{url!r} -> {result.stdout}"
