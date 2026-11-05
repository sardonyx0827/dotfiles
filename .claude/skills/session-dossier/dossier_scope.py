"""Cut a session down to the range the reader asked for.

Ranges are applied to the main transcript after everything has been linked,
so a tool result that falls just inside the range still finds its call. A
subagent belongs to the range when the Agent call that launched it does;
nested subagents follow their parent.

"Since the last report" needs a marker. Two kinds exist: the manifest a
previous dossier run wrote (it records the last record it covered), and a
session-report run visible in the transcript itself. The later of the two
wins.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from dossier_link import Session
from dossier_model import Event, ToolCall

SESSION_REPORT_MARKER = "session-report/render_report.py"
# codebase-survey renders its documents with the same script. Its file names
# (<stamp>-survey-<name>.md, the shape the doc-browser mod recognizes) are how
# such a run is told apart from a report on the session's own work. Keep it in
# step with SURVEY_SLUG in .claude/mods/doc-browser/hooks/catalog.ts.
_SURVEY_MARKDOWN = re.compile(r"^\d{8}-\d{6}-survey-[0-9A-Za-z-]+\.md$")


class ScopeError(Exception):
    """The requested range does not exist in this session."""


@dataclass
class Scope:
    label: str
    start_seq: int
    whole: bool
    events: list[Event] = field(default_factory=list)
    subagent_ids: set[str] = field(default_factory=set)
    start_ts: str | None = None
    end_ts: str | None = None
    last_uuid: str | None = None


def _subagents_in(session: Session, events: list[Event], whole: bool) -> set[str]:
    if whole:
        return set(session.subagents)
    found: set[str] = set()
    frontier = [e.tool for e in events if e.kind == "tool" and e.tool is not None]
    while frontier:
        call = frontier.pop()
        agent_id = call.subagent_id
        if agent_id and agent_id not in found and agent_id in session.subagents:
            found.add(agent_id)
            frontier.extend(
                e.tool
                for e in session.subagents[agent_id].transcript.events
                if e.kind == "tool" and e.tool is not None
            )
    return found


def select_scope(
    session: Session,
    *,
    since_prompt: int | None = None,
    after_uuid: str | None = None,
) -> Scope:
    main = session.main
    if since_prompt is not None:
        prompts = [e for e in main.events if e.kind == "prompt"]
        if not 1 <= since_prompt <= len(prompts):
            raise ScopeError(
                f"--since-prompt は 1〜{len(prompts)} の範囲で指定してください"
                f"（指定値: {since_prompt}）"
            )
        start_seq = prompts[since_prompt - 1].seq
        label = f"プロンプト #{since_prompt} 以降"
    elif after_uuid is not None:
        seq = main.uuid_seq.get(after_uuid)
        if seq is None:
            raise ScopeError(f"前回のレポート位置 {after_uuid} が記録にありません")
        start_seq = seq + 1
        label = "前回のレポート以降"
    else:
        start_seq = 0
        label = "セッション全体"
    whole = since_prompt is None and after_uuid is None
    events = [e for e in main.events if e.seq >= start_seq]
    stamps = [e.ts for e in events if e.ts]
    in_range = [(seq, uuid) for uuid, seq in main.uuid_seq.items() if seq >= start_seq]
    return Scope(
        label=label,
        start_seq=start_seq,
        whole=whole,
        events=events,
        subagent_ids=_subagents_in(session, events, whole),
        start_ts=stamps[0] if stamps else None,
        end_ts=stamps[-1] if stamps else None,
        last_uuid=max(in_range)[1] if in_range else None,
    )


def tool_calls(session: Session, scope: Scope) -> list[ToolCall]:
    """Every tool call in range: main transcript first, then each subagent."""
    calls = [e.tool for e in scope.events if e.kind == "tool" and e.tool is not None]
    for agent_id, sub in session.subagents.items():
        if agent_id in scope.subagent_ids:
            calls.extend(
                e.tool
                for e in sub.transcript.events
                if e.kind == "tool" and e.tool is not None
            )
    return calls


def raw_tool_blocks(session: Session, scope: Scope) -> int:
    """tool_use blocks in range by dossier_parse's independent walk.

    The ledger compares this with the items the page actually rendered; it is
    never derived from the normalised events, so the two can disagree.
    """
    total = sum(
        n for seq, n in session.main.raw_tool_blocks.items() if seq >= scope.start_seq
    )
    for agent_id, sub in session.subagents.items():
        if agent_id in scope.subagent_ids:
            total += sum(sub.transcript.raw_tool_blocks.values())
    return total


def _runs_session_report(command: str) -> bool:
    """True when some step of ``command`` executes the report renderer.

    Mentioning the path is not enough: grep, ruff or an editor on the file must
    not move the "last report" marker. Nor does a codebase-survey render: it
    documents a codebase, not the work done so far in this session.
    """
    for step in re.split(r"&&|\|\||[;|\n]", command):
        try:
            argv = shlex.split(step)
        except ValueError:
            continue
        if len(argv) >= 2 and Path(argv[0]).name.startswith("python"):
            args = argv[1:]
            if any(arg.endswith(SESSION_REPORT_MARKER) for arg in args) and not any(
                _SURVEY_MARKDOWN.match(Path(arg).name) for arg in args
            ):
                return True
    return False


def find_last_report_uuid(session: Session, out_dir: Path) -> str | None:
    seq_of = session.main.uuid_seq
    markers: list[tuple[int, str]] = []
    if out_dir.is_dir():
        for manifest in sorted(out_dir.glob("*-dossier-*.json")):
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            uuid = data.get("last_uuid")
            if (
                data.get("session_id") == session.session_id
                and isinstance(uuid, str)
                and uuid in seq_of
            ):
                markers.append((seq_of[uuid], uuid))
    for ev in session.main.events:
        call = ev.tool
        if call is None or call.name != "Bash" or call.status != "ok":
            continue
        command = call.input.get("command") if isinstance(call.input, dict) else None
        if (
            isinstance(command, str)
            and _runs_session_report(command)
            and call.result_uuid in seq_of
        ):
            markers.append((seq_of[call.result_uuid], call.result_uuid))
    return max(markers)[1] if markers else None
