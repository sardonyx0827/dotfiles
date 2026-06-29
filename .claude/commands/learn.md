---
description: Analyze the current session, extract reusable problem-solving patterns, and save them as auto-discoverable skills for future sessions.
---

# /learn - Extract Reusable Patterns

Analyze the current session and extract any patterns worth saving as a skill.

## Trigger

Run `/learn` at any point during a session when you've solved a non-trivial problem.

## What to Extract

Look for:

1. **Error Resolution Patterns**
   - What error occurred?
   - What was the root cause?
   - What fixed it?
   - Is this reusable for similar errors?

2. **Debugging Techniques**
   - Non-obvious debugging steps
   - Tool combinations that worked
   - Diagnostic patterns

3. **Workarounds**
   - Library quirks
   - API limitations
   - Version-specific fixes

4. **Project-Specific Patterns**
   - Codebase conventions discovered
   - Architecture decisions made
   - Integration patterns

## Output Format

A skill MUST be a directory containing a `SKILL.md` file, otherwise Claude Code will not
discover it. A flat `.md` file is ignored.

Create the file at:

```
~/.claude/skills/learned-<pattern-name>/SKILL.md
```

- `<pattern-name>`: lowercase, hyphen-separated (e.g. `learned-vite-hmr-stale-cache`)
- The directory name and the `name` frontmatter field MUST match exactly.
- The `learned-` prefix keeps session-extracted skills grouped and easy to audit/remove.

The `SKILL.md` content:

```markdown
---
name: learned-<pattern-name>
description: <one-line summary>. Use this skill when <concrete trigger conditions>.
---

# <Descriptive Pattern Name>

> Extracted: <YYYY-MM-DD> · Context: <brief description of when this applies>

## Problem

<What problem this solves - be specific>

## Solution

<The pattern/technique/workaround>

## Example

<Code example if applicable>

## When to Use

<Trigger conditions - what should activate this skill>
```

### Frontmatter rules (critical)

- `name` and `description` are **required**. Without them the skill is invisible and never
  auto-activates.
- `description` is what drives auto-activation. It MUST contain explicit trigger conditions
  ("Use this skill when …" / "… whenever you are working on …"), not just a topic label.
  Mirror the style of existing skills (e.g. `debugging-protocol`, `shell-scripting-patterns`).
- Do NOT put the date/context inside the frontmatter; keep it in the body (see template).

## Process

1. Review the session for extractable patterns.
2. Identify the most valuable/reusable insight (one pattern per skill).
3. Draft the `SKILL.md` using the template above, paying special attention to a
   trigger-rich `description`.
4. Ask the user to confirm before saving.
5. Create the directory and write the file:
   `~/.claude/skills/learned-<pattern-name>/SKILL.md`
6. Confirm the skill is discoverable (it should appear in the skills list as
   `learned-<pattern-name>`).

## Notes

- Don't extract trivial fixes (typos, simple syntax errors).
- Don't extract one-time issues (specific API outages, etc.).
- Focus on patterns that will save time in future sessions.
- Keep skills focused - one pattern per skill.
- Periodically review `~/.claude/skills/learned-*` and delete stale entries; a wrong
  skill is worse than no skill.
