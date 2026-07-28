# Agent Catalog

Role separation (single source of truth):

- **`CLAUDE.md` § Execution Layer Selection**: whether to delegate at all and how many
  SubAgents to spawn — the only place those rules live
- **Each agent's own `description`**: the situation that agent covers, including when it
  applies (`code-reviewer` says "immediately after writing or modifying code")
- **`CLAUDE.md` § Model Selection Guidelines**: model tier and effort per agent
- **This README**: a catalog of which agents exist and what each one is for. It carries no
  delegation policy; if something here reads like an instruction to launch an agent, it
  has drifted and `CLAUDE.md` wins

This file used to duplicate the policy ("Immediate Agent Usage — no user prompt needed",
"ALWAYS use parallel Task execution", a five-role fan-out) and drifted into contradicting
`CLAUDE.md`'s Single-is-default rule and its cap of four simultaneous SubAgents. Keep it a
catalog.

The `When to Use` column describes the situation an agent fits, not a trigger that fires on
its own: delegation is decided in `CLAUDE.md` first, and this table only answers "which
one" once that decision is made.

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

## Codex counterparts

`.codex/agents/*.toml` is generated from these `.md` files by
`scripts/gen_codex_agents.py`, and `tests/test_gen_codex_agents.py` pins the committed
output byte-for-byte — edit the `.md` here and regenerate, never hand-edit the generated
`.toml`. `codex-delegator` is the one exception (hand-written, because its Claude body
documents the advisor-first escalation tier that has no Codex counterpart);
`tests/test_agent_parity.py` keeps its description in sync.
