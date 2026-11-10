"""Load a whole session and link what lives in separate files.

A session is more than its main JSONL: each subagent has its own transcript,
and large tool outputs are moved out into ``tool-results/``. This module
loads all of them and pairs each piece with the tool call it belongs to, by
ID, one to one. Whatever cannot be paired is listed in the Ledger rather than
dropped, so the dossier can show "this subagent transcript has no caller" or
"this Agent call has no transcript" explicitly.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from dossier_locate import SessionPaths
from dossier_model import (
    AGENT_TOOL_NAMES,
    Ledger,
    ToolCall,
    Transcript,
    as_text,
    normalize,
)
from dossier_parse import read_jsonl


@dataclass
class Subagent:
    agent_id: str
    meta: dict[str, Any]
    transcript: Transcript
    parent_tool_use_id: str | None = None


@dataclass
class PersistedOutput:
    name: str
    path: Path
    size: int
    text: str
    tool_use_id: str | None = None


@dataclass
class Session:
    session_id: str
    path: Path
    main: Transcript
    ledger: Ledger
    subagents: dict[str, Subagent] = field(default_factory=dict)
    persisted: list[PersistedOutput] = field(default_factory=list)

    def transcripts(self) -> list[Transcript]:
        return [self.main, *(s.transcript for s in self.subagents.values())]

    def all_tool_calls(self) -> list[ToolCall]:
        return [
            ev.tool
            for tr in self.transcripts()
            for ev in tr.events
            if ev.kind == "tool" and ev.tool is not None
        ]

    def tool_call(self, tool_use_id: str) -> ToolCall | None:
        for tr in self.transcripts():
            if tool_use_id in tr.calls:
                return tr.calls[tool_use_id]
        return None


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _link_agents(session: Session) -> None:
    calls: dict[str, ToolCall] = {}
    for call in session.all_tool_calls():
        calls.setdefault(call.tool_use_id, call)
    claimed: set[str] = set()

    def link(call: ToolCall, agent_id: str) -> None:
        call.subagent_id = agent_id
        session.subagents[agent_id].parent_tool_use_id = call.tool_use_id
        claimed.add(agent_id)

    # The subagent's own meta names its caller; that is the authoritative link.
    for agent_id, sub in session.subagents.items():
        tid = sub.meta.get("toolUseId")
        if isinstance(tid, str) and tid in calls and calls[tid].subagent_id is None:
            link(calls[tid], agent_id)
    # Older or partial sessions may lack the meta file; the Agent result's
    # agentId is the fallback.
    for call in calls.values():
        if call.subagent_id is None and isinstance(call.structured, dict):
            named = call.structured.get("agentId")
            if (
                isinstance(named, str)
                and named in session.subagents
                and named not in claimed
            ):
                link(call, named)

    ledger = session.ledger
    for call in session.all_tool_calls():
        if call.name in AGENT_TOOL_NAMES or call.subagent_id:
            ledger.agent_links.append((call.tool_use_id, call.subagent_id))
    ledger.orphan_agents = sorted(a for a in session.subagents if a not in claimed)


def _mentions(text: str, name: str) -> bool:
    pattern = r"(?<![A-Za-z0-9_.-])" + re.escape(name) + r"(?![A-Za-z0-9_-])"
    return re.search(pattern, text) is not None


# Tools whose output routinely *lists* file names (a directory listing, a
# grep hit). Naming a persisted file there says nothing about who wrote it.
_LISTING_TOOLS = frozenset({"Bash", "Read", "Grep", "Glob", "LS"})


def _mentioned_by(name: str, texts: list[tuple[ToolCall, str]]) -> ToolCall | None:
    """The one non-listing call whose result names ``name``; ambiguity is no link."""
    hits = [call for call, text in texts if _mentions(text, name)]
    return hits[0] if len(hits) == 1 else None


def _load_persisted(
    directory: Path, session: Session, session_dir: Path
) -> list[PersistedOutput]:
    if directory.is_symlink():
        session.ledger.skipped_outputs.append(directory.name)
        return []
    if not directory.is_dir():
        return []
    calls = session.all_tool_calls()
    by_id = {c.tool_use_id: c for c in calls}
    by_path: dict[str, ToolCall] = {}
    for known in calls:
        if isinstance(known.structured, dict):
            ref = known.structured.get("persistedOutputPath")
            if isinstance(ref, str) and ref:
                by_path.setdefault(PurePath(ref).name, known)
    texts = [(c, as_text(c.result)) for c in calls if c.name not in _LISTING_TOOLS]
    root = session_dir.resolve()
    outputs = []
    for path in sorted(directory.iterdir()):
        # Never follow a link out of the session directory: its target could be
        # any file on the machine, and the page would publish it.
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            session.ledger.skipped_outputs.append(path.name)
            continue
        if not path.is_file():
            continue
        call: ToolCall | None = by_path.get(path.name) or by_id.get(path.stem)
        if call is None:
            call = _mentioned_by(path.name, texts)
        output = PersistedOutput(
            name=path.name,
            path=path,
            size=path.stat().st_size,
            text=path.read_text(encoding="utf-8", errors="replace"),
            tool_use_id=call.tool_use_id if call else None,
        )
        if call is not None:
            call.persisted.append(path.name)
        outputs.append(output)
    return outputs


def load_session(paths: SessionPaths) -> Session:
    ledger = Ledger()
    main = normalize(read_jsonl(paths.main, "main"), ledger, main=True)
    session = Session(
        session_id=paths.session_id, path=paths.main, main=main, ledger=ledger
    )
    sub_dir = paths.session_dir / "subagents"
    if sub_dir.is_dir():
        root = paths.session_dir.resolve()
        for path in sorted(sub_dir.glob("agent-*.jsonl")):
            # A linked transcript would pull any file on the machine into the page.
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                ledger.skipped_outputs.append(path.name)
                continue
            agent_id = path.stem.removeprefix("agent-")
            parsed = read_jsonl(path, f"agent-{agent_id}")
            session.subagents[agent_id] = Subagent(
                agent_id=agent_id,
                meta=_read_meta(path.with_name(f"agent-{agent_id}.meta.json")),
                transcript=normalize(parsed, ledger, main=False),
            )
    _link_agents(session)
    session.persisted = _load_persisted(
        paths.session_dir / "tool-results", session, paths.session_dir
    )
    return session
