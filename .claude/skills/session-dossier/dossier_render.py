"""Render a scoped session as one self-contained HTML page.

Every piece of content is escaped here, in Python, through a single helper
(``_Renderer.text``). The page's one script is the constant ``PAGE_JS``; no
string from the transcript is ever placed inside it. That is what lets the
page skip session-report's base64 embedding: there is no script for content to
break out of, and the HTML stays readable as text, which the Artifact publish
step needs (Claude reads the whole file before it may be published).

In share mode the same helper also passes every string through the redactor
before escaping, so nothing reaches the page without being scrubbed.

Sections are built body-first and the header and ledger last, because the
ledger reports facts (items rendered, notes that matched nothing) that are
only known once the body exists.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from dossier_git import GitInfo
from dossier_link import PersistedOutput, Session, Subagent
from dossier_markdown import render_markdown
from dossier_model import AGENT_TOOL_NAMES, Event, ToolCall, as_text
from dossier_scope import Scope, raw_tool_blocks, tool_calls

STATUS_LABEL = {
    "ok": "成功",
    "error": "エラー",
    "interrupted": "中断",
    "pending": "結果待ち",
}
EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
BASH_TOOLS = frozenset({"Bash", "BashOutput", "KillShell"})
LABEL_KEYS = (
    "command",
    "file_path",
    "notebook_path",
    "pattern",
    "url",
    "query",
    "description",
    "skill",
    "prompt",
)
OTHER_KINDS = ("attachment", "api_request", "meta", "unknown_record", "unknown_block")
OTHER_LABEL = {
    "attachment": "attachment（CLAUDE.md・system-reminder などの注入）",
    "api_request": "API リクエストの記録",
    "meta": "セッションのメタ情報",
    "unknown_record": "未知のレコード型",
    "unknown_block": "未知のブロック型",
}
# Share mode reports these exclusions by name, even at zero.
SHARE_EXCLUDE_FOR_KIND = {
    "attachment": "attachment",
    "api_request": "api_request",
    "meta": "meta",
    "unknown_record": "raw_json",
    "unknown_block": "raw_json",
}
LABEL_LIMIT = 160
RAW_JSON_LIMIT = 20000

NL = "\n"  # f-string expressions may not hold a backslash before Python 3.12
_ANCHOR_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_DIFF_PATH = re.compile(r"^diff --git a/(.*) b/")


class Scrubber(Protocol):
    """What the renderer needs from dossier_redact.Redactor."""

    def scrub(self, text: str, *, count: bool = True) -> str: ...

    def exclude(self, category: str, n: int = 1) -> None: ...

    def summary(self) -> list[tuple[str, str, int]]: ...


@dataclass(frozen=True)
class RenderOptions:
    target: str = "page"
    max_output_chars: int = 0
    generated_at: str = ""
    # Hard-wrap long lines of content (Artifact target): the file must stay
    # readable line by line before it may be published.
    wrap_width: int = 0
    # The user's home; file anchors hash paths with it written as "~" so an
    # anchor never encodes the account name.
    home: str = ""


@dataclass
class Notes:
    summary: str = ""
    anchors: dict[str, str] = field(default_factory=dict)


@dataclass
class RenderResult:
    html: str
    anchors: list[str]
    tool_items: int
    unresolved_notes: list[str]


def anchor_id(prefix: str, raw: str) -> str:
    """A URL- and attribute-safe anchor that stays readable for normal IDs.

    Tool IDs are already safe (``toolu_01…``) and pass through unchanged, so
    links written against a local dossier keep working against the share one.
    Anything that needed rewriting gets a short hash so two different raw IDs
    can never collapse into one anchor.
    """
    clean = _ANCHOR_UNSAFE.sub("-", raw)
    if clean != raw:
        digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False)
        clean = f"{clean}-{digest.hexdigest()[:6]}"
    return f"{prefix}-{clean}"


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:10]


def _wrap_escaped(text: str, width: int) -> str:
    """Hard-wrap escaped text without splitting an entity or a tag.

    Content is escaped, so any "<" here is a short tag the renderer wrote
    (<strong>, </code>); a cut never lands inside one.
    """
    out = []
    for line in text.split("\n"):
        while len(line) > width:
            cut = width
            amp = line.rfind("&", cut - 8, cut)
            if amp != -1 and ";" not in line[amp:cut]:
                cut = amp
            tag = line.rfind("<", cut - 12, cut)
            if tag > 0 and ">" not in line[tag:cut]:
                cut = tag
            out.append(line[:cut])
            line = line[cut:]
        out.append(line)
    return "\n".join(out)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _anchor_path(path: str, home: str) -> str:
    """The spelling a file anchor hashes: the home as "~" (any case, as macOS
    paths are) and anyone else's home as /Users/[user], so no anchor encodes an
    account name."""
    if home:
        path = re.sub(
            re.escape(home.rstrip("/")) + r"(?![A-Za-z0-9_.-])", "~", path, flags=re.I
        )
    return re.sub(r"^/(Users|home)/[^/]+", r"/\1/[user]", path)


def _tool_label(call: ToolCall) -> str:
    inp = call.input if isinstance(call.input, dict) else {}
    for key in LABEL_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0]
    for value in inp.values():
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0]
    return ""


def _group(name: str) -> str:
    if name in BASH_TOOLS:
        return "bash"
    if name in EDIT_TOOLS:
        return "edit"
    if name in AGENT_TOOL_NAMES:
        return "agent"
    return "other"


def _patch_counts(call: ToolCall) -> tuple[int, int]:
    st = call.structured
    if not isinstance(st, dict):
        return 0, 0
    if st.get("type") == "create" and isinstance(st.get("content"), str):
        return len(st["content"].splitlines()), 0
    adds = dels = 0
    for hunk in st.get("structuredPatch") or []:
        for line in hunk.get("lines", []) if isinstance(hunk, dict) else []:
            if isinstance(line, str) and line.startswith("+"):
                adds += 1
            elif isinstance(line, str) and line.startswith("-"):
                dels += 1
    return adds, dels


class _Renderer:
    def __init__(
        self,
        session: Session,
        scope: Scope,
        options: RenderOptions,
        git: GitInfo | None,
        notes: Notes,
        redactor: Scrubber | None,
    ) -> None:
        self.session = session
        self.scope = scope
        self.opts = options
        self.git = git
        self.notes = notes
        self.redactor = redactor
        self.share = redactor is not None
        self.used: set[str] = set()
        self.anchors: list[str] = []
        self.tool_anchor: dict[str, str] = {}
        self.tool_items = 0
        self.notes_used: set[str] = set()
        self.calls = tool_calls(session, scope)
        self.call_ids = {c.tool_use_id for c in self.calls}

    # -- primitives ---------------------------------------------------------
    def text(
        self, value: Any, *, cap: bool = False, limit: int = 0, scrub: bool = True
    ) -> str:
        """The only way content reaches the page: scrub, cap, escape, wrap.

        ``scrub=False`` is for text the caller already scrubbed as a whole
        (a diff split into lines afterwards), never for raw content.
        """
        raw = self.prepare(value, cap=cap, limit=limit, scrub=scrub)
        escaped = html.escape(raw, quote=True)
        if self.opts.wrap_width:
            escaped = _wrap_escaped(escaped, self.opts.wrap_width)
        return escaped

    def prepare(
        self, value: Any, *, cap: bool = False, limit: int = 0, scrub: bool = True
    ) -> str:
        """Scrub (share mode) and shorten raw content; escaping is the caller's."""
        raw = value if isinstance(value, str) else str(value)
        # The Markdown renderer drops these private-use characters; drop them
        # before scrubbing, so one cannot split a token past the scanners.
        raw = raw.replace("\ue000", "").replace("\ue001", "")
        if scrub and self.redactor is not None:
            raw = self.redactor.scrub(raw)
        if limit and len(raw) > limit:
            raw = raw[: limit - 1] + "…"
        cap_at = self.opts.max_output_chars
        if cap and cap_at and len(raw) > cap_at:
            raw = (
                raw[:cap_at]
                + f"\n… 残り {len(raw) - cap_at:,} 文字を省略（元は {len(raw):,} 文字）"
            )
        return raw

    def md(self, value: Any) -> str:
        """Markdown content (replies, prompts, reports, notes) as HTML.

        Same chokepoint as ``text``: scrubbed and capped first, then every
        character escaped by dossier_markdown, which adds only fixed tags.
        """
        width = self.opts.wrap_width
        body = render_markdown(
            self.prepare(value, cap=True),
            (lambda s: _wrap_escaped(s, width)) if width else None,
        )
        return f'<div class="md">\n{body}\n</div>'

    def aid(self, prefix: str, raw: str) -> str:
        base = anchor_id(prefix, raw)
        candidate, n = base, 2
        while candidate in self.used:
            candidate, n = f"{base}-{n}", n + 1
        self.used.add(candidate)
        self.anchors.append(candidate)
        return candidate

    def json(self, value: Any) -> str:
        """JSON with every string leaf (keys too) scrubbed before dumping.

        Dumping first would escape quotes and newlines, and a secret in its
        escaped form slips past patterns written for the plain one. The dump
        is scrubbed again by ``text`` for patterns that span key and value.
        """
        if self.redactor is None:
            return _json(value)
        scrub = self.redactor.scrub

        def clean(node: Any) -> Any:
            if isinstance(node, str):
                return scrub(node)
            if isinstance(node, dict):
                return {scrub(str(k)): clean(v) for k, v in node.items()}
            if isinstance(node, list):
                return [clean(v) for v in node]
            return node

        try:
            return _json(clean(value))
        except RecursionError:
            # Nesting too deep to walk leaf by leaf: the dump is still scrubbed
            # as a whole by ``text``; if even dumping fails, show nothing.
            try:
                return _json(value)
            except RecursionError:
                return "[入れ子が深すぎるため表示しません]"

    def result_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            images = sum(
                1
                for b in value
                if isinstance(b, dict) and b.get("type") in ("image", "document")
            )
            self.exclude("image", images)
            return as_text(value) or self.json(value)
        return self.json(value)

    def pre(self, value: Any, cls: str = "") -> str:
        klass = f' class="{cls}"' if cls else ""
        return f"<pre{klass}>{self.text(value, cap=True)}</pre>"

    def block(self, title: str, body: str) -> str:
        return f'<div class="io"><h5>{title}</h5>\n{body}\n</div>'

    def exclude(self, category: str, n: int = 1) -> None:
        if self.redactor is not None and n:
            self.redactor.exclude(category, n)

    def note(self, anchor: str) -> str:
        if anchor not in self.notes.anchors:
            return ""
        self.notes_used.add(anchor)
        body = self.md(self.notes.anchors[anchor])
        return (
            f'<aside class="claude-note" id="{self.aid("note", anchor)}">'
            f"<b>Claude による注記</b>\n{body}</aside>"
        )

    def diff_text(self, text: str, *, git: bool = False) -> str:
        """Render a diff. It is scrubbed whole before being split into lines:
        a key block or a system-reminder spans lines, and per-line scrubbing
        would let its body through."""
        if self.redactor is not None:
            text = self.redactor.scrub(text)
        lines = text.split("\n")
        spans = []
        budget = self.opts.max_output_chars
        used = 0
        for i, line in enumerate(lines):
            used += len(line) + 1
            if budget and used > budget:
                rest = len(lines) - i
                spans.append(f'<span class="hunk">… 残り {rest:,} 行を省略</span>')
                break
            cls = "ctx"
            attr = ""
            if git and line.startswith("diff --git"):
                cls = "file"
                match = _DIFF_PATH.match(line)
                if match:
                    attr = f' id="{self.aid("git", _short_hash(match.group(1)))}"'
            elif git and line.startswith(("+++", "---", "index ")):
                cls = "meta"
            elif line.startswith("@@"):
                cls = "hunk"
            elif line.startswith("+"):
                cls = "add"
            elif line.startswith("-"):
                cls = "del"
            body = self.text(line, scrub=False)
            spans.append(f'<span class="{cls}"{attr}>{body}</span>')
        return '<pre class="diff">' + "\n".join(spans) + "</pre>"

    def patch_html(self, patch: list[Any]) -> str:
        lines: list[str] = []
        for hunk in patch:
            if not isinstance(hunk, dict):
                continue
            lines.append(
                f"@@ -{hunk.get('oldStart')},{hunk.get('oldLines')}"
                f" +{hunk.get('newStart')},{hunk.get('newLines')} @@"
            )
            lines.extend(str(x) for x in hunk.get("lines", []))
        return self.diff_text("\n".join(lines))

    # -- tool items ---------------------------------------------------------
    def tool_input(self, call: ToolCall) -> str:
        inp = call.input
        if isinstance(inp, dict) and call.name in BASH_TOOLS and "command" in inp:
            body = self.pre(inp.get("command", ""), "cmd")
            desc = inp.get("description")
            if isinstance(desc, str) and desc:
                body += f'\n<p class="muted">{self.text(desc)}</p>'
            return self.block("コマンド", body)
        if isinstance(inp, dict) and call.name in EDIT_TOOLS:
            path = inp.get("file_path") or inp.get("notebook_path") or ""
            rest = {
                k: v for k, v in inp.items() if k not in ("file_path", "notebook_path")
            }
            body = f'<p class="path">{self.text(path)}</p>'
            st = call.structured
            if not (isinstance(st, dict) and st.get("structuredPatch")):
                body += "\n" + self.pre(
                    rest.get("content") if call.name == "Write" else self.json(rest)
                )
            return self.block("対象", body)
        return self.block("入力", self.pre(self.json(inp)))

    def tool_result(self, call: ToolCall) -> str:
        if call.status == "pending":
            return (
                '<p class="pending">結果待ち。実行中か、結果が記録される前に'
                "セッションが止まりました</p>"
            )
        parts = []
        st = call.structured
        if isinstance(st, dict) and ("stdout" in st or "stderr" in st):
            stdout, stderr = st.get("stdout") or "", st.get("stderr") or ""
            if stdout:
                parts.append(self.block("stdout", self.pre(stdout)))
            if stderr:
                parts.append(self.block("stderr", self.pre(stderr, "err")))
            if not stdout and not stderr:
                fallback = self.result_text(call.result)
                parts.append(
                    self.block("結果", self.pre(fallback))
                    if fallback
                    else '<p class="muted">出力なし</p>'
                )
        else:
            # A subagent's report is Markdown written for a reader; any other
            # tool's output is shown as it came.
            result = self.result_text(call.result)
            shown = (
                self.md(result) if call.name in AGENT_TOOL_NAMES else self.pre(result)
            )
            parts.append(self.block("結果", shown))
        if isinstance(st, dict):
            patch = st.get("structuredPatch")
            if isinstance(patch, list) and patch:
                parts.append(self.block("差分", self.patch_html(patch)))
            if st.get("bashEditDiff"):
                parts.append(
                    self.block(
                        "コマンドによる編集", self.pre(self.json(st["bashEditDiff"]))
                    )
                )
        for name in call.persisted:
            parts.append(
                f'<p>全文は外出し出力 <a href="#{anchor_id("tr", name)}">'
                f"{self.text(name)}</a> にあります</p>"
            )
        if call.subagent_id:
            target = anchor_id("agent", call.subagent_id)
            parts.append(
                f'<p><a href="#{target}">サブエージェント '
                f"{self.text(call.subagent_id)} の記録へ</a></p>"
            )
        elif call.name in AGENT_TOOL_NAMES:
            parts.append(
                '<p class="warn">対応するサブエージェントの記録がありません</p>'
            )
        return "\n".join(parts)

    def tool_item(self, call: ToolCall) -> str:
        self.tool_items += 1
        anchor = self.aid("tu", call.tool_use_id)
        self.tool_anchor.setdefault(call.tool_use_id, anchor)
        status = call.status
        name = self.text(call.name or "(名前なし)")
        label = self.text(_tool_label(call), limit=LABEL_LIMIT)
        server = '<span class="tag">server</span>' if call.server else ""
        opened = " open" if status == "error" else ""
        return (
            f'<details class="tool st-{status}" data-kind="tool" data-name="{name}"'
            f' data-group="{_group(call.name)}" data-status="{status}"'
            f' id="{anchor}"{opened}>\n'
            f'<summary><span class="tname">{name}</span>{server}'
            f'<span class="tlabel">{label}</span>'
            f'<span class="badge st-{status}">{STATUS_LABEL[status]}</span>'
            f'<a class="anchor" href="#{anchor}" aria-label="このステップへのリンク">#</a>'
            f"</summary>\n"
            f'<div class="tbody">\n{self.tool_input(call)}\n{self.tool_result(call)}\n'
            f"</div></details>{self.note(anchor)}"
        )

    # -- events -------------------------------------------------------------
    def images(self, n: int) -> str:
        if not n:
            return ""
        self.exclude("image", n)
        return f'<p class="muted">画像 {n} 件（ページには埋め込みません）</p>'

    def event(self, ev: Event) -> str:
        kind = ev.kind
        if kind == "prompt":
            anchor = self.aid("prompt", str(ev.prompt_index))
            return (
                f'<div class="prompt" id="{anchor}"><span class="pnum">#{ev.prompt_index}'
                f'</span><div class="ptext">{self.md(ev.text)}</div>'
                f"{self.images(ev.image_count)}</div>{self.note(anchor)}"
            )
        if kind == "assistant_text":
            return f'<div class="say">{self.md(ev.text)}</div>'
        if kind == "tool" and ev.tool is not None:
            return self.tool_item(ev.tool)
        if kind == "thinking":
            if self.share:
                self.exclude("thinking")
                return ""
            return (
                '<details class="aside"><summary>thinking</summary>'
                f"{self.pre(ev.text)}</details>"
            )
        if kind in ("inbound", "injected"):
            # Allow-list: of what users send, only the person's own prompts
            # are shared. Agent messages, command output and subagent task
            # prompts (already visible in the Agent call's input) are not.
            if self.share:
                self.exclude("injected")
                return ""
            # Messages from agents are Markdown; injected text (skills, reminders,
            # summaries) is shown as it came.
            label = "受信" if kind == "inbound" else "注入されたテキスト"
            body = self.md(ev.text) if kind == "inbound" else self.pre(ev.text)
            return (
                f'<details class="aside"><summary>{label} · {self.text(ev.subtype)}'
                f"</summary>{body}{self.images(ev.image_count)}</details>"
            )
        if kind in ("system", "tool_addition") and self.share:
            self.exclude("meta")
            return ""
        if kind == "orphan_result":
            return (
                '<details class="aside" open><summary>呼び出しの見つからない結果 · '
                f"{self.text(ev.subtype)}</summary>{self.pre(ev.text)}</details>"
            )
        if kind == "images":
            return self.images(ev.image_count)
        if kind == "system":
            body = f"{self.pre(ev.text)}" if ev.text else ""
            return (
                f'<div class="sys"><span class="tag">system</span>'
                f"{self.text(ev.subtype)}{body}</div>"
            )
        if kind == "tool_addition":
            return f'<div class="sys"><span class="tag">ツール追加</span>{self.pre(self.json(ev.raw))}</div>'
        return ""

    def events(self, evs: list[Event]) -> str:
        return "\n".join(filter(None, (self.event(ev) for ev in evs)))

    # -- sections -------------------------------------------------------------
    def timeline(self) -> str:
        controls = (
            '<div class="controls" role="group" aria-label="表示の絞り込み">'
            '<button type="button" data-filter="all" aria-pressed="true">すべて</button>'
            '<button type="button" data-filter="error">成功以外</button>'
            '<button type="button" data-filter="bash">Bash</button>'
            '<button type="button" data-filter="edit">編集</button>'
            '<button type="button" data-filter="agent">Agent</button>'
            '<button type="button" id="toggle-all" aria-pressed="false">すべて開く</button>'
            "</div>"
        )
        body = (
            self.events(self.scope.events)
            or '<p class="muted">範囲内の記録はありません</p>'
        )
        return f'<section id="timeline"><h2>タイムライン</h2>\n{controls}\n{body}\n</section>'

    def subagents(self) -> str:
        items = []
        for agent_id, sub in self.session.subagents.items():
            if agent_id in self.scope.subagent_ids:
                items.append(self.subagent(sub))
        body = (
            "\n".join(items)
            or '<p class="muted">範囲内のサブエージェントはありません</p>'
        )
        return f'<section id="subagents"><h2>サブエージェント</h2>\n{body}\n</section>'

    def subagent(self, sub: Subagent) -> str:
        anchor = self.aid("agent", sub.agent_id)
        meta = sub.meta
        rows = [("ID", sub.agent_id)]
        for key, label in (
            ("agentType", "種別"),
            ("description", "説明"),
            ("model", "モデル"),
            ("spawnDepth", "入れ子の深さ"),
            ("finished", "完了"),
        ):
            if key in meta:
                rows.append((label, str(meta[key])))
        dl = "\n".join(f"<dt>{k}</dt><dd>{self.text(v)}</dd>" for k, v in rows)
        parent = sub.parent_tool_use_id
        if parent and parent in self.call_ids:
            origin = f'<a href="#{anchor_id("tu", parent)}">呼び出し元のステップ</a>'
        elif parent:
            origin = f"呼び出し元 {self.text(parent)}（範囲外）"
        else:
            origin = '<span class="warn">呼び出し元が見つかりません（余り）</span>'
        body = self.events(sub.transcript.events)
        return (
            f'<article class="agent" id="{anchor}"><h3>{self.text(sub.agent_id)}</h3>'
            f"<dl>{dl}</dl><p>{origin}</p>{self.note(anchor)}\n{body}\n</article>"
        )

    def files(self) -> str:
        by_path: dict[str, list[ToolCall]] = {}
        for call in self.calls:
            inp = call.input if isinstance(call.input, dict) else {}
            path = inp.get("file_path") or inp.get("notebook_path")
            if call.name in EDIT_TOOLS and isinstance(path, str):
                by_path.setdefault(path, []).append(call)
        rows = []
        for path, calls in by_path.items():
            links = " ".join(
                f'<a href="#{self.tool_anchor.get(c.tool_use_id, anchor_id("tu", c.tool_use_id))}">'
                f"{self.text(c.name)} +{_patch_counts(c)[0]} −{_patch_counts(c)[1]}</a>"
                for c in calls
            )
            rows.append(
                f'<li id="{self.aid("file", _short_hash(_anchor_path(path, self.opts.home)))}"><code>{self.text(path)}</code>'
                f' <span class="muted">{len(calls)} 回</span><div>{links}</div></li>'
            )
        body = (
            f'<ul class="files">{NL.join(rows)}</ul>'
            if rows
            else ('<p class="muted">範囲内のファイル編集はありません</p>')
        )
        return (
            '<section id="files"><h2>ファイル変更（transcript の記録）</h2>\n'
            f"{body}\n</section>"
        )

    def git_section(self) -> str:
        info = self.git
        if info is None or not info.available:
            reason = info.reason if info is not None else "指定なし"
            return (
                '<section id="git"><h2>git</h2>'
                f'<p class="muted">取得なし（{self.text(reason)}）</p></section>'
            )
        commits = (
            "\n".join(
                f"<li><code>{self.text(c.short)}</code> {self.text(c.subject)}"
                f' <span class="muted">{self.text(c.author)} · {self.text(c.date)}</span></li>'
                for c in info.commits
            )
            or '<li class="muted">範囲内のコミットはありません</li>'
        )
        untracked = "\n".join(
            f"<li><code>{self.text(u)}</code></li>" for u in info.untracked
        )
        parts = [
            '<section id="git"><h2>git</h2>',
            f"<p>基点: {self.text(info.base_label)}"
            + (f" <code>{self.text(info.base[:12])}</code>" if info.base else "")
            + "</p>",
            (
                '<p class="muted">共有版のため、このセッションが編集したファイルの'
                "差分だけを表示します</p>"
                if info.scoped
                else ""
            ),
            f"<h3>コミット</h3><ul>{commits}</ul>",
            self.block("変更の概要", self.pre(info.stat or "(差分なし)")),
        ]
        if info.diff:
            parts.append(
                f"<details><summary>差分の全文</summary>"
                f"{self.diff_text(info.diff.rstrip(chr(10)), git=True)}</details>"
            )
        if untracked:
            parts.append(f"<h3>未追跡のファイル</h3><ul>{untracked}</ul>")
        parts.append("</section>")
        return "\n".join(parts)

    def persisted_in_scope(self) -> list[PersistedOutput]:
        return [
            p
            for p in self.session.persisted
            if (p.tool_use_id in self.call_ids)
            or (p.tool_use_id is None and self.scope.whole)
        ]

    def persisted(self) -> str:
        items = []
        for out in self.persisted_in_scope():
            anchor = self.aid("tr", out.name)
            if out.tool_use_id:
                target = self.tool_anchor.get(
                    out.tool_use_id, anchor_id("tu", out.tool_use_id)
                )
                origin = f'<a href="#{target}">対応するステップ</a>'
            else:
                origin = '<span class="warn">対応するステップなし（余り）</span>'
            items.append(
                f'<details class="persisted" id="{anchor}"><summary><code>'
                f"{self.text(out.name)}</code> {out.size:,} バイト · {origin}</summary>"
                f"{self.pre(out.text)}</details>"
            )
        body = "\n".join(items) or '<p class="muted">範囲内の外出し出力はありません</p>'
        return f'<section id="persisted"><h2>外出しされた出力</h2>\n{body}\n</section>'

    def other(self) -> str:
        evs = [e for e in self.scope.events if e.kind in OTHER_KINDS]
        for agent_id, sub in self.session.subagents.items():
            if agent_id in self.scope.subagent_ids:
                evs.extend(e for e in sub.transcript.events if e.kind in OTHER_KINDS)
        groups: dict[tuple[str, str], list[Event]] = {}
        for ev in evs:
            groups.setdefault((ev.kind, ev.subtype), []).append(ev)
        rows = []
        for (kind, subtype), members in sorted(groups.items()):
            head = (
                f"{OTHER_LABEL[kind]} · <code>{self.text(subtype or '(なし)')}</code>"
                f" <b>{len(members)}</b> 件"
            )
            if self.share:
                self.exclude(SHARE_EXCLUDE_FOR_KIND[kind], len(members))
                rows.append(f"<li>{head}</li>")
                continue
            if kind == "api_request":
                sizes = sum(len(json.dumps(m.raw, default=str)) for m in members)
                rows.append(
                    f'<li>{head} <span class="muted">（会話本文の複製なので中身は'
                    f"表示しません。合計 {sizes:,} 文字）</span></li>"
                )
                continue
            raws = "\n".join(
                f"<pre>{self.text(self.json(m.raw)[:RAW_JSON_LIMIT], cap=True)}</pre>"
                for m in members
            )
            rows.append(f"<li><details><summary>{head}</summary>{raws}</details></li>")
        body = (
            f'<ul class="other">{NL.join(rows)}</ul>'
            if rows
            else ('<p class="muted">該当なし</p>')
        )
        note = '<p class="muted">共有版では件数のみ表示します</p>' if self.share else ""
        return (
            f'<section id="other"><h2>その他のレコード</h2>\n{note}{body}\n</section>'
        )

    def notes_section(self) -> str:
        if not self.notes.summary:
            return ""
        return (
            '<section id="notes"><h2>Claude による注記</h2>'
            '<aside class="claude-note claude-summary"><b>Claude による注記</b>'
            f"{self.md(self.notes.summary)}</aside></section>"
        )

    def unresolved_notes(self) -> list[str]:
        return sorted(a for a in self.notes.anchors if a not in self.notes_used)

    def ledger(self, unresolved: list[str]) -> str:
        led = self.session.ledger
        expected = raw_tool_blocks(self.session, self.scope)
        ok = expected == self.tool_items
        rows = [
            (
                "記録中の tool_use ⇄ ページの項目",
                f"{expected} = {self.tool_items}"
                if ok
                else f"{expected} ≠ {self.tool_items}",
                ok,
            )
        ]
        links = [(t, a) for t, a in led.agent_links if t in self.call_ids]
        missing = [t for t, a in links if a is None]
        orphans = led.orphan_agents if self.scope.whole else []
        rows.append(
            (
                "Agent 呼び出し ⇄ サブエージェントの記録",
                f"{len(links) - len(missing)} / {len(links)}",
                not missing and not orphans,
            )
        )
        persisted = self.persisted_in_scope()
        linked = sum(1 for p in persisted if p.tool_use_id)
        rows.append(
            (
                "外出し出力の対応",
                f"{linked} / {len(persisted)}",
                linked == len(persisted),
            )
        )
        pending = sum(1 for c in self.calls if c.status == "pending")
        rows.append(("結果待ちの呼び出し", str(pending), True))
        html_rows = "\n".join(
            f'<tr><th>{label}</th><td class="num {"ok" if good else "bad"}">'
            f"{value} {'✓' if good else '!'}</td></tr>"
            for label, value, good in rows
        )
        details = []

        def listing(title: str, values: list[str]) -> None:
            if values:
                items = "\n".join(
                    f"<li><code>{self.text(v)}</code></li>" for v in values
                )
                details.append(f"<h3>{title}</h3><ul>{items}</ul>")

        listing("記録のない Agent 呼び出し", missing)
        listing("呼び出し元のないサブエージェントの記録", orphans)
        listing(
            "どのステップとも結べない外出し出力",
            [p.name for p in persisted if not p.tool_use_id],
        )
        listing("呼び出しのない結果", led.orphan_results)
        listing("読まなかった外出し出力（シンボリックリンク）", led.skipped_outputs)
        listing("重複した tool_use ID", led.duplicate_tool_ids)
        listing(
            "未知のレコード型",
            [f"{k} × {v}" for k, v in sorted(led.unknown_records.items())],
        )
        listing(
            "未知のブロック型",
            [f"{k} × {v}" for k, v in sorted(led.unknown_blocks.items())],
        )
        listing(
            "壊れた行",
            [
                f"{e.source}:{e.line_no}"
                + ("（書き込み途中の末尾）" if e.truncated_tail else "")
                for e in led.parse_errors
            ],
        )
        listing("対応先のない注記", unresolved)
        scope_note = (
            "未知の型・壊れた行・呼び出しのない結果はセッション全体の件数です。"
        )
        return (
            '<section id="ledger"><h2>網羅性台帳</h2>'
            f'<table class="ledger">{html_rows}</table>'
            f'{NL.join(details)}<p class="muted">{scope_note}</p></section>'
        )

    def redaction(self) -> str:
        if self.redactor is None:
            return ""
        rows = "\n".join(
            # Labels are constants, but one names a tag; escape, never scrub.
            f"<tr><th>{html.escape(group)}</th><td>{html.escape(label)}</td>"
            f'<td class="num">{n:,}</td></tr>'
            for group, label, n in self.redactor.summary()
        )
        return (
            '<section id="redaction"><h2>秘匿化サマリ</h2>'
            "<p>共有版です。次のものを除外・置換しました。0 件の項目も表示しています。</p>"
            f'<table class="ledger">{rows}</table></section>'
        )

    def header(self) -> str:
        led = self.session.ledger
        title = self.text(led.title or "セッションの記録")
        pill = (
            '<span class="pill share">SHARE</span>'
            if self.share
            else ('<span class="pill local">LOCAL</span>')
        )
        period = " 〜 ".join(
            self.text(x) for x in (self.scope.start_ts, self.scope.end_ts) if x
        )
        rows = [
            ("範囲", self.text(self.scope.label)),
            ("期間", period or "—"),
            ("セッション", f"<code>{self.text(self.session.session_id)}</code>"),
            ("作業ディレクトリ", f"<code>{self.text(led.cwd or '—')}</code>"),
            ("ブランチ", self.text(", ".join(sorted(led.branches)) or "—")),
            ("Claude Code", self.text(", ".join(sorted(led.versions)) or "—")),
            ("生成", self.text(self.opts.generated_at or "—")),
        ]
        dl = "\n".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows)
        return (
            f'<section id="top" class="head"><div class="eyebrow">session-dossier {pill}'
            f"</div><h1>{title}</h1><dl>{dl}</dl></section>"
        )

    def toc(self) -> str:
        prompts = []
        current: list[Any] = []
        for ev in self.scope.events:
            if ev.kind == "prompt":
                current = [ev, 0]
                prompts.append(current)
            elif ev.kind == "tool" and current:
                current[1] += 1
        items = "\n".join(
            f'<li><a href="#{anchor_id("prompt", str(ev.prompt_index))}">#{ev.prompt_index}</a> '
            f"{self.text(ev.text.strip().splitlines()[0] if ev.text.strip() else '', limit=80)}"
            f' <span class="muted">{n} ステップ</span></li>'
            for ev, n in prompts
        )
        links = [
            ("ledger", "網羅性台帳"),
            ("redaction", "秘匿化サマリ") if self.share else None,
            ("notes", "Claude による注記") if self.notes.summary else None,
            ("timeline", "タイムライン"),
            ("subagents", "サブエージェント"),
            ("files", "ファイル変更"),
            ("git", "git"),
            ("persisted", "外出し出力"),
            ("other", "その他"),
        ]
        nav = "\n".join(f'<a href="#{a}">{t}</a>' for a, t in filter(None, links))
        prompt_list = f'<ol class="prompts">{items}</ol>' if items else ""
        return f'<nav class="toc" aria-label="目次">{nav}</nav>{prompt_list}'

    def build(self) -> RenderResult:
        body_sections = [
            self.timeline(),
            self.subagents(),
            self.files(),
            self.git_section(),
            self.persisted(),
            self.other(),
        ]
        notes = self.notes_section()
        unresolved = self.unresolved_notes()
        head = [self.header(), self.toc(), self.ledger(unresolved)]
        redaction = self.redaction()
        body = "\n".join(filter(None, [*head, redaction, notes, *body_sections]))
        footer = (
            '<footer class="muted">session-dossier が transcript と git から機械的に'
            "生成しました。Claude による注記の部分だけがモデルの記述です。</footer>"
        )
        title = self.text(self.session.ledger.title or "セッションの記録", limit=80)
        page = _shell(f"{title} · dossier", f"{body}\n{footer}", self.opts.target)
        return RenderResult(
            html=page,
            anchors=list(self.anchors),
            tool_items=self.tool_items,
            unresolved_notes=unresolved,
        )


def render(
    session: Session,
    scope: Scope,
    options: RenderOptions,
    *,
    git: GitInfo | None = None,
    notes: Notes | None = None,
    redactor: Scrubber | None = None,
) -> RenderResult:
    return _Renderer(session, scope, options, git, notes or Notes(), redactor).build()


def _shell(title: str, body: str, target: str) -> str:
    if target == "artifact":
        # claude.ai wraps an Artifact in its own document skeleton, so the
        # fragment carries no doctype/html/head/body of its own.
        return (
            f"<title>{title}</title>\n"
            '<meta name="claude:viewport-fit-sides" content="cover">\n'
            f"<style>{CSS}</style>\n"
            f'<main class="dossier">\n{body}\n</main>\n'
            f"<script>{PAGE_JS}</script>\n"
        )
    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f'<main class="dossier">\n{body}\n</main>\n'
        f"<script>{PAGE_JS}</script>\n</body>\n</html>\n"
    )


CSS = """
:root {
  --bg: #f6f7f9; --surface: #ffffff; --surface-2: #eceff4; --fg: #1a1e28;
  --muted: #5b6375; --line: #d9dde5; --accent: #2f45b5; --ok: #1d7549;
  --err: #b0261d; --warn: #8a5a00; --note: #8f4d00; --note-bg: #fcefdc;
  --add-bg: #e3f4ea; --del-bg: #fbe5e2; --err-bg: #fdf0ee;
  --sans: ui-sans-serif, -apple-system, "Hiragino Sans", "Yu Gothic", "Noto Sans JP", sans-serif;
  --mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #12151c; --surface: #1a1e28; --surface-2: #232837; --fg: #e5e8f0;
    --muted: #9aa2b6; --line: #303648; --accent: #93a3ff; --ok: #63d39c;
    --err: #ff8f85; --warn: #f0c060; --note: #f2b866; --note-bg: #362915;
    --add-bg: #173326; --del-bg: #3a1c1c; --err-bg: #2c1818;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #12151c; --surface: #1a1e28; --surface-2: #232837; --fg: #e5e8f0;
  --muted: #9aa2b6; --line: #303648; --accent: #93a3ff; --ok: #63d39c;
  --err: #ff8f85; --warn: #f0c060; --note: #f2b866; --note-bg: #362915;
  --add-bg: #173326; --del-bg: #3a1c1c; --err-bg: #2c1818;
  color-scheme: dark;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.7 var(--sans); }
.dossier {
  max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 36px;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 28px 64px;
}
h1, h2, h3 { line-height: 1.35; margin: 0 0 10px; text-wrap: balance; }
h1 { font-size: 26px; } h2 { font-size: 19px; } h3 { font-size: 15px; }
h5 { margin: 0 0 4px; font-size: 11px; letter-spacing: .06em; color: var(--muted); font-weight: 600; }
a { color: var(--accent); }
a:focus-visible, button:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
code, pre { font-family: var(--mono); font-size: 12.5px; }
pre { margin: 0; padding: 10px 12px; background: var(--surface-2); border-radius: 6px;
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 480px; overflow: auto; }
pre.err { background: var(--err-bg); }
dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 2px 14px; margin: 0; }
dt { color: var(--muted); } dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.muted { color: var(--muted); } .warn { color: var(--warn); }
.eyebrow { font-size: 12px; letter-spacing: .08em; color: var(--muted); display: flex; gap: 8px; align-items: center; }
.pill { font: 600 11px var(--mono); padding: 2px 8px; border-radius: 999px; letter-spacing: .06em; }
.pill.local { background: var(--add-bg); color: var(--ok); }
.pill.share { background: var(--note-bg); color: var(--warn); }
.toc { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 13px; }
.prompts { margin: 10px 0 0; padding-left: 1.4em; font-size: 13px; }
table.ledger { border-collapse: collapse; width: 100%; max-width: 640px; background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }
table.ledger th, table.ledger td { text-align: left; padding: 7px 12px; border-bottom: 1px solid var(--line); font-weight: 400; vertical-align: top; }
td.num { font-family: var(--mono); font-variant-numeric: tabular-nums; white-space: nowrap; }
td.ok { color: var(--ok); } td.bad { color: var(--err); }
.controls { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
.controls button { font: inherit; font-size: 12.5px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--line); background: var(--surface); color: var(--fg); cursor: pointer; }
.controls button[aria-pressed="true"] { background: var(--accent); border-color: var(--accent); color: var(--surface); }
section { min-width: 0; }
#timeline > *, .agent > * { margin-bottom: 8px; }
.prompt { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 4px 10px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0; margin-top: 18px; }
.pnum { font: 600 13px var(--mono); color: var(--accent); }
.ptext, .say { overflow-wrap: anywhere; min-width: 0; }
.md > :first-child { margin-top: 0; } .md > :last-child { margin-bottom: 0; }
.md p { margin: 0 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
.md li { white-space: pre-wrap; overflow-wrap: anywhere; }
.md ul, .md ol { margin: 0 0 8px; padding-left: 1.4em; }
.md .md-h { font-weight: 700; margin: 12px 0 6px; }
.md .md-h1, .md .md-h2 { font-size: 16px; } .md .md-h3 { font-size: 15px; }
.md code { background: var(--surface-2); padding: 0 4px; border-radius: 4px; }
.md pre.md-code { margin: 0 0 8px; } .md pre.md-code code { background: none; padding: 0; }
.md-table { overflow-x: auto; margin: 0 0 8px; }
.md table { border-collapse: collapse; font-size: 13px; }
.md th, .md td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
.md th { background: var(--surface-2); }
.md blockquote { margin: 0 0 8px; padding-left: 10px; border-left: 3px solid var(--line); color: var(--muted); }
.md hr { border: 0; border-top: 1px solid var(--line); margin: 10px 0; }
.prompt .muted { grid-column: 2; margin: 0; }
.say { padding: 2px 4px; }
.sys { font-size: 12.5px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.tag { font: 600 10.5px var(--mono); border: 1px solid var(--line); border-radius: 4px; padding: 0 5px; margin-right: 6px; color: var(--muted); align-self: flex-start; }
details.tool { background: var(--surface); border: 1px solid var(--line); border-left: 3px solid var(--ok); border-radius: 0 8px 8px 0; }
details.tool.st-error { border-left-color: var(--err); }
details.tool.st-interrupted, details.tool.st-pending { border-left-color: var(--warn); }
details.tool > summary { cursor: pointer; display: grid; grid-template-columns: auto auto minmax(0, 1fr) auto auto; gap: 8px; align-items: baseline; padding: 7px 12px; }
.tname { font: 600 12.5px var(--mono); }
.tlabel { font-family: var(--mono); font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.badge { font-size: 11px; padding: 0 7px; border-radius: 999px; background: var(--surface-2); }
.badge.st-ok { color: var(--ok); } .badge.st-error { color: var(--err); }
.badge.st-interrupted, .badge.st-pending { color: var(--warn); }
.anchor { font-family: var(--mono); color: var(--muted); text-decoration: none; }
.tbody { padding: 4px 12px 12px; display: flex; flex-direction: column; gap: 10px; }
.tbody p { margin: 0; }
.path { font-family: var(--mono); font-size: 12.5px; overflow-wrap: anywhere; }
.pending { color: var(--warn); }
pre.diff span { display: block; }
pre.diff .add { background: var(--add-bg); } pre.diff .del { background: var(--del-bg); }
pre.diff .hunk, pre.diff .meta { color: var(--muted); }
pre.diff .file { font-weight: 600; margin-top: 8px; }
details.aside > summary { cursor: pointer; font-size: 12.5px; color: var(--muted); }
.claude-note { border: 1px solid var(--note); background: var(--note-bg); border-radius: 8px; padding: 8px 12px; margin-left: 18px; }
.claude-note b { color: var(--note); font-size: 11px; letter-spacing: .06em; display: block; }
.claude-note .md { margin-top: 4px; }
.claude-summary { margin-left: 0; }
.agent { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 14px; margin-bottom: 14px; }
.agent .agent { background: var(--bg); }
ul.files, ul.other { padding-left: 1.2em; display: flex; flex-direction: column; gap: 6px; }
details.persisted { margin-bottom: 8px; }
details.persisted > summary { cursor: pointer; }
footer { font-size: 12px; border-top: 1px solid var(--line); padding-top: 12px; }
@media (max-width: 640px) {
  details.tool > summary { grid-template-columns: auto minmax(0, 1fr) auto; }
  .tlabel { grid-column: 1 / -1; order: 5; }
  .tag { display: none; }
}
"""

# The only script on the page. It is a constant: no transcript content is ever
# interpolated into it (T14 pins this), which is why the page needs no base64.
PAGE_JS = """
(function () {
  var buttons = document.querySelectorAll("[data-filter]");
  buttons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var f = btn.getAttribute("data-filter");
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b === btn)); });
      document.querySelectorAll("details.tool").forEach(function (el) {
        var st = el.getAttribute("data-status");
        var show = f === "all" || (f === "error" ? st !== "ok" : el.getAttribute("data-group") === f);
        el.hidden = !show;
      });
    });
  });
  var toggle = document.getElementById("toggle-all");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var open = toggle.getAttribute("aria-pressed") !== "true";
      toggle.setAttribute("aria-pressed", String(open));
      toggle.textContent = open ? "すべて閉じる" : "すべて開く";
      document.querySelectorAll("details.tool").forEach(function (d) { d.open = open; });
    });
  }
  function reveal() {
    var id = decodeURIComponent(location.hash.slice(1));
    var el = id && document.getElementById(id);
    if (!el) return;
    el.hidden = false;
    for (var d = el; d; d = d.parentElement) {
      if (d.tagName === "DETAILS") d.open = true;
    }
    el.scrollIntoView();
  }
  window.addEventListener("hashchange", reveal);
  reveal();
})();
"""
