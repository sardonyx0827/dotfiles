"""Drift guards for the multi-runtime agent / instruction configs.

The same agents are maintained twice — `.claude/agents/*.md` (YAML frontmatter
+ body) and `.codex/agents/*.toml` (`description` + `developer_instructions`) —
and the git-workflow / security rules are copied verbatim into three
instruction files. Nothing enforced that the copies stayed in step, so they had
already drifted. These tests fail loudly when the structured, easy-to-anchor
fields diverge (existence, description) — the same rationale as
test_hook_sync.py, extended beyond the shared Python module.

Note: agent BODIES intentionally differ (tool-name adaptation: Claude's Task
tool vs Codex agents), so body text is deliberately NOT asserted here — only
the fields that must stay identical.
"""

import tomllib
from conftest import REPO_ROOT

CLAUDE_AGENTS_DIR = REPO_ROOT / ".claude/agents"
CODEX_AGENTS_DIR = REPO_ROOT / ".codex/agents"


def _claude_agent_stems() -> set[str]:
    # README.md documents the directory; it is not an agent definition.
    return {p.stem for p in CLAUDE_AGENTS_DIR.glob("*.md") if p.stem != "README"}


def _codex_agent_stems() -> set[str]:
    return {p.stem for p in CODEX_AGENTS_DIR.glob("*.toml")}


def _md_frontmatter_description(path) -> str | None:
    """Extract the `description:` value from a Markdown agent's frontmatter.

    Handles the three shapes actually used: a plain scalar, a quoted scalar, and
    a YAML folded block scalar (`>-`) whose continuation lines are joined with
    single spaces (matching how the .toml copy stores the same text on one line).
    No PyYAML dependency — the fields here are simple enough to fold by hand.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path.name}: missing frontmatter"
    # Isolate the frontmatter block (between the first two `---` fences).
    end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    fm = lines[1:end]
    for idx, line in enumerate(fm):
        if not line.startswith("description:"):
            continue
        val = line[len("description:") :].strip()
        if val[:1] in ("|", ">"):
            # Block scalar: fold the following more-indented lines into one line,
            # stopping at the next (column-0) key so a later key's value is never
            # swallowed.
            folded = []
            for cont in fm[idx + 1 :]:
                if cont.strip() == "":
                    continue
                if not cont.startswith((" ", "\t")):
                    break
                folded.append(cont.strip())
            return " ".join(folded)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        return val
    return None


def _codex_agent_data(stem: str) -> dict:
    return tomllib.loads(
        (CODEX_AGENTS_DIR / f"{stem}.toml").read_text(encoding="utf-8")
    )


def test_agent_sets_match_across_runtimes():
    claude, codex = _claude_agent_stems(), _codex_agent_stems()
    assert claude == codex, (
        f"agent drift — only in .claude: {sorted(claude - codex)}; "
        f"only in .codex: {sorted(codex - claude)}"
    )


def test_every_codex_agent_is_valid_and_non_empty():
    for stem in sorted(_codex_agent_stems()):
        data = _codex_agent_data(stem)
        assert data.get("description", "").strip(), f"{stem}.toml: empty description"
        assert data.get("developer_instructions", "").strip(), (
            f"{stem}.toml: empty developer_instructions"
        )


def test_agent_descriptions_match_across_runtimes():
    """The one structured field shared by both formats must not drift."""
    for stem in sorted(_claude_agent_stems() & _codex_agent_stems()):
        md_desc = _md_frontmatter_description(CLAUDE_AGENTS_DIR / f"{stem}.md")
        toml_desc = _codex_agent_data(stem).get("description")
        assert md_desc == toml_desc, (
            f"{stem}: description drifted between .md and .toml\n"
            f"  .claude: {md_desc!r}\n  .codex:  {toml_desc!r}"
        )


# --- Instruction files: git-workflow / security are copied into three files ---
# CLAUDE.md delegates to rules/*.md via @-references, but AGENTS.md and
# GEMINI.md inline the same text. Anchor the canonical lines so an update to
# one is not silently forgotten in the others.
INSTRUCTION_FILES = [
    REPO_ROOT / ".claude/rules/git-workflow.md",
    REPO_ROOT / ".codex/AGENTS.md",
    REPO_ROOT / ".gemini/GEMINI.md",
]
SECURITY_FILES = [
    REPO_ROOT / ".claude/rules/security.md",
    REPO_ROOT / ".codex/AGENTS.md",
    REPO_ROOT / ".gemini/GEMINI.md",
]
COMMIT_TYPES_LINE = "- Types: feat, fix, refactor, docs, test, chore, perf, ci"
SECRETS_GATE_LINE = "- [ ] No hardcoded secrets (API keys, passwords, tokens)"


def test_commit_types_line_consistent_across_instruction_files():
    for path in INSTRUCTION_FILES:
        text = path.read_text(encoding="utf-8")
        assert COMMIT_TYPES_LINE in text, f"{path}: commit-types line drifted"


def test_secrets_gate_line_consistent_across_instruction_files():
    for path in SECURITY_FILES:
        text = path.read_text(encoding="utf-8")
        assert SECRETS_GATE_LINE in text, f"{path}: secrets pre-commit gate drifted"
