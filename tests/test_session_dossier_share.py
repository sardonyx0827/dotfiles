"""session-dossier share mode: what may leave the machine (T19-T23).

Share mode is an allow-list: only the timeline's own fields are rendered, and
every string on the way to the page (or the manifest) passes the redactor.
These tests plant sensitive values in every place a transcript duplicates
them, then check that none survives anywhere in the output, including after
undoing HTML escaping.
"""

import html
import json
import re
import sys
import time
from datetime import datetime, timezone

import pytest
from conftest import REPO_ROOT
from dossier_fixtures import SessionBuilder, fake_github_token

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_redact  # noqa: E402
import dossier_render  # noqa: E402
import dossier_scope  # noqa: E402
import render_dossier  # noqa: E402

HOME = "/Users/tester"
EMAIL = "someone@example.com"
TMP = "/private/tmp/claude-501/-Users-tester-project/abc/scratchpad"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def load(main):
    return dossier_link.load_session(dossier_locate.paths_for(main))


def render(main, redactor=None, notes=None):
    session = load(main)
    scope = dossier_scope.select_scope(session)
    options = dossier_render.RenderOptions(generated_at="2026-01-02T03:04:05Z")
    return dossier_render.render(
        session, scope, options, notes=notes, redactor=redactor
    )


def redactor(authors=()):
    return dossier_redact.Redactor(home=HOME, authors=list(authors))


def excluded_session():
    b = SessionBuilder(cwd=f"{HOME}/project")
    b.record("attachment", attachment={"type": "nested_memory", "content": "ATTACH-1"})
    b.record("api-request-blob", body="API-BODY-1")
    b.record("api-request", body="API-BODY-2")
    b.record("mode", mode="MODE-VALUE")
    b.meta("META-INJECT")
    b.compact("COMPACT-SUMMARY")
    b.inbound("peer", "PEER-REPORT")
    b.record("future-x", payload="UNKNOWN-RAW")
    b.prompt("go")
    b.think("THOUGHT-1")
    b.think("THOUGHT-2")
    b.image_prompt("look at this")
    b.tool_use("Bash", {"command": "ls"}, "t1")
    rec = b.base(
        "user",
        message={
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}],
        },
        permissionDecision="PERM-DECISION",
        toolUseResult={"stdout": "ok", "stderr": ""},
    )
    b.add(rec)
    return b


class TestExclusion:
    def test_t19_excluded_categories_never_reach_the_page(self, tmp_path):
        main = excluded_session().write(tmp_path)
        r = redactor()

        page = render(main, r).html

        for marker in (
            "THOUGHT-1",
            "THOUGHT-2",
            "ATTACH-1",
            "API-BODY-1",
            "API-BODY-2",
            "MODE-VALUE",
            "META-INJECT",
            "COMPACT-SUMMARY",
            "PEER-REPORT",
            "PERM-DECISION",
            "UNKNOWN-RAW",
        ):
            assert marker not in page, marker
        assert r.excluded["thinking"] == 2
        assert r.excluded["attachment"] == 1
        assert r.excluded["api_request"] == 2
        assert r.excluded["meta"] == 1
        assert r.excluded["injected"] == 3
        assert r.excluded["image"] == 1
        assert r.excluded["raw_json"] == 1
        assert "SHARE" in page
        assert 'id="redaction"' in page

    def test_local_render_keeps_what_share_drops(self, tmp_path):
        page = render(excluded_session().write(tmp_path)).html
        assert "THOUGHT-1" in page and "META-INJECT" in page
        assert 'id="redaction"' not in page


def planted_session(tmp_path):
    token = fake_github_token()
    b = SessionBuilder(cwd=f"{HOME}/project")
    b.prompt(f"mail {EMAIL} about {HOME}/project/a.py")
    b.bash("t1", f"cat {HOME}/.env", f"TOKEN={token}\nfrom {TMP}")
    b.edit("t2", f"{HOME}/project/a.py", f"key = '{token}'", "key = env()")
    b.bash(
        "t3",
        "big",
        "(truncated)",
        extra={"persistedOutputPath": f"{HOME}/.claude/x/tool-results/big.txt"},
    )
    b.persisted("big.txt", f"{token} in ~/.claude/projects/-Users-tester-project/")
    child = b.child("agentS")
    child.legacy_prompt(f"sub reads {HOME}/secret")
    child.bash("s1", "env", f"GITHUB_TOKEN={token}")
    b.agent_call("a1", "agentS", child)
    b.tool_use("Read", {"file_path": f"{HOME}/project/b.py"}, "t4")
    b.tool_result(
        "t4",
        "body\n<system-reminder>\nhidden instructions\n</system-reminder>\nend",
    )
    return b, token


class TestReplacement:
    def test_t20_planted_values_survive_nowhere(self, tmp_path, git_repo, monkeypatch):
        b, token = planted_session(tmp_path)
        home = tmp_path / "claude"
        b.write(home)
        (git_repo / "README.md").write_text(
            f"token {token} by {EMAIL}\n", encoding="utf-8"
        )
        notes = tmp_path / "notes.json"
        notes.write_text(
            json.dumps(
                {"summary": f"see {HOME}/x", "anchors": {"tu-t1": f"leaked {token}"}}
            ),
            encoding="utf-8",
        )
        out = tmp_path / "out"
        monkeypatch.setenv("HOME", HOME)

        rc = render_dossier.main(
            [
                "--session",
                b.session_id,
                "--mode",
                "share",
                "--out",
                str(out),
                "--repo",
                str(git_repo),
                "--notes",
                str(notes),
                "--no-open",
            ],
            now=NOW,
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )

        assert rc == 0
        page = (out / "20260102-030405-dossier-11111111.share.html").read_text("utf-8")
        manifest = (out / "20260102-030405-dossier-11111111.share.json").read_text(
            "utf-8"
        )
        for text in (page, html.unescape(page), manifest):
            for planted in (
                token,
                EMAIL,
                HOME,
                "-Users-tester",
                "/private/tmp/claude-501",
                "Test User",
                "hidden instructions",
            ):
                assert planted not in text, planted
        assert "[REDACTED]" in page
        assert "~/project" in page
        assert "&lt;tmp&gt;/" in page

    def test_t21_system_reminders_are_removed_and_counted(self, tmp_path):
        b, _ = planted_session(tmp_path)
        r = redactor()
        page = render(b.write(tmp_path), r).html
        assert "hidden instructions" not in page
        visible = html.unescape(page)
        for _group, label, _n in r.summary():
            visible = visible.replace(label, "")
        assert "system-reminder" not in visible
        assert r.replaced["system_reminder"] == 1
        assert r.replaced["secret"] >= 3
        assert r.replaced["email"] >= 1

    def test_t22_anchors_do_not_move_between_local_and_share(self, tmp_path):
        b, _ = planted_session(tmp_path)
        main = b.write(tmp_path)
        assert render(main).anchors == render(main, redactor()).anchors

    def test_t23_share_summary_lists_every_category(
        self, tmp_path, monkeypatch, capsys
    ):
        b = SessionBuilder()
        b.prompt("plain")
        home = tmp_path / "claude"
        b.write(home)
        monkeypatch.setenv("HOME", HOME)
        rc = render_dossier.main(
            [
                "--session",
                b.session_id,
                "--mode",
                "share",
                "--out",
                str(tmp_path / "o"),
                "--repo",
                str(home),
                "--no-open",
            ],
            now=NOW,
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )
        assert rc == 0
        stdout = capsys.readouterr().out
        labels = [
            *dossier_redact.EXCLUDE_LABELS.values(),
            *dossier_redact.REPLACE_LABELS.values(),
        ]
        for label in labels:
            assert re.search(re.escape(label) + r": 0\b", stdout), label
        manifest = json.loads(
            next((tmp_path / "o").glob("*.share.json")).read_text("utf-8")
        )
        assert len(manifest["redaction"]) == len(labels)


class TestRedactor:
    def test_scrub_order_and_boundaries(self):
        r = dossier_redact.Redactor(home=HOME, authors=["Ann Lee", "x"])
        text = (
            f"{HOME}/a {HOME}2/b -Users-tester-proj -Users-testers "
            f"/tmp/claude-12/z by Ann Lee, Ann Leeway"
        )
        assert r.scrub(text) == (
            "~/a /Users/[user]/b ~-proj -Users-testers <tmp>/z by [author], Ann Leeway"
        )

    def test_bare_username_and_slashless_tmp_dir(self):
        r = redactor()
        text = 'pytest-of-tester/x "/private/tmp/claude-501" tester2 testers'
        assert r.scrub(text) == 'pytest-of-[user]/x "<tmp>" tester2 testers'
        assert r.replaced["username"] == 1
        assert r.replaced["tmp_dir"] == 1

    def test_short_usernames_are_left_alone(self):
        r = dossier_redact.Redactor(home="/home/me", authors=[])
        assert r.scrub("me and some mentions") == "me and some mentions"

    def test_count_false_does_not_touch_the_tally(self):
        r = redactor()
        r.scrub(EMAIL, count=False)
        assert r.replaced["email"] == 0
        r.scrub(EMAIL)
        assert r.replaced["email"] >= 1

    def test_long_runs_scrub_in_linear_time(self):
        # Regression: an unbounded e-mail pattern took ~9 s on this input.
        start = time.perf_counter()
        redactor().scrub("z" * 200_000 + " a@b.co " + "y.y" * 50_000)
        assert time.perf_counter() - start < 2.0

    def test_email_shapes(self):
        r = redactor()
        assert r.scrub("x first.last+tag@mail.example.co.jp y") == "x [REDACTED] y"
        assert r.scrub("no at sign here") == "no at sign here"

    def test_unclosed_reminder_is_removed_to_the_end(self):
        assert redactor().scrub("a <system-reminder> rest") == "a "

    def test_summary_lists_every_category_in_order(self):
        r = redactor()
        r.exclude("thinking", 2)
        summary = r.summary()
        assert summary[0] == ("除外", dossier_redact.EXCLUDE_LABELS["thinking"], 2)
        assert [s[0] for s in summary].count("置換") == len(
            dossier_redact.REPLACE_LABELS
        )

    def test_unknown_category_is_a_programming_error(self):
        with pytest.raises(KeyError):
            redactor().exclude("nope")

    def test_missing_scanners_fail_closed(self, tmp_path):
        with pytest.raises(dossier_redact.RedactionUnavailable):
            dossier_redact.load_secret_scanners(tmp_path)
        broken = tmp_path / "_bash_review_common.py"
        broken.write_text("_SECRET_SCANNERS = []\n", encoding="utf-8")
        with pytest.raises(dossier_redact.RedactionUnavailable):
            dossier_redact.load_secret_scanners(tmp_path)
        broken.write_text("raise ImportError('x')\n", encoding="utf-8")
        with pytest.raises(dossier_redact.RedactionUnavailable):
            dossier_redact.load_secret_scanners(tmp_path)

    def test_cli_reports_unavailable_redaction(self, tmp_path, monkeypatch, capsys):
        b = SessionBuilder()
        b.prompt("x")
        home = tmp_path / "claude"
        b.write(home)

        def unavailable(*a, **k):
            raise dossier_redact.RedactionUnavailable("scanners missing")

        monkeypatch.setattr(dossier_redact, "load_secret_scanners", unavailable)
        rc = render_dossier.main(
            [
                "--session",
                b.session_id,
                "--mode",
                "share",
                "--out",
                str(tmp_path / "o"),
                "--repo",
                str(home),
                "--no-open",
            ],
            now=NOW,
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )
        assert rc == 1
        assert "scanners missing" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Security review follow-ups
# --------------------------------------------------------------------------
PEM_BODY = "MIIEowIBAAKCAQEAuniqueKEYBODYline1\nKEYBODYline2abcdef"


def pem(kind="RSA PRIVATE KEY"):
    return f"-----BEGIN {kind}-----\n{PEM_BODY}\n-----END {kind}-----"


class TestSecurityFollowUps:
    @pytest.mark.parametrize(
        "block",
        [
            pem(),
            pem("OPENSSH PRIVATE KEY"),
            pem("PGP PRIVATE KEY BLOCK"),
            pem().replace("\n", "\\n"),
            "-----BEGIN PRIVATE KEY-----\nKEYBODYline1 never closed",
        ],
    )
    def test_private_key_blocks_go_whole(self, block):
        out = redactor().scrub(f"before {block} after")
        assert "KEYBODYline" not in out
        assert "END" not in out
        assert out.startswith("before [REDACTED]")

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            (
                '{\\"Authorization\\": \\"Bearer abcdefghijklmnopqrstuv0123\\"}',
                "abcdefghij",
            ),
            ("Authorization: Basic dXNlcjpwYXNzd29yZDEyMw==", "dXNlcjpw"),
            ('{"name":"DB_PASSWORD","value":"hunter2hunter2"}', "hunter2"),
            ("password=hunter2", "hunter2"),
            ("export API_TOKEN=abc123xyz", "abc123xyz"),
            ("Cookie: sid=abcdef123456; theme=dark", "abcdef123456"),
            ('"client_secret": "s3cr3tvalue"', "s3cr3tvalue"),
        ],
    )
    def test_secret_corpus_is_scrubbed(self, text, secret):
        assert secret not in redactor().scrub(text)

    def test_json_inputs_are_scrubbed_before_dumping(self, tmp_path):
        b = SessionBuilder()
        b.tool_use(
            "WebFetch",
            {
                "url": "https://x",
                "headers": {"Authorization": "Bearer abcdefghijklmnopqrstuv0123"},
            },
            "w1",
        )
        b.tool_result("w1", "ok")
        page = render(b.write(tmp_path), redactor()).html
        assert "abcdefghijklmnop" not in page

    def test_multiline_secrets_in_diffs_are_scrubbed_before_splitting(self, tmp_path):
        import dossier_git

        b = SessionBuilder()
        b.prompt("x")
        b.tool_use("Edit", {"file_path": "/x/a.py"}, "e1")
        b.tool_result(
            "e1",
            "ok",
            structured={
                "filePath": "/x/a.py",
                "structuredPatch": [
                    {
                        "oldStart": 1,
                        "oldLines": 1,
                        "newStart": 1,
                        "newLines": 4,
                        "lines": [
                            "+-----BEGIN RSA PRIVATE KEY-----",
                            "+PATCHKEYBODY",
                            "+-----END RSA PRIVATE KEY-----",
                        ],
                    }
                ],
            },
        )
        git = dossier_git.GitInfo(
            available=True,
            base_label="test",
            diff="diff --git a/r b/r\n+<system-reminder>\n+GIT-HIDDEN-BODY\n"
            "+</system-reminder>\n+-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "+GITKEYBODY\n+-----END OPENSSH PRIVATE KEY-----\n",
        )
        session = load(b.write(tmp_path))
        scope = dossier_scope.select_scope(session)
        page = dossier_render.render(
            session,
            scope,
            dossier_render.RenderOptions(),
            git=git,
            redactor=redactor(),
        ).html
        for leaked in ("PATCHKEYBODY", "GIT-HIDDEN-BODY", "GITKEYBODY"):
            assert leaked not in page, leaked

    def test_share_drops_every_non_human_inbound_and_raw_system_json(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.inbound("peer", "PEER-NOT-META")
        b.inbound("teammate", "TEAMMATE-MSG")
        b.command("<local-command-stdout>CMD-OUT</local-command-stdout>")
        b.record("system", subtype="local_command", content="SYSTEM-OUT")
        b.assistant([{"type": "tool_addition", "tools": ["TOOL-ADD"]}])
        child = b.child("agentT")
        child.legacy_prompt("SUBAGENT-TASK")
        b.agent_call("a1", "agentT", child)
        r = redactor()
        page = render(b.write(tmp_path), r).html
        for marker in (
            "PEER-NOT-META",
            "TEAMMATE-MSG",
            "CMD-OUT",
            "SYSTEM-OUT",
            "TOOL-ADD",
            "SUBAGENT-TASK",
        ):
            assert marker not in page, marker
        assert r.excluded["injected"] == 4
        assert r.excluded["meta"] == 2

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("%2FUsers%2Ftester%2Fx", "%2FUsers%2F[user]%2Fx"),
            ("line\\ntester rest", "line\\n[user] rest"),
            ("/Users/TESTER/x", "~/x"),
            ("/Users/alice/x and /home/bob/y", "/Users/[user]/x and /home/[user]/y"),
        ],
    )
    def test_home_and_user_name_evasions(self, text, expected):
        assert redactor().scrub(text) == expected

    @pytest.mark.parametrize(
        "tail",
        [
            "b3BlbnNzaC1rZXktdjEAAAA\nAACD4hC5Qm8\n-----END OPENSSH PRIVATE KEY-----",
            "b3BlbnNzaC1rZXktdjEAAAA\\nAACD4hC5Qm8\\n-----END RSA PRIVATE KEY-----",
        ],
    )
    def test_key_body_without_its_begin_line_goes_too(self, tail):
        out = redactor().scrub(f"key: {tail} done")
        assert "AACD4hC5Qm8" not in out
        assert "END" not in out
        assert out == "key: [REDACTED] done"

    def test_long_local_part_email_is_redacted_whole(self):
        out = redactor().scrub("x " + "a" * 80 + "@example.com y")
        assert out == "x [REDACTED] y"

    def test_file_anchor_hash_does_not_encode_the_home_path(self, tmp_path):
        import hashlib

        b = SessionBuilder()
        b.edit("e1", f"{HOME}/p/a.py", "a", "b")
        session = load(b.write(tmp_path))
        page = dossier_render.render(
            session,
            dossier_scope.select_scope(session),
            dossier_render.RenderOptions(home=HOME),
        ).html
        normalised = hashlib.sha1(b"~/p/a.py", usedforsecurity=False).hexdigest()[:10]
        raw = hashlib.sha1(
            f"{HOME}/p/a.py".encode(), usedforsecurity=False
        ).hexdigest()[:10]
        assert f'id="file-{normalised}"' in page
        assert raw not in page

    def test_symlinked_persisted_outputs_are_not_followed(self, tmp_path):
        b = SessionBuilder()
        b.prompt("x")
        main = b.write(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("OUTSIDE-CONTENT", encoding="utf-8")
        results = main.with_suffix("") / "tool-results"
        results.mkdir(parents=True)
        (results / "link.txt").symlink_to(outside)
        session = load(main)
        assert session.persisted == []
        assert session.ledger.skipped_outputs == ["link.txt"]
        assert "OUTSIDE-CONTENT" not in render(main).html


class TestShareGit:
    def test_share_git_shows_only_paths_the_session_edited(
        self, tmp_path, git_repo, monkeypatch
    ):
        from conftest import run_git

        (git_repo / "other.txt").write_text("base\n", encoding="utf-8")
        run_git(git_repo, "add", "other.txt")
        run_git(git_repo, "commit", "-q", "-m", "add other")
        (git_repo / "README.md").write_text("EDITED-BY-SESSION\n", encoding="utf-8")
        (git_repo / "other.txt").write_text("UNRELATED-WIP\n", encoding="utf-8")
        (git_repo / "client-secret-plan.md").write_text("x\n", encoding="utf-8")
        home = tmp_path / "claude"
        b = SessionBuilder(cwd=str(git_repo))
        b.prompt("edit readme")
        b.edit("e1", str(git_repo / "README.md"), "init", "EDITED-BY-SESSION")
        b.write(home)
        monkeypatch.setenv("HOME", HOME)
        out = tmp_path / "o"

        rc = render_dossier.main(
            [
                "--session",
                b.session_id,
                "--mode",
                "share",
                "--out",
                str(out),
                "--no-open",
            ],
            now=NOW,
            env={"CLAUDE_CONFIG_DIR": str(home)},
        )

        assert rc == 0
        page = next(out.glob("*.share.html")).read_text("utf-8")
        assert "EDITED-BY-SESSION" in page
        assert "UNRELATED-WIP" not in page
        assert "client-secret-plan" not in page

    def test_identity_names_cover_config_and_committers(self, git_repo):
        import dossier_git

        names = dossier_git.identity_names(git_repo)
        assert "Test User" in names


# --------------------------------------------------------------------------
# Security re-verification follow-ups
# --------------------------------------------------------------------------
class TestReverifyFollowUps:
    def test_edited_paths_are_literal_not_globs(self, tmp_path, git_repo):
        import dossier_git
        from conftest import run_git

        for name in ("a.md", "[ab].md"):
            (git_repo / name).write_text("base\n", encoding="utf-8")
        run_git(git_repo, "add", ".")
        run_git(git_repo, "commit", "-q", "-m", "add files")
        (git_repo / "a.md").write_text("A-MD-CHANGED\n", encoding="utf-8")
        (git_repo / "[ab].md").write_text("GLOB-FILE-CHANGED\n", encoding="utf-8")

        info = dossier_git.collect_git(
            git_repo, since_ts=None, base=None, paths=[str(git_repo / "[ab].md")]
        )

        assert "GLOB-FILE-CHANGED" in info.diff
        assert "A-MD-CHANGED" not in info.diff

    def test_share_commit_list_only_touches_edited_paths(self, tmp_path, git_repo):
        import dossier_git
        from conftest import run_git

        (git_repo / "edited.md").write_text("x\n", encoding="utf-8")
        run_git(git_repo, "add", "edited.md")
        run_git(git_repo, "commit", "-q", "-m", "docs: edited file")
        (git_repo / "other.md").write_text("y\n", encoding="utf-8")
        run_git(git_repo, "add", "other.md")
        run_git(git_repo, "commit", "-q", "-m", "fix: customer ACME-CORP outage")

        info = dossier_git.collect_git(
            git_repo, since_ts=None, base=None, paths=[str(git_repo / "edited.md")]
        )

        subjects = [c.subject for c in info.commits]
        assert "docs: edited file" in subjects
        assert not any("ACME" in s for s in subjects)

    def test_every_co_author_is_collected(self, git_repo):
        import os
        import subprocess

        import dossier_git

        (git_repo / "c.md").write_text("c\n", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }
        subprocess.run(["git", "add", "c.md"], cwd=git_repo, env=env, check=True)
        subprocess.run(
            [
                "git",
                "commit",
                "-q",
                "-m",
                "x\n\nCo-authored-by: P Q <p@q.example>\nCo-authored-by: R S <r@s.example>",
            ],
            cwd=git_repo,
            env=env,
            check=True,
            capture_output=True,
        )
        info = dossier_git.collect_git(git_repo, since_ts=None, base=None)
        assert {"P Q", "R S"} <= info.names()

    def test_symlinked_tool_results_dir_and_agent_files_are_refused(self, tmp_path):
        b = SessionBuilder()
        b.prompt("x")
        main = b.write(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "s.txt").write_text("EXTERNAL-DIR-SECRET", encoding="utf-8")
        session_dir = main.with_suffix("")
        session_dir.mkdir(exist_ok=True)
        (session_dir / "tool-results").symlink_to(outside, target_is_directory=True)
        (session_dir / "subagents").mkdir()
        foreign = tmp_path / "foreign.jsonl"
        child = b.child("agentX")
        child.legacy_prompt("FOREIGN-AGENT-TEXT")
        foreign.write_text(child.text(), encoding="utf-8")
        (session_dir / "subagents" / "agent-agentX.jsonl").symlink_to(foreign)

        session = load(main)
        page = render(main).html

        assert "EXTERNAL-DIR-SECRET" not in page
        assert "FOREIGN-AGENT-TEXT" not in page
        assert session.subagents == {}
        assert {"tool-results", "agent-agentX.jsonl"} <= set(
            session.ledger.skipped_outputs
        )

    def test_author_names_match_case_insensitively(self):
        r = dossier_redact.Redactor(home=HOME, authors=["Zed Zimmerman"], scanners=[])
        assert r.scrub("by zed zimmerman") == "by [author]"

    def test_an_unclosed_begin_line_does_not_swallow_the_rest(self):
        text = "a\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n\nprint('after')"
        out = redactor().scrub(text)
        assert "MIIEvQ" not in out
        assert "print('after')" in out

    def test_numeric_token_counts_are_not_secrets(self):
        out = redactor().scrub('{"max_tokens": 4096, "input_tokens": 12345}')
        assert out == '{"max_tokens": 4096, "input_tokens": 12345}'


def test_deeply_nested_json_line_is_a_parse_error_not_a_crash(tmp_path):
    import dossier_parse

    path = tmp_path / "s.jsonl"
    path.write_text("[" * 200_000 + "\n" + '{"type": "user"}\n', encoding="utf-8")
    res = dossier_parse.read_jsonl(path, "main")
    assert len(res.errors) == 1
    assert len(res.records) == 1


def test_anchor_paths_are_normalised_case_insensitively_and_for_other_users():
    assert dossier_render._anchor_path("/users/TESTER/p/a.py", HOME) == "~/p/a.py"
    assert (
        dossier_render._anchor_path("/Users/alice/p/a.py", HOME)
        == "/Users/[user]/p/a.py"
    )
    assert dossier_render._anchor_path("/srv/app/a.py", HOME) == "/srv/app/a.py"


# --------------------------------------------------------------------------
# Pre-commit security re-check follow-ups
# --------------------------------------------------------------------------
class TestPreCommitFollowUps:
    @pytest.mark.parametrize(
        "text",
        [
            "export PGPASSWORD=123456",
            "MYSQL_PWD=87654321",
            "DB_PWD: 20250101",
            "password: 1234",
        ],
    )
    def test_numeric_secrets_are_still_secrets(self, text):
        assert not any(
            ch.isdigit() for ch in redactor().scrub(text).split("[REDACTED]")[-1][:1]
        )
        assert "[REDACTED]" in redactor().scrub(text)

    def test_only_token_counts_keep_their_numbers(self):
        assert redactor().scrub('"max_tokens": 4096') == '"max_tokens": 4096'

    def test_private_use_characters_cannot_split_a_token_past_the_scanner(
        self, tmp_path
    ):
        token = fake_github_token()
        b = SessionBuilder()
        b.say("key " + token[:4] + "" + token[4:] + " end")
        page = render(b.write(tmp_path), redactor()).html
        assert token not in page.replace("", "")
        assert "[REDACTED]" in page

    def test_deep_json_input_does_not_crash_share_rendering(self, tmp_path):
        b = SessionBuilder()
        b.tool_use("X", {"k": "v"}, "t1")
        line = b.lines[-1]
        deep = '{"a": ' * 800 + "1" + "}" * 800
        b.lines[-1] = line.replace('"input": {"k": "v"}', '"input": ' + deep)
        b.tool_result("t1", "ok")
        page = render(b.write(tmp_path), redactor()).html
        assert 'data-kind="tool"' in page

    def test_repeated_begin_lines_scrub_in_linear_time(self):
        start = time.perf_counter()
        redactor().scrub("-----BEGIN PRIVATE KEY-----\n" * 20_000)
        assert time.perf_counter() - start < 2.0

    def test_edited_path_list_ignores_the_root_directories_and_failures(
        self, tmp_path, git_repo
    ):
        import dossier_git

        (git_repo / "README.md").write_text("UNRELATED-CHANGE\n", encoding="utf-8")
        info = dossier_git.collect_git(
            git_repo,
            since_ts=None,
            base=None,
            paths=[str(git_repo), str(git_repo / ".git"), str(git_repo / "nope")],
        )
        assert "UNRELATED-CHANGE" not in info.diff

        b = SessionBuilder(cwd=str(git_repo))
        b.edit("e1", str(git_repo / "a.md"), "x", "y")
        b.tool_use("Edit", {"file_path": str(git_repo / "b.md")}, "e2")
        b.tool_result("e2", "failed", is_error=True)
        session = load(b.write(tmp_path))
        scope = dossier_scope.select_scope(session)
        assert render_dossier._edited_paths(session, scope) == [str(git_repo / "a.md")]
