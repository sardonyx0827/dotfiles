"""session-dossier: reading, normalising, linking, scoping and locating transcripts.

Covers T01-T12 of the implementation plan. The invariant these tests protect is
that nothing in a transcript is silently dropped: every tool call becomes an
item, every subagent transcript and persisted output is accounted for, and
whatever the parser does not understand is counted rather than discarded.
Expected values come from the fixture builder's own tally or from an
independent line scan, never from the parser under test.
"""

import json
import os
import sys
import time

import pytest
from conftest import REPO_ROOT
from dossier_fixtures import (
    SessionBuilder,
    all_jsonl,
    scan_tool_use_blocks,
    write_orphan_agent,
)

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_model  # noqa: E402
import dossier_parse  # noqa: E402
import dossier_scope  # noqa: E402


def load(main):
    return dossier_link.load_session(dossier_locate.paths_for(main))


def normalize_text(tmp_path, builder, *, main=True):
    path = tmp_path / "s.jsonl"
    path.write_text(builder.text(), encoding="utf-8")
    ledger = dossier_model.Ledger()
    parsed = dossier_parse.read_jsonl(path, "main")
    return dossier_model.normalize(parsed, ledger, main=main), ledger


# --------------------------------------------------------------------------
# P1: parse + model
# --------------------------------------------------------------------------
class TestParse:
    def test_t01_broken_and_truncated_lines_are_recorded_not_raised(self, tmp_path):
        b = SessionBuilder()
        for i in range(10):
            b.say(f"line {i}")
        b.raw_line("")
        b.raw_line("[1, 2]")
        b.raw_line("{not json")
        b.raw_line('{"type": "assistant", "uuid": "cut')
        path = tmp_path / "s.jsonl"
        path.write_text(b.text().rstrip("\n"), encoding="utf-8")

        res = dossier_parse.read_jsonl(path, "main")

        assert len(res.records) == 10
        assert [(e.line_no, e.truncated_tail) for e in res.errors] == [
            (12, False),
            (13, False),
            (14, True),
        ]
        assert all(e.source == "main" for e in res.errors)
        assert res.records[0].line_no == 1

    def test_undecodable_bytes_do_not_abort_the_read(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_bytes(b'{"type": "assistant", "text": "\xff"}\n')
        res = dossier_parse.read_jsonl(path, "main")
        assert len(res.records) == 1
        assert res.errors == []

    def test_missing_file_is_an_error_the_caller_sees(self, tmp_path):
        with pytest.raises(OSError):
            dossier_parse.read_jsonl(tmp_path / "nope.jsonl", "main")


class TestNormalize:
    def test_t02_unknown_types_are_kept_and_counted(self, tmp_path):
        b = SessionBuilder()
        b.record("future-x", foo=1)
        b.assistant([{"type": "future_block", "x": 1}, {"type": "text", "text": "hi"}])
        b.record("future-x", foo=2)

        transcript, ledger = normalize_text(tmp_path, b)

        assert ledger.unknown_records == {"future-x": 2}
        assert ledger.unknown_blocks == {"future_block": 1}
        kinds = [e.kind for e in transcript.events]
        assert kinds.count("unknown_record") == 2
        assert kinds.count("unknown_block") == 1
        assert "assistant_text" in kinds
        unknown = next(e for e in transcript.events if e.kind == "unknown_block")
        assert unknown.raw == {"type": "future_block", "x": 1}

    def test_t03_every_observed_type_is_known(self, tmp_path):
        record_types = [
            "assistant",
            "user",
            "attachment",
            "api-request",
            "api-request-blob",
            "api-request-shape",
            "mode",
            "atis-latch",
            "last-prompt",
            "ai-title",
            "system",
            "tools-used",
            "bridge-session",
            "file-history-snapshot",
            "file-history-delta",
            "queue-operation",
            "cost-state",
            # Seen first on a later Claude Code build (2026-11); the ledger
            # surfaced them as unknown before they were added here.
            "permission-mode",
            "session-owner",
            "frame-link",
            "artifact-comment-monitor",
            "artifact-autoreact-ledger",
        ]
        b = SessionBuilder()
        for rtype in record_types:
            if rtype not in ("assistant", "user"):
                b.record(rtype)
        b.prompt("go")
        b.assistant(
            [
                {"type": "text", "text": "t"},
                {"type": "thinking", "thinking": "th"},
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                {"type": "tool_addition", "tools": ["X"]},
                {"type": "server_tool_use", "id": "s1", "name": "advisor", "input": {}},
                {"type": "advisor_tool_result", "tool_use_id": "s1", "content": "ok"},
            ]
        )
        b.tool_result("t1", "done")

        _, ledger = normalize_text(tmp_path, b)

        assert ledger.unknown_records == {}
        assert ledger.unknown_blocks == {}
        assert set(ledger.record_types) == set(record_types)

    def test_t04_only_human_and_legacy_text_count_as_prompts(self, tmp_path):
        b = SessionBuilder()
        b.prompt("first")
        b.inbound("peer", "from a teammate")
        b.inbound("task-notification", "a task ended")
        b.meta("injected skill text")
        b.compact("summary of earlier turns")
        b.legacy_prompt("second")
        b.command("<command-name>/foo</command-name>")
        b.image_prompt("third")

        transcript, _ = normalize_text(tmp_path, b)

        prompts = [e for e in transcript.events if e.kind == "prompt"]
        assert [(p.text, p.prompt_index) for p in prompts] == [
            ("first", 1),
            ("second", 2),
            ("third", 3),
        ]
        assert prompts[2].image_count == 1
        others = [
            (e.kind, e.subtype)
            for e in transcript.events
            if e.kind in ("inbound", "injected")
        ]
        assert others == [
            ("inbound", "peer"),
            ("inbound", "task-notification"),
            ("injected", "meta"),
            ("injected", "compact"),
            ("inbound", "command"),
        ]

    def test_subagent_prompts_are_not_numbered(self, tmp_path):
        b = SessionBuilder()
        b.legacy_prompt("task for the subagent")
        transcript, _ = normalize_text(tmp_path, b, main=False)
        assert [e.kind for e in transcript.events] == ["inbound"]

    def test_versions_branches_and_titles_are_collected(self, tmp_path):
        b = SessionBuilder()
        b.prompt("a")
        b.version = "2.2.0"
        b.say("b")
        b.record("ai-title", aiTitle="Fix the thing")
        _, ledger = normalize_text(tmp_path, b)
        assert ledger.versions == {"2.1.0", "2.2.0"}
        assert ledger.branches == {"main"}
        assert ledger.title == "Fix the thing"

    def test_system_and_redacted_thinking_events(self, tmp_path):
        b = SessionBuilder()
        b.record("system", subtype="turn_duration", content="12s")
        b.record("system", subtype="local_command", content=None)
        b.assistant([{"type": "redacted_thinking", "data": "xx"}])
        b.assistant("plain string content")
        b.record("user", message="not a dict")
        transcript, ledger = normalize_text(tmp_path, b)
        systems = [(e.kind, e.subtype, e.text) for e in transcript.events][:2]
        assert systems == [
            ("system", "turn_duration", "12s"),
            ("system", "local_command", ""),
        ]
        thinking = [e for e in transcript.events if e.kind == "thinking"]
        assert thinking and thinking[0].text == "[redacted thinking]"
        assert any(
            e.kind == "assistant_text" and e.text == "plain string content"
            for e in transcript.events
        )
        assert ledger.unknown_blocks == {}


# --------------------------------------------------------------------------
# P2: linking + ledger
# --------------------------------------------------------------------------
class TestLink:
    def test_t05_every_tool_use_is_one_call_with_the_right_status(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.bash("t1", "ls", "a")
        b.bash("t2", "false", stderr="boom", is_error=True)
        b.bash("t3", "sleep 99", interrupted=True)
        b.tool_use("Read", {"file_path": "/x"}, "t4")
        b.edit("t5", "/x/a.py", "old", "new")
        b.write_file("t6", "/x/b.py", "print(1)\n")
        b.tool_use("Grep", {"pattern": "x"}, "t7")
        b.tool_result("t7", "found")
        b.tool_result("ghost", "a result with no call")
        main = b.write(tmp_path)

        session = load(main)
        calls = session.all_tool_calls()

        assert {c.tool_use_id: c.status for c in calls} == {
            "t1": "ok",
            "t2": "error",
            "t3": "interrupted",
            "t4": "pending",
            "t5": "ok",
            "t6": "ok",
            "t7": "ok",
        }
        assert len(calls) == scan_tool_use_blocks(all_jsonl(main))
        assert len(calls) == b.truth["tool_use"]
        assert session.ledger.orphan_results == ["ghost"]

    def test_t06_agents_and_subagent_transcripts_pair_one_to_one(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        child_a = b.child("agentA")
        child_a.legacy_prompt("task a")
        child_a.bash("ta1", "pwd", "/")
        b.agent_call("a1", "agentA", child_a)

        child_b = b.child("agentB")
        child_b.legacy_prompt("task b")
        child_c = child_b.child("agentC")
        child_c.legacy_prompt("task c")
        child_c.bash("tc1", "id", "uid")
        child_b.agent_call("a3", "agentC", child_c)
        b.agent_call("a2", "agentB", child_b)

        child_d = b.child("agentD")
        child_d.legacy_prompt("task d")
        b.agent_call("a4", "agentD", child_d, name="Task")
        b.agent_call("a5", "agentGone", None)
        main = b.write(tmp_path)
        orphan = b.child("agentOrphan")
        orphan.legacy_prompt("nobody called me")
        orphan.bash("to1", "true", "")
        write_orphan_agent(main, "agentOrphan", orphan)

        session = load(main)

        assert dict(session.ledger.agent_links) == {
            "a1": "agentA",
            "a2": "agentB",
            "a3": "agentC",
            "a4": "agentD",
            "a5": None,
        }
        assert session.ledger.orphan_agents == ["agentOrphan"]
        assert session.subagents["agentC"].parent_tool_use_id == "a3"
        assert session.subagents["agentOrphan"].parent_tool_use_id is None
        calls = session.all_tool_calls()
        assert len(calls) == scan_tool_use_blocks(all_jsonl(main))
        by_id = {c.tool_use_id: c for c in calls}
        assert by_id["a1"].subagent_id == "agentA"
        assert by_id["a5"].subagent_id is None

    def test_agent_linked_by_result_agent_id_when_meta_is_missing(self, tmp_path):
        b = SessionBuilder()
        child = b.child("agentM")
        child.legacy_prompt("x")
        b.agent_call("m1", "agentM", child)
        main = b.write(tmp_path)
        (main.with_suffix("") / "subagents" / "agent-agentM.meta.json").unlink()
        session = load(main)
        assert dict(session.ledger.agent_links) == {"m1": "agentM"}
        assert session.subagents["agentM"].meta == {}

    def test_unreadable_meta_is_treated_as_missing(self, tmp_path):
        b = SessionBuilder()
        child = b.child("agentN")
        child.legacy_prompt("x")
        b.agent_call("n1", "agentN", child)
        main = b.write(tmp_path)
        meta = main.with_suffix("") / "subagents" / "agent-agentN.meta.json"
        meta.write_text("{broken", encoding="utf-8")
        session = load(main)
        assert dict(session.ledger.agent_links) == {"n1": "agentN"}

    def test_t07_persisted_outputs_are_all_read_and_linked_or_orphaned(self, tmp_path):
        b = SessionBuilder()
        b.bash(
            "p1",
            "big",
            "(truncated)",
            extra={
                "persistedOutputPath": "/x/y/tool-results/b123.txt",
                "persistedOutputSize": 99,
            },
        )
        b.persisted("b123.txt", "BIG OUTPUT")
        b.tool_use("mcp__srv__tool", {}, "toolu_X")
        b.tool_result("toolu_X", "Output too large.")
        b.persisted("toolu_X.txt", "MCP OUT")
        b.tool_use("WebFetch", {"url": "https://example.com"}, "w1")
        b.tool_result("w1", [{"type": "text", "text": "saved to tool-results/zz9.txt"}])
        b.persisted("zz9.txt", "Z")
        b.persisted("lonely.txt", "L")
        main = b.write(tmp_path)

        session = load(main)

        assert {p.name: p.tool_use_id for p in session.persisted} == {
            "b123.txt": "p1",
            "lonely.txt": None,
            "toolu_X.txt": "toolu_X",
            "zz9.txt": "w1",
        }
        assert {p.name: p.text for p in session.persisted}["b123.txt"] == "BIG OUTPUT"
        by_id = {c.tool_use_id: c for c in session.all_tool_calls()}
        assert by_id["p1"].persisted == ["b123.txt"]

    def test_t08_tool_use_result_shapes_never_crash(self, tmp_path):
        b = SessionBuilder()
        b.tool_use("Bash", {"command": "x"}, "s1")
        b.tool_result("s1", "Error: exit 1", is_error=True, structured="Error: exit 1")
        b.edit("e1", "/a.py", "a", "b")
        b.write_file("w1", "/b.py", "x")
        b.tool_use("Read", {"file_path": "/img.png"}, "r1")
        b.tool_result(
            "r1",
            [{"type": "image", "source": {}}, {"type": "text", "text": "ok"}],
            structured={"type": "image", "file": {}},
        )
        main = b.write(tmp_path)

        by_id = {c.tool_use_id: c for c in load(main).all_tool_calls()}

        assert by_id["s1"].structured == "Error: exit 1"
        assert by_id["s1"].status == "error"
        assert by_id["e1"].structured["structuredPatch"][0]["lines"][1] == "-a"
        assert by_id["w1"].structured["type"] == "create"
        assert by_id["r1"].status == "ok"


# --------------------------------------------------------------------------
# P3: scope + locate
# --------------------------------------------------------------------------
def three_prompt_session():
    b = SessionBuilder()
    b.prompt("one")
    b.bash("t1", "a", "1")
    b.prompt("two")
    b.bash("t2", "b", "2")
    b.prompt("three")
    b.bash("t3", "c", "3")
    return b


class TestScope:
    def test_t09_since_prompt_starts_at_that_prompt(self, tmp_path):
        session = load(three_prompt_session().write(tmp_path))

        whole = dossier_scope.select_scope(session)
        later = dossier_scope.select_scope(session, since_prompt=2)

        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, whole)] == [
            "t1",
            "t2",
            "t3",
        ]
        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, later)] == [
            "t2",
            "t3",
        ]
        assert later.events[0].kind == "prompt"
        assert later.events[0].prompt_index == 2
        assert later.label == "プロンプト #2 以降"
        assert whole.label == "セッション全体"
        for bad in (0, 4):
            with pytest.raises(dossier_scope.ScopeError):
                dossier_scope.select_scope(session, since_prompt=bad)

    def test_t10_since_last_report_resumes_after_the_latest_marker(self, tmp_path):
        b = three_prompt_session()
        b.tool_use(
            "Bash",
            {"command": "python3 ~/.claude/skills/session-dossier/render_dossier.py"},
            "self",
        )
        main = b.write(tmp_path / "home")
        session = load(main)
        out = tmp_path / "out"
        out.mkdir()

        assert dossier_scope.find_last_report_uuid(session, out) is None

        t1_result = session.tool_call("t1").result_uuid
        (out / "20260101-000000-dossier-11111111.json").write_text(
            json.dumps({"session_id": b.session_id, "last_uuid": t1_result}),
            encoding="utf-8",
        )
        (out / "20260101-000001-dossier-99999999.json").write_text(
            json.dumps({"session_id": "other", "last_uuid": "zzz"}), encoding="utf-8"
        )
        (out / "20260101-000002-dossier-11111111.json").write_text(
            "{broken", encoding="utf-8"
        )
        marker = dossier_scope.find_last_report_uuid(session, out)
        scope = dossier_scope.select_scope(session, after_uuid=marker)

        assert marker == t1_result
        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, scope)] == [
            "t2",
            "t3",
            "self",
        ]
        assert scope.label == "前回のレポート以降"

    def test_session_report_run_wins_when_it_is_later(self, tmp_path):
        b = three_prompt_session()
        b.bash(
            "r1",
            "python3 ~/.claude/skills/session-report/render_report.py r.md",
            "r.html",
        )
        b.prompt("four")
        b.bash("t4", "d", "4")
        main = b.write(tmp_path / "home")
        session = load(main)
        out = tmp_path / "out"
        out.mkdir()
        (out / "20260101-000000-dossier-11111111.json").write_text(
            json.dumps(
                {
                    "session_id": b.session_id,
                    "last_uuid": session.tool_call("t1").result_uuid,
                }
            ),
            encoding="utf-8",
        )
        marker = dossier_scope.find_last_report_uuid(session, out)
        assert marker == session.tool_call("r1").result_uuid
        scope = dossier_scope.select_scope(session, after_uuid=marker)
        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, scope)] == [
            "t4"
        ]

    def test_unknown_marker_is_a_scope_error(self, tmp_path):
        session = load(three_prompt_session().write(tmp_path))
        with pytest.raises(dossier_scope.ScopeError):
            dossier_scope.select_scope(session, after_uuid="nope")

    def test_t11_subagent_launched_out_of_range_is_excluded(self, tmp_path):
        b = SessionBuilder()
        b.prompt("one")
        child = b.child("agentEarly")
        child.legacy_prompt("x")
        child.bash("c1", "ls", "")
        b.agent_call("a1", "agentEarly", child)
        b.prompt("two")
        child2 = b.child("agentLate")
        child2.legacy_prompt("y")
        child2.bash("c2", "ls", "")
        b.agent_call("a2", "agentLate", child2)
        session = load(b.write(tmp_path))

        scope = dossier_scope.select_scope(session, since_prompt=2)

        assert scope.subagent_ids == {"agentLate"}
        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, scope)] == [
            "a2",
            "c2",
        ]
        whole = dossier_scope.select_scope(session)
        assert whole.subagent_ids == {"agentEarly", "agentLate"}

    def test_orphan_subagents_are_in_the_whole_session_scope_only(self, tmp_path):
        b = three_prompt_session()
        main = b.write(tmp_path)
        orphan = b.child("agentOrphan")
        orphan.legacy_prompt("x")
        write_orphan_agent(main, "agentOrphan", orphan)
        session = load(main)
        assert "agentOrphan" in dossier_scope.select_scope(session).subagent_ids
        assert (
            "agentOrphan"
            not in dossier_scope.select_scope(session, since_prompt=2).subagent_ids
        )

    def test_empty_session_has_an_empty_scope(self, tmp_path):
        b = SessionBuilder()
        main = b.write(tmp_path)
        scope = dossier_scope.select_scope(load(main))
        assert scope.events == []
        assert scope.start_ts is None
        assert scope.last_uuid is None


class TestLocate:
    def test_t12_dotted_cwd_resolves_by_slug(self, tmp_path):
        b = SessionBuilder(cwd="/work/my.proj")
        main = b.write(tmp_path)
        paths, warnings = dossier_locate.locate_session(
            None, cwd="/work/my.proj", home=tmp_path, now=time.time()
        )
        assert paths.main == main
        assert warnings == []

    def test_t12_mismatched_slug_falls_back_to_scanning_cwd(self, tmp_path):
        b = SessionBuilder(cwd="/work/other")
        b.prompt("x")
        main = b.write(tmp_path)
        renamed = main.parent.parent / "weird-name"
        main.parent.rename(renamed)
        paths, _ = dossier_locate.locate_session(
            None, cwd="/work/other", home=tmp_path, now=time.time()
        )
        assert paths.main == renamed / main.name

    def test_t12_concurrent_sessions_pick_newest_and_warn(self, tmp_path):
        old = SessionBuilder(session_id="aaaaaaaa-0000-0000-0000-000000000000")
        new = SessionBuilder(session_id="bbbbbbbb-0000-0000-0000-000000000000")
        old_main = old.write(tmp_path)
        new_main = new.write(tmp_path)
        now = time.time()
        os.utime(old_main, (now - 60, now - 60))
        os.utime(new_main, (now - 5, now - 5))

        paths, warnings = dossier_locate.locate_session(
            None, cwd=old.cwd, home=tmp_path, now=now
        )

        assert paths.main == new_main
        assert len(warnings) == 1
        assert "aaaaaaaa" in warnings[0]

    def test_stale_other_sessions_do_not_warn(self, tmp_path):
        old = SessionBuilder(session_id="aaaaaaaa-0000-0000-0000-000000000000")
        new = SessionBuilder(session_id="bbbbbbbb-0000-0000-0000-000000000000")
        old_main = old.write(tmp_path)
        new.write(tmp_path)
        now = time.time()
        os.utime(old_main, (now - 3600, now - 3600))
        _, warnings = dossier_locate.locate_session(
            None, cwd=old.cwd, home=tmp_path, now=now
        )
        assert warnings == []

    def test_explicit_session_id_and_path(self, tmp_path):
        b = SessionBuilder()
        main = b.write(tmp_path)
        by_id, _ = dossier_locate.locate_session(
            b.session_id, cwd="/elsewhere", home=tmp_path, now=0
        )
        by_path, _ = dossier_locate.locate_session(
            str(main), cwd="/elsewhere", home=tmp_path, now=0
        )
        assert by_id.main == main
        assert by_path.main == main
        assert by_id.session_dir == main.with_suffix("")

    @pytest.mark.parametrize("bad", ["../etc/passwd", "no-such-session"])
    def test_bad_or_unknown_session_is_a_locate_error(self, tmp_path, bad):
        SessionBuilder().write(tmp_path)
        with pytest.raises(dossier_locate.LocateError):
            dossier_locate.locate_session(bad, cwd="/work/proj", home=tmp_path, now=0)

    def test_no_projects_dir_is_a_locate_error(self, tmp_path):
        with pytest.raises(dossier_locate.LocateError):
            dossier_locate.locate_session(None, cwd="/x", home=tmp_path, now=0)

    def test_project_without_sessions_is_a_locate_error(self, tmp_path):
        (tmp_path / "projects" / "-work-proj").mkdir(parents=True)
        with pytest.raises(dossier_locate.LocateError):
            dossier_locate.locate_session(None, cwd="/work/proj", home=tmp_path, now=0)

    def test_claude_home_honours_config_dir(self, tmp_path):
        assert dossier_locate.claude_home({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == (
            tmp_path
        )
        assert dossier_locate.claude_home({}).name == ".claude"


# --------------------------------------------------------------------------
# Review follow-ups: gaps in the "never drop anything" promise
# --------------------------------------------------------------------------
class TestReviewFollowUps:
    def test_raw_tool_block_count_is_independent_of_normalisation(self, tmp_path):
        b = SessionBuilder()
        b.prompt("go")
        b.bash("t1", "ls", "a")
        # A tool_use where the normaliser does not look for one...
        b.record(
            "user",
            message={"role": "user", "content": [{"type": "tool_use", "id": "u1"}]},
        )
        b.record("progress", data={"message": {"content": [{"type": "tool_use"}]}})
        # ...an MCP connector block the normaliser should treat as a call...
        b.assistant([{"type": "mcp_tool_use", "id": "mcp1", "name": "x", "input": {}}])
        # ...and the API request copy of the conversation, which must not count.
        b.record("api-request-blob", message={"content": [{"type": "tool_use"}]})
        session = load(b.write(tmp_path))

        whole = dossier_scope.select_scope(session)

        assert dossier_scope.raw_tool_blocks(session, whole) == 4
        assert [c.tool_use_id for c in dossier_scope.tool_calls(session, whole)] == [
            "t1",
            "mcp1",
        ]

    def test_duplicate_tool_ids_each_keep_their_own_result(self, tmp_path):
        b = SessionBuilder()
        b.bash("dup", "echo 1", "1")
        b.bash("dup", "echo 2", "2")
        session = load(b.write(tmp_path))
        calls = session.all_tool_calls()
        assert [(c.input["command"], c.status) for c in calls] == [
            ("echo 1", "ok"),
            ("echo 2", "ok"),
        ]
        assert [c.structured["stdout"] for c in calls] == ["1", "2"]
        assert session.ledger.duplicate_tool_ids == ["dup"]

    def test_malformed_messages_are_counted_not_dropped(self, tmp_path):
        b = SessionBuilder()
        b.record("assistant", message="not a dict")
        b.record("user", message={"role": "user", "content": 123})
        transcript, ledger = normalize_text(tmp_path, b)
        assert ledger.unknown_records == {
            "assistant（形が想定外）": 1,
            "user（形が想定外）": 1,
        }
        assert [e.kind for e in transcript.events] == ["unknown_record"] * 2

    def test_orphan_results_keep_their_body(self, tmp_path):
        b = SessionBuilder()
        b.tool_result("ghost", "orphan body")
        transcript, ledger = normalize_text(tmp_path, b)
        assert ledger.orphan_results == ["ghost"]
        orphan = [e for e in transcript.events if e.kind == "orphan_result"]
        assert [(o.subtype, o.text) for o in orphan] == [("ghost", "orphan body")]

    def test_images_beside_a_tool_result_are_kept(self, tmp_path):
        b = SessionBuilder()
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
        transcript, _ = normalize_text(tmp_path, b)
        images = [e for e in transcript.events if e.kind == "images"]
        assert [e.image_count for e in images] == [1]
        assert not [e for e in transcript.events if e.kind == "prompt"]

    def test_only_known_tags_make_a_command_and_interrupts_are_not_prompts(
        self, tmp_path
    ):
        b = SessionBuilder()
        b.legacy_prompt("<div>why does this render?</div>")
        b.command("<command-name>/foo</command-name>")
        b.command("<local-command-stdout>ok</local-command-stdout>")
        b.legacy_prompt("[Request interrupted by user]")
        transcript, _ = normalize_text(tmp_path, b)
        assert [(e.kind, e.subtype, e.prompt_index) for e in transcript.events] == [
            ("prompt", "", 1),
            ("inbound", "command", None),
            ("inbound", "command", None),
            ("inbound", "interrupt", None),
        ]


class TestMarkerAndLinkFollowUps:
    def test_grep_or_failed_runs_of_the_report_script_are_not_markers(self, tmp_path):
        b = three_prompt_session()
        b.bash("g1", "grep -n foo .claude/skills/session-report/render_report.py", "x")
        b.bash(
            "f1",
            "python3 ~/.claude/skills/session-report/render_report.py r.md",
            stderr="boom",
            is_error=True,
        )
        b.bash(
            "ok1",
            "cd /x && python3 ~/.claude/skills/session-report/render_report.py r.md",
            "r.html",
        )
        b.bash("g2", "ruff check .claude/skills/session-report/render_report.py", "")
        session = load(b.write(tmp_path / "home"))
        marker = dossier_scope.find_last_report_uuid(session, tmp_path / "none")
        assert marker == session.tool_call("ok1").result_uuid

    def test_codebase_survey_renders_are_not_session_report_markers(self, tmp_path):
        # codebase-survey reuses the same renderer, but a survey says nothing
        # about where the session's work was last reported.
        b = three_prompt_session()
        b.bash(
            "ok1",
            "python3 ~/.claude/skills/session-report/render_report.py r.md",
            "r.html",
        )
        b.bash(
            "s1",
            "python3 ~/.claude/skills/session-report/render_report.py"
            ' "/h/.claude/reports/p/20261105-120000-survey-p.md" --base-dir /x',
            "survey.html",
        )
        b.bash(
            "s2",
            "python3 ~/.claude/skills/session-report/render_report.py"
            " --no-open --base-dir /x /r/20261105-120001-survey-p-nvim.md",
            "survey.html",
        )
        session = load(b.write(tmp_path / "home"))
        marker = dossier_scope.find_last_report_uuid(session, tmp_path / "none")
        assert marker == session.tool_call("ok1").result_uuid

    @pytest.mark.parametrize(
        "md",
        [
            "/r/20261105-120000-add-survey-skill.md",
            "/r/20261105-120000-survey-foo_bar.md",
        ],
    )
    def test_names_outside_the_survey_rule_stay_markers(self, tmp_path, md):
        # Only <stamp>-survey-<[0-9A-Za-z-]+>.md is a survey: a session-report
        # slug that mentions "survey", or a name doc-browser would not list as
        # 調査, still marks the last report.
        b = three_prompt_session()
        b.bash(
            "ok1",
            f"python3 ~/.claude/skills/session-report/render_report.py {md}",
            "r.html",
        )
        session = load(b.write(tmp_path / "home"))
        marker = dossier_scope.find_last_report_uuid(session, tmp_path / "none")
        assert marker == session.tool_call("ok1").result_uuid

    def test_non_string_manifest_marker_is_ignored(self, tmp_path):
        b = three_prompt_session()
        session = load(b.write(tmp_path / "home"))
        out = tmp_path / "out"
        out.mkdir()
        (out / "20260101-000000-dossier-11111111.json").write_text(
            json.dumps({"session_id": b.session_id, "last_uuid": ["a"]}),
            encoding="utf-8",
        )
        assert dossier_scope.find_last_report_uuid(session, out) is None

    def test_mention_fallback_needs_a_unique_non_listing_match(self, tmp_path):
        b = SessionBuilder()
        b.bash("ls1", "ls tool-results", "toolu_real.txt\nother.txt\n")
        b.tool_use("WebFetch", {"url": "https://a"}, "w1")
        b.tool_result("w1", "saved to tool-results/twice.txt")
        b.tool_use("WebFetch", {"url": "https://b"}, "w2")
        b.tool_result("w2", "saved to tool-results/twice.txt")
        b.persisted("other.txt", "O")
        b.persisted("twice.txt", "T")
        session = load(b.write(tmp_path))
        assert {p.name: p.tool_use_id for p in session.persisted} == {
            "other.txt": None,
            "twice.txt": None,
        }
