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
2. Split by logical unit — if the diff spans multiple unrelated concerns (e.g. an unrelated fix + refactor, or changes to separate features), plan one commit per concern instead of a single mixed commit
3. For each unit, stage its files (`git add` — skip if already staged)
4. Commit following the commit message format above, then repeat steps 3-4 for any remaining units

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

**This section is the single source of truth for delegation.** The `description` fields in
`.codex/agents/*.toml` ("Use PROACTIVELY", "MUST BE USED for all code changes", "Automatically
activated") exist so the _right_ agent is picked once delegation is already warranted — they are
capability advertisements, **not invocation mandates**, and they never override the layers below.
Where a description and this section disagree, this section wins.

### 1. Single (executed by the main agent itself) — Default

If any of the following apply, execute sequentially without delegating to SubAgents:

- Work that strongly depends on the immediately preceding conversation context or unconfirmed premises
- Continuously editing the same file, or where edit locations depend on the result of the previous step
- State transitions are sequential and intermediate results need review / user confirmation
- Small-scale changes of 1–2 files, interactive debugging, minor fixes

### 2. SubAgents (launched in parallel via Codex's agent feature)

Delegating is not free: each SubAgent re-establishes context, re-explores, and reports back, and the main agent then re-reads that report. Delegate when the payoff clearly exceeds that overhead — if any of the following apply (follow the `[agents]` settings in `config.toml` for the concurrency cap):

- Large-scale exploration where you don't want to pollute the context (Grep/search, log scanning, understanding the entire codebase)
- Parallel tasks that can run independently of each other (generating multiple proposals, multi-perspective reviews, test generation)
- Work where quality improves through role separation, such as Writer / Reviewer

Do NOT delegate when:

- You could finish the work yourself in a handful of tool calls (a few reads, a simple search, a couple of edits)
- One modest job would be split across several SubAgents. Parallel fan-out is for genuinely independent tracks, not for slicing one small task
- One SubAgent would do. Prefer one over several; keep spawn counts low

Once you delegate, commit to it: do not redo a SubAgent's work or re-derive its findings after it reports back.

Conventions when calling:

- Specify the "target file path" and "format of the artifact to return" for each SubAgent
- Return only a summary (diff / conclusion). Do not return raw logs to the main agent
- Do not launch SubAgents that write to the same file simultaneously (to avoid conflicting overwrites)

> Codex does not have an AgentTeam (tmux) layer like Claude. Parallel processing is done with SubAgents.

## 5. Model Selection Guidelines

- Main session: `model` is left unset in `config.toml`, so it inherits the Codex CLI's built-in default (auto-tracks on CLI update; pin a tier only to override)
- Reasoning effort (`model_reasoning_effort`): lower it for light work that needs no reasoning, such as Grep/search and template extraction; raise it for design, large-scale refactoring, and overall analysis
- SubAgents: follow `[agents]` in `config.toml` (`max_threads` / `max_depth`)
- On failure, raise the reasoning effort by one level and retry

## 6. Development Workflow

- For new features, bug fixes, and refactoring, follow the **tdd-workflow** skill (test-first; it defines the coverage policy)
- After writing or modifying code, review with the **code-reviewer** agent (for Go, use **go-reviewer**)
- Do not constantly inline the details of coding standards or patterns; instead follow the relevant skill
  (backend-patterns / frontend-patterns / golang-patterns / docker-patterns / postgres-patterns, etc.)
- Requests dropped into `docs/requests/` are driven to completion following the **request-harness** skill (auto-activates when handling `docs/requests/`)
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
