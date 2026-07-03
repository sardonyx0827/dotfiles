---
description: "Usage: /auto-improve [--dry-run] [--scope <path>] [--max <N>] - Autonomously scan the project for improvement opportunities, prioritize them, auto-apply only safe fixes, and report the rest. Designed for repeated runs."
---

# Auto Improve

Discover, prioritize, and apply project improvements without explicit instructions.
This command is an orchestration layer: it finds and triages improvements, then
delegates heavy lifting to existing agents/commands (refactor-cleaner, doc-updater,
security-reviewer, /verify). Do NOT re-implement their logic here.

Design principle: small, repeatable runs. Improve the top few items per run
instead of attempting everything at once.

## Phase 0: Preflight

1. Detect project type(s) from marker files:
   - `go.mod` -> Go / `package.json` -> Node/TS / `*.sh`, `.zshrc` -> shell/dotfiles
   - `pyproject.toml` or `requirements.txt` -> Python / `Dockerfile`, `compose.yml` -> containers
   - `.github/workflows/` -> CI
     Pick scanners in Phase 1 based on what exists.
2. Check `git status`:
   - **Dirty worktree -> disable ALL auto-apply for this run (report-only mode).**
     Never mix generated changes into the user's in-progress work.
3. Read the improvement log `.claude/improve-log.md` if it exists.
   Items marked `rejected` or `wontfix` must NOT be proposed again.
   Items marked `done` are skipped unless they have regressed.

## Phase 1: Scan (read-only, parallel SubAgents)

Launch 2-4 read-only SubAgents in parallel, selected by project type.
Each agent returns a summary list of findings only (no raw logs), with
`file:line`, category, and a one-line rationale per finding.

Perspectives (pick what applies, skip what doesn't):

- **Code health**: linter/type-checker warnings, TODO/FIXME/HACK comments,
  obviously duplicated logic
- **Dead weight**: unused files/exports/dependencies candidates
  (candidates only — actual removal is delegated to refactor-cleaner in Phase 3)
- **Tests**: coverage gaps in core logic, skipped/disabled tests, missing
  tests for recently changed files
- **Dependencies**: outdated packages, known vulnerabilities
  (`npm audit` / `govulncheck` / etc.)
- **Docs drift**: README vs actual scripts/commands mismatch, stale setup
  instructions, undocumented entry points
- **CI/config**: unpinned action versions, missing timeout, obvious workflow
  inefficiencies

Honor `--scope <path>` by restricting all scans to that subtree.

## Phase 2: Prioritize

Score each finding: **impact (high/med/low) x effort (S/M/L) x risk (safe/careful/dangerous)**.

- Keep only the top N items (default 5, override with `--max <N>`).
- Prefer high-impact + small-effort + safe items.
- Explicitly list what was cut, in one line ("deferred: 12 low-impact findings").

## Phase 3: Apply by safety tier

Assign each selected item a tier, then act:

| Tier | Criteria                                                                                                                | Action                                                    |
| ---- | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| A    | Reversible, mechanical, behavior-preserving: lint autofix, formatting, typo fixes, doc sync, pinning CI action versions | Auto-apply (unless `--dry-run` or dirty worktree)         |
| B    | Small refactors, dead code removal, minor dependency bumps, new tests                                                   | Show concrete diff/plan, ask for approval before applying |
| C    | Architecture changes, major upgrades, anything touching auth/payments/data                                              | Report only, with a suggested approach                    |

Rules:

- Delegate Tier A/B work to the matching specialist when one exists:
  dead code -> refactor-cleaner, docs -> doc-updater, security findings -> security-reviewer.
- Never run two agents that write to the same file simultaneously.
- Any security finding follows the Security Response Protocol in
  `~/.claude/rules/security.md` regardless of tier.

## Phase 4: Verify

After any Tier A/B change is applied:

1. Run `/verify` (or the project's equivalent quality gate).
2. If verification fails, revert the offending change and downgrade the item
   to a Tier C report entry with the failure reason.

## Phase 5: Report and log

1. Update `.claude/improve-log.md` (create if missing) by appending one entry per item:

   ```markdown
   ## 2026-07-03

   - [done] fix: pin actions/checkout to v4 in ci.yml (Tier A)
   - [proposed] refactor: dedupe retry logic in sync.sh / backup.sh (Tier B)
   - [reported] deps: express 4 -> 5 major upgrade (Tier C)
   ```

   When the user declines a Tier B proposal, record it as `[rejected]` so future
   runs stay quiet about it.

2. Output the final report:

   ```
   AUTO-IMPROVE REPORT
   ===================
   Scanned: <perspectives run> / Scope: <path or repo root>

   APPLIED (Tier A)
   - <item> (verified: PASS)

   PROPOSED (Tier B - awaiting approval)
   - <item>: <one-line diff summary>

   REPORTED (Tier C)
   - <item>: <suggested approach>

   Deferred: <N> low-priority findings
   Verification: <PASS/FAIL>
   ```

3. Do NOT commit or push. Follow the git-workflow Command Triggers only when the
   user explicitly asks.

## Arguments

$ARGUMENTS:

- (none) - Scan + auto-apply Tier A + propose Tier B (default)
- `--dry-run` - Scan and report only; apply nothing
- `--scope <path>` - Restrict scanning and changes to a subtree
- `--max <N>` - Max improvements handled per run (default 5)
