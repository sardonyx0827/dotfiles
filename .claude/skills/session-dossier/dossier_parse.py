"""Read a transcript JSONL without ever giving up on it.

A transcript is an internal, unversioned format that is also being appended to
while the session runs, so this reader assumes the worst: a line may be cut off
mid-write, may not be JSON, or may be JSON that is not an object. None of those
stop the read. Each is recorded as a ParseError with its line number so the
dossier can show the reader exactly what it could not use.

This module also takes the dossier's *independent* count of tool calls. It
walks every record whole, whatever its type or shape, and counts blocks whose
type ends in ``tool_use``. The normaliser never sees this number, so when the
two disagree the ledger can say so instead of agreeing with itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# These records carry a copy of the whole conversation sent to the API; every
# tool_use in them is a duplicate of one already counted in its own record.
API_REQUEST_TYPES = frozenset({"api-request", "api-request-blob", "api-request-shape"})


@dataclass(frozen=True)
class RawRecord:
    source: str
    line_no: int
    data: dict[str, Any]
    tool_blocks: int = 0


@dataclass(frozen=True)
class ParseError:
    source: str
    line_no: int
    length: int
    # The last line of a file still being written is routinely incomplete;
    # flagging it lets the page say "in progress" instead of "corrupt".
    truncated_tail: bool


@dataclass
class ParseResult:
    source: str = ""
    records: list[RawRecord] = field(default_factory=list)
    errors: list[ParseError] = field(default_factory=list)


def count_tool_blocks(data: dict[str, Any]) -> int:
    """Blocks anywhere in the record whose type ends in ``tool_use``."""
    if data.get("type") in API_REQUEST_TYPES:
        return 0
    count = 0
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            kind = node.get("type")
            if node is not data and isinstance(kind, str) and kind.endswith("tool_use"):
                count += 1
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return count


def read_jsonl(path: Path, source: str) -> ParseResult:
    """Parse every line of ``path``; malformed lines become ParseErrors.

    Reads line by line rather than slurping, and decodes with errors="replace"
    so a stray invalid byte costs one character, not the whole file. A missing
    file is the caller's problem and raises OSError as usual.
    """
    result = ParseResult(source=source)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                data = json.loads(stripped)
            except (ValueError, RecursionError):
                data = None
            if isinstance(data, dict):
                result.records.append(
                    RawRecord(source, line_no, data, count_tool_blocks(data))
                )
            else:
                result.errors.append(
                    ParseError(
                        source=source,
                        line_no=line_no,
                        length=len(stripped),
                        truncated_tail=not line.endswith("\n") and data is None,
                    )
                )
    return result
