"""Tests for the .codex/agents generator.

The headline test is test_committed_agents_are_current: it is the drift guard
that the old test_agent_parity could not be. Comparing only the name set and
the one-line description let commit 7009f67 neutralize the .md copies while
leaving a private project's vocabulary in the .toml twins. Regenerating and
comparing bytes makes that class of drift impossible rather than merely
visible.

The rest cover the failure modes that make the generator safe to trust: the
substitutions actually firing, the residue guard, and the TOML string choices
(a literal string, because three bodies carry backslashes that a basic string
cannot hold).
"""

import pytest
import tomllib
from conftest import REPO_ROOT
from gen_codex_agents import (
    HAND_MAINTAINED,
    GenerationError,
    adapt_body,
    build,
    frontmatter_value,
    main,
    render_toml,
    source_agents,
    split_frontmatter,
)

CODEX_AGENTS_DIR = REPO_ROOT / ".codex/agents"


# --- The drift guard -------------------------------------------------------


def test_committed_agents_are_current():
    """Every generated .toml must match what the SSOT produces right now."""
    assert main(["--check"]) == 0, (
        "committed .codex/agents/*.toml drifted from .claude/agents/*.md -- "
        "run `python3 scripts/gen_codex_agents.py` and commit the result"
    )


def test_generation_is_deterministic():
    for path in source_agents():
        if path.stem in HAND_MAINTAINED:
            continue
        assert build(path) == build(path)


def test_every_source_agent_has_a_codex_twin():
    for path in source_agents():
        assert (CODEX_AGENTS_DIR / f"{path.stem}.toml").exists(), (
            f"{path.stem}: no Codex twin"
        )


def test_no_orphan_codex_agents():
    stems = {p.stem for p in source_agents()}
    orphans = [p.name for p in CODEX_AGENTS_DIR.glob("*.toml") if p.stem not in stems]
    assert orphans == [], f"Codex agents with no .md source: {orphans}"


def test_generated_bodies_round_trip_byte_exact():
    for path in source_agents():
        if path.stem in HAND_MAINTAINED:
            continue
        _, body = split_frontmatter(path.read_text(encoding="utf-8"), path.stem)
        expected = adapt_body(body, path.stem)
        target = CODEX_AGENTS_DIR / f"{path.stem}.toml"
        parsed = tomllib.loads(target.read_text(encoding="utf-8"))
        assert parsed["developer_instructions"] == expected


def test_hand_maintained_agents_are_not_generated(tmp_path, monkeypatch):
    """The allowlisted exception must survive a generate run untouched."""
    import gen_codex_agents

    target = CODEX_AGENTS_DIR / "codex-delegator.toml"
    before = target.read_text(encoding="utf-8")
    monkeypatch.setattr(gen_codex_agents, "CODEX_AGENTS_DIR", CODEX_AGENTS_DIR)
    assert gen_codex_agents.run(check=True) == 0
    assert target.read_text(encoding="utf-8") == before


def test_hand_maintained_list_stays_justified():
    """A second exception means it is time to build a real block mechanism."""
    assert HAND_MAINTAINED == frozenset({"codex-delegator"}), (
        "HAND_MAINTAINED grew: one-off allowlisting no longer scales -- "
        "introduce an explicit per-block mechanism instead"
    )


# --- Substitutions and the residue guard -----------------------------------


def test_substitutions_are_applied():
    body = adapt_body(
        "read ~/.claude/skills/tdd-workflow/SKILL.md and CLAUDE.md\n", "t"
    )
    assert body == "read ~/.codex/skills/tdd-workflow/SKILL.md and AGENTS.md\n"


def test_residue_guard_rejects_leftover_claude_path():
    with pytest.raises(GenerationError, match="survives adaptation"):
        adapt_body("see ~/.claude/hooks/lint.sh\n", "t")


def test_no_tool_names_are_substituted():
    """Tool names must never be rewritten -- prose would be corrupted.

    `Read Performance` is real text in architect.md; a `Read` -> `read` rule
    would silently rewrite it. Codex's format carries no tool list at all, so
    there is nothing to translate in the first place.
    """
    body = adapt_body("- **Denormalized for Read Performance**: use Bash\n", "t")
    assert body == "- **Denormalized for Read Performance**: use Bash\n"


def test_generated_toml_carries_no_tools_key():
    for path in source_agents():
        parsed = tomllib.loads(
            (CODEX_AGENTS_DIR / f"{path.stem}.toml").read_text(encoding="utf-8")
        )
        assert set(parsed) == {"name", "description", "developer_instructions"}


# --- TOML rendering hazards ------------------------------------------------


def test_bodies_with_backslashes_survive():
    """A basic (\"\"\") string would reject \\*; a literal string keeps it.

    The old hand-written refactor-cleaner.toml used \"\"\" and had lost the
    backslash from `api/products/\\*` -- silent corruption this test forbids.
    """
    rendered = render_toml("t", "d", "- see api/products/\\*, api/x/[slug]/\n")
    assert tomllib.loads(rendered)["developer_instructions"] == (
        "- see api/products/\\*, api/x/[slug]/\n"
    )


def test_body_that_breaks_a_literal_string_is_rejected():
    with pytest.raises(GenerationError, match="literal string"):
        render_toml("t", "d", "a ''' b\n")


def test_description_needing_escapes_is_rejected():
    with pytest.raises(GenerationError, match="quote or backslash"):
        render_toml("t", 'says "hi"', "body\n")


# --- Frontmatter parsing ---------------------------------------------------


def test_split_frontmatter_rejects_missing_fence():
    with pytest.raises(GenerationError, match="missing frontmatter"):
        split_frontmatter("# no frontmatter\n", "t")


def test_split_frontmatter_rejects_unterminated_fence():
    with pytest.raises(GenerationError, match="unterminated"):
        split_frontmatter("---\nname: t\n", "t")


def test_frontmatter_value_folds_block_scalar():
    fm = ["name: t", "description: >-", "  first line", "  second line", "model: opus"]
    assert frontmatter_value(fm, "description", "t") == "first line second line"


def test_frontmatter_value_strips_quotes():
    assert frontmatter_value(['description: "quoted"'], "description", "t") == "quoted"


def test_frontmatter_value_requires_the_key():
    with pytest.raises(GenerationError, match="no `description`"):
        frontmatter_value(["name: t"], "description", "t")


# --- CLI behaviour ---------------------------------------------------------


def test_check_reports_stale_output(tmp_path, monkeypatch, capsys):
    import gen_codex_agents

    src = tmp_path / "claude"
    out = tmp_path / "codex"
    src.mkdir()
    out.mkdir()
    (src / "demo.md").write_text(
        "---\nname: demo\ndescription: d\n---\n\n# Demo\n", encoding="utf-8"
    )
    (out / "demo.toml").write_text("name = 'demo'\n", encoding="utf-8")
    monkeypatch.setattr(gen_codex_agents, "CLAUDE_AGENTS_DIR", src)
    monkeypatch.setattr(gen_codex_agents, "CODEX_AGENTS_DIR", out)
    monkeypatch.setattr(gen_codex_agents, "HAND_MAINTAINED", frozenset())

    assert gen_codex_agents.main(["--check"]) == 1
    assert "is stale" in capsys.readouterr().err


def test_check_reports_orphans(tmp_path, monkeypatch, capsys):
    import gen_codex_agents

    src = tmp_path / "claude"
    out = tmp_path / "codex"
    src.mkdir()
    out.mkdir()
    (out / "ghost.toml").write_text("name = 'ghost'\n", encoding="utf-8")
    monkeypatch.setattr(gen_codex_agents, "CLAUDE_AGENTS_DIR", src)
    monkeypatch.setattr(gen_codex_agents, "CODEX_AGENTS_DIR", out)

    assert gen_codex_agents.main(["--check"]) == 1
    assert "has no ghost.md source" in capsys.readouterr().err


def test_generate_writes_and_removes_orphans(tmp_path, monkeypatch):
    import gen_codex_agents

    src = tmp_path / "claude"
    out = tmp_path / "codex"
    src.mkdir()
    out.mkdir()
    (src / "demo.md").write_text(
        "---\nname: demo\ndescription: d\n---\n\n# Demo\n", encoding="utf-8"
    )
    (out / "ghost.toml").write_text("name = 'ghost'\n", encoding="utf-8")
    monkeypatch.setattr(gen_codex_agents, "CLAUDE_AGENTS_DIR", src)
    monkeypatch.setattr(gen_codex_agents, "CODEX_AGENTS_DIR", out)
    monkeypatch.setattr(gen_codex_agents, "HAND_MAINTAINED", frozenset())

    assert gen_codex_agents.main([]) == 0
    assert not (out / "ghost.toml").exists()
    assert tomllib.loads((out / "demo.toml").read_text())["name"] == "demo"
    assert gen_codex_agents.main(["--check"]) == 0


def test_broken_source_fails_generation(tmp_path, monkeypatch, capsys):
    import gen_codex_agents

    src = tmp_path / "claude"
    out = tmp_path / "codex"
    src.mkdir()
    out.mkdir()
    (src / "bad.md").write_text("# no frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(gen_codex_agents, "CLAUDE_AGENTS_DIR", src)
    monkeypatch.setattr(gen_codex_agents, "CODEX_AGENTS_DIR", out)
    monkeypatch.setattr(gen_codex_agents, "HAND_MAINTAINED", frozenset())

    assert gen_codex_agents.main([]) == 1
    assert "missing frontmatter" in capsys.readouterr().err
    assert not (out / "bad.toml").exists()
