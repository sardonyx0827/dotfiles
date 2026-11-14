"""session-dossier: a lone surrogate in a transcript must not stop the render.

A transcript string cut in the middle of an emoji (a UTF-16 slice of a pair)
is written by Claude Code as a lone ``\\ud83d`` escape. ``json.loads`` accepts
it and yields a str that cannot be encoded as UTF-8, so building the page
raised UnicodeEncodeError and no dossier was written, against the reader's
"never gives up" contract. The reader now replaces each lone surrogate with
U+FFFD, in string values and in dict keys at any depth, and keeps every
properly paired character.

Fixture note: SessionBuilder dumps with ensure_ascii=False, which cannot hold a
lone surrogate (the write itself fails), so these records are dumped with the
default ensure_ascii=True, which spells the surrogate as an escape.
"""

import json
import sys
from datetime import datetime, timezone

import pytest
from conftest import REPO_ROOT
from dossier_fixtures import SessionBuilder

SKILL_DIR = REPO_ROOT / ".claude/skills/session-dossier"
sys.path.insert(0, str(SKILL_DIR))

import dossier_link  # noqa: E402
import dossier_locate  # noqa: E402
import dossier_parse  # noqa: E402
import dossier_redact  # noqa: E402
import dossier_render  # noqa: E402
import dossier_scope  # noqa: E402
import render_dossier  # noqa: E402

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
HOME = "/Users/tester"
FFFD = "\ufffd"
EMOJI = "\U0001f600"
# json.dumps spells these (ensure_ascii=True) as "\ud83d" and "\ude00"; written
# on their own they are the two halves of a pair, separated.
HIGH = "\ud83d"
LOW = "\ude00"


def add_raw(builder, rec):
    """Add a record whose lone surrogates survive as escapes in the file."""
    builder.lines.append(json.dumps(rec))
    builder.truth["records"] += 1


def walk_strings(node):
    """Every str in the structure, dict keys included."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            yield cur
        elif isinstance(cur, dict):
            for key, value in cur.items():
                yield key
                stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)


def read_one(tmp_path, record):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    result = dossier_parse.read_jsonl(path, "main")
    assert result.errors == []
    return result.records[0].data


class TestReader:
    def test_a_lone_surrogate_is_replaced_in_a_string_value(self, tmp_path):
        data = read_one(tmp_path, {"type": "user", "text": f"a{HIGH}b"})
        assert data["text"] == f"a{FFFD}b"

    def test_a_low_surrogate_and_a_reversed_pair_are_lone_too(self, tmp_path):
        data = read_one(tmp_path, {"type": "user", "text": f"{LOW}{HIGH}"})
        assert data["text"] == FFFD * 2

    def test_every_surrogate_is_replaced_nested_in_lists_and_dicts(self, tmp_path):
        record = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": f"x{HIGH}"},
                    {"type": "tool_use", "input": {"a": [f"{LOW}y", [f"z{HIGH}"]]}},
                ]
            },
            "list": [[[f"{HIGH}"]]],
        }
        data = read_one(tmp_path, record)
        assert data["message"]["content"][0]["text"] == f"x{FFFD}"
        assert data["message"]["content"][1]["input"]["a"] == [f"{FFFD}y", [f"z{FFFD}"]]
        assert data["list"] == [[[FFFD]]]
        for text in walk_strings(data):
            text.encode("utf-8")

    def test_dict_keys_are_replaced_at_any_depth(self, tmp_path):
        record = {
            "type": "user",
            f"k{HIGH}": 1,
            "outer": [{f"{LOW}": {f"deep{HIGH}": "v"}}],
        }
        data = read_one(tmp_path, record)
        assert f"k{FFFD}" in data
        assert data["outer"] == [{FFFD: {f"deep{FFFD}": "v"}}]
        for text in walk_strings(data):
            text.encode("utf-8")

    def test_key_order_survives_a_replaced_key(self, tmp_path):
        record = {"type": "user", "a": 1, f"b{HIGH}": 2, "c": 3}
        data = read_one(tmp_path, record)
        assert list(data) == ["type", "a", f"b{FFFD}", "c"]

    def test_two_keys_that_clean_to_the_same_name_do_not_crash(self, tmp_path):
        data = read_one(tmp_path, {"type": "user", HIGH: 1, LOW: 2})
        assert data[FFFD] in (1, 2)

    def test_a_valid_pair_and_a_raw_emoji_are_kept(self, tmp_path):
        path = tmp_path / "s.jsonl"
        # a raw emoji, the same emoji as an escaped pair, then one lone half
        path.write_text(
            '{"type": "user", "text": "\U0001f600 \\ud83d\\ude00 \\ud83d"}\n',
            encoding="utf-8",
        )
        data = dossier_parse.read_jsonl(path, "main").records[0].data
        assert data["text"] == f"{EMOJI} {EMOJI} {FFFD}"

    def test_a_valid_pair_in_a_key_is_kept(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text('{"type": "user", "\\ud83d\\ude00": 1}\n', encoding="utf-8")
        data = dossier_parse.read_jsonl(path, "main").records[0].data
        assert EMOJI in data

    def test_a_record_without_surrogates_is_returned_as_parsed(self, tmp_path):
        record = {"type": "user", "text": "plain \u00e9 text", "n": [1, 2.5, None]}
        assert read_one(tmp_path, record) == record

    def test_an_escaped_backslash_before_ud83d_is_not_a_surrogate(self, tmp_path):
        path = tmp_path / "s.jsonl"
        path.write_text('{"type": "user", "text": "\\\\ud83d"}\n', encoding="utf-8")
        data = dossier_parse.read_jsonl(path, "main").records[0].data
        assert data["text"] == "\\ud83d"

    def test_nesting_deeper_than_the_recursion_limit_still_reads(self, tmp_path):
        # json.loads nests deeper than the Python recursion limit allows, so a
        # recursive walker would turn a parsable line back into a crash.
        depth = sys.getrecursionlimit() + 200
        text = (
            '{"type": "user", "deep": ' + "[" * depth + '"x\\ud83d"' + "]" * depth + "}"
        )
        try:
            json.loads(text)
        except RecursionError:
            pytest.skip("this interpreter's json cannot nest that deep")
        path = tmp_path / "s.jsonl"
        path.write_text(text + "\n", encoding="utf-8")
        data = dossier_parse.read_jsonl(path, "main").records[0].data
        node = data["deep"]
        for _ in range(depth):
            node = node[0]
        assert node == f"x{FFFD}"

    def test_the_tool_block_count_is_unchanged(self, tmp_path):
        record = {
            "type": "assistant",
            f"k{HIGH}": [{"type": "tool_use", "input": {f"{LOW}": HIGH}}],
        }
        path = tmp_path / "s.jsonl"
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        assert dossier_parse.read_jsonl(path, "main").records[0].tool_blocks == 1


def surrogate_session():
    b = SessionBuilder(cwd=f"{HOME}/project")
    add_raw(
        b,
        b.base(
            "user",
            message={
                "role": "user",
                "content": f"cut mid-emoji {HIGH} but {EMOJI} stays {HIGH}{LOW} too",
            },
        ),
    )
    b.truth["prompts"] += 1
    add_raw(
        b,
        b.base(
            "assistant",
            message={
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"answer {LOW}"},
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {
                            "command": f"echo {HIGH}",
                            f"flag{HIGH}": [{f"k{LOW}": f"v{HIGH}"}],
                        },
                    },
                ],
            },
        ),
    )
    add_raw(
        b,
        b.base(
            "user",
            message={
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": f"o{HIGH}"}
                ],
            },
            toolUseResult={"stdout": f"o{HIGH}", "stderr": ""},
        ),
    )
    return b


def render(tmp_path, redactor=None):
    main = surrogate_session().write(tmp_path)
    session = dossier_link.load_session(dossier_locate.paths_for(main))
    scope = dossier_scope.select_scope(session)
    options = dossier_render.RenderOptions(generated_at="2026-01-02T03:04:05Z")
    return dossier_render.render(session, scope, options, redactor=redactor)


class TestRender:
    def test_local_page_encodes_and_keeps_the_emoji(self, tmp_path):
        page = render(tmp_path).html
        page.encode("utf-8")
        assert EMOJI in page
        assert FFFD in page
        assert not any("\ud800" <= ch <= "\udfff" for ch in page)

    def test_share_page_encodes_and_keeps_the_emoji(self, tmp_path):
        redactor = dossier_redact.Redactor(home=HOME, authors=[])
        page = render(tmp_path, redactor).html
        page.encode("utf-8")
        assert EMOJI in page
        assert FFFD in page
        assert not any("\ud800" <= ch <= "\udfff" for ch in page)


def cli(home, *args):
    return render_dossier.main(
        [*args, "--repo", str(home), "--no-open"],
        now=NOW,
        env={"CLAUDE_CONFIG_DIR": str(home)},
        opener=lambda path: None,
    )


class TestCli:
    @pytest.mark.parametrize("mode", ["local", "share"])
    def test_the_dossier_is_written_to_disk(self, tmp_path, monkeypatch, mode):
        home = tmp_path / "home"
        builder = surrogate_session()
        builder.write(home)
        monkeypatch.setenv("HOME", HOME)
        out = tmp_path / "out"
        rc = cli(
            home,
            "--session",
            builder.session_id,
            "--mode",
            mode,
            "--out",
            str(out),
        )
        assert rc == 0
        page = next(out.glob("*.html")).read_text(encoding="utf-8")
        assert EMOJI in page
        assert FFFD in page
        manifest = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
        assert manifest["counts"]["parse_errors"] == 0
