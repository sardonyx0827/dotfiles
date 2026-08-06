"""Drift guards for the parts of the multi-runtime config that are still copied.

Agent bodies used to live here. They no longer do: `.codex/agents/*.toml` is
generated from `.claude/agents/*.md` by scripts/gen_codex_agents.py, and
tests/test_gen_codex_agents.py enforces that the committed output matches the
SSOT byte-for-byte. What is left in this file is the drift that generation
does not cover:

- codex-delegator, the one agent whose Codex body genuinely diverges in
  meaning (the Claude copy documents the advisor-first escalation tier, which
  has no Codex counterpart) and so is still hand-written.
- The git-workflow / security rules, which are copied verbatim into three
  instruction files that different runtimes read.
"""

import re

import tomllib
from conftest import REPO_ROOT
from gen_codex_agents import HAND_MAINTAINED, frontmatter_value, split_frontmatter

CLAUDE_AGENTS_DIR = REPO_ROOT / ".claude/agents"
CODEX_AGENTS_DIR = REPO_ROOT / ".codex/agents"


def _codex_agent_data(stem: str) -> dict:
    return tomllib.loads(
        (CODEX_AGENTS_DIR / f"{stem}.toml").read_text(encoding="utf-8")
    )


def _claude_description(stem: str) -> str:
    path = CLAUDE_AGENTS_DIR / f"{stem}.md"
    fm, _ = split_frontmatter(path.read_text(encoding="utf-8"), stem)
    return frontmatter_value(fm, "description", stem)


def test_every_codex_agent_is_valid_and_non_empty():
    for path in sorted(CODEX_AGENTS_DIR.glob("*.toml")):
        data = _codex_agent_data(path.stem)
        assert data.get("description", "").strip(), f"{path.name}: empty description"
        assert data.get("developer_instructions", "").strip(), (
            f"{path.name}: empty developer_instructions"
        )


def test_hand_maintained_agent_descriptions_match():
    """Generation guarantees this for the other agents; not for these."""
    for stem in sorted(HAND_MAINTAINED):
        assert _codex_agent_data(stem).get("description") == _claude_description(
            stem
        ), f"{stem}: description drifted between .md and the hand-written .toml"


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


# --- The delegation policy lives in one place, and the catalog must not re-grow it ---
# CLAUDE.md and AGENTS.md both said "Single is the default" and "do not delegate what you
# could finish yourself" while their Development Workflow section separately ordered a
# code-reviewer run after *every* edit. That second definition is gone: CLAUDE.md's
# Execution Layer Selection decides whether to delegate at all, and each agent's own
# description says when that agent applies. Nothing else may define review timing.
#
# The catalog must not grow its own policy back. Anchored to the heading form (`## ` at
# line start) on purpose: the README's own history paragraph quotes these titles inline
# while explaining why they were removed, and a plain substring test would fail on it.
AGENT_CATALOG = REPO_ROOT / ".claude/agents/README.md"
POLICY_HEADINGS = [
    "## Immediate Agent Usage",
    "## Parallel Task Execution",
    "## Multi-Perspective Analysis",
]

# `| <agent> | <model> | ...` -- the catalog's first two columns. The separator row's
# dashes also match the agent group (`-` is in the class) and the `| Agent | Model |`
# header does not (uppercase); both are dropped by the "is there a matching .md" filter.
_CATALOG_ROW = re.compile(
    r"^\|\s*(?P<agent>[a-z0-9-]+)\s*\|\s*(?P<model>[a-z]+)\s*\|", re.M
)


def test_agent_catalog_carries_no_delegation_policy():
    """agents/README.md is a catalog; CLAUDE.md owns when to delegate."""
    text = AGENT_CATALOG.read_text(encoding="utf-8")
    # Positive anchor first: a "must not contain" assertion alone would keep passing
    # against an emptied or renamed file.
    assert "Role separation (single source of truth):" in text, (
        "agents/README.md lost the block that points delegation policy at CLAUDE.md"
    )
    lines = text.splitlines()
    offenders = [
        f"{n}: {line}"
        for n, line in enumerate(lines, 1)
        for heading in POLICY_HEADINGS
        if line.startswith(heading)
    ]
    assert not offenders, (
        "agents/README.md re-grew a delegation-policy section; that policy belongs in "
        f"CLAUDE.md § Execution Layer Selection: {offenders}"
    )


# --- The concurrency cap is a number, and a number copied is a number that drifts ---
# CLAUDE.md § Execution Layer Selection owns how many SubAgents may run at once. Two
# dependents restated it as their own figure and then went stale when CLAUDE.md's cap
# moved: subagent-prompt-design/SKILL.md said "Per `CLAUDE.md`: run 2-4" (an attribution
# CLAUDE.md never supported) and hard-capped its checklist at 4, while agents/README.md
# described "CLAUDE.md's ... cap of four". CLAUDE.md says ~6 and endorses "five
# independent tickets, five agents", so the skill's checklist forbade a fan-out the SSOT
# explicitly allows -- a live contradiction an agent had to arbitrate mid-task.
#
# The fix is not "update 4 to 6" (that just re-arms the same trap); it is that these two
# files must not carry a global cap figure at all. Scoped counts stay legal on purpose:
# a command or skill saying "launch 2-4 request-workers *for this task*" is a recipe, not
# a claim about the global ceiling, so this guard covers only the two policy dependents.
CAP_DEPENDENTS = (
    REPO_ROOT / ".claude/skills/subagent-prompt-design/SKILL.md",
    REPO_ROOT / ".claude/agents/README.md",
)

# Matches a *global ceiling* phrasing only. "never exceed 4", "cap of four", "no more
# than 4 simultaneous" -- not a bare "2-4" range, which scoped recipes legitimately use.
_GLOBAL_CAP_CLAIM = re.compile(
    r"(never (?:exceed|more than)\s+\w+"
    r"|cap of \w+"
    r"|(?:maximum|max|at most|no more than)\s+\w+\s+(?:simultaneous|concurrent|parallel))",
    re.I,
)

# The second half of the same defect: SKILL.md opened its parallelism section with
# "Per `CLAUDE.md`: run 2-4 SubAgents concurrently". A bare "2-4" is legal (scoped
# recipes use it), but *sourcing a count to CLAUDE.md* is checkable and was false --
# CLAUDE.md has never carried that range. Citing the SSOT by name is what makes a
# reader stop verifying, so a wrong citation costs more than an uncited wrong number.
_ATTRIBUTED_COUNT = re.compile(
    r"(?:per|from|follows?)\s+.{0,4}CLAUDE\.md.{0,40}?\d", re.I
)


def test_dependents_do_not_restate_the_subagent_concurrency_cap():
    """CLAUDE.md owns the concurrency ceiling; nothing else may name its own number."""
    claude_md = (REPO_ROOT / ".claude/CLAUDE.md").read_text(encoding="utf-8")
    # Positive anchor: if CLAUDE.md ever loses the sentence, the guard below would
    # silently start policing a cap that no longer has an owner.
    assert "Past ~6 concurrent" in claude_md, (
        "CLAUDE.md lost its concurrency-cap sentence; this guard has no SSOT to defer to"
    )

    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}"
        for path in CAP_DEPENDENTS
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _GLOBAL_CAP_CLAIM.search(line) or _ATTRIBUTED_COUNT.search(line)
    ]
    assert not offenders, (
        "a delegation-policy dependent restated the global SubAgent concurrency cap, or "
        "sourced a count to CLAUDE.md that CLAUDE.md does not carry; defer to "
        "CLAUDE.md § Execution Layer Selection instead of naming a figure that goes "
        f"stale when CLAUDE.md's changes: {offenders}"
    )


def test_agent_catalog_model_column_matches_frontmatter():
    """The catalog's Model column duplicates frontmatter -- the same copy-without-a-guard
    shape that let the delegation policy drift. Pin it rather than trust it."""
    rows = {
        m.group("agent"): m.group("model")
        for m in _CATALOG_ROW.finditer(AGENT_CATALOG.read_text(encoding="utf-8"))
        if (CLAUDE_AGENTS_DIR / f"{m.group('agent')}.md").is_file()
    }
    stems = {p.stem for p in CLAUDE_AGENTS_DIR.glob("*.md")} - {"README"}
    assert rows.keys() == stems, (
        f"catalog rows and agents/*.md disagree on which agents exist: {rows.keys() ^ stems}"
    )
    for stem, model in sorted(rows.items()):
        fm, _ = split_frontmatter(
            (CLAUDE_AGENTS_DIR / f"{stem}.md").read_text(encoding="utf-8"), stem
        )
        assert frontmatter_value(fm, "model", stem) == model, (
            f"{stem}: catalog Model column drifted from the frontmatter"
        )
