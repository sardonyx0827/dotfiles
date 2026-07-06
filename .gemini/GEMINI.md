# GEMINI.md

> An instruction set treating Claude Code's `~/.claude/CLAUDE.md` as the source of truth, aligned for Gemini CLI.
> Details such as coding standards, testing, and security are not inlined here but delegated to the skills installed under `~/.claude/skills/` (Gemini CLI has no `Skill` tool of its own — before work matching a trigger below, read the referenced `SKILL.md` file directly with the file-read tool and follow it).

## 1. Language Policy (Required)

- All interactions and outputs must be in **Japanese**
- **Git commit messages must be written in English** (follow Conventional Commits)

## 2. Web / Browser Operations

- Prefer Gemini CLI's built-in `google_web_search` / `web_fetch` tools for searching and fetching web content
- If `fetch` / `curl` is needed instead, explain the reason before executing
- Do not send personal information or secrets to external services

## 3. Git Workflow

### Commit Message Format

```
<type>: <description>

<optional body>
```

- Written in English, following Conventional Commits
- Summary ~50 characters, add a body if needed
- Types: feat, fix, refactor, docs, test, chore, perf, ci

### Command Triggers

#### When a push is requested (e.g. "push して", "プッシュして", "push this")

1. Review the changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above
4. Push to remote (propose PR creation if direct push to the default branch is inappropriate)

#### When a commit is requested (e.g. "commit して", "コミットして", "commit this")

1. Review the changes (`git status` / `git diff`)
2. Stage files (`git add`) — skip if already staged
3. Commit following the commit message format above

#### When PR creation is requested (e.g. "pr作成して", "PR作って", "create a PR")

1. Review the current changes and branch structure
2. Create a new branch from the current branch (naming convention: `fix/`, `feat/`, `style/` prefix)
3. Commit following the commit message format above
4. Push the new branch to remote
5. Create a pull request against the original branch (follow the PR quality standards below)
6. Switch back to the original branch
7. Suggest deleting the working branch after merge

### Pull Request Quality Standards

1. Analyze the full commit history, not just the latest commit
2. Use `git diff [base-branch]...HEAD` to review all changes
3. Draft a comprehensive PR summary in Japanese
4. Include a test plan with TODOs
5. Push with the `-u` flag for new branches
6. If direct push to the default branch is inappropriate, propose PR creation

## 4. Execution Layer Selection (Single only)

Gemini CLI has no SubAgent / parallel-agent primitive like Claude Code's Task tool or Codex's `[agents]` config. Always execute directly in the main session:

- Break large tasks into smaller sequential steps instead of fanning out
- When editing the same file continuously, or when an edit location depends on the previous step's result, keep the work strictly sequential
- When intermediate results need review or user confirmation, stop and confirm before continuing
- For work that would benefit from independent parallel review (multiple proposals, multi-perspective review), run the passes yourself one at a time, or suggest the user switch to Claude Code / Codex for that layer

## 5. Model Selection Guidelines

- Main session model: `model.name` in `settings.json` (currently `gemini-3.1-flash-lite-latest`)
- Prefer a stronger model for design, large-scale refactoring, and overall analysis; the lighter default is fine for mechanical search/template work
- On failure, retry once with a stronger model before giving up

## 6. Development Workflow

Do not inline coding standards, patterns, or checklists here — read the relevant skill file under `~/.claude/skills/` first, then follow it:

- New features, bug fixes, refactoring → `~/.claude/skills/tdd-workflow/SKILL.md` (test-first, 80%+ coverage)
- General style and patterns → `~/.claude/skills/coding-standards/SKILL.md`, plus `backend-patterns` / `frontend-patterns` / `golang-patterns` / `docker-patterns` / `postgres-patterns` under the same directory as relevant
- Requests dropped into `docs/requests/` → `~/.claude/skills/request-harness/SKILL.md`
- Investigating bugs, test failures, or unexplained behavior → `~/.claude/skills/debugging-protocol/SKILL.md`
- After writing or modifying code, review it yourself against `~/.claude/skills/coding-standards/SKILL.md` before handing it back (Gemini CLI has no dedicated code-reviewer subagent to delegate to)

## 7. Safety Guards

- Always confirm with the user before executing destructive operations (`rm -rf` / force push / production DB operations, etc.)
- Follow the project's default rules for file edits, dependency additions, and external communication
- Exclude personal information and secrets from browser automation and external transmission

## 8. Security

Pre-commit gate (at minimum):

- [ ] No hardcoded secrets (API keys, passwords, tokens)
- [ ] All user inputs are validated
- [ ] Parameterized queries only (SQL injection prevention)
- [ ] Error messages and logs don't leak sensitive data

When implementing authentication, user input handling, secrets, API endpoints, payments, or file uploads,
read `~/.claude/skills/security-review/SKILL.md` and verify against its full checklist and vulnerability patterns.

If a security issue is found:

1. Stop immediately
2. Analyze the root cause
3. Fix CRITICAL issues before continuing
4. Rotate any exposed secrets
5. Review the entire codebase for similar issues
