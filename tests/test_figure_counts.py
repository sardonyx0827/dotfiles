"""Figure counts: the agent / command / skill / test counts baked into the SVG
architecture diagrams must match the actual files in the repo.

The prose in README.md deliberately avoids hardcoding these counts, but the SVG
figures embed them as literal text (`N agents · N commands`, `N test suites`,
`agents (N ⇆ N)`, ...). Unlike the hook duplicates (guarded byte-for-byte by
test_hook_sync) or the config paths (checked by test_config_wiring), nothing
stopped those figures from drifting as agents / commands / tests were added or
removed -- and they did. This test closes that gap: every count rendered in a
figure is re-derived from the filesystem and compared, so a stale diagram fails
CI instead of shipping.
"""

import re

from conftest import REPO_ROOT

ARCHITECTURE_SVG = REPO_ROOT / "assets/architecture.svg"
ORCHESTRATION_SVG = REPO_ROOT / "assets/llm-orchestration.svg"


def _claude_agents() -> int:
    # agents/README.md documents the set; it is not itself an agent.
    return len(
        [p for p in (REPO_ROOT / ".claude/agents").glob("*.md") if p.stem != "README"]
    )


def _commands() -> int:
    return len(list((REPO_ROOT / ".claude/commands").glob("*.md")))


def _skills() -> int:
    # Every skill on disk, templates included. The figures used to exclude the
    # `*-example` template and render 23 while docs/claude-architecture.md said
    # 24, so the repo carried two authoritative answers to "how many skills".
    # Neither was wrong -- they counted different things -- but a reader has no
    # way to tell which definition a given "N skills" is using. Settled on the
    # raw count, because the architecture doc's Codex-sharing breakdown ("24 中
    # 18 を共有") is already stated in those terms and the template is a real
    # directory that Codex-side sharing decisions apply to like any other.
    return len(list((REPO_ROOT / ".claude/skills").glob("*/SKILL.md")))


def _test_suites() -> int:
    return len(list((REPO_ROOT / "tests").glob("test_*.py")))


def _codex_agents() -> int:
    return len(list((REPO_ROOT / ".codex/agents").glob("*.toml")))


def _assert_all(text: str, pattern: str, expected: int, label: str, svg: str) -> None:
    """Every `<n> <label>` the figure renders must equal `expected`.

    Extracting *all* matches (not just asserting one right value exists) catches
    drift in either direction, and the non-empty guard fails loudly if the SVG
    wording changes so the pattern silently matches nothing (a vacuous pass).
    """
    found = [int(m.group(1)) for m in re.finditer(pattern, text)]
    assert found, f"{svg}: no '{label}' count found -- did the figure wording drift?"
    for n in found:
        assert n == expected, f"{svg}: figure says {n} {label}, repo has {expected}"


def test_derived_counts_are_nonzero():
    # Guard the globs themselves: a renamed directory must fail here, not make
    # every count comparison vacuously compare against 0.
    counts = {
        "agents": _claude_agents(),
        "commands": _commands(),
        "skills": _skills(),
        "test suites": _test_suites(),
        "codex agents": _codex_agents(),
    }
    for label, value in counts.items():
        assert value > 0, f"derived count for {label} is 0 -- glob drifted?"


def test_architecture_svg_counts_match_repo():
    text = ARCHITECTURE_SVG.read_text(encoding="utf-8")
    # `(?!\s*\(\.toml\))` keeps the Claude-agent count separate from the Codex
    # `N agents (.toml)` count, which is asserted on its own below.
    _assert_all(
        text,
        r"(\d+)\s+agents(?!\s*\(\.toml\))",
        _claude_agents(),
        "agents",
        "architecture.svg",
    )
    _assert_all(text, r"(\d+)\s+commands", _commands(), "commands", "architecture.svg")
    _assert_all(text, r"(\d+)\s+skills", _skills(), "skills", "architecture.svg")
    _assert_all(
        text, r"(\d+)\s+test suites", _test_suites(), "test suites", "architecture.svg"
    )
    _assert_all(
        text,
        r"(\d+)\s+agents\s*\(\.toml\)",
        _codex_agents(),
        "codex agents",
        "architecture.svg",
    )


def test_orchestration_svg_counts_match_repo():
    text = ORCHESTRATION_SVG.read_text(encoding="utf-8")
    _assert_all(
        text,
        r"(\d+)\s+agents(?!\s*\(\.toml\))",
        _claude_agents(),
        "agents",
        "llm-orchestration.svg",
    )
    _assert_all(
        text, r"(\d+)\s+commands", _commands(), "commands", "llm-orchestration.svg"
    )
    _assert_all(text, r"(\d+)\s+skills", _skills(), "skills", "llm-orchestration.svg")
    # The `agents (claude ⇆ codex)` parity pair. `\D+` matches the arrow glyph
    # without pinning the test to its exact codepoint.
    pair = re.search(r"agents\s*\((\d+)\D+(\d+)\)", text)
    assert pair, "llm-orchestration.svg: missing 'agents (N ⇆ M)' parity pair"
    assert int(pair.group(1)) == _claude_agents(), (
        f"llm-orchestration.svg: parity pair Claude side {pair.group(1)} != {_claude_agents()}"
    )
    assert int(pair.group(2)) == _codex_agents(), (
        f"llm-orchestration.svg: parity pair Codex side {pair.group(2)} != {_codex_agents()}"
    )


# The prose architecture doc is the side this repo settled on as authoritative when the
# skill count disagreed (it said 24; the SVGs said 23 because the figure helper excluded
# the *-example template). The exclusion is gone and the SVGs now render 24 -- but the
# doc itself was the one artifact with nothing checking it, so the number everyone else
# was aligned TO could drift freely. Tie it to the filesystem too.
ARCHITECTURE_DOC = REPO_ROOT / "docs/claude-architecture.md"


def _codex_skill_links() -> int:
    """Skills shared with Codex = the symlinks into .claude/skills, and only those.

    Counts symlinks rather than directory entries: `.codex/skills/.system/` is Codex
    CLI's own managed directory (gitignored, not ours), and pathlib's glob("*") -- unlike
    the shell's -- returns dotted names, so a plain entry count is one too many.
    """
    return len([p for p in (REPO_ROOT / ".codex/skills").glob("*") if p.is_symlink()])


def test_architecture_doc_counts_match_repo():
    text = ARCHITECTURE_DOC.read_text(encoding="utf-8")
    # Every way the doc spells a skill total: the inventory sentence, the mermaid node,
    # and the sharing-boundary node. All must equal the same filesystem count.
    for pattern, expected, label in (
        (r"スキル\s*(\d+)", _skills(), "skills (inventory sentence)"),
        (r"(\d+)\s*スキル", _skills(), "skills (mermaid node)"),
        (r"エージェント\s*(\d+)", _claude_agents(), "agents"),
        (r"コマンド\s*(\d+)", _commands(), "commands"),
    ):
        _assert_all(text, pattern, expected, label, "claude-architecture.md")

    # `skills/ (N)` appears twice with DIFFERENT meanings: the Claude side is every
    # skill, the Codex side is how many are symlinked across. Match them in order.
    boxes = [int(m) for m in re.findall(r"skills/\s*\((\d+)\)", text)]
    assert boxes == [_skills(), _codex_skill_links()], (
        f"claude-architecture.md: skills/ boxes {boxes} != "
        f"[{_skills()}, {_codex_skill_links()}]"
    )

    # "24 中 18 を共有" -- the same two numbers restated in prose.
    shared = re.search(r"(\d+)\s*中\s*(\d+)\s*を共有", text)
    assert shared, "claude-architecture.md: the sharing-ratio sentence is gone"
    assert (int(shared.group(1)), int(shared.group(2))) == (
        _skills(),
        _codex_skill_links(),
    ), (
        f"claude-architecture.md: sharing ratio {shared.groups()} != "
        f"({_skills()}, {_codex_skill_links()})"
    )
