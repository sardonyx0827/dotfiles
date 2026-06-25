# AGENTS.md

> An instruction set treating Claude Code's `~/.claude/CLAUDE.md` as the source of truth, aligned for Codex.
> Details such as coding standards, testing, and security are not inlined here but delegated to the installed skills.

## 1. Language Policy (Required)

- All interactions and outputs must be in **Japanese**
- **Git commit messages must be written in English** (follow Conventional Commits)

## 2. Web / Browser Operations

- Prefer Codex's web search feature (`web_search`) for searching and fetching web content
- If fetch / curl is needed, explain the reason before executing
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

> On `git push`, the PreToolUse hook (`hooks/git-push-review.sh`) presents a summary of the commits to be pushed and blocks the operation, so review the contents before re-running.

## 4. Execution Layer Selection (Single / SubAgents)

When a task is received, evaluate in the following order and execute at the first matching layer.

### 1. Single (executed by the main agent itself) — Default

If any of the following apply, execute sequentially without delegating to SubAgents:

- Work that strongly depends on the immediately preceding conversation context or unconfirmed premises
- Continuously editing the same file, or where edit locations depend on the result of the previous step
- State transitions are sequential and intermediate results need review / user confirmation
- Small-scale changes of 1–2 files, interactive debugging, minor fixes

### 2. SubAgents (launched in parallel via Codex's agent feature)

If any of the following apply, actively launch SubAgents in parallel (follow the `[agents]` settings in `config.toml`):

- Large-scale exploration where you don't want to pollute the context (Grep/search, log scanning, understanding the entire codebase)
- Parallel tasks that can run independently of each other (generating multiple proposals, multi-perspective reviews, test generation)
- Work where quality improves through role separation, such as Writer / Reviewer

Conventions when calling:

- Specify the "target file path" and "format of the artifact to return" for each SubAgent
- Return only a summary (diff / conclusion). Do not return raw logs to the main agent
- Do not launch SubAgents that write to the same file simultaneously (to avoid conflicting overwrites)

> Codex does not have an AgentTeam (tmux) layer like Claude. Parallel processing is done with SubAgents.

## 5. Model Selection Guidelines

- Main session: `model` in `config.toml` (default: gpt-5.5)
- Reasoning effort (`model_reasoning_effort`): lower it for light work that needs no reasoning, such as Grep/search and template extraction; raise it for design, large-scale refactoring, and overall analysis
- SubAgents: follow `[agents]` in `config.toml` (`max_threads` / `max_depth`)
- On failure, raise the reasoning effort by one level and retry

## 6. Development Workflow

- For new features, bug fixes, and refactoring, follow the **tdd-workflow** skill (test-first, 80%+ coverage)
- After writing or modifying code, review with the **code-reviewer** agent (for Go, use **go-reviewer**)
- Do not constantly inline the details of coding standards or patterns; instead follow the relevant skill
  (coding-standards / backend-patterns / frontend-patterns / golang-patterns / docker-patterns / postgres-patterns, etc.)
- When investigating bugs, test failures, or unexplained behavior, isolate the cause with a systematic debugging procedure

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
verify against the full checklist and vulnerability patterns by following the **security-review** skill.

If a security issue is found:

1. Stop immediately
2. Analyze the root cause
3. Fix CRITICAL issues before continuing
4. Rotate any exposed secrets
5. Review the entire codebase for similar issues
