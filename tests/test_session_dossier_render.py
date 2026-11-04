"""session-dossier: rendering the page, collecting git, and the CLI.

Covers T13-T18 and T26 (and T24-T25 for the Artifact target). The page's
central promise is that every tool call in range becomes exactly one item, so
T13 checks the rendered item count against an independent line scan of the
JSONL files, not against anything the dossier itself reports.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from conftest import REPO_ROOT, run_git
from dossier_fixtures import (
    SessionBuilder,
    all_jsonl,
    scan_tool_use_blocks,
    write_orphan_agent,
)

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_git  # noqa: E402
import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_render  # noqa: E402
import dossier_scope  # noqa: E402
import render_dossier  # noqa: E402

GENERATED_AT = "2026-01-02T03:04:05+09:00"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def load(main):
    return dossier_link.load_session(dossier_locate.paths_for(main))


def render_session(
    main,
    *,
    since_prompt=None,
    notes=None,
    git=None,
    target="page",
    max_output_chars=0,
    wrap_width=0,
):
    session = load(main)
    scope = dossier_scope.select_scope(session, since_prompt=since_prompt)
    options = dossier_render.RenderOptions(
        target=target,
        max_output_chars=max_output_chars,
        generated_at=GENERATED_AT,
        wrap_width=wrap_width,
    )
    return dossier_render.render(session, scope, options, git=git, notes=notes)


def section(html, anchor):
    start = html.index(f'<section id="{anchor}"')
    return html[start : html.index("</section>", start)]


def rich_builder():
    b = SessionBuilder()
    b.record("ai-title", aiTitle="Rich session")
    b.meta("<system-reminder>injected</system-reminder>")
    b.prompt("first prompt\nsecond line")
    b.think("considering options")
    b.say("I will run some tools.")
    b.bash("t1", "ls -la", "a\nb")
    b.bash("t2", "false", stderr="boom", is_error=True)
    b.bash("t3", "sleep 99", interrupted=True)
    b.tool_use("Read", {"file_path": "/x/a.py"}, "t4")
    b.edit("t5", "/x/a.py", "old", "new")
    b.write_file("t6", "/x/b.py", "print(1)\n")
    b.tool_use("Grep", {"pattern": "needle", "path": "/x"}, "t7")
    b.tool_result("t7", "found")
    b.tool_use("WebFetch", {"url": "https://example.com"}, "t8")
    b.tool_result("t8", "page", structured={"url": "https://example.com", "code": 200})
    b.tool_use("mcp__srv__op", {"nested": {"k": [1, 2]}}, "t9")
    b.tool_result("t9", [{"type": "text", "text": "mcp ok"}, {"type": "image"}])
    b.bash(
        "t10",
        "cat big",
        "(truncated)",
        extra={"persistedOutputPath": "/x/tool-results/big1.txt"},
    )
    b.persisted("big1.txt", "BIG BODY")
    b.persisted("stray.txt", "nobody's")
    b.bash("t11", "sed -i s/a/b/ f", "", extra={"bashEditDiff": {"files": []}})
    b.server_tool("advisor", "srv1", "advice text")
    b.assistant([{"type": "tool_addition", "tools": ["X"]}])
    b.record("system", subtype="turn_duration", content="3s")
    b.record("attachment", attachment={"type": "skill_listing", "content": "x"})
    b.record("api-request-blob", body="huge")
    b.record("mode", mode="auto")
    b.record("future-x", foo=1)
    b.assistant([{"type": "future_block"}])
    b.inbound("peer", "teammate says hi")
    b.prompt("second prompt")
    child = b.child("agentA")
    child.legacy_prompt("sub task")
    child.bash("c1", "pwd", "/")
    grandchild = child.child("agentB")
    grandchild.legacy_prompt("deeper")
    grandchild.bash("g1", "id", "uid")
    child.agent_call("c2", "agentB", grandchild)
    b.agent_call("a1", "agentA", child)
    b.agent_call("a2", "agentGone", None)
    b.raw_line("{broken")
    return b


def rich_session(tmp_path):
    b = rich_builder()
    main = b.write(tmp_path)
    orphan = b.child("agentOrphan")
    orphan.legacy_prompt("nobody called me")
    orphan.bash("o1", "true", "")
    write_orphan_agent(main, "agentOrphan", orphan)
    return main


# --------------------------------------------------------------------------
# P4: render
# --------------------------------------------------------------------------
class TestRender:
    def test_t13_every_tool_use_becomes_exactly_one_item(self, tmp_path):
        main = rich_session(tmp_path)
        expected = scan_tool_use_blocks(all_jsonl(main))

        result = render_session(main)

        assert result.html.count('data-kind="tool"') == expected
        assert result.tool_items == expected
        ledger = section(result.html, "ledger")
        assert f"{expected} = {expected}" in ledger

    def test_t13_ranged_render_counts_only_the_range(self, tmp_path):
        main = rich_session(tmp_path)
        session = load(main)
        scope = dossier_scope.select_scope(session, since_prompt=2)
        expected = len(dossier_scope.tool_calls(session, scope))

        result = render_session(main, since_prompt=2)

        assert result.html.count('data-kind="tool"') == expected == 5
        assert "プロンプト #2 以降" in result.html

    def test_t14_content_cannot_inject_markup_or_script(self, tmp_path):
        b = SessionBuilder()
        b.prompt('</script><img src=x onerror=alert(1)> "quoted"')
        b.bash('t"1<x>', 'echo "</script><img src=y>"', "<b>out</b>")
        b.say("<style>body{display:none}</style>")
        main = b.write(tmp_path)

        html = render_session(main).html

        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert scripts == [dossier_render.PAGE_JS]
        assert html.count("<script") == 1
        assert "<img" not in html
        assert "<b>out" not in html
        assert html.count("<style>") == 1
        ids = re.findall(r'id="([^"]*)"', html)
        assert all(re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids)
        assert len(ids) == len(set(ids))

    def test_anchor_ids_stay_readable_and_distinct(self):
        assert dossier_render.anchor_id("tu", "toolu_01Ab") == "tu-toolu_01Ab"
        dotted = dossier_render.anchor_id("tu", "a.b")
        dashed = dossier_render.anchor_id("tu", "a-b")
        assert dotted != dashed
        assert re.fullmatch(r"tu-a-b-[0-9a-f]{6}", dotted)

    def test_t15_same_input_same_bytes(self, tmp_path):
        main = rich_session(tmp_path)
        assert render_session(main).html == render_session(main).html

    def test_t16_notes_are_marked_and_unresolved_ones_listed(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.bash("t1", "ls", "a")
        notes = dossier_render.Notes(
            summary="全体として順調",
            anchors={
                "tu-t1": "note A",
                "prompt-1": "note B",
                "tu-missing": "note C",
            },
        )

        result = render_session(b.write(tmp_path), notes=notes)

        assert result.html.count('class="claude-note"') == 2
        assert "Claude による注記" in result.html
        assert "全体として順調" in section(result.html, "notes")
        assert result.unresolved_notes == ["tu-missing"]
        assert "tu-missing" in section(result.html, "ledger")

    def test_t26_every_version_seen_is_shown(self, tmp_path):
        b = SessionBuilder()
        b.prompt("a")
        b.version = "2.2.0"
        b.say("b")
        header = section(render_session(b.write(tmp_path)).html, "top")
        assert "2.1.0" in header and "2.2.0" in header

    def test_sections_cover_everything_the_session_holds(self, tmp_path):
        html = render_session(rich_session(tmp_path)).html

        assert "Rich session" in section(html, "top")
        timeline = section(html, "timeline")
        assert "considering options" in timeline
        assert "I will run some tools." in timeline
        assert 'data-status="error"' in timeline
        assert 'data-status="interrupted"' in timeline
        assert 'data-status="pending"' in timeline
        assert "turn_duration" in timeline
        assert "teammate says hi" in timeline
        subagents = section(html, "subagents")
        assert 'id="agent-agentA"' in subagents
        assert 'id="agent-agentB"' in subagents
        assert "agentOrphan" in subagents
        files = section(html, "files")
        assert "/x/a.py" in files and "/x/b.py" in files
        persisted = section(html, "persisted")
        assert "BIG BODY" in persisted
        assert "stray.txt" in persisted
        other = section(html, "other")
        for name in ("skill_listing", "api-request-blob", "mode", "future-x"):
            assert name in other
        ledger = section(html, "ledger")
        assert "agentGone" in ledger or "a2" in ledger
        assert "future_block" in ledger
        assert "壊れた行" in ledger

    def test_edit_diff_is_rendered_line_by_line(self, tmp_path):
        b = SessionBuilder()
        b.edit("e1", "/x/a.py", "old line", "new line")
        html = render_session(b.write(tmp_path)).html
        assert '<span class="del">-old line</span>' in html
        assert '<span class="add">+new line</span>' in html

    def test_output_cap_truncates_with_a_visible_note(self, tmp_path):
        b = SessionBuilder()
        b.bash("t1", "yes", "y" * 500)
        html = render_session(b.write(tmp_path), max_output_chars=100).html
        assert "y" * 101 not in html
        assert "省略" in html

    def test_git_section_without_git_info(self, tmp_path):
        b = SessionBuilder()
        b.prompt("x")
        html = render_session(b.write(tmp_path)).html
        assert "取得なし" in section(html, "git")


# --------------------------------------------------------------------------
# P4: git
# --------------------------------------------------------------------------
def _dated_commit(repo, path, text, message, when):
    (repo / path).write_text(text, encoding="utf-8")
    run_git(repo, "add", path)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S%z")
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_DATE": stamp,
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", message],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
    )


class TestGit:
    def test_t17_commits_since_range_start_and_working_tree(self, git_repo):
        start = datetime.now(timezone.utc) + timedelta(seconds=5)
        _dated_commit(
            git_repo, "a.txt", "a\n", "in range", start + timedelta(minutes=1)
        )
        (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
        (git_repo / "new.txt").write_text("u\n", encoding="utf-8")
        since = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        info = dossier_git.collect_git(git_repo, since_ts=since, base=None)

        assert info.available
        assert [c.subject for c in info.commits] == ["in range"]
        assert info.commits[0].author == "Test User"
        assert "+changed" in info.diff
        assert "+a" in info.diff
        assert info.untracked == ["new.txt"]
        assert "開始" in info.base_label

        b = SessionBuilder()
        b.prompt("x")
        html = render_session(b.write(git_repo.parent / "h"), git=info).html
        git_html = section(html, "git")
        assert "in range" in git_html and "new.txt" in git_html
        assert '<span class="add">+changed</span>' in git_html

    def test_explicit_base(self, git_repo):
        first = run_git(git_repo, "rev-parse", "HEAD").strip()
        _dated_commit(git_repo, "b.txt", "b\n", "second", datetime.now(timezone.utc))
        info = dossier_git.collect_git(git_repo, since_ts=None, base=first)
        assert info.base == first
        assert [c.subject for c in info.commits] == ["second"]
        assert "--git-base" in info.base_label

    def test_no_commit_before_range_means_working_tree_only(self, git_repo):
        info = dossier_git.collect_git(
            git_repo, since_ts="2000-01-01T00:00:00Z", base=None
        )
        assert info.available
        assert info.base is None
        assert [c.subject for c in info.commits] == ["initial commit"]
        assert "基点なし" in info.base_label

    def test_no_range_start_lists_recent_commits(self, git_repo):
        info = dossier_git.collect_git(git_repo, since_ts=None, base=None)
        assert [c.subject for c in info.commits] == ["initial commit"]

    def test_unparseable_range_start_is_ignored(self, git_repo):
        info = dossier_git.collect_git(git_repo, since_ts="yesterday-ish", base=None)
        assert info.available

    def test_not_a_repo_or_bad_base(self, tmp_path, git_repo):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert not dossier_git.collect_git(plain, since_ts=None, base=None).available
        bad = dossier_git.collect_git(git_repo, since_ts=None, base="nope-rev")
        assert not bad.available
        assert "nope-rev" in bad.reason

    def test_missing_git_binary(self, git_repo, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("git")

        monkeypatch.setattr(dossier_git.subprocess, "run", boom)
        info = dossier_git.collect_git(git_repo, since_ts=None, base=None)
        assert not info.available


# --------------------------------------------------------------------------
# P4: CLI
# --------------------------------------------------------------------------
def cli(home, *args, opener=None):
    # Point git at the (non-repository) temp home unless a test says otherwise,
    # so the CLI never inspects the real working copy the suite runs from.
    argv = list(args) if "--repo" in args else [*args, "--repo", str(home)]
    return render_dossier.main(
        argv,
        now=NOW,
        env={"CLAUDE_CONFIG_DIR": str(home)},
        opener=opener or (lambda path: None),
    )


class TestCli:
    def write_session(self, tmp_path):
        home = tmp_path / "home"
        b = SessionBuilder()
        b.prompt("one")
        b.bash("t1", "ls", "a")
        b.prompt("two")
        b.bash("t2", "pwd", "/")
        b.write(home)
        return home, b

    def test_t18_writes_page_and_manifest(self, tmp_path, capsys):
        home, b = self.write_session(tmp_path)
        out = tmp_path / "out"

        rc = cli(
            home,
            "--session",
            b.session_id,
            "--cwd",
            b.cwd,
            "--out",
            str(out),
            "--repo",
            str(tmp_path),
            "--no-open",
        )

        assert rc == 0
        html_path = out / "20260102-030405-dossier-11111111.html"
        manifest = json.loads(
            (out / "20260102-030405-dossier-11111111.json").read_text("utf-8")
        )
        stdout = capsys.readouterr().out
        assert str(html_path) in stdout
        assert html_path.is_file()
        assert "tu-t1" in manifest["anchors"]
        assert manifest["session_id"] == b.session_id
        assert manifest["counts"]["tool_items"] == 2
        assert manifest["mode"] == "local"
        assert manifest["last_uuid"]

    def test_since_last_report_uses_the_previous_manifest(self, tmp_path, capsys):
        home, b = self.write_session(tmp_path)
        out = tmp_path / "out"
        assert (
            cli(
                home,
                "--session",
                b.session_id,
                "--out",
                str(out),
                "--since-last-report",
                "--no-open",
            )
            == 1
        )
        assert "前回のレポート" in capsys.readouterr().err
        assert cli(home, "--session", b.session_id, "--out", str(out), "--no-open") == 0
        b.prompt("three")
        b.bash("t3", "id", "uid")
        b.write(home)
        later = render_dossier.main(
            [
                "--session",
                b.session_id,
                "--out",
                str(out),
                "--since-last-report",
                "--no-open",
                "--repo",
                str(home),
            ],
            now=NOW + timedelta(seconds=1),
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )
        assert later == 0
        newest = json.loads(
            (out / "20260102-030406-dossier-11111111.json").read_text("utf-8")
        )
        assert newest["counts"]["tool_items"] == 1

    def test_opens_the_page_unless_told_not_to(self, tmp_path):
        home, b = self.write_session(tmp_path)
        opened = []
        assert (
            cli(
                home,
                "--session",
                b.session_id,
                "--out",
                str(tmp_path / "o"),
                opener=opened.append,
            )
            == 0
        )
        assert len(opened) == 1 and opened[0].suffix == ".html"

    def test_default_out_dir_is_under_claude_home(self, tmp_path):
        home, b = self.write_session(tmp_path)
        assert (
            cli(
                home,
                "--session",
                b.session_id,
                "--cwd",
                str(tmp_path / "nowhere"),
                "--no-open",
            )
            == 0
        )
        assert list((home / "reports" / "no-repo").glob("*-dossier-*.html"))

    def test_default_session_with_concurrent_warning(self, tmp_path, capsys):
        home, b = self.write_session(tmp_path)
        other = SessionBuilder(session_id="22222222-0000-0000-0000-000000000000")
        other.write(home)
        assert cli(home, "--cwd", b.cwd, "--out", str(tmp_path / "o"), "--no-open") == 0
        captured = capsys.readouterr()
        assert "警告" in captured.err

    @pytest.mark.parametrize(
        "args",
        [
            ["--session", "no-such"],
            [
                "--session",
                "11111111-2222-3333-4444-555555555555",
                "--since-prompt",
                "9",
            ],
        ],
    )
    def test_lookup_and_range_errors_exit_1(self, tmp_path, capsys, args):
        home, _ = self.write_session(tmp_path)
        assert cli(home, *args, "--out", str(tmp_path / "o"), "--no-open") == 1
        assert capsys.readouterr().err.startswith("エラー")

    def test_conflicting_target_and_mode_is_a_usage_error(self, tmp_path):
        home, b = self.write_session(tmp_path)
        with pytest.raises(SystemExit) as exc:
            cli(
                home,
                "--session",
                b.session_id,
                "--target",
                "artifact",
                "--mode",
                "local",
            )
        assert exc.value.code == 2

    def test_artifact_target_requires_an_explicit_session(self, tmp_path):
        home, _ = self.write_session(tmp_path)
        with pytest.raises(SystemExit) as exc:
            cli(home, "--target", "artifact")
        assert exc.value.code == 2

    def test_notes_file(self, tmp_path):
        home, b = self.write_session(tmp_path)
        good = tmp_path / "notes.json"
        good.write_text(
            json.dumps({"summary": "まとめ", "anchors": {"tu-t1": "ここが要点"}}),
            encoding="utf-8",
        )
        out = tmp_path / "o"
        assert (
            cli(
                home,
                "--session",
                b.session_id,
                "--out",
                str(out),
                "--no-open",
                "--notes",
                str(good),
            )
            == 0
        )
        html = next(out.glob("*.html")).read_text("utf-8")
        assert "ここが要点" in html

    @pytest.mark.parametrize(
        "body", ["{broken", "[]", '{"anchors": []}', '{"anchors": {"a": 1}}']
    )
    def test_bad_notes_file_exits_1(self, tmp_path, body):
        home, b = self.write_session(tmp_path)
        bad = tmp_path / "notes.json"
        bad.write_text(body, encoding="utf-8")
        assert (
            cli(
                home,
                "--session",
                b.session_id,
                "--out",
                str(tmp_path / "o"),
                "--no-open",
                "--notes",
                str(bad),
            )
            == 1
        )

    def test_open_in_browser_uses_the_platform_opener(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            render_dossier.subprocess, "run", lambda cmd, **kw: calls.append(cmd)
        )
        for system, opener in (("Darwin", "open"), ("Linux", "xdg-open")):
            monkeypatch.setattr(render_dossier.platform, "system", lambda s=system: s)
            render_dossier.open_in_browser(tmp_path / "x.html")
            assert calls[-1][0] == opener
        monkeypatch.setattr(render_dossier.platform, "system", lambda: "Windows")
        render_dossier.open_in_browser(tmp_path / "x.html")
        assert len(calls) == 2

    def test_open_failure_is_reported_not_raised(self, tmp_path, monkeypatch, capsys):
        def boom(cmd, **kw):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(render_dossier.subprocess, "run", boom)
        monkeypatch.setattr(render_dossier.platform, "system", lambda: "Linux")
        render_dossier.open_in_browser(tmp_path / "x.html")
        assert "開けません" in capsys.readouterr().err


# --------------------------------------------------------------------------
# P6: Artifact target
# --------------------------------------------------------------------------
def artifact_render(main, **kw):
    import dossier_redact

    session = load(main)
    scope = dossier_scope.select_scope(session)
    options = dossier_render.RenderOptions(
        target="artifact",
        max_output_chars=kw.get("max_output_chars", 20_000),
        generated_at=GENERATED_AT,
        wrap_width=kw.get("wrap_width", 1_000),
    )
    r = dossier_redact.Redactor(home="/Users/tester", authors=[])
    return dossier_render.render(session, scope, options, redactor=r)


# claude.ai's Read tool truncates very long lines; the publish step needs the
# whole fragment readable, so every line must stay well under that.
READABLE_LINE = 2_000


class TestArtifact:
    def test_t24_fragment_is_self_contained_and_theme_aware(self, tmp_path):
        page = artifact_render(rich_session(tmp_path)).html

        lowered = page.lower()
        for forbidden in ("<!doctype", "<html", "<head", "<body", "<link"):
            assert forbidden not in lowered, forbidden
        assert "<script src" not in lowered
        assert "url(http" not in lowered
        assert "fetch(" not in page
        assert page.startswith("<title>")
        assert '[data-theme="dark"]' in page
        assert "prefers-color-scheme: dark" in page
        assert 'name="claude:viewport-fit-sides"' in page
        assert "max(16px, env(safe-area-inset-left" in page

    def test_t25_huge_outputs_are_capped_and_lines_stay_readable(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.bash("t1", "dump", "z" * 100_000)
        b.bash("t2", "lines", "\n".join(f"line {i}" for i in range(50_000)))
        for i in range(40):
            b.tool_use(f"mcp__srv__op{i}", {"k": "v" * 50}, f"m{i}")
            b.tool_result(f"m{i}", "ok")
            b.record(f"future-{i}", foo=i)

        page = artifact_render(b.write(tmp_path)).html

        assert "z" * 20_001 not in page
        assert "省略" in page
        assert max(len(line) for line in page.splitlines()) < READABLE_LINE

    def test_wrap_never_splits_an_entity(self):
        escaped = "a" * 7 + "&amp;" + "b" * 10
        wrapped = dossier_render._wrap_escaped(escaped, 10)
        assert "&amp;" in wrapped
        assert all(len(line) <= 10 for line in wrapped.split("\n"))

    def test_cli_artifact_writes_fragment_and_never_opens(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        b = SessionBuilder()
        b.prompt("one")
        b.bash("t1", "ls", "a")
        b.write(home)
        monkeypatch.setenv("HOME", "/Users/tester")
        opened = []
        rc = cli(
            home,
            "--session",
            b.session_id,
            "--target",
            "artifact",
            "--out",
            str(tmp_path / "o"),
            opener=opened.append,
        )
        assert rc == 0
        assert opened == []
        written = list((tmp_path / "o").glob("*.artifact.html"))
        assert len(written) == 1
        assert written[0].read_text("utf-8").startswith("<title>")

    def test_cli_artifact_over_the_byte_limit_writes_nothing(self, tmp_path, capsys):
        home = tmp_path / "home"
        b = SessionBuilder()
        b.prompt("one")
        b.bash("t1", "ls", "a" * 5_000)
        b.write(home)
        out = tmp_path / "o"
        rc = cli(
            home,
            "--session",
            b.session_id,
            "--target",
            "artifact",
            "--out",
            str(out),
            "--max-bytes",
            "1000",
        )
        assert rc == 3
        assert not out.exists() or not list(out.iterdir())
        assert "範囲を狭めて" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Review follow-ups
# --------------------------------------------------------------------------
class TestReviewFollowUps:
    def test_ledger_turns_red_when_a_tool_use_was_not_itemised(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.bash("t1", "ls", "a")
        b.record("progress", data={"content": [{"type": "tool_use", "id": "x"}]})
        result = render_session(b.write(tmp_path))
        ledger = section(result.html, "ledger")
        assert "2 ≠ 1" in ledger
        assert 'class="num bad"' in ledger

    def test_orphan_results_and_side_images_are_rendered(self, tmp_path):
        b = SessionBuilder()
        b.tool_result("ghost", "orphan body")
        b.tool_use("Read", {"file_path": "/a.png"}, "r1")
        b.record(
            "user",
            message={
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "r1", "content": "ok"},
                    {"type": "image", "source": {}},
                ],
            },
        )
        timeline = section(render_session(b.write(tmp_path)).html, "timeline")
        assert "orphan body" in timeline
        assert "呼び出しの見つからない結果" in timeline
        assert "画像 1 件" in timeline

    def test_repo_and_out_default_to_the_session_cwd(self, tmp_path, git_repo):
        home = tmp_path / "home"
        b = SessionBuilder(cwd=str(git_repo))
        b.prompt("x")
        b.write(home)
        rc = render_dossier.main(
            ["--session", b.session_id, "--no-open"],
            now=NOW,
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )
        assert rc == 0
        written = list((home / "reports" / git_repo.name).glob("*-dossier-*.json"))
        assert len(written) == 1
