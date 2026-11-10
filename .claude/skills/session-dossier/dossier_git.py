"""Collect what git knows about the range: commits, the diff, untracked files.

The transcript records each edit Claude made; git records what actually ended
up in the tree, including edits made outside the session. The dossier shows
both, and says which base the git diff was taken against, because "the diff"
means nothing without one.

Default base: the commit HEAD pointed at when the range began (first-parent
history, by committer date). ``--git-base`` overrides it. With no commit before
the range start there is no base, and only the working-tree diff is shown.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

GIT_TIMEOUT_S = 20
_FIELD_SEP = "\x1f"
_LOG_FORMAT = _FIELD_SEP.join(
    [
        "%H",
        "%h",
        "%an",
        "%aI",
        "%s",
        "%cn",
        "%(trailers:key=Co-authored-by,valueonly,separator=%x1e)",
    ]
)
_TRAILER_SEP = "\x1e"
# No log entries for an unbounded range would be listed otherwise.
_RECENT_LIMIT = "-20"


@dataclass
class Commit:
    sha: str
    short: str
    author: str
    date: str
    subject: str
    committer: str = ""
    coauthors: list[str] = field(default_factory=list)


@dataclass
class GitInfo:
    available: bool
    reason: str = ""
    toplevel: str = ""
    base: str | None = None
    base_label: str = ""
    commits: list[Commit] = field(default_factory=list)
    stat: str = ""
    diff: str = ""
    untracked: list[str] = field(default_factory=list)
    # Share mode limits the diff to files the session edited.
    scoped: bool = False

    def names(self) -> set[str]:
        """Every person named in the listed commits (for redaction)."""
        found: set[str] = set()
        for commit in self.commits:
            found.update({commit.author, commit.committer, *commit.coauthors})
        return {n for n in found if n}


def _git(repo: Path, *args: str) -> str | None:
    cmd = [
        "git",
        "-C",
        str(repo),
        # A path the session edited ("[id].tsx") is a name, not a glob.
        "--literal-pathspecs",
        "-c",
        "color.ui=false",
        "-c",
        "core.quotepath=false",
    ]
    try:
        proc = subprocess.run(
            [*cmd, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _git_date(ts: str) -> str | None:
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S %z").strip()


def _commits(text: str | None) -> list[Commit]:
    commits = []
    # Not splitlines(): it also splits on \x1e, the trailer separator.
    for line in (text or "").split("\n"):
        parts = line.split(_FIELD_SEP)
        if len(parts) == 7:
            sha, short, author, date, subject, committer, trailers = parts
            coauthors = [
                t.split("<", 1)[0].strip() for t in trailers.split(_TRAILER_SEP) if t
            ]
            commits.append(
                Commit(sha, short, author, date, subject, committer, coauthors)
            )
    return commits


def toplevel(repo: Path) -> str | None:
    """The repository root containing ``repo``, or None outside a repository."""
    out = _git(repo, "rev-parse", "--show-toplevel")
    return out.strip() if out else None


def identity_names(repo: Path) -> set[str]:
    """The configured git user name, which can appear in any command output."""
    where = repo if repo.is_dir() else Path.home()
    name = (_git(where, "config", "user.name") or "").strip()
    return {name} if name else set()


def _relative_paths(root: str, paths: list[str]) -> list[str]:
    base = Path(root).resolve()
    out = []
    for raw in paths:
        candidate = Path(raw)
        full = (candidate if candidate.is_absolute() else base / candidate).resolve()
        # A directory (the repository root included, whose relative form "."
        # matches everything) would widen the diff to unrelated changes.
        if full.is_relative_to(base) and full != base and not full.is_dir():
            out.append(full.relative_to(base).as_posix())
    return sorted(set(out))


def collect_git(
    repo: Path,
    *,
    since_ts: str | None,
    base: str | None,
    paths: list[str] | None = None,
) -> GitInfo:
    """Commits, diff and untracked files for the range.

    ``paths`` (share mode) limits the diff and the untracked list to those
    files, so unrelated work in the tree never reaches a shared page.
    """
    root = toplevel(repo)
    if root is None:
        return GitInfo(available=False, reason="git リポジトリではありません")
    since = _git_date(since_ts) if since_ts else None

    if base:
        resolved = _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
        if not resolved:
            return GitInfo(available=False, reason=f"基点 {base} が見つかりません")
        base_sha: str | None = resolved.strip()
        label = f"--git-base {base}"
    elif since:
        found = (
            _git(repo, "rev-list", "-1", "--first-parent", f"--before={since}", "HEAD")
            or ""
        ).strip()
        base_sha = found or None
        label = f"範囲の開始（{since_ts}）時点の HEAD"
    else:
        base_sha = None
        label = ""

    log = ["log", "--first-parent", f"--format={_LOG_FORMAT}"]
    limit: list[str] = []
    # Share mode with no edited file in the repository: no diff at all.
    no_diff = False
    if paths is not None:
        rel = _relative_paths(root, paths)
        limit = ["--", *rel]
        no_diff = not rel
    if base_sha:
        commits = (
            [] if no_diff else _commits(_git(repo, *log, f"{base_sha}..HEAD", *limit))
        )
        stat = (
            None
            if no_diff
            else _git(repo, "diff", "--no-ext-diff", "--stat", base_sha, *limit)
        )
        diff = (
            None if no_diff else _git(repo, "diff", "--no-ext-diff", base_sha, *limit)
        )
    else:
        bound = [f"--since={since}"] if since else [_RECENT_LIMIT]
        commits = [] if no_diff else _commits(_git(repo, *log, *bound, *limit))
        stat = (
            None
            if no_diff
            else _git(repo, "diff", "--no-ext-diff", "--stat", "HEAD", *limit)
        )
        diff = None if no_diff else _git(repo, "diff", "--no-ext-diff", "HEAD", *limit)
        label = "基点なし（範囲より前のコミットがありません）。作業ツリーの差分のみ"
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard") or ""
    names = [line for line in untracked.splitlines() if line]
    if paths is not None:
        allowed = set(_relative_paths(root, paths))
        names = [n for n in names if n in allowed]
    return GitInfo(
        available=True,
        toplevel=root,
        base=base_sha,
        base_label=label,
        commits=commits,
        stat=stat or "",
        diff=diff or "",
        untracked=names,
        scoped=paths is not None,
    )
