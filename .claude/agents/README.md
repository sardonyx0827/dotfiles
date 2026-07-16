# Agent Orchestration

## Available Agents

Located in `~/.claude/agents/`:

### Planning & Design

| Agent     | Model | Purpose                     | When to Use                   |
| --------- | ----- | --------------------------- | ----------------------------- |
| planner   | opus  | Implementation planning     | Complex features, refactoring |
| architect | opus  | System design & scalability | Architectural decisions       |

### Implementation & TDD

| Agent      | Model  | Purpose                                    | When to Use             |
| ---------- | ------ | ------------------------------------------ | ----------------------- |
| tdd-guide  | sonnet | Test-driven development (see tdd-workflow) | New features, bug fixes |
| e2e-runner | sonnet | E2E testing (Agent Browser / Playwright)   | Critical user flows     |

### Review & Quality

| Agent             | Model  | Purpose                                   | When to Use                    |
| ----------------- | ------ | ----------------------------------------- | ------------------------------ |
| code-reviewer     | sonnet | General code review                       | After writing code             |
| security-reviewer | sonnet | Security & OWASP Top 10 analysis          | Before commits                 |
| go-reviewer       | sonnet | Idiomatic Go review (concurrency, errors) | Go code changes                |
| database-reviewer | sonnet | PostgreSQL/Supabase query & schema review | SQL, migrations, schema design |

### Build & Maintenance

| Agent                | Model  | Purpose                                          | When to Use         |
| -------------------- | ------ | ------------------------------------------------ | ------------------- |
| build-error-resolver | sonnet | Fix TS/build errors (minimal diffs)              | When build fails    |
| go-build-resolver    | sonnet | Fix Go build/vet/lint errors                     | When Go builds fail |
| refactor-cleaner     | opus   | Dead code cleanup (knip, depcheck, ts-prune)     | Code maintenance    |
| doc-updater          | sonnet | Docs & codemaps (/update-docs, /update-codemaps) | Updating docs       |

### Delegation

| Agent           | Model  | Purpose                      | When to Use                                           |
| --------------- | ------ | ---------------------------- | ----------------------------------------------------- |
| codex-delegator | sonnet | Route decisions to Codex MCP | Spec discussion, fix strategy, complex tech decisions |

### Chores (docs/requests)

| Agent          | Model  | Purpose                                                             | When to Use                                                       |
| -------------- | ------ | ------------------------------------------------------------------- | ----------------------------------------------------------------- |
| request-worker | sonnet | Execute one docs/requests ticket end-to-end (request-harness skill) | Parallel processing of independent tickets picked up by /requests |

## Immediate Agent Usage

No user prompt needed:

1. Complex feature requests - Use **planner** agent
2. Code just written/modified - Use **code-reviewer** agent (**go-reviewer** for Go)
3. Bug fix or new feature - Use **tdd-guide** agent
4. Architectural decision - Use **architect** agent
5. Build failure - Use **build-error-resolver** (**go-build-resolver** for Go)
6. SQL / migration / schema work - Use **database-reviewer** agent
7. Handling auth, user input, or secrets - Use **security-reviewer** agent
8. Spec / strategy needs a second opinion - Use **codex-delegator** agent

## Parallel Task Execution

ALWAYS use parallel Task execution for independent operations:

```markdown
# GOOD: Parallel execution

Launch 3 agents in parallel:

1. Agent 1: Security analysis of auth.ts
2. Agent 2: Performance review of cache system
3. Agent 3: Type checking of utils.ts

# BAD: Sequential when unnecessary

First agent 1, then agent 2, then agent 3
```

## Multi-Perspective Analysis

For complex problems, use split role sub-agents:

- Factual reviewer
- Senior engineer
- Security expert
- Consistency reviewer
- Redundancy checker
