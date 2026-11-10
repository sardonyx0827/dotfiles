"""Render a fixed subset of Markdown to HTML, safely, in Python.

Claude's replies, the person's prompts and subagent reports are Markdown, and
shown as plain text their tables and headings are hard to read. The page must
still carry no script but its one constant, and nothing from a transcript may
become markup. So this renderer:

* escapes every piece of content with html.escape before it is placed;
* produces only fixed tags with fixed class names (and an ``<ol start>``
  that is always an integer it formatted itself): block elements for
  headings, tables, lists, quotes, rules and fenced code, and inline
  <code> and <strong>;
* never produces links or images, so no URL from content reaches an
  attribute.

Supported: ATX headings, GFM tables, fenced code (``` and ~~~, also inside a
list item), bullet and ordered lists (indentation nesting, continuation
lines, items separated by blank lines, the first number kept), block quotes,
thematic breaks, paragraphs (line breaks kept), `code` spans and **bold**.
Anything else, ``***x***`` and links included, is shown as the text it is.

Every pattern is anchored at a line start or bounded, trailing heading
hashes are stripped in plain Python, and quote nesting is capped, so long or
hostile input stays linear.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

Wrap = Callable[[str], str]

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_FENCE_IN_ITEM = re.compile(r"^([ \t]+)(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
_RULE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_SEP_CELL = re.compile(r"^:?-+:?$")
_CODE_SPAN = re.compile(r"`[^`\n]{1,2000}`")
_BOLD = re.compile(r"(?<!\*)\*\*(?![\s*])(.{1,2000}?)(?<![\s*])\*\*(?!\*)")
# Code spans are parked behind private-use characters while bold is matched,
# so **`x`** works; content's own private-use characters are dropped first.
_PARK_OPEN, _PARK_CLOSE = "", ""
_PARKED = re.compile(f"{_PARK_OPEN}(\\d+){_PARK_CLOSE}")
_MAX_QUOTE_DEPTH = 4
_MAX_COLUMNS = 64


def _identity(text: str) -> str:
    return text


def _inline(text: str) -> str:
    """Escape ``text`` and render `code` spans and **bold** inside it."""
    text = text.replace(_PARK_OPEN, "").replace(_PARK_CLOSE, "")
    spans: list[str] = []

    def park(match: re.Match[str]) -> str:
        spans.append(match.group(0)[1:-1])
        return f"{_PARK_OPEN}{len(spans) - 1}{_PARK_CLOSE}"

    escaped = html.escape(_CODE_SPAN.sub(park, text))
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    return _PARKED.sub(
        lambda m: f"<code>{html.escape(spans[int(m.group(1))])}</code>", escaped
    )


def _heading_title(raw: str | None) -> str:
    """Strip an ATX heading's optional closing hashes, without a regex."""
    title = (raw or "").rstrip(" \t")
    bare = title.rstrip("#")
    if bare != title and (bare == "" or bare[-1] in " \t"):
        title = bare.rstrip(" \t")
    return title


def _cells(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(row):
        if row[i] == "\\" and row[i + 1 : i + 2] == "|":
            current.append("|")
            i += 2
            continue
        if row[i] == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(row[i])
        i += 1
    cells.append("".join(current).strip())
    return cells


def _is_separator(line: str) -> bool:
    if "|" not in line or "-" not in line:
        return False
    return all(_SEP_CELL.match(cell) for cell in _cells(line))


def _is_table_start(header: str, separator: str) -> bool:
    """A GFM table: a header no wider than _MAX_COLUMNS and a separator row of
    the same width."""
    if not _is_separator(separator):
        return False
    width = len(_cells(header))
    return width <= _MAX_COLUMNS and len(_cells(separator)) == width


def _dedent(line: str, width: int) -> str:
    """Drop up to ``width`` columns of leading whitespace."""
    expanded = line.expandtabs(4)
    strip = min(width, len(expanded) - len(expanded.lstrip(" ")))
    return expanded[strip:]


class _Item:
    def __init__(self, indent: int, tag: str, start: int | None, text: str) -> None:
        self.indent = indent
        self.tag = tag
        self.start = start
        # ("text", [lines]) and ("code", text) segments, in order.
        self.segments: list[tuple[str, list[str] | str]] = [("text", [text])]

    def text_lines(self) -> list[str]:
        if self.segments[-1][0] != "text":
            self.segments.append(("text", []))
        lines = self.segments[-1][1]
        assert isinstance(lines, list)
        return lines


class _Renderer:
    def __init__(self, wrap: Wrap, depth: int) -> None:
        self.wrap = wrap
        self.depth = depth
        self.out: list[str] = []
        self.para: list[str] = []

    def flush(self) -> None:
        if self.para:
            body = "\n".join(_inline(line) for line in self.para)
            self.out.append(f"<p>{self.wrap(body)}</p>")
            self.para = []

    def code_html(self, text: str) -> str:
        return f'<pre class="md-code"><code>{self.wrap(html.escape(text))}</code></pre>'

    def render(self, lines: list[str]) -> str:
        i = 0
        while i < len(lines):
            i = self.block(lines, i)
        self.flush()
        return "\n".join(self.out)

    def block(self, lines: list[str], i: int) -> int:
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            return self.code(lines, i, fence.group(1))
        heading = _HEADING.match(line)
        if heading:
            self.flush()
            level = len(heading.group(1))
            title = self.wrap(_inline(_heading_title(heading.group(2))))
            self.out.append(f'<div class="md-h md-h{level}">{title}</div>')
            return i + 1
        if "|" in line and i + 1 < len(lines) and _is_table_start(line, lines[i + 1]):
            return self.table(lines, i)
        if _RULE.match(line):
            self.flush()
            self.out.append("<hr>")
            return i + 1
        if _LIST.match(line):
            return self.listing(lines, i)
        if _QUOTE.match(line) and self.depth < _MAX_QUOTE_DEPTH:
            return self.quote(lines, i)
        if line.strip() == "":
            self.flush()
            return i + 1
        self.para.append(line)
        return i + 1

    def code(self, lines: list[str], i: int, marker: str) -> int:
        self.flush()
        body: list[str] = []
        j = i + 1
        while j < len(lines) and not lines[j].lstrip().startswith(marker):
            body.append(lines[j])
            j += 1
        self.out.append(self.code_html("\n".join(body)))
        return j + 1

    def table(self, lines: list[str], i: int) -> int:
        self.flush()
        header = _cells(lines[i])
        rows: list[list[str]] = []
        j = i + 2
        while j < len(lines) and lines[j].strip() and "|" in lines[j]:
            # Extra cells are dropped (GFM); missing ones are not padded, so the
            # output never grows faster than the input.
            rows.append(_cells(lines[j])[: len(header)])
            j += 1
        head = "".join(f"<th>{self.wrap(_inline(c))}</th>" for c in header)
        body = "\n".join(
            "<tr>" + "".join(f"<td>{self.wrap(_inline(c))}</td>" for c in row) + "</tr>"
            for row in rows
        )
        self.out.append(
            '<div class="md-table"><table><thead><tr>'
            f"{head}</tr></thead><tbody>\n{body}\n</tbody></table></div>"
        )
        return j

    def collect_items(self, lines: list[str], i: int) -> tuple[list[_Item], int]:
        items: list[_Item] = []
        j = i
        while j < len(lines):
            line = lines[j]
            match = _LIST.match(line)
            if match:
                marker = match.group(2)
                ordered = marker not in "-*+"
                items.append(
                    _Item(
                        len(match.group(1).expandtabs(4)),
                        "ol" if ordered else "ul",
                        int(marker[:-1]) if ordered else None,
                        match.group(3),
                    )
                )
                j += 1
                continue
            if line.strip() == "":
                # A blank line ends the list only if what follows is neither
                # another item nor an indented continuation of this one.
                k = j
                while k < len(lines) and lines[k].strip() == "":
                    k += 1
                if (
                    k < len(lines)
                    and lines[k][:1] in " \t"
                    and not _LIST.match(lines[k])
                ):
                    items[-1].text_lines().append("")
                    j = k
                    continue
                if k < len(lines) and _LIST.match(lines[k]):
                    j = k
                    continue
                break
            if line[:1] not in " \t":
                break
            fence = _FENCE_IN_ITEM.match(line)
            if fence:
                width = len(fence.group(1).expandtabs(4))
                marker = fence.group(2)
                body: list[str] = []
                k = j + 1
                while k < len(lines) and not lines[k].lstrip().startswith(marker):
                    body.append(_dedent(lines[k], width))
                    k += 1
                items[-1].segments.append(("code", "\n".join(body)))
                j = k + 1
                continue
            items[-1].text_lines().append(line.strip())
            j += 1
        return items, j

    def item_html(self, item: _Item) -> str:
        parts = []
        for kind, value in item.segments:
            if kind == "code":
                assert isinstance(value, str)
                parts.append(self.code_html(value))
                continue
            assert isinstance(value, list)
            lines = list(value)
            while lines and lines[-1] == "":
                lines.pop()
            while lines and lines[0] == "":
                lines.pop(0)
            if lines:
                parts.append(self.wrap("\n".join(_inline(t) for t in lines)))
        return "".join(parts)

    def listing(self, lines: list[str], i: int) -> int:
        self.flush()
        items, j = self.collect_items(lines, i)

        def opening(item: _Item) -> str:
            if item.tag == "ol" and item.start not in (None, 1):
                return f'<ol start="{int(item.start or 1)}">'
            return f"<{item.tag}>"

        parts: list[str] = []
        stack: list[tuple[int, str]] = []
        for item in items:
            if not stack or item.indent > stack[-1][0]:
                parts.append(opening(item))
                stack.append((item.indent, item.tag))
            else:
                while len(stack) > 1 and item.indent < stack[-1][0]:
                    parts.append(f"</li></{stack.pop()[1]}>")
                parts.append("</li>")
                if stack[-1][1] != item.tag:
                    parts.append(f"</{stack[-1][1]}>{opening(item)}")
                    stack[-1] = (stack[-1][0], item.tag)
            parts.append(f"<li>{self.item_html(item)}")
        while stack:
            parts.append(f"</li></{stack.pop()[1]}>")
        self.out.append("".join(parts))
        return j

    def quote(self, lines: list[str], i: int) -> int:
        self.flush()
        inner: list[str] = []
        j = i
        while j < len(lines):
            match = _QUOTE.match(lines[j])
            if match is None:
                break
            inner.append(match.group(1))
            j += 1
        body = _Renderer(self.wrap, self.depth + 1).render(inner)
        self.out.append(f"<blockquote>{body}</blockquote>")
        return j


def render_markdown(text: str, wrap: Wrap | None = None) -> str:
    """Escape ``text`` and render its Markdown subset as HTML.

    ``wrap`` receives each block's escaped inline HTML (text plus the short
    tags this module adds) and may hard-wrap it; it must not cut inside a tag,
    which dossier_render._wrap_escaped guarantees.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return _Renderer(wrap or _identity, 0).render(lines)
