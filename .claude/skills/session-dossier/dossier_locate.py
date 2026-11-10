"""Find the transcript JSONL for a session, and the files that belong with it.

Claude Code stores a session as::

    <claude home>/projects/<project slug>/<session id>.jsonl
    <claude home>/projects/<project slug>/<session id>/subagents/agent-*.jsonl
    <claude home>/projects/<project slug>/<session id>/tool-results/*.txt

The slug is derived from the working directory, but the exact rule is not
documented, so this module tries the likely encodings and then falls back to
reading each project's transcripts for a matching ``cwd``.

Picking "the newest JSONL" is a guess when several sessions run in the same
project at once. The guess is reported, never hidden: the caller prints the
chosen path, and any other recently written transcript produces a warning.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# A session id is a UUID in practice. Anything outside this set (a slash, a
# "..") would let --session reach outside the projects directory.
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Another transcript written this recently probably belongs to a concurrent
# session, which makes "newest" ambiguous.
CONCURRENT_WINDOW_S = 600

# How far into a transcript to look for its cwd before giving up on it.
_CWD_SCAN_LINES = 50


class LocateError(Exception):
    """The session could not be found; the message says where it looked."""


@dataclass(frozen=True)
class SessionPaths:
    main: Path

    @property
    def session_dir(self) -> Path:
        return self.main.with_suffix("")

    @property
    def session_id(self) -> str:
        return self.main.stem


def paths_for(main: Path) -> SessionPaths:
    return SessionPaths(main=main)


def claude_home(env: Mapping[str, str]) -> Path:
    configured = env.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def slug_candidates(cwd: str) -> list[str]:
    candidates = [re.sub(r"[^A-Za-z0-9]", "-", cwd), cwd.replace("/", "-")]
    return list(dict.fromkeys(candidates))


def _first_cwd(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= _CWD_SCAN_LINES:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and isinstance(rec.get("cwd"), str):
                    return str(rec["cwd"])
    except OSError:
        return None
    return None


def find_project_dir(projects: Path, cwd: str) -> Path | None:
    for slug in slug_candidates(cwd):
        candidate = projects / slug
        if candidate.is_dir():
            return candidate
    if not projects.is_dir():
        return None
    for proj in sorted(p for p in projects.iterdir() if p.is_dir()):
        for transcript in sorted(proj.glob("*.jsonl")):
            if _first_cwd(transcript) == cwd:
                return proj
    return None


def _by_id(session: str, projects: Path, cwd: str) -> SessionPaths:
    if not SESSION_ID_RE.match(session):
        raise LocateError(f"セッション ID として不正です: {session!r}")
    proj = find_project_dir(projects, cwd)
    candidates = [proj / f"{session}.jsonl"] if proj else []
    if projects.is_dir():
        candidates.extend(sorted(projects.glob(f"*/{session}.jsonl")))
    for candidate in candidates:
        if candidate.is_file():
            return paths_for(candidate)
    raise LocateError(f"セッション {session} が {projects} の下に見つかりません")


def locate_session(
    session: str | None, *, cwd: str, home: Path, now: float
) -> tuple[SessionPaths, list[str]]:
    """Resolve ``session`` (an id, a .jsonl path, or None for newest).

    Returns the paths and any warnings the caller should print.
    """
    projects = home / "projects"
    if session:
        as_path = Path(session).expanduser()
        if as_path.suffix == ".jsonl" and as_path.is_file():
            return paths_for(as_path.absolute()), []
        return _by_id(session, projects, cwd), []

    proj = find_project_dir(projects, cwd)
    if proj is None:
        raise LocateError(f"{cwd} に対応するプロジェクトが {projects} にありません")
    transcripts = sorted(
        proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not transcripts:
        raise LocateError(f"{proj} にセッションの記録がありません")
    chosen = transcripts[0]
    warnings = [
        f"同時に更新中のセッションがあります: {other.name}"
        " （対象が違う場合は --session で指定してください）"
        for other in transcripts[1:]
        if now - other.stat().st_mtime < CONCURRENT_WINDOW_S
    ]
    return paths_for(chosen), warnings
