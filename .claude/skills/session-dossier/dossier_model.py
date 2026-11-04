"""Turn raw transcript records into the dossier's event model.

This is the one place that knows the transcript's record and block types. The
format is internal to Claude Code and changes between versions, so the rule is:
anything not recognised, or recognised but not in the expected shape, becomes
an ``unknown_*`` event and is counted in the Ledger. It is never dropped,
because a dossier that silently loses records cannot claim to be complete.

Tool results are paired with their tool_use here, while the records stream
past, since a result always follows its call in the same file. Calls that
share an ID are paired first-in first-out, so each keeps its own result.
Linking across files (subagents, persisted outputs) is dossier_link's job.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from dossier_parse import API_REQUEST_TYPES, ParseError, ParseResult

# Record types seen on real transcripts. Those not handled specially below are
# bookkeeping (titles, modes, costs, file-history snapshots) and become "meta".
KNOWN_RECORD_TYPES = frozenset(
    {
        "assistant",
        "user",
        "system",
        "attachment",
        "mode",
        "atis-latch",
        "last-prompt",
        "ai-title",
        "tools-used",
        "bridge-session",
        "file-history-snapshot",
        "file-history-delta",
        "queue-operation",
        "cost-state",
        # Added after the ledger reported them as unknown on a 2026-11 build.
        "permission-mode",
        "session-owner",
        "frame-link",
        "artifact-comment-monitor",
        "artifact-autoreact-ledger",
        *API_REQUEST_TYPES,
    }
)

IMAGE_BLOCK_TYPES = frozenset({"image", "document"})

AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})

# Origin-less user text that is the harness speaking, not the person: slash
# command echoes, local command output, `!` shell input, memory input.
_COMMAND_TAG = re.compile(
    r"^\s*<(?:command-|local-command-|bash-|user-memory-input|task-notification)"
)
_INTERRUPT = "[Request interrupted"


@dataclass
class ToolCall:
    tool_use_id: str
    name: str
    input: Any
    source: str
    server: bool = False
    use_uuid: str = ""
    has_result: bool = False
    is_error: bool = False
    result: Any = None
    structured: Any = None
    result_uuid: str = ""
    persisted: list[str] = field(default_factory=list)
    subagent_id: str | None = None

    @property
    def status(self) -> str:
        if not self.has_result:
            return "pending"
        if self.is_error:
            return "error"
        if isinstance(self.structured, dict) and self.structured.get("interrupted"):
            return "interrupted"
        return "ok"


@dataclass
class Event:
    kind: str
    uuid: str
    ts: str
    source: str
    seq: int
    text: str = ""
    subtype: str = ""
    raw: Any = None
    tool: ToolCall | None = None
    prompt_index: int | None = None
    image_count: int = 0
    is_meta: bool = False


@dataclass
class Ledger:
    """Everything the dossier must account for, in one place.

    The page renders this as the 網羅性台帳 so a reader can check the dossier's
    completeness claim instead of taking it on trust.
    """

    record_types: Counter[str] = field(default_factory=Counter)
    unknown_records: Counter[str] = field(default_factory=Counter)
    unknown_blocks: Counter[str] = field(default_factory=Counter)
    parse_errors: list[ParseError] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)
    branches: set[str] = field(default_factory=set)
    title: str = ""
    cwd: str = ""
    orphan_results: list[str] = field(default_factory=list)
    duplicate_tool_ids: list[str] = field(default_factory=list)
    agent_links: list[tuple[str, str | None]] = field(default_factory=list)
    orphan_agents: list[str] = field(default_factory=list)
    skipped_outputs: list[str] = field(default_factory=list)


@dataclass
class Transcript:
    source: str
    events: list[Event] = field(default_factory=list)
    calls: dict[str, ToolCall] = field(default_factory=dict)
    uuid_seq: dict[str, int] = field(default_factory=dict)
    # seq -> tool_use blocks found by dossier_parse's independent walk.
    raw_tool_blocks: dict[int, int] = field(default_factory=dict)


def as_text(value: Any) -> str:
    """Flatten a content value (str, list of blocks, other) to plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and item.get("type") in IMAGE_BLOCK_TYPES:
                parts.append("[画像]")
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(value)


def _content_of(d: dict[str, Any]) -> tuple[bool, Any]:
    """(well_formed, content) for a user/assistant record."""
    msg = d.get("message")
    if not isinstance(msg, dict):
        return False, None
    content = msg.get("content")
    return isinstance(content, (str, list)), content


class _Normalizer:
    def __init__(self, parsed: ParseResult, ledger: Ledger, *, main: bool) -> None:
        self.ledger = ledger
        self.main = main
        self.tr = Transcript(source=parsed.source)
        self.prompt_count = 0
        # tool_use_id -> calls still waiting for a result, oldest first.
        self.waiting: dict[str, list[ToolCall]] = {}
        ledger.parse_errors.extend(parsed.errors)
        for seq, rec in enumerate(parsed.records):
            if rec.tool_blocks:
                self.tr.raw_tool_blocks[seq] = rec.tool_blocks
            self._record(seq, rec.data)

    def _event(self, kind: str, d: dict[str, Any], seq: int, **kw: Any) -> Event:
        ev = Event(
            kind=kind,
            uuid=str(d.get("uuid") or ""),
            ts=str(d.get("timestamp") or ""),
            source=self.tr.source,
            seq=seq,
            **kw,
        )
        self.tr.events.append(ev)
        return ev

    def _header(self, d: dict[str, Any], rtype: str) -> None:
        if isinstance(d.get("version"), str):
            self.ledger.versions.add(d["version"])
        if isinstance(d.get("gitBranch"), str) and d["gitBranch"]:
            self.ledger.branches.add(d["gitBranch"])
        if rtype == "ai-title" and isinstance(d.get("aiTitle"), str):
            self.ledger.title = d["aiTitle"]
        if self.main and not self.ledger.cwd and isinstance(d.get("cwd"), str):
            self.ledger.cwd = d["cwd"]

    def _unknown_record(self, seq: int, d: dict[str, Any], name: str) -> None:
        self.ledger.unknown_records[name] += 1
        self._event("unknown_record", d, seq, subtype=name, raw=d)

    def _record(self, seq: int, d: dict[str, Any]) -> None:
        raw_type = d.get("type")
        rtype = raw_type if isinstance(raw_type, str) else repr(raw_type)
        self.ledger.record_types[rtype] += 1
        if d.get("uuid"):
            self.tr.uuid_seq[str(d["uuid"])] = seq
        self._header(d, rtype)
        if rtype in ("user", "assistant"):
            well_formed, content = _content_of(d)
            if not well_formed:
                self._unknown_record(seq, d, f"{rtype}（形が想定外）")
            elif rtype == "user":
                self._user(seq, d, content)
            else:
                self._assistant(seq, d, content)
        elif rtype == "system":
            self._event(
                "system",
                d,
                seq,
                subtype=str(d.get("subtype") or ""),
                text=as_text(d.get("content")),
                raw=d,
            )
        elif rtype == "attachment":
            att = d.get("attachment")
            kind = att.get("type") if isinstance(att, dict) else None
            self._event("attachment", d, seq, subtype=str(kind or ""), raw=d)
        elif rtype in API_REQUEST_TYPES:
            self._event("api_request", d, seq, subtype=rtype, raw=d)
        elif rtype in KNOWN_RECORD_TYPES:
            self._event("meta", d, seq, subtype=rtype, raw=d)
        else:
            self._unknown_record(seq, d, rtype)

    def _unknown_block(self, seq: int, d: dict[str, Any], block: Any) -> None:
        btype = block.get("type") if isinstance(block, dict) else type(block).__name__
        name = btype if isinstance(btype, str) else repr(btype)
        self.ledger.unknown_blocks[name] += 1
        self._event("unknown_block", d, seq, subtype=name, raw=block)

    # -- user ---------------------------------------------------------------
    def _user(self, seq: int, d: dict[str, Any], content: Any) -> None:
        blocks: list[Any] = (
            [{"type": "text", "text": content}] if isinstance(content, str) else content
        )
        texts: list[str] = []
        images = 0
        results: list[dict[str, Any]] = []
        for block in blocks:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                texts.append(str(block.get("text", "")))
            elif btype == "tool_result":
                results.append(block)
            elif btype in IMAGE_BLOCK_TYPES:
                images += 1
            else:
                self._unknown_block(seq, d, block)
        for block in results:
            self._attach_result(seq, block, d, single=len(results) == 1)
        if results and not texts:
            if images:
                self._event("images", d, seq, image_count=images)
            return
        if not texts and not images:
            return
        self._classify_user(seq, d, "\n".join(texts), images)

    def _classify_user(
        self, seq: int, d: dict[str, Any], text: str, images: int
    ) -> None:
        origin = d.get("origin")
        okind = origin.get("kind") if isinstance(origin, dict) else None
        is_meta = bool(d.get("isMeta"))
        common = {"text": text, "image_count": images, "is_meta": is_meta, "raw": d}
        if okind == "human" and self.main:
            self.prompt_count += 1
            self._event("prompt", d, seq, prompt_index=self.prompt_count, **common)
        elif isinstance(okind, str) and okind:
            self._event("inbound", d, seq, subtype=okind, **common)
        elif d.get("isCompactSummary"):
            self._event("injected", d, seq, subtype="compact", **common)
        elif is_meta:
            self._event("injected", d, seq, subtype="meta", **common)
        elif text.startswith(_INTERRUPT):
            self._event("inbound", d, seq, subtype="interrupt", **common)
        elif _COMMAND_TAG.match(text):
            self._event("inbound", d, seq, subtype="command", **common)
        elif self.main:
            self.prompt_count += 1
            self._event("prompt", d, seq, prompt_index=self.prompt_count, **common)
        else:
            self._event("inbound", d, seq, subtype="task", **common)

    def _take_waiting(
        self, seq: int, tid: str, block: Any, d: dict[str, Any]
    ) -> ToolCall | None:
        queue = self.waiting.get(tid)
        if queue:
            return queue.pop(0)
        # A result with no call to belong to: keep its body visible anyway.
        self.ledger.orphan_results.append(tid)
        content = block.get("content") if isinstance(block, dict) else None
        self._event("orphan_result", d, seq, subtype=tid, text=as_text(content))
        return None

    def _attach_result(
        self, seq: int, block: dict[str, Any], d: dict[str, Any], *, single: bool
    ) -> None:
        tid = str(block.get("tool_use_id") or "")
        call = self._take_waiting(seq, tid, block, d)
        if call is None:
            return
        call.has_result = True
        call.is_error = bool(block.get("is_error"))
        call.result = block.get("content")
        call.result_uuid = str(d.get("uuid") or "")
        if "toolUseResult" in d and (single or d.get("sourceToolUseID") == tid):
            call.structured = d["toolUseResult"]

    # -- assistant ----------------------------------------------------------
    def _assistant(self, seq: int, d: dict[str, Any], content: Any) -> None:
        if isinstance(content, str):
            self._event("assistant_text", d, seq, text=content)
            return
        for block in content:
            btype = block.get("type") if isinstance(block, dict) else None
            if btype == "text":
                self._event("assistant_text", d, seq, text=str(block.get("text", "")))
            elif btype == "thinking":
                self._event("thinking", d, seq, text=str(block.get("thinking", "")))
            elif btype == "redacted_thinking":
                self._event("thinking", d, seq, text="[redacted thinking]")
            elif isinstance(btype, str) and btype.endswith("tool_use"):
                # tool_use, server_tool_use, mcp_tool_use: all are calls.
                self._tool_use(seq, d, block, server=btype != "tool_use")
            elif btype == "tool_addition":
                self._event("tool_addition", d, seq, raw=block)
            elif isinstance(btype, str) and btype.endswith("_tool_result"):
                self._attach_server_result(seq, block, d)
            else:
                self._unknown_block(seq, d, block)

    def _tool_use(
        self, seq: int, d: dict[str, Any], block: dict[str, Any], *, server: bool
    ) -> None:
        tid = str(block.get("id") or "")
        call = ToolCall(
            tool_use_id=tid,
            name=str(block.get("name") or ""),
            input=block.get("input"),
            source=self.tr.source,
            server=server,
            use_uuid=str(d.get("uuid") or ""),
        )
        if tid in self.tr.calls:
            self.ledger.duplicate_tool_ids.append(tid)
        else:
            self.tr.calls[tid] = call
        self.waiting.setdefault(tid, []).append(call)
        self._event("tool", d, seq, tool=call)

    def _attach_server_result(
        self, seq: int, block: dict[str, Any], d: dict[str, Any]
    ) -> None:
        tid = str(block.get("tool_use_id") or "")
        call = self._take_waiting(seq, tid, block, d)
        if call is None:
            return
        content = block.get("content")
        call.has_result = True
        call.result = content
        call.is_error = isinstance(content, dict) and "error" in str(
            content.get("type", "")
        )


def normalize(parsed: ParseResult, ledger: Ledger, *, main: bool) -> Transcript:
    """Normalise one transcript file. ``main`` numbers human prompts."""
    return _Normalizer(parsed, ledger, main=main).tr
