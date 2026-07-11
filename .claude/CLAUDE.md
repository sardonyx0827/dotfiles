# CLAUDE.md

## Language Policy

- All interactions / outputs must be in **Japanese**
- **Git commit messages must be written in English** (follow the conventions in `@~/.claude/rules/git-workflow.md`)

## Browser Operations

- Use claude-in-chrome for fetching/operating web content
- If fetch / curl is needed, explain the reason before executing

## Git Operations

When instructed to push / commit / create a PR, follow the Command Triggers in `@~/.claude/rules/git-workflow.md`

## Execution Layer Selection (Single / SubAgents / AgentTeams)

When receiving a task, evaluate in the following order and execute at the first matching layer.

### 1. Single (executed by the main agent itself) - Default

If any of the following apply, execute sequentially without delegating to SubAgent / AgentTeam:

- Work that strongly depends on the immediately preceding conversation context or unconfirmed premises
- Continuously editing the same file, or where edit locations depend on the result of the previous step
- State transitions are sequential and intermediate results need review / user confirmation
- Small-scale changes of 1–2 files, interactive debugging, minor fixes

### 2. SubAgents (launched in parallel with the Agent tool)

If any of the following apply, actively launch SubAgents in parallel (guideline: 2–4 simultaneously):

- Large-scale exploration where you don't want to pollute the context (Glob/Grep, log scanning, understanding the entire codebase)
- Parallel tasks that can run independently of each other (generating multiple proposals, multi-perspective reviews, test generation)
- Work where quality improves through role separation, such as Writer / Reviewer

Conventions when calling:

- Specify the "file path" and "format of the artifact to return" for each SubAgent
- Return only a summary (diff / conclusion). Do not return raw logs to the main agent
- Do not launch SubAgents that write to the same file simultaneously (to avoid conflicting overwrites)

### 3. AgentTeams (tmux)

Use only for intermediate cases between Single and SubAgents where parallel processing is possible but context maintenance is difficult with SubAgents alone.
Launch conditions (when any of the following are met):

- The user has explicitly instructed AgentTeam / team / tmux launch
- Changes span multiple layers such as FE / BE / tests, and teammates need to consult with each other
- Debugging competing hypotheses, where independent teammates refute each other's hypotheses
- Large-scale refactoring or cross-cutting analysis that takes more than 10 minutes

## Model Selection Guidelines (Common to Single / SubAgent / AgentTeam)

- Main session: Opus (CLI default; not pinned via `model` in `settings.json`)
- Haiku: Tasks requiring no reasoning such as Glob/Grep, template extraction, document consistency checks
- Sonnet: Implementation / debugging / review (default for SubAgents)
- Opus: Design / large-scale refactoring / overall analysis / team lead
- On failure, retry with the next higher model

## Development Workflow

- For new features, bug fixes, and refactoring, follow the **tdd-workflow** skill (test-first, 80%+ coverage)
- After writing or modifying code, review with the **code-reviewer** agent (for Go, use **go-reviewer**)
- Do not always load code pattern/style details; instead follow the relevant skill (coding-standards / backend-patterns / frontend-patterns, etc.)

## Safety Guards

- Always confirm before executing destructive operations (rm -rf / force push / production DB operations, etc.)
- Personal information and secrets are excluded from browser automation

## External Agent Integration

When working on specification review, design, bug fixes, or test code creation, follow the **codex-consultation** skill.

Two second-opinion layers coexist — treat them as depth tiers, not interchangeable:

- **advisor (Opus)** — fast primary self-check over the whole trajectory. Call before substantive work, before declaring done, and when first stuck. Low friction (no prompt to author), so use it as the routine checkpoint.
- **Codex** — selective escalation for an independent, cross-vendor opinion, limited to the heavier cases the **codex-consultation** / **debugging-protocol** skills define (spec/design proposals, large-scale changes, test strategy, 2+ consecutive failed fixes → root-cause).
- **On conflicting advice** — do not silently pick a side; surface both opinions to the user.

## Visual Asset Generation

When a task needs a generated bitmap image written to disk (site `public/` assets, hero/OG images, illustrations, mockups), delegate to OpenAI Codex's built-in `image_gen` via the `mcp__codex__codex` tool. Follow the Triggers in `@~/.claude/rules/image-generation.md` (full workflow in the **codex-image-gen** skill). The built-in path needs no `OPENAI_API_KEY`; only true native transparency does — confirm with the user first.
