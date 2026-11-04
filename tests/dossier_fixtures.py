"""Synthetic transcript builder for the session-dossier tests.

Every record here is invented. Real transcripts carry CLAUDE.md, home paths and
whatever the session printed, so none of their content may be copied into a
fixture; only their *shape* (record types, block types, toolUseResult keys) is
mirrored, as observed on real files.

The builder also keeps its own tally of what it wrote (``truth``), so tests can
compare the dossier against what the fixture actually contains rather than
against what the parser under test reports.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def fake_github_token() -> str:
    # Assembled at runtime so the literal never appears in the repository,
    # where the repo's own secret scanners would (rightly) flag it.
    return "gh" + "p_" + "a" * 36


@dataclass
class SessionBuilder:
    session_id: str = "11111111-2222-3333-4444-555555555555"
    cwd: str = "/work/proj"
    version: str = "2.1.0"
    agent_id: str | None = None
    lines: list[str] = field(default_factory=list)
    truth: Counter = field(default_factory=Counter)
    subagents: dict[str, tuple[SessionBuilder, dict[str, Any]]] = field(
        default_factory=dict
    )
    tool_results: dict[str, str] = field(default_factory=dict)
    _n: int = 0

    # -- low level ---------------------------------------------------------
    def _next(self) -> int:
        self._n += 1
        return self._n

    def base(self, rtype: str, **extra: Any) -> dict[str, Any]:
        n = self._next()
        prefix = self.agent_id or "m"
        rec: dict[str, Any] = {
            "type": rtype,
            "uuid": f"{prefix}-u{n:04d}",
            "timestamp": f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}.000Z",
            "sessionId": self.session_id,
            "cwd": self.cwd,
            "version": self.version,
            "gitBranch": "main",
        }
        if self.agent_id:
            rec["isSidechain"] = True
            rec["agentId"] = self.agent_id
        rec.update(extra)
        return rec

    def add(self, rec: dict[str, Any]) -> str:
        self.lines.append(json.dumps(rec, ensure_ascii=False))
        self.truth["records"] += 1
        return str(rec.get("uuid", ""))

    def raw_line(self, text: str) -> None:
        self.lines.append(text)

    def record(self, rtype: str, **extra: Any) -> str:
        return self.add(self.base(rtype, **extra))

    # -- user side ---------------------------------------------------------
    def prompt(self, text: str) -> str:
        self.truth["prompts"] += 1
        return self.record(
            "user",
            origin={"kind": "human"},
            message={"role": "user", "content": text},
        )

    def legacy_prompt(self, text: str) -> str:
        self.truth["prompts"] += 1
        return self.record("user", message={"role": "user", "content": text})

    def meta(self, text: str) -> str:
        return self.record(
            "user", isMeta=True, message={"role": "user", "content": text}
        )

    def compact(self, text: str) -> str:
        return self.record(
            "user",
            isCompactSummary=True,
            message={"role": "user", "content": [{"type": "text", "text": text}]},
        )

    def inbound(self, kind: str, text: str) -> str:
        return self.record(
            "user", origin={"kind": kind}, message={"role": "user", "content": text}
        )

    def command(self, text: str) -> str:
        return self.record("user", message={"role": "user", "content": text})

    def image_prompt(self, text: str) -> str:
        self.truth["prompts"] += 1
        return self.record(
            "user",
            origin={"kind": "human"},
            message={
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image", "source": {"type": "base64", "data": "AAAA"}},
                ],
            },
        )

    # -- assistant side ----------------------------------------------------
    def assistant(self, blocks: list[dict[str, Any]]) -> str:
        return self.record(
            "assistant",
            message={"role": "assistant", "model": "claude-test", "content": blocks},
        )

    def say(self, text: str) -> str:
        return self.assistant([{"type": "text", "text": text}])

    def think(self, text: str) -> str:
        return self.assistant(
            [{"type": "thinking", "thinking": text, "signature": "sig"}]
        )

    def tool_use(self, name: str, tool_input: Any, tool_id: str) -> str:
        self.truth["tool_use"] += 1
        self.assistant(
            [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]
        )
        return tool_id

    def tool_result(
        self,
        tool_id: str,
        content: Any,
        *,
        is_error: bool = False,
        structured: Any = None,
    ) -> str:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        extra: dict[str, Any] = {
            "message": {"role": "user", "content": [block]},
            "sourceToolUseID": tool_id,
        }
        if structured is not None:
            extra["toolUseResult"] = structured
        return self.record("user", **extra)

    def bash(
        self,
        tool_id: str,
        command: str,
        stdout: str = "",
        *,
        stderr: str = "",
        is_error: bool = False,
        interrupted: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> str:
        self.tool_use("Bash", {"command": command, "description": "run"}, tool_id)
        structured = {
            "stdout": stdout,
            "stderr": stderr,
            "interrupted": interrupted,
            "isImage": False,
            "noOutputExpected": False,
        }
        structured.update(extra or {})
        self.tool_result(
            tool_id, stdout or stderr, is_error=is_error, structured=structured
        )
        return tool_id

    def edit(self, tool_id: str, file_path: str, old: str, new: str) -> str:
        self.tool_use(
            "Edit",
            {"file_path": file_path, "old_string": old, "new_string": new},
            tool_id,
        )
        self.tool_result(
            tool_id,
            "The file has been updated successfully.",
            structured={
                "filePath": file_path,
                "oldString": old,
                "newString": new,
                "originalFile": f"line1\n{old}\nline3\n",
                "structuredPatch": [
                    {
                        "oldStart": 1,
                        "oldLines": 3,
                        "newStart": 1,
                        "newLines": 3,
                        "lines": [" line1", f"-{old}", f"+{new}", " line3"],
                    }
                ],
                "replaceAll": False,
                "userModified": False,
            },
        )
        return tool_id

    def write_file(self, tool_id: str, file_path: str, content: str) -> str:
        self.tool_use("Write", {"file_path": file_path, "content": content}, tool_id)
        self.tool_result(
            tool_id,
            "File created successfully",
            structured={
                "type": "create",
                "filePath": file_path,
                "content": content,
                "structuredPatch": [],
                "originalFile": None,
                "userModified": False,
            },
        )
        return tool_id

    def server_tool(self, name: str, server_id: str, result: Any) -> str:
        self.truth["tool_use"] += 1
        return self.assistant(
            [
                {"type": "server_tool_use", "id": server_id, "name": name, "input": {}},
                {
                    "type": f"{name}_tool_result",
                    "tool_use_id": server_id,
                    "content": result,
                },
            ]
        )

    def agent_call(
        self,
        tool_id: str,
        agent_id: str | None,
        child: SessionBuilder | None,
        *,
        name: str = "Agent",
        meta_tool_use_id: str | None = None,
    ) -> str:
        self.tool_use(name, {"description": "sub", "prompt": "do it"}, tool_id)
        structured = {"status": "completed", "prompt": "do it", "description": "sub"}
        if agent_id:
            structured["agentId"] = agent_id
        self.tool_result(
            tool_id, [{"type": "text", "text": "done"}], structured=structured
        )
        if agent_id and child is not None:
            self.subagents[agent_id] = (
                child,
                {
                    "agentType": "general-purpose",
                    "description": "sub",
                    "toolUseId": meta_tool_use_id or tool_id,
                    "spawnDepth": 1,
                    "model": "claude-test",
                    "finished": True,
                },
            )
        return tool_id

    def child(self, agent_id: str) -> SessionBuilder:
        return SessionBuilder(
            session_id=self.session_id,
            cwd=self.cwd,
            version=self.version,
            agent_id=agent_id,
        )

    def persisted(self, name: str, content: str) -> None:
        self.tool_results[name] = content

    # -- output -----------------------------------------------------------
    def text(self) -> str:
        return "\n".join(self.lines) + "\n"

    def write(self, root: Path, *, trailing_newline: bool = True) -> Path:
        """Write a projects/<slug>/<session>.jsonl tree under root; return the JSONL."""
        slug = self.cwd.replace("/", "-").replace(".", "-")
        proj = root / "projects" / slug
        proj.mkdir(parents=True, exist_ok=True)
        main = proj / f"{self.session_id}.jsonl"
        body = self.text()
        main.write_text(
            body if trailing_newline else body.rstrip("\n"), encoding="utf-8"
        )
        sdir = proj / self.session_id
        # Subagent transcripts live in one flat directory however deep the
        # nesting, so walk every generation of children.
        pending = list(self.subagents.items())
        flat: list[tuple[str, tuple[SessionBuilder, dict[str, Any]]]] = []
        while pending:
            item = pending.pop(0)
            flat.append(item)
            pending.extend(item[1][0].subagents.items())
        for agent_id, (child, meta) in flat:
            sub = sdir / "subagents"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / f"agent-{agent_id}.jsonl").write_text(child.text(), encoding="utf-8")
            (sub / f"agent-{agent_id}.meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            for name, content in child.tool_results.items():
                (sdir / "tool-results").mkdir(parents=True, exist_ok=True)
                (sdir / "tool-results" / name).write_text(content, encoding="utf-8")
        for name, content in self.tool_results.items():
            (sdir / "tool-results").mkdir(parents=True, exist_ok=True)
            (sdir / "tool-results" / name).write_text(content, encoding="utf-8")
        return main


def write_orphan_agent(main: Path, agent_id: str, child: SessionBuilder) -> None:
    """A subagent transcript that no Agent call in the parent points at."""
    sub = main.with_suffix("") / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"agent-{agent_id}.jsonl").write_text(child.text(), encoding="utf-8")
    (sub / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"toolUseId": "toolu_nobody", "agentType": "x"}), encoding="utf-8"
    )


def scan_tool_use_blocks(paths: list[Path]) -> int:
    """Count tool_use + server_tool_use blocks with a plain line scan.

    Deliberately independent of the dossier parser: this is the expected value
    the parser's output is checked against.
    """
    total = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            if isinstance(content, list):
                total += sum(
                    1
                    for b in content
                    if isinstance(b, dict)
                    and b.get("type") in ("tool_use", "server_tool_use")
                )
    return total


def all_jsonl(main: Path) -> list[Path]:
    sub = main.with_suffix("") / "subagents"
    return [main, *sorted(sub.glob("*.jsonl"))] if sub.is_dir() else [main]
