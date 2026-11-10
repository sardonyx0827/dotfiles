#!/usr/bin/env python3
"""Render a Claude Code session as a complete, browsable HTML dossier.

Where session-report is a summary the model writes, this is the record the
model does not write: every prompt, tool call, result, subagent and persisted
output in range, taken mechanically from the transcript JSONL and from git.
A ledger at the top of the page checks that claim (tool calls found = items
rendered, subagent transcripts paired, unknown records counted).

Usage:
    python3 ~/.claude/skills/session-dossier/render_dossier.py --session ID
    python3 ~/.claude/skills/session-dossier/render_dossier.py --session ID \\
        --mode share --target artifact

Exit codes: 0 written; 1 session / range / notes / redaction problem;
2 usage error; 3 Artifact output larger than --max-bytes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from dossier_git import collect_git, identity_names, toplevel
from dossier_link import Session, load_session
from dossier_locate import LocateError, claude_home, locate_session
from dossier_render import EDIT_TOOLS, Notes, RenderOptions, Scrubber, render
from dossier_scope import (
    Scope,
    ScopeError,
    find_last_report_uuid,
    select_scope,
    tool_calls,
)

ARTIFACT_MAX_OUTPUT = 20_000
ARTIFACT_WRAP = 1_000
# The Artifact fragment is read in full before it may be published, so keep
# it small enough for that read to be real; narrow the range for more.
DEFAULT_MAX_BYTES = 200_000
OPEN_TIMEOUT_S = 30


class NotesError(Exception):
    """The --notes file is missing or not in the expected shape."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render_dossier.py",
        description="セッションの全記録を 1 枚の HTML にする",
    )
    p.add_argument("--session", help="セッション ID か .jsonl のパス（省略時は最新）")
    p.add_argument("--cwd", help="プロジェクトを決める作業ディレクトリ")
    p.add_argument("--mode", choices=["local", "share"], help="既定は local")
    p.add_argument("--target", choices=["page", "artifact"], default="page")
    rng = p.add_mutually_exclusive_group()
    rng.add_argument("--since-prompt", type=int, metavar="N")
    rng.add_argument("--since-last-report", action="store_true")
    p.add_argument("--notes", type=Path, help="Claude の注記の JSON")
    p.add_argument("--repo", type=Path, help="git 情報の取得元（既定は --cwd）")
    p.add_argument("--git-base", help="git 差分の基点")
    p.add_argument("--out", type=Path, help="出力先（既定は ~/.claude/reports/<repo>）")
    p.add_argument("--max-output-chars", type=int, metavar="N")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, metavar="N")
    p.add_argument("--no-open", action="store_true", help="ブラウザで開かない")
    return p


def load_notes(path: Path) -> Notes:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NotesError(f"注記ファイルを読めません: {exc}") from exc
    if not isinstance(data, dict):
        raise NotesError("注記ファイルは JSON オブジェクトにしてください")
    summary = data.get("summary", "")
    anchors = data.get("anchors", {})
    if not isinstance(summary, str) or not isinstance(anchors, dict):
        raise NotesError(
            '注記ファイルの形式は {"summary": 文字列, "anchors": {…}} です'
        )
    if not all(isinstance(v, str) for v in anchors.values()):
        raise NotesError("anchors の値は文字列にしてください")
    return Notes(summary=summary, anchors={str(k): v for k, v in anchors.items()})


def project_slug(repo: Path) -> str:
    root = toplevel(repo)
    return Path(root).name if root else "no-repo"


def atomic_write(path: Path, content: str) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if path.exists():
            shutil.copymode(path, tmp)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def open_in_browser(path: Path) -> None:
    opener = {"Darwin": "open", "Linux": "xdg-open"}.get(platform.system())
    if opener is None:
        print(f"ブラウザで開いてください: {path}", file=sys.stderr)
        return
    try:
        subprocess.run(
            [opener, str(path)],
            check=False,
            capture_output=True,
            timeout=OPEN_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        print(f"ブラウザで開けませんでした: {path}", file=sys.stderr)


def _edited_paths(session: Session, scope: Scope) -> list[str]:
    """Files the session edited in range; share mode shows git only for these."""
    paths = []
    for call in tool_calls(session, scope):
        inp = call.input if isinstance(call.input, dict) else {}
        path = inp.get("file_path") or inp.get("notebook_path")
        # Only edits that went through: a failed Edit changed nothing.
        if call.name in EDIT_TOOLS and call.status == "ok" and isinstance(path, str):
            paths.append(path)
    return paths


def _make_redactor(home: str, authors: list[str]) -> Scrubber:
    # Imported here so local mode never loads the hooks' secret scanners.
    from dossier_redact import Redactor

    return Redactor(home=home, authors=authors)


def main(
    argv: list[str] | None = None,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    opener: Callable[[Path], None] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    artifact = args.target == "artifact"
    if artifact and args.mode == "local":
        parser.error("--target artifact は共有用です。--mode local とは併用できません")
    if artifact and not args.session:
        parser.error("--target artifact では --session を必ず指定してください")
    share = artifact or args.mode == "share"
    stamp = now or datetime.now().astimezone()
    cwd = args.cwd or os.getcwd()
    home = claude_home(os.environ if env is None else env)

    try:
        paths, warnings = locate_session(
            args.session, cwd=cwd, home=home, now=stamp.timestamp()
        )
        for warning in warnings:
            print(f"警告: {warning}", file=sys.stderr)
        session = load_session(paths)
        # git and the reports directory follow the session's own working
        # directory, not wherever this command happens to run from.
        work = args.cwd or session.ledger.cwd or os.getcwd()
        repo = args.repo or Path(work)
        out_dir = args.out or home / "reports" / project_slug(repo)
        after = None
        if args.since_last_report:
            after = find_last_report_uuid(session, out_dir)
            if after is None:
                raise ScopeError(
                    "前回のレポートが見つかりません（dossier の manifest も"
                    " session-report の実行記録もありません）"
                )
        scope = select_scope(session, since_prompt=args.since_prompt, after_uuid=after)
        notes = load_notes(args.notes) if args.notes else Notes()
        git = collect_git(
            repo,
            since_ts=scope.start_ts,
            base=args.git_base,
            paths=_edited_paths(session, scope) if share else None,
        )
        redactor = (
            _make_redactor(str(Path.home()), sorted(git.names() | identity_names(repo)))
            if share
            else None
        )
    except (LocateError, ScopeError, NotesError, RuntimeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if args.max_output_chars is not None:
        max_chars = args.max_output_chars
    else:
        max_chars = ARTIFACT_MAX_OUTPUT if artifact else 0
    options = RenderOptions(
        target=args.target,
        max_output_chars=max_chars,
        generated_at=stamp.isoformat(timespec="seconds"),
        wrap_width=ARTIFACT_WRAP if artifact else 0,
        home=str(Path.home()),
    )
    result = render(session, scope, options, git=git, notes=notes, redactor=redactor)
    size = len(result.html.encode("utf-8"))
    if artifact and size > args.max_bytes:
        print(
            f"エラー: 出力が {size:,} バイトで、上限 {args.max_bytes:,} バイトを超えました。"
            "--since-prompt などで範囲を狭めてください",
            file=sys.stderr,
        )
        return 3

    suffix = ".artifact" if artifact else ".share" if share else ""
    stem = f"{stamp:%Y%m%d-%H%M%S}-dossier-{session.session_id[:8]}{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / f"{stem}.html"
    manifest_path = out_dir / f"{stem}.json"
    transcript = str(paths.main)
    if redactor is not None:
        transcript = redactor.scrub(transcript, count=False)
    manifest: dict[str, Any] = {
        "generator": "session-dossier",
        "session_id": session.session_id,
        "transcript": transcript,
        "mode": "share" if share else "local",
        "target": args.target,
        "generated_at": options.generated_at,
        "scope": {
            "label": scope.label,
            "start_ts": scope.start_ts,
            "end_ts": scope.end_ts,
        },
        "last_uuid": scope.last_uuid,
        "html": html_path.name,
        "anchors": result.anchors,
        "counts": {
            "tool_items": result.tool_items,
            "tool_calls_in_scope": len(tool_calls(session, scope)),
            "subagents": len(scope.subagent_ids),
            "persisted_outputs": len(session.persisted),
            "unknown_records": sum(session.ledger.unknown_records.values()),
            "unknown_blocks": sum(session.ledger.unknown_blocks.values()),
            "parse_errors": len(session.ledger.parse_errors),
            "unresolved_notes": len(result.unresolved_notes),
        },
    }
    if redactor is not None:
        manifest["redaction"] = [
            {"group": g, "label": label, "count": n}
            for g, label, n in redactor.summary()
        ]
    atomic_write(html_path, result.html)
    atomic_write(
        manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )

    print(f"session: {paths.main}")
    print(f"html: {html_path}")
    print(f"manifest: {manifest_path}")
    print(f"tool items: {result.tool_items}")
    if redactor is not None:
        print("redaction:")
        for group, label, n in redactor.summary():
            print(f"  {group} / {label}: {n}")
    if not artifact and not args.no_open:
        (opener or open_in_browser)(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
