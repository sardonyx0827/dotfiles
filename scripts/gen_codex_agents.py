#!/usr/bin/env python3
"""Generate .codex/agents/*.toml from .claude/agents/*.md (the SSOT).

Why this exists
---------------
The same agents have to exist in two runtime formats: Claude Code reads
`.claude/agents/*.md` (YAML frontmatter + Markdown body), Codex reads
`.codex/agents/*.toml` (`name` / `description` / `developer_instructions`).
Neither runtime supports includes, so the body must be physically present in
both files -- a symlink (the fix used for `.codex/skills/`) is not available.

Hand-maintaining both copies had already failed: commit 7009f67 neutralized
the project-specific examples in the `.md` copies and never touched the
`.toml` twins, leaving a private project's domain vocabulary behind in
`.codex/`. Measured across all 14 pairs at that point, only ~4% of the body
delta was genuine runtime adaptation; ~81% was unintended drift.

Design constraints (learned the hard way -- do not relax without evidence)
-------------------------------------------------------------------------
* No tool names in the rules. Codex's format has no `tools` key at all, and
  Claude's tool list lives in frontmatter, which is not copied. Bodies mention
  tool-like words only as ordinary English ("Read error message carefully",
  "Denormalized for Read Performance"), so a `Read` -> `read` style rule would
  corrupt prose while buying nothing. Runtime-specific wording is neutralized
  in the SSOT instead of being substituted here.
* Literal (''') TOML strings only. Three agent bodies contain `\\*`, `` \\` ``
  and `\\/`, which are invalid escapes in a basic (\"\"\") string. The old
  hand-written `refactor-cleaner.toml` used \"\"\" and had silently lost the
  backslash from `api/products/\\*`. A literal string keeps the body byte-exact.
* The generator never reformats. Quote style, semicolons and blank lines in
  the body are Prettier's business; touching them here would churn the output
  and change the meaning of code examples.
"""

import argparse
import sys
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_AGENTS_DIR = REPO_ROOT / ".claude/agents"
CODEX_AGENTS_DIR = REPO_ROOT / ".codex/agents"

# Agents whose Codex body genuinely diverges in meaning (not just wording) and
# so stay hand-written. Keep this list as short as the evidence demands: today
# only codex-delegator qualifies, because the Claude copy documents the
# advisor-first escalation tier, which has no counterpart under Codex.
# A second entry is the signal to build a real block-level mechanism instead.
HAND_MAINTAINED = frozenset({"codex-delegator"})

# Narrow, deterministic runtime adaptations. Both are structural facts about
# where each runtime keeps its files -- neither is a tool name, so neither is
# affected when a runtime renames or adds tools.
SUBSTITUTIONS = (
    ("~/.claude/skills/", "~/.codex/skills/"),
    ("CLAUDE.md", "AGENTS.md"),
)

# After substitution no Claude-only path or instruction file may survive in a
# Codex body. This catches a rule that silently stopped matching far more
# robustly than pinning expected hit counts, which would break on every
# unrelated SSOT edit.
FORBIDDEN_RESIDUE = ("~/.claude/", "CLAUDE.md")


class GenerationError(RuntimeError):
    """Raised when an agent cannot be generated safely."""


def split_frontmatter(text: str, name: str) -> tuple[list[str], str]:
    """Split a Markdown agent into (frontmatter lines, body)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise GenerationError(f"{name}: missing frontmatter opening fence")
    try:
        end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    except StopIteration:
        raise GenerationError(f"{name}: unterminated frontmatter") from None
    return lines[1:end], "\n".join(lines[end + 1 :]).strip() + "\n"


def frontmatter_value(fm: list[str], key: str, name: str) -> str:
    """Read one scalar out of the frontmatter.

    Handles the shapes actually used here: plain scalar, quoted scalar, and a
    YAML folded block scalar (`>-`) whose continuation lines join with single
    spaces. No PyYAML dependency -- the fields are simple enough to fold by
    hand, and the test suite asserts the folded result matches the .toml copy.
    """
    prefix = f"{key}:"
    for idx, line in enumerate(fm):
        if not line.startswith(prefix):
            continue
        val = line[len(prefix) :].strip()
        if val[:1] in ("|", ">"):
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
    raise GenerationError(f"{name}: frontmatter has no `{key}`")


def adapt_body(body: str, name: str) -> str:
    """Apply the runtime substitutions and prove nothing Claude-only survived."""
    for src, dst in SUBSTITUTIONS:
        body = body.replace(src, dst)
    for residue in FORBIDDEN_RESIDUE:
        if residue in body:
            raise GenerationError(
                f"{name}: {residue!r} survives adaptation -- either a "
                f"substitution rule stopped matching, or the SSOT gained a "
                f"Claude-only reference that needs a neutral wording."
            )
    return body


def render_toml(name: str, description: str, body: str) -> str:
    """Serialize one agent deterministically.

    `description` is a basic string (it is one line and free of quotes today,
    which is asserted rather than assumed); the body is a literal string so it
    survives byte-exact.
    """
    if '"' in description or "\\" in description:
        raise GenerationError(
            f"{name}: description contains a quote or backslash; the basic-string "
            f"rendering here would need escaping rules before that is allowed."
        )
    if "'''" in body or body.rstrip("\n").endswith("'"):
        raise GenerationError(
            f"{name}: body cannot be held in a TOML literal string (contains "
            f"''' or ends with a quote)."
        )
    return (
        f'name = "{name}"\n'
        f'description = "{description}"\n'
        f"developer_instructions = '''\n"
        f"{body}"
        f"'''\n"
    )


def build(path: Path) -> tuple[str, str]:
    """Return (stem, rendered TOML) for one Markdown agent."""
    name = path.stem
    fm, body = split_frontmatter(path.read_text(encoding="utf-8"), name)
    description = frontmatter_value(fm, "description", name)
    rendered = render_toml(name, description, adapt_body(body, name))

    # Round-trip: the file we are about to write must parse, and the body must
    # come back byte-identical. This is what makes "the .toml is a faithful
    # copy" a checked fact rather than a hope.
    parsed = tomllib.loads(rendered)
    if parsed["developer_instructions"] != adapt_body(body, name):
        raise GenerationError(f"{name}: TOML round-trip altered the body")
    if parsed["description"] != description:
        raise GenerationError(f"{name}: TOML round-trip altered the description")
    return name, rendered


def source_agents() -> list[Path]:
    # README.md documents the directory; it is not an agent definition.
    return sorted(p for p in CLAUDE_AGENTS_DIR.glob("*.md") if p.stem != "README")


def _rel(path: Path) -> str:
    """Repo-relative path for messages, tolerating paths outside the repo.

    The dirs are module constants so tests can point them at a tmp_path, where
    relative_to(REPO_ROOT) would raise -- a reporting helper must never be the
    thing that fails.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def run(check: bool) -> int:
    problems: list[str] = []
    expected: set[str] = set()

    for path in source_agents():
        expected.add(path.stem)
        if path.stem in HAND_MAINTAINED:
            continue
        try:
            name, rendered = build(path)
        except GenerationError as exc:
            problems.append(str(exc))
            continue

        target = CODEX_AGENTS_DIR / f"{name}.toml"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == rendered:
            continue
        if check:
            verb = "is stale" if current is not None else "is missing"
            problems.append(f"{_rel(target)} {verb}")
        else:
            target.write_text(rendered, encoding="utf-8")
            print(f"wrote {_rel(target)}")

    # An agent deleted from the SSOT must not leave its Codex twin behind --
    # `git diff` alone would never notice an orphan that nobody rewrote.
    for orphan in sorted(CODEX_AGENTS_DIR.glob("*.toml")):
        if orphan.stem in expected:
            continue
        if check:
            problems.append(f"{_rel(orphan)} has no {orphan.stem}.md source")
        else:
            orphan.unlink()
            print(f"removed orphan {_rel(orphan)}")

    if problems:
        for p in problems:
            print(f"error: {p}", file=sys.stderr)
        if check:
            print(
                "\nRun `python3 scripts/gen_codex_agents.py` and commit the result.",
                file=sys.stderr,
            )
        return 1
    if check:
        print(f"{len(expected) - len(HAND_MAINTAINED)} generated agents are current.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed .toml files match the SSOT; write nothing",
    )
    args = parser.parse_args(argv)
    return run(check=args.check)


if __name__ == "__main__":
    sys.exit(main())
